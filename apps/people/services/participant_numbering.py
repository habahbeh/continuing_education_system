"""
Participant number generation — BR-001, BR-002.

    participant_number = year(4) + type_code(1) + sequence(4)   →  9 digits

The demo derived the sequence from ``DB.students.length + 1``, which collides
on deletion and under concurrency. Production consumes a locked NumberSequence
row inside the caller's transaction, so a rollback takes the counter with it
and no gap appears (T-084).

**Why a missing active semester is a hard error rather than a fallback.**

The number is permanent, is never corrected retroactively (BR-001), and is
printed on the certificate. Defaulting the year to ``today`` would produce a
plausible-looking number that is wrong forever, discovered years later by
someone holding a certificate. Failing loudly costs one confused minute; the
silent fallback costs a number that cannot be fixed (T-287).
"""

from __future__ import annotations

from typing import Any

from apps.core.services.numbering_service import ensure_sequence, next_number

#: BR-002 — a centre participant's type digit is 5, whatever the semester says.
CENTER_TYPE_CODE = 5

SCOPE = "participant"
SEQUENCE_PADDING = 4


class NoActiveSemesterError(Exception):
    """Raised when a participant number is requested with no active semester."""


def active_semester() -> Any:
    from apps.core.models import Semester

    semester = Semester.objects.filter(is_active=True).first()
    if semester is None:
        raise NoActiveSemesterError(
            "لا يمكن توليد الرقم الجامعي — لا يوجد فصل دراسي نشط. عرّف الفصل الحالي أولاً (BR-001)."
        )
    return semester


def academic_year_digits(semester: Any) -> str:
    """
    The four year digits of the number.

    Taken from the semester, never from the clock. ``academic_year`` is stored
    as "2026/2027", so the opening year is the one that identifies the intake —
    matching the demo's numbers (202650002 is a 2026 centre participant).
    """
    raw = (semester.academic_year or "").strip()
    head = raw[:4]
    if len(head) == 4 and head.isdigit():
        return head
    # A malformed academic_year is a data error, not something to paper over
    # with the current date: the number would be wrong and permanent.
    raise NoActiveSemesterError(
        f"الفصل النشط يحمل سنة دراسية غير صالحة: {raw!r}. "
        "يجب أن تبدأ بأربعة أرقام (مثال: 2026/2027)."
    )


def type_code_for(category: str, semester: Any) -> int:
    """BR-001 · BR-002 — 5 for a centre participant, else the semester's code."""
    from apps.people.models import ParticipantCategory

    if category == ParticipantCategory.CENTER:
        return CENTER_TYPE_CODE
    return int(semester.type_code)


def partition_for(*, category: str, semester: Any) -> str:
    """The counter partition — year + type digit, e.g. "20261" or "20265"."""
    return f"{academic_year_digits(semester)}{type_code_for(category, semester)}"


def prepare_sequence(*, category: str, semester: Any) -> str:
    """
    Create the counter row if this partition is new. **Call OUTSIDE the transaction.**

    A new partition is created at the start of every term, which is also when
    several clerks register participants at once. The counter row has to be
    committed before anyone locks it, or concurrent callers deadlock; inside a
    transaction that commit cannot happen. ``ensure_sequence`` raises
    ``SequenceNotPrepared`` if this was skipped, so the mistake surfaces the
    first time rather than intermittently under load.

    Returns the partition, so the caller can pass it straight on.
    """
    partition = partition_for(category=category, semester=semester)
    ensure_sequence(SCOPE, partition, padding=SEQUENCE_PADDING)
    return partition


def next_participant_number(*, category: str, semester: Any = None) -> str:
    """
    Consume and return the next participant number.

    MUST be called inside the transaction that creates the participant, so the
    counter rolls back with a failed insert (no gap) — and ``prepare_sequence``
    MUST have been called before that transaction opened.
    """
    semester = semester or active_semester()
    # Partitioning by year+type restarts the sequence each year and each type,
    # which is what makes the 4-digit tail sufficient.
    partition = partition_for(category=category, semester=semester)
    return next_number(SCOPE, partition, prefix=partition, padding=SEQUENCE_PADDING)


__all__ = [
    "CENTER_TYPE_CODE",
    "SCOPE",
    "NoActiveSemesterError",
    "academic_year_digits",
    "active_semester",
    "next_participant_number",
    "partition_for",
    "prepare_sequence",
    "type_code_for",
]
