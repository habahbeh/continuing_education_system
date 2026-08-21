"""
The headline arithmetic of Sprint 8A: a discount reaches the partner ONCE.

The demo's defect (C-02) was showing a discount share as a deduction without
subtracting it, overstating the base. The correction that Sprint 5 shipped —
a database constraint tying the base equation together — made the opposite
mistake possible: subtract the partner's burden from a base that had ALREADY
fallen, and the same discount lands twice.

These tests pin the exact dinars. On SC-NET at the university rate the quote
is 20 registration (excluded from the partner base, §4.2 · BR-009) and 250
tuition (shared), against a 50% agreement:

======================  ==========  ==========  ==============================
Case                    Base        Share       Why
======================  ==========  ==========  ==============================
no discount             250.000     125.000     the ordinary shape
50 discount             200.000     100.000     partner bore 25 = half of 50
double-counted (wrong)  175.000      87.500     what this sprint prevents
======================  ==========  ==========  ==============================
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.billing.services import discount_service
from apps.billing.services.account_service import get_account_state
from apps.settlements.services import claim_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)

#: What a university participant on SC-NET pays, from the Sprint 3 price list.
REGISTRATION = Decimal("20.000")
TUITION = Decimal("250.000")
FULL = REGISTRATION + TUITION


def _grant(manager, enrollment, amount: str):
    from apps.billing.models import DiscountType

    return discount_service.grant_discount(
        actor=manager,
        enrollment=enrollment,
        discount_type=DiscountType.AMOUNT,
        amount=Decimal(amount),
        reason_ar="حالة اجتماعية",
        president_approval_ref="PR-2026-77",
        president_approval_date=TERM_START,
    )


def _claim(finance, agreement, cohort):
    return claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=cohort,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )


def test_the_undiscounted_shape_is_the_baseline(
    finance, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """250 tuition shared at 50% is 125, and registration never enters it."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    claim = _claim(finance, percent_agreement, cohort_with_agreement)

    assert claim.excluded_registration == REGISTRATION
    assert claim.distribution_base == TUITION
    assert claim.partner_share == Decimal("125.000")


def test_a_discount_reaches_the_partner_exactly_once(
    manager, finance, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    A 50-dinar discount costs a 50% partner exactly 25 — not 50, and not 37.5.

    The participant pays 220 instead of 270, so 200 lands on the shared
    tuition line and the base is 200 of its own accord. The partner's share
    falls from 125 to 100: they bore half the discount because the agreement
    gives them half the revenue, which IS the by-ratio split the signed
    agreement calls for.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="")
    _grant(manager, enrollment, "50.000")

    from apps.cashbox.services import payment_service

    payment_service.take_payment(
        actor=_cashier_for(enrollment),
        enrollment=enrollment,
        amount=FULL - Decimal("50.000"),
        payment_method=_cash_method(),
        received_on=TERM_START,
    )

    assert get_account_state(enrollment).balance == Decimal("0.000")

    claim = _claim(finance, percent_agreement, cohort_with_agreement)

    # The base fell by the whole discount, because the participant paid less.
    assert claim.distribution_base == TUITION - Decimal("50.000")
    # And the burden is NOT subtracted a second time.
    assert claim.discount_partner_burden == Decimal("0.000")
    assert claim.partner_share == Decimal("100.000")

    # 125 - 100 = 25 — half of the 50, which is what the agreement says.
    assert Decimal("125.000") - claim.partner_share == Decimal("25.000")


def test_the_discount_row_records_the_burden_without_imposing_it(
    manager, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    ``partner_burden`` is a RECORD of what was borne, not an instruction.

    C-04 makes the two burdens reconstitute the discount exactly, so the row
    is readable on a statement — while the claim base stays untouched by it.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="")
    discount = _grant(manager, enrollment, "50.000")

    assert discount.partner_burden == Decimal("25.000")
    assert discount.university_burden == Decimal("25.000")
    assert discount.partner_burden + discount.university_burden == discount.amount
    assert discount.discount_split_mode_snapshot == "HALF"
    assert discount.base_amount == TUITION


def test_by_ratio_and_half_agree_at_fifty_percent(
    manager, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    تناغم is 50%, so «مناصفة» and «حسب النسبة» are the same number.

    Worth pinning: the agreement text says by-ratio and the model defaults to
    half, and at this rate the difference is invisible. It would not be at 60%,
    which is why the unsupported case is refused rather than approximated.
    """
    from apps.partners.models import DiscountSplitMode

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="")

    percent_agreement.discount_split_mode = DiscountSplitMode.BY_RATIO
    percent_agreement.save(update_fields=["discount_split_mode"])

    discount = _grant(manager, enrollment, "50.000")
    assert discount.partner_burden == Decimal("25.000")


def test_an_irreconcilable_split_is_refused_not_approximated(
    manager, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    HALF on a 60% agreement cannot be honoured, so it is refused by name.

    The partner absorbs 60% of any discount through the base whether the
    contract likes it or not, and moving 10% back needs a compensating
    movement no model here can express — ``ClaimDeduction.amount`` must be
    positive. Paying the wrong number quietly is the worse option.
    """
    from apps.partners.models import DiscountSplitMode

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="")

    percent_agreement.percent_rate = Decimal("60.0000")
    percent_agreement.discount_split_mode = DiscountSplitMode.HALF
    percent_agreement.save(update_fields=["percent_rate", "discount_split_mode"])

    with pytest.raises(discount_service.UnsupportedSplitError) as excinfo:
        _grant(manager, enrollment, "50.000")

    assert "30.000" in str(excinfo.value)  # what it would have to bear
    assert "25.000" in str(excinfo.value)  # what the contract asked for


def test_a_fixed_per_student_partner_is_untouched_by_a_discount(
    manager, advance_agreement, priced_catalog, active_semester, make_paid_enrollment
) -> None:
    """
    صرح collects 195 a head whatever the university charges the participant.

    Their share never reads the distribution base, so a discount costs them
    nothing and the university bears all of it — which is exactly what
    UNIVERSITY_ONLY means on that agreement.
    """
    from apps.catalog.models import Program
    from apps.operations.models import Cohort
    from apps.partners.models import DiscountSplitMode

    advance_agreement.discount_split_mode = DiscountSplitMode.UNIVERSITY_ONLY
    advance_agreement.save(update_fields=["discount_split_mode"])

    cohort = Cohort.objects.create(
        code="CO-FIX-1",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة مبلغ ثابت",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
        agreement=advance_agreement,
    )
    enrollment = make_paid_enrollment(cohort, index=7, amount="")

    discount = _grant(manager, enrollment, "50.000")

    assert discount.partner_burden == Decimal("0.000")
    assert discount.university_burden == Decimal("50.000")


def test_no_agreement_means_the_university_bears_the_whole_discount(
    manager, priced_catalog, active_semester, make_paid_enrollment
) -> None:
    """A cohort with no partner has nobody to share a discount with."""
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    cohort = Cohort.objects.create(
        code="CO-SOLO-1",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة بلا شريك",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
    )
    enrollment = make_paid_enrollment(cohort, index=8, amount="")

    discount = _grant(manager, enrollment, "40.000")

    assert discount.university_burden == Decimal("40.000")
    assert discount.partner_burden == Decimal("0.000")
    assert discount.discount_split_mode_snapshot == discount_service.NO_AGREEMENT_MODE


def test_the_claim_records_which_split_governed_the_period(
    finance, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """ADR-012 — read the claim, not an agreement that may since have moved."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    claim = _claim(finance, percent_agreement, cohort_with_agreement)

    assert claim.exclusions_snapshot["discount_split_mode"] == (
        percent_agreement.discount_split_mode
    )


# --- helpers -------------------------------------------------------------


def _cash_method():
    from apps.cashbox.models import PaymentMethod

    return PaymentMethod.objects.get(code="CASH")


def _cashier_for(enrollment):
    from apps.people.models import Role, User

    return User.objects.filter(role=Role.CASHIER).first() or User.objects.create_user(
        username="cash.discount", password="probe-password-1234", role=Role.CASHIER
    )
