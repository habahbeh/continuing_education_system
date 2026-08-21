"""
Closing a cycle with a partner (BR-053, §3.1, تناغم بند 11).

``Agreement.settlement_cycle`` has existed since Sprint 5 and nothing read it.
These tests are what makes it mean something: the period is DERIVED from the
agreement rather than typed by whoever opens the settlement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.settlements.models import SettlementStatus
from apps.settlements.services import claim_service, settlement_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
FULL = Decimal("270.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.settle2", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def test_a_four_month_cycle_tiles_without_overlapping(percent_agreement) -> None:
    """
    §3.1 — the diploma rhythm. Four calendar months, ending the day before.

    Consecutive periods must not share a day: a claim landing on a boundary
    would otherwise belong to two settlements at once.
    """
    from apps.partners.models import SettlementCycle

    percent_agreement.settlement_cycle = SettlementCycle.EVERY_4_MONTHS
    percent_agreement.save(update_fields=["settlement_cycle"])

    first = settlement_service.period_for(agreement=percent_agreement, opens_on=date(2026, 1, 1))
    second = settlement_service.period_for(agreement=percent_agreement, opens_on=date(2026, 5, 1))

    assert first == (date(2026, 1, 1), date(2026, 4, 30))
    assert second == (date(2026, 5, 1), date(2026, 8, 31))
    assert first[1] < second[0]


def test_a_four_month_cycle_survives_a_short_month(percent_agreement) -> None:
    """31 October + 4 months has no 31st to land on; it steps back, not forward."""
    from apps.partners.models import SettlementCycle

    percent_agreement.settlement_cycle = SettlementCycle.EVERY_4_MONTHS
    percent_agreement.save(update_fields=["settlement_cycle"])

    period = settlement_service.period_for(agreement=percent_agreement, opens_on=date(2026, 10, 31))
    assert period == (date(2026, 10, 31), date(2027, 2, 27))


def test_an_end_of_course_cycle_takes_the_cohorts_end(
    percent_agreement, cohort_with_agreement
) -> None:
    """تناغم بند 11 — «مخالصة مالية في نهاية كل دورة قصيرة»."""
    period = settlement_service.period_for(
        agreement=percent_agreement, opens_on=TERM_START, cohort=cohort_with_agreement
    )
    assert period == (TERM_START, cohort_with_agreement.ends_on)


def test_an_end_of_course_cycle_without_a_cohort_is_refused(percent_agreement) -> None:
    """There is no "end of course" without naming the course."""
    with pytest.raises(settlement_service.SettlementCycleError):
        settlement_service.period_for(agreement=percent_agreement, opens_on=TERM_START)


def test_a_settlement_totals_the_claims_it_closes(
    finance, approver, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """250 tuition at 50% is 125, and the settlement carries it."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    claim_service.approve_claim(actor=approver, claim=claim)

    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-001",
    )
    claims = settlement_service.attach_claims(actor=finance, settlement=settlement)

    settlement.refresh_from_db()
    assert len(claims) == 1
    assert settlement.total_due == Decimal("125.000")
    assert settlement.balance == Decimal("125.000")
    assert settlement.cycle_type == percent_agreement.settlement_cycle


def test_a_draft_claim_is_not_settleable(
    finance, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    BR-051 — an unapproved figure carries no seal.

    Closing a period over numbers that can still change would make the
    settlement a statement about nothing.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )

    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-002",
    )
    claims = settlement_service.attach_claims(actor=finance, settlement=settlement)

    assert claims == []
    settlement.refresh_from_db()
    assert settlement.total_due == Decimal("0.000")


def test_signing_requires_a_nil_balance(
    finance, approver, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    BR-053 — a settlement says nothing is outstanding, so nothing may be.

    Signing over a live balance would put an untrue statement on the document
    both parties keep.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    claim_service.approve_claim(actor=approver, claim=claim)

    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-003",
    )
    settlement_service.attach_claims(actor=finance, settlement=settlement)
    settlement.refresh_from_db()

    with pytest.raises(settlement_service.SettlementStateError):
        settlement_service.sign_settlement(
            actor=approver, settlement=settlement, signed_on=PERIOD_END
        )

    settlement_service.record_payment(
        actor=finance, settlement=settlement, amount=Decimal("125.000")
    )
    settlement.refresh_from_db()
    assert settlement.balance == Decimal("0.000")

    signed = settlement_service.sign_settlement(
        actor=approver, settlement=settlement, signed_on=PERIOD_END
    )
    assert signed.status == SettlementStatus.SIGNED
    assert signed.signed_on == PERIOD_END


def test_overpaying_a_settlement_is_refused(
    finance, approver, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    claim_service.approve_claim(actor=approver, claim=claim)

    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-004",
    )
    settlement_service.attach_claims(actor=finance, settlement=settlement)
    settlement.refresh_from_db()

    with pytest.raises(ValidationError):
        settlement_service.record_payment(
            actor=finance, settlement=settlement, amount=Decimal("200.000")
        )


def test_a_signed_settlement_is_closed_to_further_movement(
    finance, approver, percent_agreement, cohort_with_agreement
) -> None:
    """What both parties signed is not edited afterwards."""
    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-005",
    )
    settlement_service.sign_settlement(actor=approver, settlement=settlement, signed_on=PERIOD_END)

    with pytest.raises(settlement_service.SettlementStateError):
        settlement_service.attach_claims(actor=finance, settlement=settlement)


def test_only_one_cycle_is_open_at_a_time(
    finance, percent_agreement, cohort_with_agreement
) -> None:
    """Two open settlements would let one claim fall into both."""
    settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-006",
    )

    with pytest.raises(settlement_service.SettlementStateError):
        settlement_service.open_settlement(
            actor=finance,
            agreement=percent_agreement,
            opens_on=TERM_START,
            cohort=cohort_with_agreement,
            code="STL-007",
        )


def test_the_centre_manager_signs_and_the_finance_officer_does_not(
    finance, approver, percent_agreement, cohort_with_agreement
) -> None:
    """§8 — the finance officer prepares, the centre manager approves."""
    settlement = settlement_service.open_settlement(
        actor=finance,
        agreement=percent_agreement,
        opens_on=TERM_START,
        cohort=cohort_with_agreement,
        code="STL-008",
    )

    with pytest.raises(PermissionDenied):
        settlement_service.sign_settlement(
            actor=finance, settlement=settlement, signed_on=PERIOD_END
        )
