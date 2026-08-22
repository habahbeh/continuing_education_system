"""
The four policies the centre decided (Sprint 8D-3).

Sprint 8D-2 built the reviewed gateway and left four questions open, because
they were the client's to answer and not the code's. These are the answers,
each one asserted against the behaviour it is supposed to produce:

1. a credit goes forward onto a later registration, or back as cash;
2. an unpaid old debt stops a clearance, and therefore a certificate;
3. money recovered on a prior year is reported apart from this year's trade;
4. a payment clears the old debt first.

Two of them reverse a default Sprint 8D-2 argued for. The tests say so, so
that nobody later reads the reversal as a mistake.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.billing.models import (
    ALLOCATION_ORDER,
    ChargeLine,
    ChargeType,
    OpeningBalanceDirection,
    OpeningBalanceStatus,
)
from apps.billing.services import account_service
from apps.billing.services import opening_balance_service as obs

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
PASSWORD = "probe-password-1234"
DEBT = Decimal("650.000")
CREDIT = Decimal("225.000")


@pytest.fixture
def proposer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.p8d3", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def reviewer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.r8d3", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.8d3", password=PASSWORD, role=Role.CENTER_MANAGER)


@pytest.fixture
def live_enrollment(cohort_with_agreement, make_paid_enrollment):
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


@pytest.fixture
def unpaid_enrollment(cohort_with_agreement, make_paid_enrollment):
    """An enrolment with charges and no payment — 270 outstanding."""
    return make_paid_enrollment(cohort_with_agreement, index=2, amount="")


def _open_and_clear_custody(clearance_service, finance, manager, enrollment, code):
    """
    Open a clearance and get past step 1, so step 2 is the one under test.

    The MANAGER opens it: §3.6/30 gives ``C`` on this screen to the centre
    manager alone, and ``clearance_custody_role`` puts step 1 with them too.
    The finance officer's part is the first signature on step 2, which is
    where the old-debt guard lives.
    """
    del finance
    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        case_type="GRADUATION",
        opened_on=PERIOD_END,
        code=code,
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"label": "هوية المركز", "returned": True}],
    )
    return clearance


def _approved(proposer, reviewer, approver, *, code, direction, amount, enrollment=None):
    balance = obs.propose_manually(
        actor=proposer,
        code=code,
        direction=direction,
        amount=amount,
        as_of=AS_OF,
        description_ar="من دفعة 2022",
        legacy_number="202251024",
    )
    obs.review(actor=reviewer, balance=balance, enrollment=enrollment, note_ar="قوبل بالملف الورقي")
    if enrollment is not None:
        obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    balance.refresh_from_db()
    return balance


# ===========================================================================
# Decision 1 — what becomes of a credit
# ===========================================================================
def test_a_credit_is_carried_forward_onto_a_later_registration(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """
    «إذا كان الطالب سجّل مواد لاحقاً، يُرحَّل له الرصيد على ذلك التسجيل.»

    The enrolment owes less afterwards, and it owes less through its own term
    in the balance equation — not through a payment it never received.
    """
    before = account_service.get_account_state(live_enrollment)
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-1",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )

    obs.carry_forward(
        actor=approver,
        balance=balance,
        enrollment=live_enrollment,
        note_ar="للمشارك تسجيل لاحق في 2026",
    )

    after = account_service.get_account_state(live_enrollment)
    assert after.opening_credit_applied == CREDIT
    assert after.balance == before.balance - CREDIT
    assert after.total_paid == before.total_paid  # NOT a payment
    assert after.revenue == before.revenue  # NOT revenue

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.APPLIED
    assert balance.resolved_by_id == approver.pk
    assert balance.resolved_at is not None


def test_carrying_forward_creates_no_receipt(proposer, reviewer, approver, live_enrollment) -> None:
    """
    The line that must never be crossed.

    A credit applied is an obligation acknowledged, not money received. If a
    ``Receipt`` appeared here the system would be asserting that cash came
    through its till in 2026 for a 2022 overpayment it never saw.
    """
    from apps.cashbox.models import PaymentAllocation, Receipt

    receipts_before = Receipt.objects.count()
    allocations_before = PaymentAllocation.objects.count()
    lines_before = ChargeLine.objects.count()

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-2",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )
    obs.carry_forward(actor=approver, balance=balance, enrollment=live_enrollment, note_ar="ترحيل")

    assert Receipt.objects.count() == receipts_before
    assert PaymentAllocation.objects.count() == allocations_before
    assert ChargeLine.objects.count() == lines_before


def test_a_credit_with_no_later_registration_becomes_refund_due(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """
    «إذا لم يكن للطالب تسجيل لاحق، يُعاد له الرصيد.»

    Declaring it is the whole of this step. Handing over the cash is a real
    movement with a real document, recorded by the cashbox on the day it
    happens — this service will not manufacture one.
    """
    from apps.cashbox.models import Receipt

    receipts_before = Receipt.objects.count()
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-RD-1",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )

    obs.mark_refund_due(actor=approver, balance=balance, note_ar="لا تسجيل لاحق للمشارك")

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE
    assert balance.resolved_at is not None
    assert Receipt.objects.count() == receipts_before

    # And it reduces nothing, because nothing was carried anywhere.
    assert account_service.get_account_state(live_enrollment).opening_credit_applied == Decimal(
        "0.000"
    )


def test_a_credit_is_carried_forward_not_backward(
    proposer, reviewer, approver, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    "Later" is enforced, not assumed.

    Applying a 2026 credit to an enrolment dated before it would be rewriting
    a year that is already closed.
    """
    earlier = make_paid_enrollment(cohort_with_agreement, index=3, amount="270.000")
    earlier.enrolled_on = AS_OF - timedelta(days=400)
    earlier.save(update_fields=["enrolled_on"])

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-3",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=earlier,
    )

    with pytest.raises(obs.NotALaterRegistrationError, match="أسبق"):
        obs.carry_forward(
            actor=approver, balance=balance, enrollment=earlier, note_ar="ترحيل للخلف"
        )

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.APPROVED


def test_a_credit_is_resolved_once(proposer, reviewer, approver, live_enrollment) -> None:
    """Twice would hand the participant their money twice."""
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-4",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )
    obs.carry_forward(actor=approver, balance=balance, enrollment=live_enrollment, note_ar="ترحيل")
    balance.refresh_from_db()

    with pytest.raises(obs.AlreadyResolvedError):
        obs.carry_forward(
            actor=approver, balance=balance, enrollment=live_enrollment, note_ar="مرة ثانية"
        )
    with pytest.raises(obs.AlreadyResolvedError):
        obs.mark_refund_due(actor=approver, balance=balance, note_ar="أو ردّاً")

    assert account_service.get_account_state(live_enrollment).opening_credit_applied == CREDIT


def test_a_receivable_cannot_be_carried_forward_or_refunded(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """The two outcomes belong to credits alone — a debt is not a credit."""
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-5",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=live_enrollment,
    )

    with pytest.raises(obs.NotACreditError):
        obs.carry_forward(
            actor=approver, balance=balance, enrollment=live_enrollment, note_ar="ترحيل"
        )
    with pytest.raises(obs.NotACreditError):
        obs.mark_refund_due(actor=approver, balance=balance, note_ar="ردّ")


def test_resolving_a_credit_needs_a_reason(proposer, reviewer, approver, live_enrollment) -> None:
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CF-6",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )
    with pytest.raises(ValidationError):
        obs.mark_refund_due(actor=approver, balance=balance, note_ar="   ")


# ===========================================================================
# Decision 2 — old debt blocks clearance, and therefore the certificate
# ===========================================================================
def test_an_unpaid_old_debt_blocks_the_clearance(
    proposer, reviewer, approver, finance, live_enrollment
) -> None:
    """
    «لا يُمنح براءة ذمة حتى تُسدَّد الذمة القديمة.»

    The enrolment's own account is square — it was paid in full. What stops
    the clearance is a debt the balance equation cannot see, which is exactly
    the case BR-073 alone would have let through.
    """
    from apps.core.models import AuditEvent
    from apps.operations.services import clearance_service

    assert account_service.get_account_state(live_enrollment).balance == Decimal("0.000")

    _approved(
        proposer,
        reviewer,
        approver,
        code="OB-BLK-1",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=live_enrollment,
    )

    clearance = _open_and_clear_custody(
        clearance_service, finance, approver, live_enrollment, "CLR-8D3-1"
    )

    with pytest.raises(clearance_service.ClearanceBlockedError, match="ذمة قديمة"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    assert AuditEvent.objects.filter(
        action="DENIED_ATTEMPT", denial_rule="BR-094", reference=clearance.code
    ).exists()


def test_the_block_lifts_when_the_old_debt_is_posted_and_paid(
    proposer, reviewer, approver, finance, cashier, cash_method, live_enrollment
) -> None:
    """
    The debt has to be settleable, not merely blocking.

    Once posted it is a charge line and BR-073 owns it; once paid the account
    is square and the clearance proceeds. Both handovers are asserted because
    a guard that never lifts is a bug that looks like a policy.
    """
    from apps.cashbox.services import payment_service
    from apps.operations.services import clearance_service

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-BLK-2",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=live_enrollment,
    )
    obs.post(actor=approver, balance=balance)

    # Now visible to the balance equation, under its own rule.
    assert obs.unsettled_debt_for(live_enrollment.participant) == []
    assert account_service.get_account_state(live_enrollment).balance == DEBT

    payment_service.take_payment(
        actor=cashier,
        enrollment=live_enrollment,
        amount=DEBT,
        payment_method=cash_method,
        received_on=AS_OF,
    )
    assert account_service.get_account_state(live_enrollment).balance == Decimal("0.000")

    clearance = _open_and_clear_custody(
        clearance_service, finance, approver, live_enrollment, "CLR-8D3-2"
    )
    step = clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    assert step is not None


def test_an_unpaid_old_debt_blocks_the_certificate(
    proposer, reviewer, approver, finance, live_enrollment
) -> None:
    """
    «ولا شهادة حتى تُسدَّد.»

    Through BR-075 rather than a second guard: no certificate without a
    COMPLETED clearance, and the debt stops the clearance completing. Reusing
    the existing control is the point — a parallel check could drift from it.
    """
    from apps.operations.services import certificate_service, clearance_service

    _approved(
        proposer,
        reviewer,
        approver,
        code="OB-BLK-3",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=live_enrollment,
    )

    clearance = _open_and_clear_custody(
        clearance_service, finance, approver, live_enrollment, "CLR-8D3-3"
    )
    with pytest.raises(clearance_service.ClearanceBlockedError):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    with pytest.raises(certificate_service.ClearanceRequiredError):
        certificate_service.issue_certificate(
            actor=approver, enrollment=live_enrollment, grade="GOOD", issued_on=PERIOD_END
        )


def test_a_rejected_old_debt_blocks_nothing(proposer, reviewer, approver, live_enrollment) -> None:
    """
    Somebody looked at it and said no. That is an answer, and it stands.

    Rejected from REVIEWED rather than from APPROVED, because ``reject``
    refuses an approved balance — approval is the point of no return, and
    changing your mind after it is a new balance, not an edit of this one.
    """
    balance = obs.propose_manually(
        actor=proposer,
        code="OB-BLK-4",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        as_of=AS_OF,
        description_ar="ذمة مشكوك فيها",
    )
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="راجعت")
    obs.reject(actor=approver, balance=balance, note_ar="لا سند لهذه الذمة")

    assert obs.unsettled_debt_for(live_enrollment.participant) == []
    assert obs.unsettled_debt_total(live_enrollment.participant) == Decimal("0.000")


def test_a_credit_never_blocks_a_clearance_as_old_debt(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """The guard is about debts. Money the centre OWES is BR-071's business."""
    _approved(
        proposer,
        reviewer,
        approver,
        code="OB-BLK-5",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )
    assert obs.unsettled_debt_for(live_enrollment.participant) == []


def test_a_debt_on_one_enrollment_blocks_a_clearance_on_another(
    proposer, reviewer, approver, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    The reason the participant is recorded and not only the enrolment.

    Client decision 2 blocks the PERSON. Without the participant link a
    student could clear their 2026 enrolment while a debt sat on their 2022
    one, which is the loophole this closes.
    """
    from apps.operations.models import Cohort
    from apps.operations.services import clearance_service

    first = make_paid_enrollment(cohort_with_agreement, index=4, amount="270.000")
    # A second cohort, because unique(participant, cohort) rightly refuses the
    # same person twice on one cohort — the point here is one person across
    # two registrations, not two rows on one.
    other_cohort = Cohort.objects.create(
        code="CO-8D3-2",
        program=cohort_with_agreement.program,
        semester=cohort_with_agreement.semester,
        name_ar="دفعة لاحقة",
        starts_on=cohort_with_agreement.starts_on,
        ends_on=cohort_with_agreement.ends_on,
        capacity=25,
        agreement=cohort_with_agreement.agreement,
    )
    second = make_paid_enrollment(other_cohort, index=5, amount="270.000")
    second.participant = first.participant
    second.save(update_fields=["participant"])

    _approved(
        proposer,
        reviewer,
        approver,
        code="OB-BLK-6",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=first,
    )

    clearance = _open_and_clear_custody(clearance_service, finance, approver, second, "CLR-8D3-4")

    with pytest.raises(clearance_service.ClearanceBlockedError, match="ذمة قديمة"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)


# ===========================================================================
# Decision 3 — prior-year collections are reported apart
# ===========================================================================
def test_prior_year_collections_are_separated_from_current_revenue(
    proposer, reviewer, approver, finance, cashier, cash_method, unpaid_enrollment
) -> None:
    """
    «يُدرج تحصيلها كتسديد ذمم سنوات سابقة لا كإيراد سنة جارية.»

    One payment, large enough to clear the old debt and part of this term's
    fees, and the reports must take it apart correctly: the arrear on its own
    line, the current business on another, and the cash that actually came
    through the door still visible as a total.
    """
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-REP-1",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=unpaid_enrollment,
    )
    obs.post(actor=approver, balance=balance)

    payment_amount = DEBT + Decimal("100.000")
    payment_service_amount = payment_amount
    from apps.cashbox.services import payment_service

    payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=payment_service_amount,
        payment_method=cash_method,
        received_on=AS_OF,
    )

    revenue = report_service.revenue_report(actor=finance, **window)
    assert revenue["total"] == payment_amount
    assert revenue["prior_year_settlements"] == DEBT
    assert revenue["current_period_total"] == payment_amount - DEBT

    net = report_service.net_income_report(actor=finance, **window)
    assert net["prior_year_settlements"] == DEBT
    assert net["collected"] == payment_amount - DEBT
    assert net["total_cash_in"] == payment_amount
    # The arrear is not in the trading result.
    assert net["net_income"] == net["collected"] - net["partner_total"] - net["expenses_total"]


def test_ordinary_revenue_is_unaffected_when_no_arrear_was_collected(
    finance, cashier, cash_method, unpaid_enrollment
) -> None:
    """The split must cost nothing when there is nothing to split."""
    from apps.cashbox.services import payment_service
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("270.000"),
        payment_method=cash_method,
        received_on=AS_OF,
    )

    revenue = report_service.revenue_report(actor=finance, **window)
    assert revenue["prior_year_settlements"] == Decimal("0.000")
    assert revenue["current_period_total"] == revenue["total"]

    net = report_service.net_income_report(actor=finance, **window)
    assert net["prior_year_settlements"] == Decimal("0.000")
    assert net["total_cash_in"] == net["collected"]


# ===========================================================================
# Decision 4 — the old debt is paid first
# ===========================================================================
def test_the_opening_balance_is_first_in_the_allocation_order() -> None:
    """
    BR-022, reversed from Sprint 8D-2 by the client.

    8D-2 put it last and argued that money handed over for this term is for
    this term. The centre decided the other way: an old debt is the one most
    at risk of never being collected. Their call, recorded as a decision.
    """
    assert ALLOCATION_ORDER[0] == ChargeType.OPENING_BALANCE
    assert set(ALLOCATION_ORDER) == set(ChargeType.values)


def test_a_payment_clears_the_old_debt_before_this_terms_fees(
    proposer, reviewer, approver, cashier, cash_method, unpaid_enrollment
) -> None:
    """
    The order, exercised rather than read off a tuple.

    650 of old debt and 270 of current fees; a 700 payment must leave the
    arrear settled and 220 still owed on this term — not the other way round.
    """
    from apps.cashbox.services import payment_service

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-ALLOC-1",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=unpaid_enrollment,
    )
    line = obs.post(actor=approver, balance=balance)

    payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("700.000"),
        payment_method=cash_method,
        received_on=AS_OF,
    )

    assert account_service.outstanding_for_line(line) == Decimal("0.000")

    current = ChargeLine.objects.filter(enrollment=unpaid_enrollment).exclude(pk=line.pk)
    still_owed = sum(
        (account_service.outstanding_for_line(item) for item in current), Decimal("0.000")
    )
    assert still_owed == Decimal("220.000")


def test_a_part_payment_goes_entirely_to_the_old_debt(
    proposer, reviewer, approver, cashier, cash_method, unpaid_enrollment
) -> None:
    """Less than the arrear, so nothing at all reaches this term's fees."""
    from apps.cashbox.services import payment_service

    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-ALLOC-2",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        enrollment=unpaid_enrollment,
    )
    line = obs.post(actor=approver, balance=balance)

    payment_service.take_payment(
        actor=cashier,
        enrollment=unpaid_enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=AS_OF,
    )

    assert account_service.outstanding_for_line(line) == DEBT - Decimal("400.000")
    current = ChargeLine.objects.filter(enrollment=unpaid_enrollment).exclude(pk=line.pk)
    untouched = sum(
        (account_service.outstanding_for_line(item) for item in current), Decimal("0.000")
    )
    assert untouched == Decimal("270.000")


# ===========================================================================
# The 8D-1 and 8D-2 boundaries, still standing
# ===========================================================================
def test_the_whole_credit_workflow_still_writes_nothing_to_the_ledger(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """
    Propose → review → approve → resolve, with the census taken throughout.

    A credit's entire life leaves the ledger untouched, which is what makes
    "no fake receipts" a structural claim rather than a promise.
    """
    from apps.cashbox.models import PaymentAllocation, Receipt, ReceiptVoid
    from apps.settlements.models import PartnerClaim

    def census() -> dict[str, int]:
        return {
            model.__name__: model.objects.count()
            for model in (ChargeLine, Receipt, PaymentAllocation, ReceiptVoid, PartnerClaim)
        }

    before = census()
    balance = _approved(
        proposer,
        reviewer,
        approver,
        code="OB-CENSUS-1",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        enrollment=live_enrollment,
    )
    assert census() == before

    obs.carry_forward(actor=approver, balance=balance, enrollment=live_enrollment, note_ar="ترحيل")
    assert census() == before
