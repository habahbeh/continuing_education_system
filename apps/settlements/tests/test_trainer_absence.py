"""
Trainer absences, the penalty and its exception (تناغم بند 13).

Every number here comes from the signed clause: three times the lecture's
cost, excused only in writing, replacement only past four. Two of these
contradict what the demo shows and what requirements.md echoes, and the signed
agreement outranks both — so the tests assert the agreement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.settlements.services import absence_service, claim_service, obligation_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
LECTURE = Decimal("50.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def priced_cohort(cohort_with_agreement):
    """A cohort whose lecture cost is known — BR-057's third input."""
    cohort_with_agreement.lecture_cost = LECTURE
    cohort_with_agreement.save(update_fields=["lecture_cost"])
    return cohort_with_agreement


def _absences(manager, cohort, count, start_day=1, waived_days=()):
    for day in range(start_day, start_day + count):
        occurred = date(2026, 10, day)
        waived = day in waived_days
        absence_service.record_absence(
            actor=manager,
            cohort=cohort,
            trainer_name="سعيد المدرّس",
            occurred_on=occurred,
            is_waived=waived,
            waiver_approval_ref="APP-2026-3" if waived else "",
            waiver_approval_date=occurred if waived else None,
        )


# ---------------------------------------------------------------------------
# The penalty
# ---------------------------------------------------------------------------
def test_the_penalty_is_three_times_the_lecture_cost(manager, priced_cohort) -> None:
    """«ثلاثة أضعاف نفقات المحاضرة» — three absences at 50 is 450."""
    _absences(manager, priced_cohort, count=3)

    obligation = absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-1",
    )

    assert obligation.amount == Decimal("450.000")
    assert obligation.absence_count == 3
    assert obligation.lecture_cost == LECTURE
    assert obligation.multiplier == 3


def test_a_waived_absence_is_not_fined(manager, priced_cohort) -> None:
    """
    «ما عدا الحالات الطارئة» — three absences, one excused, so 300 not 450.

    The excused lecture is still ON RECORD with its written approval, which is
    the whole reason absences are rows rather than a counter.
    """
    _absences(manager, priced_cohort, count=3, waived_days=(2,))

    obligation = absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-2",
    )

    assert obligation.amount == Decimal("300.000")
    assert obligation.absence_count == 2

    rows = absence_service.list_absences(actor=manager, cohort_code=priced_cohort.code)
    waived = [r for r in rows if r["is_waived"]]
    assert len(waived) == 1
    assert waived[0]["waiver_approval_ref"] == "APP-2026-3"
    assert waived[0]["counts_toward_penalty"] is False


def test_a_waiver_without_written_approval_is_refused(manager, priced_cohort) -> None:
    """The exception exists only in writing — the clause's own condition."""
    with pytest.raises(absence_service.WaiverApprovalRequiredError):
        absence_service.record_absence(
            actor=manager,
            cohort=priced_cohort,
            trainer_name="سعيد المدرّس",
            occurred_on=date(2026, 10, 1),
            is_waived=True,
        )


def test_the_same_lecture_cannot_be_fined_twice(manager, priced_cohort) -> None:
    """One missed lecture, one row — the unique constraint says so too."""
    _absences(manager, priced_cohort, count=1)

    with pytest.raises(ValidationError, match="مسجَّل سلفاً"):
        absence_service.record_absence(
            actor=manager,
            cohort=priced_cohort,
            trainer_name="سعيد المدرّس",
            occurred_on=date(2026, 10, 1),
        )


def test_absences_already_penalised_are_not_penalised_again(manager, priced_cohort) -> None:
    """A second penalty covers only what the first did not."""
    _absences(manager, priced_cohort, count=2)
    absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-3",
    )

    with pytest.raises(absence_service.NoPenaltyDueError):
        absence_service.raise_penalty(
            actor=manager,
            cohort=priced_cohort,
            trainer_name="سعيد المدرّس",
            occurred_on=TERM_START,
            code="OBL-ABS-4",
        )

    _absences(manager, priced_cohort, count=1, start_day=3)
    second = absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-5",
    )
    assert second.absence_count == 1
    assert second.amount == Decimal("150.000")


def test_a_penalty_without_a_lecture_cost_is_refused(manager, cohort_with_agreement) -> None:
    """
    BR-057 — a penalty is a formula and this input is missing.

    The database says the same thing through
    ``settlements_obligation_penalty_has_inputs``; the service says it in
    words the centre can act on.
    """
    _absences(manager, cohort_with_agreement, count=1)

    with pytest.raises(absence_service.LectureCostMissingError):
        absence_service.raise_penalty(
            actor=manager,
            cohort=cohort_with_agreement,
            trainer_name="سعيد المدرّس",
            occurred_on=TERM_START,
            code="OBL-ABS-6",
        )


# ---------------------------------------------------------------------------
# The replacement threshold
# ---------------------------------------------------------------------------
def test_four_absences_do_not_trigger_replacement(manager, priced_cohort) -> None:
    """«لأكثر من أربع» — four is not more than four."""
    _absences(manager, priced_cohort, count=4)

    assert (
        absence_service.replacement_is_due(
            cohort=priced_cohort, trainer_name="سعيد المدرّس", as_of=TERM_START
        )
        is False
    )
    assert absence_service.replacement_alerts(actor=manager, as_of=TERM_START) == []


def test_the_fifth_absence_triggers_replacement(manager, priced_cohort) -> None:
    """
    The demo says «بعد 4 غيابات» and the signed agreement says «لأكثر من أربع».

    One lecture of difference, and its cost is a replacement trainer's whole
    fee. The agreement wins.
    """
    _absences(manager, priced_cohort, count=5)

    assert (
        absence_service.replacement_is_due(
            cohort=priced_cohort, trainer_name="سعيد المدرّس", as_of=TERM_START
        )
        is True
    )
    alerts = absence_service.replacement_alerts(actor=manager, as_of=TERM_START)
    assert len(alerts) == 1
    assert alerts[0]["absence_count"] == 5
    assert alerts[0]["limit"] == 4


def test_a_waived_absence_does_not_count_toward_replacement(manager, priced_cohort) -> None:
    """
    Five absences, one excused in writing — four count, so no replacement.

    An excused lecture cannot also be evidence for removing the trainer.
    """
    _absences(manager, priced_cohort, count=5, waived_days=(3,))

    assert absence_service.countable(priced_cohort, "سعيد المدرّس") == 4
    assert (
        absence_service.replacement_is_due(
            cohort=priced_cohort, trainer_name="سعيد المدرّس", as_of=TERM_START
        )
        is False
    )


def test_waiving_after_the_penalty_is_refused(manager, priced_cohort) -> None:
    """
    The obligation may already have been offset against a claim.

    Unpicking it silently would restate what a partner was paid, so the
    correct act is waiving the OBLIGATION, visibly.
    """
    _absences(manager, priced_cohort, count=2)
    absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-7",
    )
    absence = absence_service.list_absences(actor=manager)[0]

    with pytest.raises(ValidationError, match="التنازل عن الالتزام"):
        absence_service.waive_absence(
            actor=manager,
            absence=absence_service.absence_instance(actor=manager, absence_id=absence["id"]),
            approval_ref="APP-LATE",
            approval_date=TERM_START,
        )


# ---------------------------------------------------------------------------
# Reaching the claim
# ---------------------------------------------------------------------------
def test_the_penalty_is_recovered_from_the_next_claim(
    manager, finance, priced_cohort, percent_agreement, make_paid_enrollment
) -> None:
    """
    §5.6 — the obligation is deducted from later claims, never invoiced.

    250 tuition at 50% is 125; a 150-dinar penalty exceeds it, so BR-036 caps
    the deduction at what the claim can bear and the rest stays OPEN.
    """
    make_paid_enrollment(priced_cohort, index=1, amount="270.000")
    _absences(manager, priced_cohort, count=1)
    absence_service.raise_penalty(
        actor=manager,
        cohort=priced_cohort,
        trainer_name="سعيد المدرّس",
        occurred_on=TERM_START,
        code="OBL-ABS-8",
    )

    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=priced_cohort,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    deductions = claim_service.apply_offsets(actor=finance, claim=claim)

    assert len(deductions) == 1
    assert deductions[0].deduction_type == "TRAINER_ABSENCE"
    claim.refresh_from_db()
    assert claim.net_payable >= Decimal("0.000")
    assert "غياب" in deductions[0].label_ar


def test_the_absence_penalty_cannot_be_recorded_by_hand(manager, partner) -> None:
    """
    BR-057 — it is a formula with inputs, computed from the absences.

    Typing an amount would produce a penalty that cannot show its working,
    which is exactly what the existing constraint refuses.
    """
    with pytest.raises(ValidationError, match="BR-057"):
        obligation_service.record_obligation(
            actor=manager,
            partner=partner,
            obligation_type="TRAINER_ABSENCE_PENALTY",
            amount=Decimal("450.000"),
            occurred_on=TERM_START,
            statement_reference="كشف",
            code="OBL-MANUAL-BAD",
        )
