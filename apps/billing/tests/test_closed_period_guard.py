"""
The closed month, applied to every dated money movement (Sprint 8D-6, D-23).

``FinancialPeriod``'s docstring has said since Sprint 4 that "every financial
write validates that its date falls in an OPEN period". It was not true.
Sprint 8D-5 centralised the rule and left it wired to three callers; this
sprint wires it to the rest, and these tests are the inventory.

Two properties matter more than the individual cases.

**A refusal leaves evidence.** ``require_open`` writes a DENIED_ATTEMPT under
D-23 before it raises, and it runs among the guards rather than inside the
transaction — otherwise the rollback would take the record of the attempt with
it, which is the defect BR-085 exists to prevent.

**A refusal writes nothing else.** Every case below counts the rows that the
movement would have created and asserts none of them appeared.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.services.period_service import ClosedPeriodError

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
IN_CLOSED = date(2026, 10, 5)
IN_OPEN = date(2026, 11, 3)
PASSWORD = "probe-password-1234"


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.8d6", password=PASSWORD, role=Role.CENTER_MANAGER)


@pytest.fixture
def closed_october(manager):
    """October 2026, closed and signed for."""
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    return FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=manager,
        closed_at=timezone.now(),
    )


@pytest.fixture
def paid_enrollment(cohort_with_agreement, make_paid_enrollment):
    """270 charged and 270 paid, on the term-start date."""
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


@pytest.fixture
def unpaid_enrollment(cohort_with_agreement, make_paid_enrollment):
    return make_paid_enrollment(cohort_with_agreement, index=2, amount="")


@pytest.fixture
def deposit_enrollment(cashier, cash_method, make_enrollment):
    """
    English level 1 — the one demo programme with a deposit policy.

    Paid in an OPEN month so the deposit exists to be returned or forfeited;
    the closed month in these tests is October, and this lands in September.
    """
    from apps.cashbox.services import payment_service

    enrollment, _quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("130.000"),
        payment_method=cash_method,
        received_on=TERM_START,
    )
    return enrollment


def _denials() -> int:
    from apps.core.models import AuditEvent

    return AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-23").count()


def _assert_denial_recorded(before: int, *, movement: str) -> None:
    """One DENIED_ATTEMPT, under D-23, naming the movement that was refused."""
    from apps.core.models import AuditEvent

    assert _denials() == before + 1
    event = (
        AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-23")
        .order_by("-id")
        .first()
    )
    assert event is not None
    assert (event.changes or {}).get("movement") == movement
    assert event.summary_ar


# ===========================================================================
# Cash in — the receipt
# ===========================================================================
def test_a_payment_in_an_open_period_still_works(
    cashier, cash_method, unpaid_enrollment, closed_october
) -> None:
    """
    The guard must not cost the ordinary case anything.

    Asserted first, and with a closed period present, because a guard that
    refuses everything also passes every refusal test.
    """
    from apps.cashbox.models import Receipt
    from apps.cashbox.services import payment_service

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=IN_OPEN,
    )

    assert receipt.received_on == IN_OPEN
    assert Receipt.objects.filter(pk=receipt.pk).exists()


def test_a_payment_into_a_closed_period_is_refused_and_recorded(
    cashier, cash_method, unpaid_enrollment, closed_october
) -> None:
    from apps.cashbox.models import PaymentAllocation, Receipt
    from apps.cashbox.services import payment_service

    receipts, allocations = Receipt.objects.count(), PaymentAllocation.objects.count()
    before = _denials()

    with pytest.raises(ClosedPeriodError, match="مقفلة"):
        payment_service.take_payment(
            actor=cashier,
            enrollment=unpaid_enrollment,
            amount=Decimal("100.000"),
            payment_method=cash_method,
            received_on=IN_CLOSED,
        )

    _assert_denial_recorded(before, movement="قبض")
    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations


# ===========================================================================
# The void — guarded on the period it CHANGES
# ===========================================================================
def test_voiding_a_receipt_from_a_closed_period_is_refused(
    finance, cashier, cash_method, unpaid_enrollment, manager
) -> None:
    """
    A void has no date of its own.

    Its reversing allocations hang off the receipt being cancelled, so
    cancelling an October receipt in November takes October's money out of
    October's report. 8D-5's rule of "check the correction's own date" gives
    the wrong answer here, for exactly the reason that rule exists: it is the
    month being changed that must be open.
    """
    from apps.cashbox.models import PaymentAllocation
    from apps.cashbox.services import payment_service
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=IN_CLOSED,
    )
    void = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ في المبلغ")

    # October closes only now — after the receipt, before the void.
    FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=manager,
        closed_at=timezone.now(),
    )

    allocations = PaymentAllocation.objects.count()
    before = _denials()

    with pytest.raises(ClosedPeriodError):
        payment_service.approve_void(actor=finance, void_record=void)

    _assert_denial_recorded(before, movement="إلغاء سند")
    assert PaymentAllocation.objects.count() == allocations
    receipt.refresh_from_db()
    assert receipt.status == "ISSUED"  # untouched


def test_voiding_a_receipt_from_an_open_period_still_works(
    finance, cashier, cash_method, unpaid_enrollment, closed_october
) -> None:
    from apps.cashbox.services import payment_service

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=IN_OPEN,
    )
    void = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    payment_service.approve_void(actor=finance, void_record=void)

    receipt.refresh_from_db()
    assert receipt.status == "VOIDED"


# ===========================================================================
# Cash out — refund, credit return, deposit return, forfeiture
# ===========================================================================
def test_executing_a_refund_into_a_closed_period_is_refused(
    finance, manager, paid_enrollment, closed_october
) -> None:
    from apps.billing.models import Refund
    from apps.billing.services import refund_service

    refund = refund_service.request_refund(
        actor=finance,
        enrollment=paid_enrollment,
        amount=Decimal("50.000"),
        reason_ar="انسحاب",
        refund_type="PARTIAL",
        code="RFD-8D6",
        official_letter_ref="LTR-1",
        official_letter_date=TERM_START,
        president_approval_ref="PRS-1",
        president_approval_date=TERM_START,
    )
    refund_service.approve_refund(actor=manager, refund=refund)
    before = _denials()

    with pytest.raises(ClosedPeriodError):
        refund_service.execute_refund(actor=finance, refund=refund, executed_on=IN_CLOSED)

    _assert_denial_recorded(before, movement="تنفيذ استرداد")
    refund.refresh_from_db()
    assert refund.status != "EXECUTED"
    assert Refund.objects.filter(code="RFD-8D6", executed_at__isnull=True).exists()


def test_returning_a_credit_into_a_closed_period_is_refused(
    finance, cashier, cash_method, paid_enrollment, closed_october
) -> None:
    from apps.billing.models import CreditReturn
    from apps.billing.services import credit_service
    from apps.cashbox.services import payment_service

    # Overpay in an OPEN month so a credit exists to return.
    payment_service.take_payment(
        actor=cashier,
        enrollment=paid_enrollment,
        amount=Decimal("80.000"),
        payment_method=cash_method,
        received_on=IN_OPEN,
    )
    before = _denials()

    with pytest.raises(ClosedPeriodError):
        credit_service.return_credit(
            actor=finance,
            enrollment=paid_enrollment,
            returned_on=IN_CLOSED,
            reason_ar="دفع زائد",
            code="CR-8D6",
        )

    _assert_denial_recorded(before, movement="ردّ رصيد دائن")
    assert not CreditReturn.objects.filter(code="CR-8D6").exists()


def test_returning_a_credit_into_an_open_period_still_works(
    finance, cashier, cash_method, paid_enrollment, closed_october
) -> None:
    from apps.billing.services import credit_service
    from apps.cashbox.services import payment_service

    payment_service.take_payment(
        actor=cashier,
        enrollment=paid_enrollment,
        amount=Decimal("80.000"),
        payment_method=cash_method,
        received_on=IN_OPEN,
    )
    returned = credit_service.return_credit(
        actor=finance,
        enrollment=paid_enrollment,
        returned_on=IN_OPEN,
        reason_ar="دفع زائد",
        code="CR-8D6-OK",
    )
    assert returned.amount == Decimal("80.000")


def test_returning_a_deposit_into_a_closed_period_is_refused(
    finance, deposit_enrollment, closed_october
) -> None:
    from apps.billing.models import DepositReturn
    from apps.billing.services import deposit_service

    before = _denials()

    with pytest.raises(ClosedPeriodError):
        deposit_service.return_deposit(
            actor=finance, enrollment=deposit_enrollment, returned_on=IN_CLOSED
        )

    _assert_denial_recorded(before, movement="إعادة تأمين")
    assert not DepositReturn.objects.filter(enrollment=deposit_enrollment).exists()


def test_returning_a_deposit_into_an_open_period_still_works(
    finance, deposit_enrollment, closed_october
) -> None:
    from apps.billing.services import deposit_service

    returned = deposit_service.return_deposit(
        actor=finance, enrollment=deposit_enrollment, returned_on=IN_OPEN
    )
    assert returned.amount > Decimal("0.000")


def test_forfeiting_a_deposit_into_a_closed_period_is_refused(
    finance, manager, deposit_enrollment, closed_october
) -> None:
    """
    A forfeiture turns a liability into REVENUE through a new charge line.

    So it moves the income of whatever month it is dated in — which is exactly
    what a closed month may not have moved.
    """
    from apps.billing.models import DepositForfeiture
    from apps.billing.services import deposit_service

    before = _denials()

    with pytest.raises(ClosedPeriodError):
        deposit_service.forfeit_deposit(
            actor=finance,
            enrollment=deposit_enrollment,
            reason="NO_SHOW",
            justification_ar="لم يحضر",
            forfeited_on=IN_CLOSED,
            approved_by=manager,
        )

    _assert_denial_recorded(before, movement="مصادرة تأمين")
    assert not DepositForfeiture.objects.filter(enrollment=deposit_enrollment).exists()


# ===========================================================================
# The three already guarded — still guarded, now audited
# ===========================================================================
def test_an_expense_into_a_closed_period_is_refused_and_now_recorded(
    finance, closed_october
) -> None:
    """
    Guarded since 8C-2, audited since 8D-6.

    The refusal always worked; what it never did was leave a trace, so nobody
    could later ask who had tried.
    """
    from apps.expenses.models import Expense
    from apps.expenses.services import expense_service

    before = _denials()

    with pytest.raises(expense_service.ClosedPeriodError):
        expense_service.record(
            actor=finance,
            code="EXP-8D6",
            category="MARKETING",
            amount=Decimal("40.000"),
            incurred_on=IN_CLOSED,
            description_ar="إعلان",
        )

    _assert_denial_recorded(before, movement="مصروف")
    assert not Expense.objects.filter(code="EXP-8D6").exists()


def test_the_opening_balance_payout_guard_still_holds_and_now_records(
    finance, manager, cash_method, paid_enrollment, closed_october
) -> None:
    """Sprint 8D-5's guard, unchanged in behaviour and now audited."""
    from apps.billing.models import OpeningBalanceDirection, OpeningBalanceRefund
    from apps.billing.services import opening_balance_service as obs
    from apps.people.models import Role, User

    second_officer = User.objects.create_user(
        username="fin.8d6.two", password=PASSWORD, role=Role.FINANCE_OFFICER
    )
    balance = obs.propose_manually(
        actor=finance,
        code="OB-8D6",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("225.000"),
        as_of=TERM_START,
        description_ar="رصيد دائن",
    )
    obs.review(actor=second_officer, balance=balance, enrollment=paid_enrollment, note_ar="قوبل")
    obs.approve(actor=manager, balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=manager, balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()

    before = _denials()
    with pytest.raises(ClosedPeriodError):
        obs.pay_refund_due(
            actor=finance,
            balance=balance,
            code="PAY-8D6",
            amount=balance.amount,
            paid_on=IN_CLOSED,
            payment_method=cash_method,
            external_reference="SND-1",
            payee_name_ar="مستلم",
        )

    _assert_denial_recorded(before, movement="صرف رصيد افتتاحي")
    assert not OpeningBalanceRefund.objects.filter(code="PAY-8D6").exists()


# ===========================================================================
# The rule itself
# ===========================================================================
def test_a_date_with_no_period_at_all_is_allowed(finance, cash_method, unpaid_enrollment) -> None:
    """
    Periods are opened as the centre needs them.

    Refusing every date outside one would make the system unusable before the
    first period is defined — and the centre's own history predates any period
    row by four years.
    """
    from apps.core.services import period_service

    assert period_service.period_for(date(2019, 3, 1)) is None
    assert period_service.is_open(date(2019, 3, 1)) is True


def test_an_open_period_row_does_not_block_anything(
    manager, cashier, cash_method, unpaid_enrollment
) -> None:
    from apps.cashbox.services import payment_service
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    FinancialPeriod.objects.create(
        starts_on=date(2026, 11, 1),
        ends_on=date(2026, 11, 30),
        status=FinancialPeriodStatus.OPEN,
    )
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=IN_OPEN,
    )
    assert receipt.received_on == IN_OPEN


def test_the_refusal_is_decided_before_any_transaction(
    cashier, cash_method, unpaid_enrollment, closed_october
) -> None:
    """
    BR-085 — the audit row survives the raise.

    Written as its own test rather than trusted from the others: the whole
    point of the DENIED_ATTEMPT is that it is still there afterwards, and a
    guard placed one line lower would pass every other test in this file
    while losing the evidence.
    """
    from apps.cashbox.services import payment_service
    from apps.core.models import AuditEvent

    with pytest.raises(ClosedPeriodError):
        payment_service.take_payment(
            actor=cashier,
            enrollment=unpaid_enrollment,
            amount=Decimal("100.000"),
            payment_method=cash_method,
            received_on=IN_CLOSED,
        )

    event = (
        AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-23")
        .order_by("-id")
        .first()
    )
    assert event is not None
    assert event.actor_id == cashier.pk
    assert (event.changes or {})["movement_date"] == IN_CLOSED.isoformat()


def test_charge_creation_is_deliberately_not_guarded(finance, manager, paid_enrollment) -> None:
    """
    The boundary of this sprint, stated rather than left to be discovered.

    A charge line is an accrual, not a cash movement, and one path creates
    them ON PURPOSE with a historical date: ``opening_balance_service.post``
    dates its line ``as_of``, which is 2022 for a debt carried in from the
    workbooks. Guarding ``charged_on`` would make posting an opening balance
    impossible the moment the centre closes an old period, which is the
    opposite of what the archive is for.

    Whether enrolment charges should be period-guarded is a real question and
    a wider one — it would refuse enrolling anybody into a closed month — so
    it is left open rather than answered as a side effect here.
    """
    import inspect

    from apps.billing.services import charge_service

    source = inspect.getsource(charge_service)
    assert "require_open" not in source
    assert "period_service" not in source


def test_discounts_are_deliberately_not_guarded(finance) -> None:
    """
    The other deliberate omission, recorded for the same reason.

    A ``Discount`` reduces ``total_due`` and carries no posting date. Its only
    date is ``president_approval_date``, which records when the president
    signed BR-030's approval — not when the reduction hits the account.
    Guarding that would refuse a discount because a signature happened to fall
    in a closed month, which is the wrong field answering the wrong question.

    Giving ``Discount`` a real posting date is a schema change and an
    accounting decision about which month a waiver belongs to. Neither is
    something a period-guard sprint should settle on its own.
    """
    import inspect

    from apps.billing.models import Discount
    from apps.billing.services import discount_service

    source = inspect.getsource(discount_service)
    assert "require_open" not in source
    assert "period_service" not in source

    # The absence of a posting date is the actual reason, so it is asserted
    # rather than described: if somebody adds one, this test fails and the
    # decision gets revisited on purpose.
    #
    # Each of the three that DO exist answers a different question, and none
    # of them is "which month does this reduction belong to":
    #   president_approval_date — when BR-030's signature happened
    #   approved_at             — when this system recorded that signature
    #   created_at              — when the row was typed
    date_fields = {
        field.name
        for field in Discount._meta.get_fields()
        if field.get_internal_type() in {"DateField", "DateTimeField"}
    }
    assert date_fields == {"president_approval_date", "approved_at", "created_at"}


def test_reversing_a_payout_into_a_closed_period_is_refused_and_recorded(
    finance, manager, cash_method, paid_enrollment
) -> None:
    """
    The reversal path's own DENIED_ATTEMPT — Sprint 8D-6 left this indirect.

    8D-6 covered it through the shared helper and said so; a shared helper is
    not evidence that a particular caller passes it the right arguments. This
    asserts the reversal's own audit row, and that a refused reversal leaves
    absolutely everything as it was: the payout still live, the balance still
    REFUNDED, and no second row anywhere.
    """
    from apps.billing.models import (
        OpeningBalanceDirection,
        OpeningBalanceRefund,
        OpeningBalanceStatus,
    )
    from apps.billing.services import opening_balance_service as obs
    from apps.cashbox.models import PaymentAllocation, Receipt
    from apps.core.models import AuditEvent, FinancialPeriod, FinancialPeriodStatus
    from apps.people.models import Role, User

    second_officer = User.objects.create_user(
        username="fin.8d6a.two", password=PASSWORD, role=Role.FINANCE_OFFICER
    )
    balance = obs.propose_manually(
        actor=finance,
        code="OB-8D6A",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("225.000"),
        as_of=TERM_START,
        description_ar="رصيد دائن",
    )
    obs.review(actor=second_officer, balance=balance, enrollment=paid_enrollment, note_ar="قوبل")
    obs.approve(actor=manager, balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=manager, balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()

    # Paid in an OPEN month, so only the reversal's own date is at issue.
    payout = obs.pay_refund_due(
        actor=finance,
        balance=balance,
        code="PAY-8D6A",
        amount=balance.amount,
        paid_on=IN_OPEN,
        payment_method=cash_method,
        external_reference="SND-8D6A",
        payee_name_ar="مستلم",
    )
    balance.refresh_from_db()

    # October closes; the correction is attempted into it.
    FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=manager,
        closed_at=timezone.now(),
    )

    receipts, allocations = Receipt.objects.count(), PaymentAllocation.objects.count()
    payouts = OpeningBalanceRefund.objects.count()
    before = _denials()

    with pytest.raises(ClosedPeriodError, match="مقفلة"):
        obs.reverse_refund_payout(
            actor=finance,
            balance=balance,
            reversed_on=IN_CLOSED,
            reason_ar="شيك مرتجع",
        )

    # 1 · the refusal is on the record, under D-23, naming this movement.
    _assert_denial_recorded(before, movement="عكس صرف")
    event = (
        AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-23")
        .order_by("-id")
        .first()
    )
    assert event is not None
    # The pair pins the path: the payout guard uses the same entity_type but
    # the movement «صرف رصيد افتتاحي», so this row can only be the reversal.
    assert event.entity_type == "billing.OpeningBalanceRefund"
    assert event.reference == "PAY-8D6A"
    assert (event.changes or {})["movement_date"] == IN_CLOSED.isoformat()
    assert event.actor_id == finance.pk

    # 2 · the original payout is untouched and still live.
    payout.refresh_from_db()
    assert payout.reversed_at is None
    assert payout.reversed_on is None
    assert payout.reversal_reason_ar == ""
    assert payout.active_key == 1
    assert payout.paid_on == IN_OPEN
    assert payout.external_reference == "SND-8D6A"

    # 3 · the balance did not reopen.
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED
    assert obs.outstanding_refunds(actor=finance) == []

    # 4 · nothing was created anywhere.
    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations
    assert OpeningBalanceRefund.objects.count() == payouts


def test_a_reversal_into_an_open_period_still_works_after_all_this(
    finance, manager, cash_method, paid_enrollment, closed_october
) -> None:
    """The guard refuses a closed month, not reversals in general."""
    from apps.billing.models import OpeningBalanceDirection, OpeningBalanceStatus
    from apps.billing.services import opening_balance_service as obs
    from apps.people.models import Role, User

    second_officer = User.objects.create_user(
        username="fin.8d6a.ok", password=PASSWORD, role=Role.FINANCE_OFFICER
    )
    balance = obs.propose_manually(
        actor=finance,
        code="OB-8D6A-OK",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("225.000"),
        as_of=TERM_START,
        description_ar="رصيد دائن",
    )
    obs.review(actor=second_officer, balance=balance, enrollment=paid_enrollment, note_ar="قوبل")
    obs.approve(actor=manager, balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=manager, balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()

    obs.pay_refund_due(
        actor=finance,
        balance=balance,
        code="PAY-8D6A-OK",
        amount=balance.amount,
        paid_on=IN_OPEN,
        payment_method=cash_method,
        external_reference="SND-OK",
        payee_name_ar="مستلم",
    )
    balance.refresh_from_db()

    obs.reverse_refund_payout(
        actor=finance, balance=balance, reversed_on=IN_OPEN, reason_ar="شيك مرتجع"
    )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE
    assert [row["code"] for row in obs.outstanding_refunds(actor=finance)] == ["OB-8D6A-OK"]
