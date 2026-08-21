"""
Recovering a partner's share after a refund — exactly once (§5.3).

تناغم clause 4 says how it comes back: «بإعادة حصة الطالب للفريق الأول وذلك عن
طريق حسمها من الفريق الثاني من المطالبات اللاحقة» — deducted from later claims,
never invoiced.

The trap these tests exist for: entitlement is cash basis, so a reversal
lowers what was collected all by itself. Raising an obligation on top of that
recovers the same dinar twice. The rule is therefore conditional — an
obligation only for what the partner has ALREADY been paid.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.billing.services import refund_service
from apps.settlements.models import ObligationType, PartnerObligation
from apps.settlements.services import claim_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
FULL = Decimal("270.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def approver(seeded_settings):
    """A second centre manager, so nobody approves their own claim (D-18)."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.recovery", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _refund(finance, manager, enrollment, amount):
    refund = refund_service.request_refund(
        actor=finance,
        enrollment=enrollment,
        refund_type="PARTIAL",
        amount=amount,
        reason_ar="إلغاء الدورة لعدم اكتمال العدد",
        official_letter_ref="LT-2026-11",
        official_letter_date=TERM_START,
        president_approval_ref="PR-2026-11",
        president_approval_date=TERM_START,
        code="RF-REC-1",
    )
    refund_service.approve_refund(actor=manager, refund=refund)
    return refund_service.execute_refund(actor=finance, refund=refund, executed_on=TERM_START)


def _claim(finance, agreement, cohort, period_to=PERIOD_END):
    return claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=cohort,
        period_from=TERM_START,
        period_to=period_to,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )


def test_a_refund_before_any_claim_raises_no_obligation(
    finance, manager, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    The reversal IS the correction — nothing else is owed.

    The partner has not been paid for this enrolment, so lowering what was
    collected is the whole story. An obligation here would take the money
    twice.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    refund = _refund(finance, manager, enrollment, Decimal("100.000"))

    assert refund.partner_recovery_amount == Decimal("0.000")
    assert not PartnerObligation.objects.filter(
        obligation_type=ObligationType.REFUND_RECOVERY
    ).exists()


def test_the_reversal_alone_lowers_the_partner_base(
    finance, manager, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    250 tuition shared at 50% is 125; refund 100 and the share is 75.

    The 100 comes off the tuition allocation, so the base is 150 and half of
    that is 75 — arrived at with no obligation and no deduction anywhere.

    Claimed inside the payment grace period on purpose: a refund leaves a
    balance owing, and past ``payment_overdue_days`` that would make the
    enrolment ineligible for a different reason entirely. That interaction is
    real and gets its own test below; here it would only obscure the
    arithmetic under examination.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    _refund(finance, manager, enrollment, Decimal("100.000"))

    claim = _claim(finance, percent_agreement, cohort_with_agreement, period_to=date(2026, 10, 10))

    assert claim.distribution_base == Decimal("150.000")
    assert claim.partner_share == Decimal("75.000")
    assert claim.total_deductions == Decimal("0.000")


def test_a_refund_that_leaves_a_debt_eventually_makes_the_enrolment_ineligible(
    finance, manager, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    §5.4 — «الشريك لا يستحق عن المتأخر عن الدفع».

    A refund hands money back without cancelling the charge, so the balance
    swings positive and the clock starts. Past the grace period the enrolment
    is overdue and the partner earns nothing from it AT ALL — not a reduced
    share, none. Worth pinning: it is a much larger effect than the refund
    itself, and it is the documented rule rather than a defect.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    _refund(finance, manager, enrollment, Decimal("100.000"))

    claim = _claim(finance, percent_agreement, cohort_with_agreement, period_to=PERIOD_END)

    assert claim.distribution_base == Decimal("0.000")
    assert claim.partner_share == Decimal("0.000")
    assert claim.lines.filter(is_included=False, exclusion_reason="PAYMENT_OVERDUE").exists()


def test_a_refund_after_an_approved_claim_raises_an_obligation(
    finance, manager, approver, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    Now the partner HAS been paid, so the share of the refund comes back.

    50% of a 100 refund is 50, recorded as an obligation pinned to the
    agreement that produced it.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim = _claim(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=approver, claim=claim)

    refund = _refund(finance, manager, enrollment, Decimal("100.000"))

    assert refund.partner_recovery_amount == Decimal("50.000")
    obligation = PartnerObligation.objects.get(obligation_type=ObligationType.REFUND_RECOVERY)
    assert obligation.amount == Decimal("50.000")
    assert obligation.restricted_to_agreement_id == percent_agreement.pk
    assert refund.code in obligation.statement_reference


def test_the_obligation_is_recovered_by_deduction_from_a_later_claim(
    finance, manager, approver, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    §5.3 — «بالحسم من المطالبات اللاحقة، لا نقداً».

    The next claim carries the recovery as a named deduction, so a partner
    seeing a smaller payment can read the row that made it smaller.
    """
    first = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim = _claim(finance, percent_agreement, cohort_with_agreement, period_to=date(2026, 10, 10))
    claim_service.approve_claim(actor=approver, claim=claim)
    _refund(finance, manager, first, Decimal("100.000"))

    make_paid_enrollment(cohort_with_agreement, index=2, amount=str(FULL))
    later = _claim(finance, percent_agreement, cohort_with_agreement, period_to=date(2026, 11, 10))
    deductions = claim_service.apply_offsets(actor=finance, claim=later)

    assert len(deductions) == 1
    assert deductions[0].amount == Decimal("50.000")
    assert deductions[0].deduction_type == "REFUND_RECOVERY"
    later.refresh_from_db()
    assert later.net_payable == later.partner_share - Decimal("50.000")


def test_a_fixed_per_student_partner_owes_nothing_back_on_a_refund(
    finance, manager, advance_agreement, priced_catalog, active_semester, make_paid_enrollment
) -> None:
    """
    Their share never read the base the refund moved.

    صرح is paid per head; a participant refunded part of their fee does not
    change the headcount. Recovering an advance for someone who became
    INELIGIBLE is ``clawback_service``'s job and a different question.
    """
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    cohort = Cohort.objects.create(
        code="CO-FIX-REC",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة مبلغ ثابت",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
        agreement=advance_agreement,
    )
    enrollment = make_paid_enrollment(cohort, index=9, amount=str(FULL))

    refund = _refund(finance, manager, enrollment, Decimal("100.000"))

    assert refund.partner_recovery_amount == Decimal("0.000")
    assert not PartnerObligation.objects.filter(
        obligation_type=ObligationType.REFUND_RECOVERY
    ).exists()


def test_a_cohort_without_a_partner_produces_no_recovery(
    finance, manager, priced_catalog, active_semester, make_paid_enrollment
) -> None:
    """No agreement, nobody to recover from."""
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    cohort = Cohort.objects.create(
        code="CO-SOLO-REC",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة بلا شريك",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
    )
    enrollment = make_paid_enrollment(cohort, index=10, amount=str(FULL))

    refund = _refund(finance, manager, enrollment, Decimal("100.000"))
    assert refund.partner_recovery_amount == Decimal("0.000")
