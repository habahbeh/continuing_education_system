"""
Participant number generation — T-082, T-084, T-287 (BR-001, BR-002).

The demo derived the sequence from an array length, which collides on deletion
and under concurrency. T-084 is the test that would have caught it: a hundred
callers at once, and every number distinct with no gap.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from django.db import connection, transaction

from apps.core.models import Semester
from apps.people.models import ParticipantCategory
from apps.people.services import participant_numbering as numbering

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# BR-001 — structure
# ---------------------------------------------------------------------------
def test_number_is_year_type_sequence(active_semester: Semester) -> None:
    number = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=active_semester
    )
    assert len(number) == 9
    assert number.isdigit()
    assert number.startswith("20261")  # 2026 + type 1
    assert number[5:] == "0001"


def test_sequence_increments_within_the_same_partition(active_semester: Semester) -> None:
    first = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=active_semester
    )
    second = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=active_semester
    )
    assert int(second) == int(first) + 1


# ---------------------------------------------------------------------------
# T-082 / BR-002 — the centre participant's type digit is fixed at 5
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("type_code", "code"), [(1, "2026-1"), (2, "2026-2"), (3, "2026-3")])
def test_t082_centre_participant_is_always_type_5(type_code: int, code: str) -> None:
    """Whatever the semester — first, second or SUMMER — a centre student is 5."""
    semester = Semester.objects.create(
        code=code,
        name_ar=f"فصل {type_code}",
        type_code=type_code,
        academic_year="2026/2027",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )
    number = numbering.next_participant_number(
        category=ParticipantCategory.CENTER, semester=semester
    )
    assert number[4] == "5", f"summer semester leaked into the number: {number}"
    assert number.startswith("20265")


def test_t082_university_participant_follows_the_semester(active_semester: Semester) -> None:
    number = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=active_semester
    )
    assert number[4] == "1"


def test_centre_and_university_sequences_are_independent(active_semester: Semester) -> None:
    """Different partitions — one does not consume the other's numbers."""
    uni = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=active_semester
    )
    centre = numbering.next_participant_number(
        category=ParticipantCategory.CENTER, semester=active_semester
    )
    assert uni.endswith("0001")
    assert centre.endswith("0001")
    assert uni != centre


def test_employee_is_numbered_like_a_university_participant(
    active_semester: Semester,
) -> None:
    """BR-002 fixes only CENTER; EMPLOYEE follows the semester."""
    number = numbering.next_participant_number(
        category=ParticipantCategory.EMPLOYEE, semester=active_semester
    )
    assert number[4] == "1"


# ---------------------------------------------------------------------------
# T-287 — no active semester is a hard failure
# ---------------------------------------------------------------------------
def test_t287_missing_active_semester_fails_loudly() -> None:
    """
    The number is permanent and printed on the certificate (BR-001).

    Defaulting the year to today would produce a plausible number that is
    wrong forever. One loud failure now beats a number nobody can correct.
    """
    assert not Semester.objects.filter(is_active=True).exists()
    with pytest.raises(numbering.NoActiveSemesterError):
        numbering.next_participant_number(category=ParticipantCategory.UNIVERSITY)


def test_t287_inactive_semester_does_not_count(active_semester: Semester) -> None:
    active_semester.is_active = False
    active_semester.save()
    with pytest.raises(numbering.NoActiveSemesterError):
        numbering.next_participant_number(category=ParticipantCategory.UNIVERSITY)


def test_t287_malformed_academic_year_fails_rather_than_guessing() -> None:
    semester = Semester.objects.create(
        code="bad",
        name_ar="فصل بسنة تالفة",
        type_code=1,
        academic_year="سنة",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )
    with pytest.raises(numbering.NoActiveSemesterError):
        numbering.next_participant_number(
            category=ParticipantCategory.UNIVERSITY, semester=semester
        )


def test_year_comes_from_the_semester_not_the_clock() -> None:
    """A 2030 semester issues 2030 numbers even when run today."""
    semester = Semester.objects.create(
        code="2030-2",
        name_ar="الفصل الثاني 2030",
        type_code=2,
        academic_year="2030/2031",
        starts_on=date(2031, 2, 1),
        ends_on=date(2031, 6, 1),
        is_active=True,
    )
    number = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=semester
    )
    assert number.startswith("20302")


# ---------------------------------------------------------------------------
# T-084 — a hundred at once, no duplicates, no gaps
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_t084_concurrent_allocation_yields_unique_gapless_numbers() -> None:
    semester = Semester.objects.create(
        code="2026-1",
        name_ar="الفصل الأول",
        type_code=1,
        academic_year="2026/2027",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )

    def allocate(_index: int) -> str:
        try:
            # The real call shape: prepare the counter outside, consume inside.
            numbering.prepare_sequence(category=ParticipantCategory.UNIVERSITY, semester=semester)
            with transaction.atomic():
                return numbering.next_participant_number(
                    category=ParticipantCategory.UNIVERSITY, semester=semester
                )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=20) as pool:
        numbers = list(pool.map(allocate, range(100)))

    assert len(numbers) == 100
    assert len(set(numbers)) == 100, "duplicate participant numbers issued"

    tails = sorted(int(n[5:]) for n in numbers)
    assert tails == list(range(1, 101)), f"gap in the sequence: {tails[:5]} … {tails[-5:]}"


def test_lock_contention_is_not_retried_inside_a_caller_transaction(
    active_semester: Semester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The trap this sprint uncovered.

    A MySQL deadlock aborts the whole transaction, so retrying inside a
    caller's transaction runs the next attempt against a transaction the
    server has already rolled back — and the caller ends up seeing
    TransactionManagementError instead of the deadlock that caused it.

    The numbering service must therefore hand a deadlock straight back to the
    caller, who owns the transaction and can start a fresh one.
    """
    from django.db import OperationalError

    from apps.core.services import numbering_service

    attempts = {"count": 0}
    real_get = numbering_service.NumberSequence.objects.select_for_update

    def exploding(*args: object, **kwargs: object):
        attempts["count"] += 1
        raise OperationalError(1213, "Deadlock found when trying to get lock")

    monkeypatch.setattr(numbering_service.NumberSequence.objects, "select_for_update", exploding)
    numbering.prepare_sequence(category=ParticipantCategory.UNIVERSITY, semester=active_semester)

    with pytest.raises(OperationalError), transaction.atomic():
        numbering.next_participant_number(
            category=ParticipantCategory.UNIVERSITY, semester=active_semester
        )

    assert attempts["count"] == 1, (
        "the deadlock was retried inside a transaction the server had already "
        "rolled back — the retry belongs to the caller"
    )
    assert real_get is not None


def test_prepared_partition_is_usable_inside_a_transaction(
    active_semester: Semester,
) -> None:
    numbering.prepare_sequence(category=ParticipantCategory.UNIVERSITY, semester=active_semester)
    with transaction.atomic():
        number = numbering.next_participant_number(
            category=ParticipantCategory.UNIVERSITY, semester=active_semester
        )
    assert number == "202610001"


@pytest.mark.django_db(transaction=True)
def test_rolled_back_transaction_leaves_no_gap() -> None:
    """A failed insert must return its number to the pool (ADR-011)."""
    semester = Semester.objects.create(
        code="2026-1",
        name_ar="الفصل الأول",
        type_code=1,
        academic_year="2026/2027",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )

    class BoomError(Exception):
        pass

    first = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=semester
    )
    try:
        with transaction.atomic():
            numbering.next_participant_number(
                category=ParticipantCategory.UNIVERSITY, semester=semester
            )
            raise BoomError
    except BoomError:
        pass

    third = numbering.next_participant_number(
        category=ParticipantCategory.UNIVERSITY, semester=semester
    )
    assert int(third) == int(first) + 1, "the rolled-back allocation left a gap"
