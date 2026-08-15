"""
Advance payouts and their recovery — ⚠️ ASSUMPTION (Q-09).

The demo paid CFM 20 × 195 = 3,900 dinars before the course ran and had no
mechanism at all for what happens when participants then withdraw. The centre
absorbed it. The assumption implemented here turns that loss into a recorded
obligation, recovered by deduction from a later claim — never by invoicing the
partner, which BR-036 forbids.

The point of every test below is that the recovery is VISIBLE: a row with a
count, a rate and a cohort, not an adjustment nobody can trace.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.settlements.models import (
    DeductionType,
    ObligationStatus,
    ObligationType,
    PartnerObligation,
)
from apps.settlements.services import claim_service, clawback_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
TERM_END = date(2026, 12, 20)


@pytest.fixture
def advance_cohort(priced_catalog, active_semester, advance_agreement):
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    return Cohort.objects.create(
        code="CO-ADV-1",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة صرف مقدّم",
        starts_on=TERM_START,
        ends_on=TERM_END,
        capacity=30,
        agreement=advance_agreement,
    )


def test_the_deadline_comes_from_the_agreement(advance_agreement, advance_cohort) -> None:
    """BR-054 — the notice period is contractual, not a constant in code."""
    assert clawback_service.name_list_deadline(advance_cohort, advance_agreement) == (
        TERM_START + timedelta(days=7)
    )


def test_a_fully_eligible_cohort_owes_nothing_back(
    seeded_settings, finance, advance_agreement, advance_cohort, make_paid_enrollment
) -> None:
    """
    The ordinary case, and it returns None rather than a zero obligation.

    A zero-amount row would read as "we recovered nothing from them", which is
    a different statement from "there was nothing to recover".
    """
    make_paid_enrollment(advance_cohort, index=1)
    make_paid_enrollment(advance_cohort, index=2)

    result = clawback_service.close_name_list(
        actor=finance,
        cohort=advance_cohort,
        agreement=advance_agreement,
        as_of=TERM_START + timedelta(days=7),
    )
    assert result is None
    assert not PartnerObligation.objects.exists()


def test_withdrawn_participants_become_a_recorded_clawback(
    seeded_settings, finance, advance_agreement, advance_cohort, make_paid_enrollment
) -> None:
    """
    Q-09 — the money is already paid, so it comes back as an obligation.

    Two withdrawals at 195 each: 390 recorded against the partner, with the
    count and the per-student rate on the row so the figure can be defended.
    """
    make_paid_enrollment(advance_cohort, index=1)
    make_paid_enrollment(advance_cohort, index=2, status="WITHDRAWN")
    make_paid_enrollment(advance_cohort, index=3, status="WITHDRAWN")

    obligation = clawback_service.close_name_list(
        actor=finance,
        cohort=advance_cohort,
        agreement=advance_agreement,
        as_of=TERM_START + timedelta(days=7),
    )

    assert obligation is not None
    assert obligation.amount == Decimal("390.000")
    assert obligation.obligation_type == ObligationType.ADVANCE_CLAWBACK
    assert obligation.cohort_id == advance_cohort.pk
    assert "195.000" in obligation.statement_reference
    assert "2" in obligation.statement_reference


def test_the_clawback_is_pinned_to_the_agreement_that_produced_it(
    seeded_settings, finance, advance_agreement, advance_cohort, make_paid_enrollment
) -> None:
    """
    Tighter than the Q-08 default, deliberately.

    An advance paid under one contract should not be recovered from an
    unrelated agreement with the same partner unless the client says so.
    """
    make_paid_enrollment(advance_cohort, index=2, status="WITHDRAWN")
    obligation = clawback_service.close_name_list(
        actor=finance,
        cohort=advance_cohort,
        agreement=advance_agreement,
        as_of=TERM_START + timedelta(days=7),
    )
    assert obligation is not None
    assert obligation.restricted_to_agreement_id == advance_agreement.pk


def test_a_participant_behind_on_payment_counts_as_exposure(
    seeded_settings, finance, advance_agreement, advance_cohort, make_paid_enrollment
) -> None:
    """
    BR-045 joins Q-16 here: overdue earns the partner nothing either.

    The advance already paid for them, so the exposure is the same as a
    withdrawal even though the participant is still enrolled.
    """
    make_paid_enrollment(advance_cohort, index=4, amount="50.000")
    as_of = TERM_START + timedelta(days=40)

    amount, count = clawback_service.exposure_for(
        cohort=advance_cohort, agreement=advance_agreement, as_of=as_of
    )
    assert (amount, count) == (Decimal("195.000"), 1)


def test_a_percentage_agreement_has_no_advance_exposure(
    seeded_settings, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    A percentage agreement settles on what was collected.

    Paying it early is a timing question, not an overpayment per head, so
    there is nothing to claw back per participant.
    """
    make_paid_enrollment(cohort_with_agreement, index=2, status="WITHDRAWN")
    amount, count = clawback_service.exposure_for(
        cohort=cohort_with_agreement, agreement=percent_agreement, as_of=TERM_END
    )
    assert (amount, count) == (Decimal("0.000"), 0)


def test_closing_a_list_on_a_non_advance_agreement_is_refused(
    seeded_settings, finance, percent_agreement, cohort_with_agreement
) -> None:
    """Nothing was paid in advance, so there is nothing to close against."""
    with pytest.raises(ValidationError):
        clawback_service.close_name_list(
            actor=finance,
            cohort=cohort_with_agreement,
            agreement=percent_agreement,
            as_of=TERM_END,
        )


def test_the_clawback_returns_as_a_labelled_deduction_not_arithmetic(
    seeded_settings, finance, advance_agreement, advance_cohort, make_paid_enrollment
) -> None:
    """
    The whole point of Q-09 being obligations rather than silent maths.

    The partner's next claim is smaller, and the row that made it smaller
    names the clawback, its cohort and its agreement.
    """
    make_paid_enrollment(advance_cohort, index=1)
    make_paid_enrollment(advance_cohort, index=2, status="WITHDRAWN")

    obligation = clawback_service.close_name_list(
        actor=finance,
        cohort=advance_cohort,
        agreement=advance_agreement,
        as_of=TERM_START + timedelta(days=7),
    )
    assert obligation is not None

    claim = claim_service.build_claim(
        actor=finance,
        agreement=advance_agreement,
        cohort=advance_cohort,
        period_from=TERM_START,
        period_to=TERM_END,
        trigger_type="END_OF_COURSE",
    )
    # One eligible participant on a 195-per-student agreement.
    assert claim.partner_share == Decimal("195.000")

    deductions = claim_service.apply_offsets(actor=finance, claim=claim)
    assert len(deductions) == 1
    assert deductions[0].deduction_type == DeductionType.ADVANCE_CLAWBACK
    assert obligation.code in deductions[0].label_ar
    assert advance_agreement.agreement_number in deductions[0].label_ar
    assert claim.net_payable == Decimal("0.000")

    obligation.refresh_from_db()
    assert obligation.status == ObligationStatus.RECOVERED
