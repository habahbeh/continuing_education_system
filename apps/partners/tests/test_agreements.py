"""
The agreement is the contract the money is split by, so it must be complete.

Every test here is about a shape the demo permitted and the database now
refuses. The headline one is C-01: the demo applied BY_RATIO discount
splitting to a fixed-per-student agreement, treating 195 dinars as a
percentage, and printed a negative university share.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.partners.models import (
    Agreement,
    AgreementProgramSnapshot,
    AgreementStatus,
    CalculationModel,
    DiscountSplitMode,
    Partner,
    PartnerType,
)

pytestmark = pytest.mark.django_db

TERMS = {
    "signed_on": date(2026, 8, 1),
    "valid_from": date(2026, 9, 1),
    "valid_to": date(2027, 8, 31),
}


@pytest.fixture
def a_partner() -> Partner:
    return Partner.objects.create(
        code="PRT-TEST", name_ar="شريك اختبار", partner_type=PartnerType.COMPANY
    )


def _agreement(a_partner: Partner, **overrides: object) -> Agreement:
    fields: dict = {
        "agreement_number": "T/001",
        "partner": a_partner,
        "title_ar": "اتفاقية",
        "calculation_model": CalculationModel.PERCENT,
        "percent_rate": Decimal("50.0000"),
        **TERMS,
    }
    fields.update(overrides)
    return Agreement.objects.create(**fields)


def test_by_ratio_on_a_fixed_per_student_agreement_is_unrepresentable(
    a_partner: Partner,
) -> None:
    """
    C-01 — the demo's live defect, now refused by the database.

    Splitting a discount "by the ratio" needs a ratio. A fixed-per-student
    agreement has an AMOUNT, and reading 195 as a percentage is what produced
    the negative university share.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(
            a_partner,
            calculation_model=CalculationModel.FIXED_PER_STUDENT,
            percent_rate=None,
            fixed_amount_per_student=Decimal("195.000"),
            sell_price=Decimal("250.000"),
            discount_split_mode=DiscountSplitMode.BY_RATIO,
        )


def test_by_ratio_is_allowed_on_a_percentage_agreement(a_partner: Partner) -> None:
    """The constraint bans a meaningless combination, not the feature."""
    agreement = _agreement(a_partner, discount_split_mode=DiscountSplitMode.BY_RATIO)
    assert agreement.pk is not None


def test_a_percentage_agreement_needs_a_rate(a_partner: Partner) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(a_partner, percent_rate=None)


def test_a_rate_above_one_hundred_is_refused(a_partner: Partner) -> None:
    """Above 100% the centre pays the partner more than it collected."""
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(a_partner, percent_rate=Decimal("120.0000"))


def test_a_fixed_per_student_agreement_needs_both_numbers(a_partner: Partner) -> None:
    """
    The sell price is not decoration — it is the ceiling.

    Without it nothing stops an agreement paying the partner 195 per student
    on a course sold for 150.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(
            a_partner,
            calculation_model=CalculationModel.FIXED_PER_STUDENT,
            percent_rate=None,
            fixed_amount_per_student=Decimal("195.000"),
            sell_price=None,
        )


def test_a_partner_share_above_the_sell_price_is_refused(a_partner: Partner) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(
            a_partner,
            calculation_model=CalculationModel.FIXED_PER_STUDENT,
            percent_rate=None,
            fixed_amount_per_student=Decimal("300.000"),
            sell_price=Decimal("250.000"),
        )


def test_a_commission_above_the_service_price_is_refused(a_partner: Partner) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(
            a_partner,
            calculation_model=CalculationModel.SERVICE_COMMISSION,
            percent_rate=None,
            service_price=Decimal("500.000"),
            commission_amount=Decimal("600.000"),
        )


def test_validity_must_run_forwards(a_partner: Partner) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _agreement(a_partner, valid_from=date(2027, 1, 1), valid_to=date(2026, 1, 1))


def test_deposits_are_excluded_from_the_base_by_default(a_partner: Partner) -> None:
    """
    Q-01/BR-092 — a deposit is refundable money held, not revenue earned.

    Sharing it would pay the partner from money that may have to be returned.
    Including it stays possible, but only as a deliberate act.
    """
    agreement = _agreement(a_partner)
    assert agreement.exclude_deposits is True
    assert agreement.exclude_registration_fee is True


def test_an_active_agreement_reports_itself_frozen(a_partner: Partner) -> None:
    """D-15 — the snapshot stops moving once the agreement is live (BR-042)."""
    draft = _agreement(a_partner)
    assert draft.is_frozen is False

    draft.status = AgreementStatus.ACTIVE
    draft.save()
    assert draft.is_frozen is True


def test_a_programme_cannot_be_snapshotted_twice_at_the_same_level(
    a_partner: Partner, priced_catalog
) -> None:
    """
    MySQL does not collide NULLs, so ``level_key`` carries the uniqueness.

    Without it an unlevelled programme could be snapshotted twice on one
    agreement with two different signed fees, and nothing would say which one
    the parties agreed.
    """
    from apps.catalog.models import Program

    agreement = _agreement(a_partner)
    program = Program.objects.get(code="SC-NET")

    AgreementProgramSnapshot.objects.create(
        agreement=agreement,
        program=program,
        level=None,
        course_fee_at_signing=Decimal("250.000"),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AgreementProgramSnapshot.objects.create(
            agreement=agreement,
            program=program,
            level=None,
            course_fee_at_signing=Decimal("300.000"),
        )


def test_the_signed_fee_does_not_follow_the_catalogue(a_partner: Partner, priced_catalog) -> None:
    """
    BR-042 — the catalogue moves; a signed agreement does not.

    The snapshot is what both parties put their names to, so a later price
    rise must not silently restate the contract.
    """
    from apps.catalog.models import Program

    agreement = _agreement(a_partner)
    program = Program.objects.get(code="SC-NET")
    snapshot = AgreementProgramSnapshot.objects.create(
        agreement=agreement,
        program=program,
        course_fee_at_signing=Decimal("250.000"),
        subjects_snapshot=[{"code": "SUB-1", "name_ar": "مادة"}],
    )

    program.name_ar = "هندسة الشبكات — نسخة منقّحة"
    program.save()

    snapshot.refresh_from_db()
    assert snapshot.course_fee_at_signing == Decimal("250.000")
    assert snapshot.subjects_snapshot == [{"code": "SUB-1", "name_ar": "مادة"}]
