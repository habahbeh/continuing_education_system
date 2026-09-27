"""
The academic semester behind a screen — BR-001, BR-002, DATA_MODEL §3.1.

**Found by walking a fresh install.** ``Semester`` has existed since Sprint 1
and no screen in the system could write one. The only two roads to a row were
``manage.py seed_semester`` and the Django admin — a command the centre cannot
run and a page that writes straight to the table with no audit line behind it.

That is not a cosmetic gap. ``participant_numbering.active_semester`` raises
``NoActiveSemesterError`` when no semester is flagged current, and every
participant number is minted through it (BR-001). So on a brand-new database
**not one participant can be registered**, and the refusal the registrar meets
says «عرّف الفصل الحالي أولاً» — pointing at a screen that did not exist.

**The settings permission, and no new matrix cell.** PERMISSIONS.md §3.7/35
gives the centre manager ``V E P`` over settings, the finance officer ``V`` and
the audit account ``V P``. A semester is exactly that kind of row: the frame
the centre's year is counted in, set once by whoever administers the system,
read by everybody. ``EDIT`` therefore guards all three acts here — writing a
semester, correcting one, and naming the current one.

**What this module refuses, and why it refuses it rather than the form.**

``academic_year`` and ``type_code`` are the two fields the participant number
is built from, and BR-001 says that number is permanent and is never corrected
retroactively. Once a number has been issued under ``2026`` + ``1``, moving
this row to ``2027`` or to the summer code would leave the numbers already
printed on certificates describing a semester that no longer says what they
say. So the change is refused for a semester whose numbers have been issued —
in the service, because a form is presentation and this is a rule about
permanence.

**Deleting is not offered at all.** Nothing points at ``Semester`` by foreign
key, which is precisely the danger: the participant numbers that belong to it
carry its year and type as DIGITS, so deleting the row would orphan them
silently rather than being refused by the database.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Left

from apps.core.models import Semester
from apps.core.models.semester import SemesterType
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "core.Semester"

#: Configuring the calendar the year is counted in is a settings act — see the
#: module docstring. Named here so the screen and its tests read one decision.
SCREEN = Screen.SETTINGS

#: BR-002 — a centre participant's type digit, which is deliberately NOT a
#: semester type. Their numbers therefore belong to the YEAR and cannot be
#: attributed to any one semester in it, which the screen has to say out loud
#: rather than fold into a count that would then be wrong.
CENTER_TYPE_DIGIT = "5"

#: year(4) + type(1) — the participant-number prefix a semester mints under.
_PARTITION_WIDTH = 5


def semester_type_choices() -> list[tuple[int, str]]:
    """The three codes, for a form. Code 5 is not among them (BR-002)."""
    return [(value, str(label)) for value, label in SemesterType.choices]


def semester_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every semester, with what has already been minted under it.

    ``numbers`` is the count of participants whose number carries this
    semester's year and type digit. It is the reason no delete is offered and
    the reason the year and the type freeze: it is how many permanent numbers
    now describe this row.

    ``center_numbers`` is counted separately and named separately. A centre
    participant's digit is 5 whatever the semester (BR-002), so those numbers
    belong to the academic YEAR; adding them into the row's own count would
    report the same participants under all three semesters of that year.

    ``overlaps`` lists the codes whose dates cross this one's. Nothing in the
    schema forbids that, and two semesters covering one day is a question with
    no answer — so it is surfaced rather than silently tolerated.
    """
    policy.require(actor, SCREEN, Action.VIEW, request=request)

    tallies = _number_tallies()
    semesters = list(Semester.objects.order_by("-starts_on"))

    rows: list[dict[str, Any]] = []
    for semester in semesters:
        year = _year_digits(semester)
        rows.append(
            {
                "code": semester.code,
                "name_ar": semester.name_ar,
                "type_code": semester.type_code,
                "type_label": _type_label(semester.type_code),
                "academic_year": semester.academic_year,
                "year_digits": year,
                "starts_on": semester.starts_on,
                "ends_on": semester.ends_on,
                "is_active": semester.is_active,
                "numbers": tallies.get(f"{year}{semester.type_code}", 0) if year else 0,
                "center_numbers": tallies.get(f"{year}{CENTER_TYPE_DIGIT}", 0) if year else 0,
                "year_valid": bool(year),
                "overlaps": [
                    other.code
                    for other in semesters
                    if other.pk != semester.pk
                    and other.starts_on <= semester.ends_on
                    and other.ends_on >= semester.starts_on
                ],
            }
        )
    return rows


def active_semester_row(*, actor: Any, request: Any = None) -> dict[str, Any] | None:
    """The current semester as a row, or ``None`` — what the screen warns on."""
    rows = semester_rows(actor=actor, request=request)
    return next((row for row in rows if row["is_active"]), None)


def _number_tallies() -> dict[str, int]:
    """
    ``{year+type digits: participants}`` for the whole register, in one query.

    Counted from the NUMBER rather than from a foreign key because there is no
    foreign key: BR-001 encodes the semester into the digits, which is exactly
    why deleting a semester cannot be refused by the database.
    """
    from apps.people.models import Participant

    return {
        row["partition"]: row["n"]
        for row in Participant.objects.annotate(
            partition=Left("participant_number", _PARTITION_WIDTH)
        )
        .values("partition")
        .annotate(n=Count("id"))
    }


def _year_digits(semester: Any) -> str:
    """
    The four opening digits of ``academic_year``, or ``""`` if it is malformed.

    ``participant_numbering.academic_year_digits`` RAISES on a malformed value,
    which is right when a permanent number is about to be minted and wrong for
    a list: one bad row would take the whole screen down with it. Here the
    answer is empty and the screen says so.
    """
    head = (semester.academic_year or "").strip()[:4]
    return head if len(head) == 4 and head.isdigit() else ""


def _type_label(type_code: int) -> str:
    try:
        return str(SemesterType(type_code).label)
    except ValueError:  # pragma: no cover - the check constraint forbids it
        return str(type_code)


def create_semester(*, actor: Any, data: dict[str, Any], request: Any = None) -> Semester:
    """
    A new semester, audited — which neither the command nor the admin was.

    ``is_active`` is honoured through ``activate_semester`` rather than written
    with the row: at most one semester may be active
    (``core_semester_single_active``), so standing the previous one down is
    part of the act and not a separate thing to remember.
    """
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    fields = dict(data)
    make_current = bool(fields.pop("is_active", False))

    with transaction.atomic():
        semester = Semester(is_active=False, **fields)
        semester.full_clean()
        semester.save()
        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(semester.pk),
            reference=semester.code,
            summary_ar=f"إنشاء فصل دراسي — {semester.name_ar}",
            actor=actor,
            request=request,
        )
        if make_current:
            _make_current(semester=semester, actor=actor, request=request)
    return semester


def update_semester(
    *, actor: Any, semester: Semester, data: dict[str, Any], request: Any = None
) -> Semester:
    """
    Correct a semester — except the two fields its numbers are built from.

    See the module docstring: ``academic_year`` and ``type_code`` become digits
    in a permanent participant number (BR-001). Once one has been issued they
    are history, and history is not a form field.
    """
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    fields = dict(data)
    make_current = bool(fields.pop("is_active", False))
    _refuse_frozen_change(semester, fields)

    before = {field: getattr(semester, field) for field in fields}
    with transaction.atomic():
        for field, value in fields.items():
            setattr(semester, field, value)
        semester.full_clean()
        semester.save()
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(semester.pk),
            reference=semester.code,
            summary_ar=f"تعديل فصل دراسي — {semester.name_ar}",
            actor=actor,
            changes={
                field: {"from": str(before[field]), "to": str(getattr(semester, field))}
                for field in fields
                if before[field] != getattr(semester, field)
            },
            request=request,
        )
        if make_current and not semester.is_active:
            _make_current(semester=semester, actor=actor, request=request)
    return semester


def _refuse_frozen_change(semester: Semester, fields: dict[str, Any]) -> None:
    """BR-001 — the year and the type are frozen once a number carries them."""
    issued = numbers_issued(semester)
    if not issued:
        return
    refusal = (
        f"لا يُعدَّل هذا الحقل: {issued} رقماً جامعياً صدر بأرقام هذا الفصل، "
        "والرقم دائم لا يُصحَّح بأثر رجعي (BR-001). أنشئ فصلاً جديداً."
    )
    offending: dict[str, Any] = {
        field: ValidationError(refusal)
        for field in ("academic_year", "type_code")
        if field in fields and str(fields[field]) != str(getattr(semester, field))
    }
    if offending:
        raise ValidationError(offending)


def numbers_issued(semester: Semester) -> int:
    """How many participant numbers carry this semester's year and type digit."""
    year = _year_digits(semester)
    if not year:
        return 0
    return _number_tallies().get(f"{year}{semester.type_code}", 0)


def activate_semester(*, actor: Any, semester: Semester, request: Any = None) -> Semester:
    """
    Name this semester the current one — which is what BR-001 reads.

    The act the fresh install had no road to at all: without it
    ``active_semester()`` raises and the participants register cannot mint a
    single number.
    """
    policy.require(actor, SCREEN, Action.EDIT, request=request)
    with transaction.atomic():
        _make_current(semester=semester, actor=actor, request=request)
    return semester


def _make_current(*, semester: Semester, actor: Any, request: Any = None) -> None:
    """
    Stand every other semester down, then raise this one — in that order.

    ``active_flag`` is a nullable column made unique because MySQL has no
    partial index (DATA_MODEL §11.3), so raising this row before clearing the
    other would collide on that unique key rather than replace it.
    """
    stood_down = list(
        Semester.objects.filter(is_active=True)
        .exclude(pk=semester.pk)
        .values_list("code", flat=True)
    )
    Semester.objects.exclude(pk=semester.pk).update(is_active=False, active_flag=None)
    semester.is_active = True
    semester.save()
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(semester.pk),
        reference=semester.code,
        summary_ar=f"تعيين الفصل الحالي — {semester.name_ar}",
        actor=actor,
        changes={
            "is_active": {"from": "False", "to": "True"},
            "stood_down": {"from": ", ".join(stood_down), "to": ""},
        },
        request=request,
    )


def semester_instance(*, actor: Any, code: str, request: Any = None) -> Semester:
    """The row itself, for handing back into this module."""
    policy.require(actor, SCREEN, Action.VIEW, request=request)
    return Semester.objects.get(code=code)


__all__ = [
    "CENTER_TYPE_DIGIT",
    "ENTITY",
    "SCREEN",
    "activate_semester",
    "active_semester_row",
    "create_semester",
    "numbers_issued",
    "semester_instance",
    "semester_rows",
    "semester_type_choices",
    "update_semester",
]
