"""
Correcting a payout, dating one honestly, and the closed month (Sprint 8D-5).

Sprint 8D-4 closed the REFUND_DUE loop and left three gaps it named itself: a
payout could not be undone, could not be dated to the day the cash actually
left, and could be filed into a month somebody had already closed and
reported. These are the three.

The shape of the correction is the thing worth holding: **the original row is
never edited or deleted.** Its amount, date, voucher and payee stay exactly as
recorded, because the paper voucher in the filing cabinet still has to have a
row that matches it. The reversal is a second set of facts written beside the
first, and the obligation comes back.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.billing.models import (
    OpeningBalanceDirection,
    OpeningBalanceRefund,
    OpeningBalanceStatus,
)
from apps.billing.services import opening_balance_service as obs
from apps.core.services import period_service

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
PAID_ON = date(2026, 10, 5)
PASSWORD = "probe-password-1234"
CREDIT = Decimal("225.000")


@pytest.fixture
def proposer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.p8d5", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def payer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.pay8d5", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.8d5", password=PASSWORD, role=Role.CENTER_MANAGER)


@pytest.fixture
def live_enrollment(cohort_with_agreement, make_paid_enrollment):
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


@pytest.fixture
def closed_october(approver):
    """October 2026, closed and signed for — the month nobody may touch."""
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    return FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=approver,
        closed_at=timezone.now(),
    )


def _refund_due(proposer, payer, approver, enrollment, *, code="OB-8D5"):
    balance = obs.propose_manually(
        actor=proposer,
        code=code,
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        as_of=AS_OF,
        description_ar="رصيد دائن من 2022",
        legacy_number="202251024",
    )
    obs.review(actor=payer, balance=balance, enrollment=enrollment, note_ar="قوبل")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=approver, balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()
    return balance


def _pay(actor, balance, *, code="PAY-8D5", paid_on=PAID_ON, method=None):
    return obs.pay_refund_due(
        actor=actor,
        balance=balance,
        code=code,
        amount=balance.amount,
        paid_on=paid_on,
        payment_method=method,
        external_reference="SND-2026-0042",
        payee_name_ar="انوار رضوان الاحمد",
    )


# ===========================================================================
# Backdating
# ===========================================================================
def test_a_payout_can_be_dated_to_the_day_the_cash_left(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The voucher was paid on the 5th and entered later. It files as the 5th.

    Sprint 8D-4 hardcoded ``date.today()`` in the view, which put a Tuesday's
    cash in Thursday's day — and across a month end, in the wrong period
    entirely.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment)
    payout = _pay(payer, balance, method=cash_method, paid_on=PAID_ON)

    assert payout.paid_on == PAID_ON
    assert payout.paid_on != date.today()  # not the clock's answer, the clerk's


def test_the_payout_date_is_bounded_only_by_the_closed_period(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    A future-date guard was written here first, and removed.

    No other money movement in this system bounds its own date —
    ``take_payment`` accepts a forward-dated ``received_on`` and so does an
    expense — so refusing one here would make this screen reject a date the
    till accepts three clicks away. A rule about cash dates belongs to every
    movement at once or to none, and inventing it for the smallest of them
    would have been this sprint overreaching.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-FWD")
    ahead = date.today() + timedelta(days=30)

    payout = _pay(payer, balance, code="PAY-FWD", method=cash_method, paid_on=ahead)
    assert payout.paid_on == ahead


# ===========================================================================
# The closed period (D-23)
# ===========================================================================
def test_a_payout_into_a_closed_month_is_refused(
    proposer, payer, approver, cash_method, live_enrollment, closed_october
) -> None:
    """
    D-23 — «لا حركة مالية بتاريخ في فترة مقفلة».

    October is closed and reported. A payout filed into it would change a
    figure somebody has already signed.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-CP")

    with pytest.raises(period_service.ClosedPeriodError, match="مقفلة"):
        _pay(payer, balance, code="PAY-CP", method=cash_method, paid_on=PAID_ON)

    assert not OpeningBalanceRefund.objects.filter(opening_balance=balance).exists()
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE


def test_a_payout_into_an_open_month_beside_a_closed_one_is_fine(
    proposer, payer, approver, cash_method, live_enrollment, closed_october
) -> None:
    """The guard is about the date, not about the existence of closed periods."""
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-OP")
    payout = _pay(payer, balance, code="PAY-OP", method=cash_method, paid_on=date(2026, 11, 3))
    assert payout.paid_on == date(2026, 11, 3)


def test_a_reversal_into_a_closed_month_is_refused(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """The reversal's OWN date is guarded, on its own merits."""
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-RC")
    _pay(payer, balance, code="PAY-RC", method=cash_method, paid_on=date(2026, 11, 3))

    FinancialPeriod.objects.create(
        starts_on=date(2026, 11, 1),
        ends_on=date(2026, 11, 30),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=approver,
        closed_at=timezone.now(),
    )

    with pytest.raises(period_service.ClosedPeriodError):
        obs.reverse_refund_payout(
            actor=payer,
            balance=balance,
            reversed_on=date(2026, 11, 20),
            reason_ar="شيك مرتجع",
        )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED


def test_a_payout_in_a_now_closed_month_is_still_reversible_in_an_open_one(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The original date is history and stays untouched.

    A cheque paid in October and returned in November is corrected in
    November. Refusing that because October has since closed would leave a
    known-bad payout standing forever — and the guard exists to protect
    October's figures, which a November-dated correction does not touch.
    """
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-HIST")
    payout = _pay(payer, balance, code="PAY-HIST", method=cash_method, paid_on=PAID_ON)

    FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=approver,
        closed_at=timezone.now(),
    )

    obs.reverse_refund_payout(
        actor=payer,
        balance=balance,
        reversed_on=date(2026, 11, 4),
        reason_ar="شيك مرتجع من البنك",
    )

    payout.refresh_from_db()
    assert payout.paid_on == PAID_ON  # October, untouched
    assert payout.reversed_on == date(2026, 11, 4)


def test_the_closed_period_guard_is_the_one_the_expenses_already_used(
    approver, closed_october
) -> None:
    """
    Centralised, not copied.

    ``expense_service`` had the only implementation and everything else was on
    its honour. Two copies would disagree the first time one changed, and
    "which of our two closed-period rules applies here?" is not a question
    anybody should have to answer about a closed month.
    """
    from apps.expenses.services import expense_service

    assert expense_service.ClosedPeriodError is period_service.ClosedPeriodError
    assert period_service.is_open(date(2026, 10, 15)) is False
    assert period_service.is_open(date(2026, 11, 15)) is True


# ===========================================================================
# The reversal itself
# ===========================================================================
def test_a_reversal_reopens_the_obligation(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The centre still owes the money, so it goes back in the queue.

    A terminal "reversed" state would have left a real obligation with no row
    to act on — which is the failure this whole sprint exists to avoid.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-RE")
    _pay(payer, balance, code="PAY-RE", method=cash_method)
    balance.refresh_from_db()
    assert obs.outstanding_refunds(actor=payer) == []

    obs.reverse_refund_payout(
        actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="دُفع لغير صاحبه"
    )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE
    queue = obs.outstanding_refunds(actor=payer)
    assert [row["code"] for row in queue] == ["OB-8D5-RE"]


def test_the_reversal_never_deletes_or_edits_the_original(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The paper voucher still has to have a row that matches it.

    Amount, date, voucher number and payee are all exactly what they were;
    the correction is written beside them, never over them.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-KEEP")
    payout = _pay(payer, balance, code="PAY-KEEP", method=cash_method)
    original = (payout.amount, payout.paid_on, payout.external_reference, payout.payee_name_ar)

    obs.reverse_refund_payout(
        actor=payer,
        balance=balance,
        reversed_on=PERIOD_END,
        reason_ar="شيك مرتجع",
        reversal_reference="CORR-77",
    )

    payout.refresh_from_db()
    assert (
        payout.amount,
        payout.paid_on,
        payout.external_reference,
        payout.payee_name_ar,
    ) == original
    assert payout.reversal_reason_ar == "شيك مرتجع"
    assert payout.reversal_reference == "CORR-77"
    assert payout.reversed_by_id == payer.pk
    assert payout.is_reversed is True
    assert OpeningBalanceRefund.objects.filter(pk=payout.pk).exists()


def test_a_reversal_needs_a_reason(proposer, payer, approver, cash_method, live_enrollment) -> None:
    """No silent reversal — a voucher in the file with no explanation."""
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-NR")
    _pay(payer, balance, code="PAY-NR", method=cash_method)

    with pytest.raises(ValidationError, match="سبب عكس"):
        obs.reverse_refund_payout(
            actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="  "
        )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED


def test_the_database_refuses_a_half_written_reversal(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """A date with no reason is a correction nobody can evaluate."""
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-HALF")
    payout = _pay(payer, balance, code="PAY-HALF", method=cash_method)

    with pytest.raises(IntegrityError), transaction.atomic():
        OpeningBalanceRefund.objects.filter(pk=payout.pk).update(
            reversed_at=timezone.now(), reversed_on=PERIOD_END, active_key=None
        )


def test_a_second_reversal_is_refused(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-2R")
    _pay(payer, balance, code="PAY-2R", method=cash_method)
    obs.reverse_refund_payout(actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="خطأ")
    balance.refresh_from_db()

    with pytest.raises(obs.NotReversibleError):
        obs.reverse_refund_payout(
            actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="مرة ثانية"
        )

    assert OpeningBalanceRefund.objects.filter(opening_balance=balance).count() == 1


def test_reversing_a_balance_that_was_never_paid_is_refused(
    proposer, payer, approver, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-NP")

    with pytest.raises(obs.NotReversibleError, match="لا صرف قائم"):
        obs.reverse_refund_payout(
            actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="لا شيء"
        )


def test_the_reversal_is_audited_with_the_original_beside_it(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    from apps.core.models import AuditEvent

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-AU")
    _pay(payer, balance, code="PAY-AU", method=cash_method)
    obs.reverse_refund_payout(
        actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="شيك مرتجع"
    )

    event = AuditEvent.objects.filter(reference="PAY-AU", action="UPDATE").order_by("-id").first()
    assert event is not None
    assert event.entity_type == "billing.OpeningBalanceRefund"

    changes = event.changes or {}
    assert changes["original_paid_on"] == PAID_ON.isoformat()
    assert changes["original_voucher"] == "SND-2026-0042"
    assert changes["reason_ar"] == "شيك مرتجع"
    assert changes["balance_status"] == OpeningBalanceStatus.REFUND_DUE
    assert "محفوظ" in changes["original_row"]


def test_a_refused_reversal_leaves_its_denial_behind(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """BR-085 — the permission check runs outside the transaction."""
    from apps.core.models import AuditEvent

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-DN")
    _pay(payer, balance, code="PAY-DN", method=cash_method)
    before = AuditEvent.objects.filter(action="DENIED_ATTEMPT").count()

    with pytest.raises(PermissionDenied):
        obs.reverse_refund_payout(
            actor=approver, balance=balance, reversed_on=PERIOD_END, reason_ar="المدير"
        )

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT").count() == before + 1
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED


# ===========================================================================
# Paying again after a correction
# ===========================================================================
def test_a_corrected_balance_can_be_paid_again(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The whole reason the OneToOne became a ForeignKey.

    Both rows survive: the mistake and the correction, each with its own
    voucher, which is exactly what a reader reconciling the cash box needs.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-AGAIN")
    _pay(payer, balance, code="PAY-AGAIN-1", method=cash_method)
    obs.reverse_refund_payout(
        actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="لغير صاحبه"
    )
    balance.refresh_from_db()

    second = obs.pay_refund_due(
        actor=payer,
        balance=balance,
        code="PAY-AGAIN-2",
        amount=balance.amount,
        paid_on=PERIOD_END,
        payment_method=cash_method,
        external_reference="SND-2026-0099",
        payee_name_ar="انوار رضوان الاحمد",
    )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED
    assert OpeningBalanceRefund.objects.filter(opening_balance=balance).count() == 2
    assert second.active_key == 1
    assert OpeningBalanceRefund.objects.get(code="PAY-AGAIN-1").active_key is None


def test_two_live_payouts_are_impossible_at_the_database(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    ``(opening_balance, active_key)`` is unique and MySQL does not collide
    NULLs — the same device ``Clearance.active_key`` uses.

    A conditional UniqueConstraint would have read better and been silently
    skipped on this backend, which is how Sprint 8D-2's
    ``one_per_source_row`` came to exist in the model and not in the database.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-DB")
    _pay(payer, balance, code="PAY-DB-1", method=cash_method)

    with pytest.raises(IntegrityError), transaction.atomic():
        OpeningBalanceRefund.objects.create(
            code="PAY-DB-2",
            opening_balance=balance,
            amount=CREDIT,
            paid_on=PERIOD_END,
            payment_method=cash_method,
            external_reference="SND-DUP",
            payee_name_ar="شخص آخر",
            paid_by=payer,
        )


def test_the_8d2_source_row_constraint_now_exists_in_the_database(
    proposer, committed_batch
) -> None:
    """
    A defect found while designing 8D-5, fixed here.

    Sprint 8D-2 wrote this constraint with ``condition=...``, and MySQL has no
    partial indexes, so Django SKIPPED it silently — the protection looked
    structural and was service-level only. The condition was never needed:
    MySQL does not collide NULLs, so a plain unique already allows unlimited
    hand-entered balances while refusing a second one from the same archive
    row.
    """
    from django.db import connection

    from apps.datamigration.models import HistoricalEnrollment

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s "
            "AND non_unique = 0",
            ["billing_openingbalance", "billing_opening_balance_one_per_source_row"],
        )
        assert cursor.fetchone()[0] == 1

    historical = HistoricalEnrollment.objects.filter(batch=committed_batch).first()
    assert historical is not None
    for index in (1, 2):
        kwargs = {
            "actor": proposer,
            "historical_enrollment": historical,
            "code": f"OB-SRC-{index}",
            "direction": OpeningBalanceDirection.RECEIVABLE,
            "amount": Decimal("100.000"),
            "as_of": AS_OF,
            "description_ar": "ذمة",
        }
        if index == 1:
            obs.propose_from_archive(**kwargs)
        else:
            with pytest.raises(ValidationError):
                obs.propose_from_archive(**kwargs)


# ===========================================================================
# Nothing fake, nothing leaked
# ===========================================================================
def test_neither_payout_nor_reversal_creates_a_receipt_or_allocation(
    proposer, payer, approver, cashier, cash_method, live_enrollment
) -> None:
    from apps.cashbox.models import PaymentAllocation, Receipt
    from apps.cashbox.services import closing_service

    receipts = Receipt.objects.count()
    allocations = PaymentAllocation.objects.count()
    till, till_count = closing_service.system_total_for(cashier=cashier, closing_date=PAID_ON)

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-FAKE")
    _pay(payer, balance, code="PAY-FAKE", method=cash_method)
    obs.reverse_refund_payout(actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="خطأ")

    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations
    assert closing_service.system_total_for(cashier=cashier, closing_date=PAID_ON) == (
        till,
        till_count,
    )


def test_the_whole_archive_path_still_writes_nothing_to_ledger_or_cashbox(
    manager, sample_workbook
) -> None:
    """
    Read → validate → commit, with both ledgers counted.

    Restated in this sprint because 8D-5 adds two more money-moving services
    to the billing app, and the archive must remain unable to reach any of
    them.
    """
    from apps.billing.models import ChargeLine
    from apps.cashbox.models import PaymentAllocation, Receipt
    from apps.datamigration.services import archive_service, batch_service, validation_service

    def census() -> dict[str, int]:
        return {
            m.__name__: m.objects.count()
            for m in (ChargeLine, Receipt, PaymentAllocation, OpeningBalanceRefund)
        }

    before = census()
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-8D5")
    validation_service.validate(actor=manager, batch=batch)
    archive_service.commit(actor=manager, batch=batch)

    assert census() == before


def test_the_archive_still_cannot_import_a_financial_app() -> None:
    import ast
    from pathlib import Path

    apps_dir = Path(__file__).resolve().parents[3] / "apps"
    offenders: list[str] = []
    for path in (apps_dir / "datamigration").rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module.startswith(("apps.billing", "apps.cashbox", "apps.settlements")):
                offenders.append(f"{path.name} → {module}")
    assert not offenders, ", ".join(offenders)


def test_the_preserved_constraints_are_still_there() -> None:
    from apps.datamigration.models import HistoricalParticipant
    from apps.people.models import Participant

    number = Participant._meta.get_field("participant_number")
    assert number.max_length == 9
    assert "people_participant_number_format" in {c.name for c in Participant._meta.constraints}

    legacy = HistoricalParticipant._meta.get_field("legacy_number")
    assert legacy.unique is False
    assert not any(
        "format" in c.name or "regex" in c.name for c in HistoricalParticipant._meta.constraints
    )


# ===========================================================================
# Reporting
# ===========================================================================
def test_a_reversed_payout_stops_counting_as_a_paid_refund(
    proposer, payer, approver, finance, cash_method, live_enrollment
) -> None:
    """
    And the reversal is disclosed on its own date, not netted into the payout's.

    A cheque paid in October and returned in November did both things, in
    different months, and a reader of either month is entitled to see the one
    that happened in theirs.
    """
    from apps.reporting.services import report_service

    october = {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31)}
    november = {"date_from": date(2026, 11, 1), "date_to": date(2026, 11, 30)}

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-REP")
    _pay(payer, balance, code="PAY-REP", method=cash_method, paid_on=PAID_ON)

    paid_month = report_service.net_income_report(actor=finance, **october)
    assert paid_month["historical_refunds_paid"] == CREDIT
    assert paid_month["historical_refunds_net"] == CREDIT

    obs.reverse_refund_payout(
        actor=payer, balance=balance, reversed_on=date(2026, 11, 4), reason_ar="مرتجع"
    )

    after = report_service.net_income_report(actor=finance, **october)
    assert after["historical_refunds_paid"] == Decimal("0.000")
    assert after["historical_refunds_reversed"] == Decimal("0.000")

    nov = report_service.net_income_report(actor=finance, **november)
    assert nov["historical_refunds_reversed"] == CREDIT
    assert nov["historical_refunds_paid"] == Decimal("0.000")


def test_neither_payout_nor_reversal_touches_net_income_or_revenue(
    proposer, payer, approver, finance, cash_method, live_enrollment
) -> None:
    """
    Returning an inherited liability costs the year nothing, and taking the
    return back gives it nothing.
    """
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    before_net = report_service.net_income_report(actor=finance, **window)["net_income"]
    before_rev = report_service.revenue_report(actor=finance, **window)["total"]

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D5-NI")
    _pay(payer, balance, code="PAY-NI", method=cash_method)
    obs.reverse_refund_payout(actor=payer, balance=balance, reversed_on=PERIOD_END, reason_ar="خطأ")

    assert report_service.net_income_report(actor=finance, **window)["net_income"] == before_net
    assert report_service.revenue_report(actor=finance, **window)["total"] == before_rev
