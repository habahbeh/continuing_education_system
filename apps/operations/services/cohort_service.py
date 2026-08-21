"""
Running instances of a programme — the cohort (BR-013, WORKFLOWS §1.1).

Sprint 8B added this because nothing else could. ``Cohort`` had existed since
Sprint 6 with its constraints in place and no service that created one: every
cohort in the repository came from a test fixture calling ``.objects.create()``
directly. That left the whole operational chain unreachable from a screen —
no cohort means no enrolment, no enrolment means no payment, and clearance and
certificates sit at the far end of a path with no entrance.

**Deliberately thin.** Opening a cohort, listing them, and reading one. The
ministry approval that decides whether a cohort may accept enrolments already
belongs to ``mohe_service`` and is not duplicated here — this module reports
that status, it does not decide it.

The agreement is attached at cohort level rather than per enrolment because
that is how the signed agreements are actually organised: the تناغم schedules
name programmes and terms, and the Excel history keeps one sheet per cohort
per partner ("20223تناغم 7"). A participant does not choose a partner; the
cohort they join was already placed under one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.display import text_of
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Cohort"


def list_cohorts(
    *,
    actor: Any,
    query: str = "",
    status: str = "",
    program_code: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Cohorts as rows, with the counts a list screen needs.

    Returns dictionaries rather than model instances so views never hold an
    ORM object they might be tempted to traverse — A-05 keeps models out of
    the view layer, and handing one over would defeat it in spirit.
    """
    from apps.operations.models import Cohort, EnrollmentStatus

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    queryset = Cohort.objects.select_related("program", "semester", "agreement__partner")
    if query:
        queryset = queryset.filter(code__icontains=query) | queryset.filter(
            name_ar__icontains=query
        )
    if status:
        queryset = queryset.filter(status=status)
    if program_code:
        queryset = queryset.filter(program__code=program_code)

    counted = {EnrollmentStatus.CANCELLED, EnrollmentStatus.TRANSFERRED_OUT}
    rows: list[dict[str, Any]] = []
    for cohort in queryset.order_by("-starts_on", "code"):
        enrolled = sum(1 for e in cohort.enrollments.all() if e.status not in counted)
        rows.append(
            {
                "code": cohort.code,
                "name_ar": cohort.name_ar,
                "program_code": cohort.program.code,
                "program_name": cohort.program.name_ar,
                "level": cohort.level,
                "semester": str(cohort.semester),
                "starts_on": cohort.starts_on,
                "ends_on": cohort.ends_on,
                "capacity": cohort.capacity,
                "enrolled_count": enrolled,
                "seats_left": max(cohort.capacity - enrolled, 0),
                "status": cohort.status,
                "status_display": cohort.get_status_display(),
                "trainer_name": cohort.trainer_name,
                "location": cohort.location,
                "partner_name": text_of(getattr(cohort.agreement, "partner", None), "name_ar"),
                "agreement_number": text_of(cohort.agreement, "agreement_number"),
            }
        )
    return rows


def get_cohort(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """One cohort, with the ministry status the enrolment gate reads."""
    from apps.operations.models import Cohort
    from apps.operations.services import mohe_service

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    cohort = Cohort.objects.select_related("program", "semester", "agreement__partner").get(
        code=code
    )

    rows = list_cohorts(actor=actor, query=code, request=request)
    row = next((r for r in rows if r["code"] == code), {})
    row.update(
        {
            "delivery_method": cohort.delivery_method,
            "is_mohe_approved": mohe_service.cohort_is_approved(cohort),
            "mohe_course_number": _mohe_number(cohort),
        }
    )
    return row


def _mohe_number(cohort: Any) -> str:
    from apps.operations.services import mohe_service

    submission = mohe_service.approved_submission_for(cohort)
    return getattr(submission, "mohe_course_number", "") if submission else ""


def open_cohort(
    *,
    actor: Any,
    program_code: str,
    semester_code: str,
    code: str,
    name_ar: str,
    starts_on: date,
    ends_on: date,
    capacity: int,
    level: int | None = None,
    trainer_name: str = "",
    location: str = "",
    delivery_method: str = "IN_PERSON",
    agreement_number: str = "",
    lecture_cost: Decimal | None = None,
    request: Any = None,
) -> Any:
    """
    Open a cohort in PLANNED state.

    PLANNED, never RUNNING: BR-013 makes ministry approval the gate on
    enrolment, and a cohort that opened as running would look enrollable
    before anyone had asked the ministry. The status moves through
    ``mohe_service`` as the submission is decided.
    """
    from apps.catalog.models import Program
    from apps.core.models import Semester
    from apps.operations.models import Cohort, CohortStatus
    from apps.partners.models import Agreement

    policy.require(actor, Screen.COHORTS, Action.CREATE, request=request)

    if not name_ar.strip():
        raise ValidationError("اسم الدفعة إلزامي.")
    if ends_on <= starts_on:
        raise ValidationError("تاريخ الانتهاء يجب أن يلي تاريخ البداية.")
    if capacity <= 0:
        raise ValidationError("السعة يجب أن تكون أكبر من صفر.")
    if Cohort.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الدفعة {code} مستعمل سلفاً.")

    try:
        program = Program.objects.get(code=program_code)
    except Program.DoesNotExist as exc:
        raise ValidationError(f"برنامج غير معروف: {program_code}") from exc

    try:
        semester = Semester.objects.get(code=semester_code)
    except Semester.DoesNotExist as exc:
        raise ValidationError(f"فصل غير معروف: {semester_code}") from exc

    # BR-007 — a levelled programme's cohort names its level, and one that is
    # not levelled has none to name. Getting this wrong makes the price
    # resolver pick the wrong row.
    if program.is_leveled and not level:
        raise ValidationError(f"البرنامج {program.code} ذو مستويات — يجب تحديد المستوى للدفعة.")
    if not program.is_leveled and level:
        raise ValidationError(f"البرنامج {program.code} بلا مستويات — لا يُحدَّد له مستوى.")

    agreement = None
    if agreement_number:
        try:
            agreement = Agreement.objects.get(agreement_number=agreement_number)
        except Agreement.DoesNotExist as exc:
            raise ValidationError(f"اتفاقية غير معروفة: {agreement_number}") from exc

    return _open_cohort(
        actor=actor,
        code=code,
        program=program,
        semester=semester,
        name_ar=name_ar.strip(),
        starts_on=starts_on,
        ends_on=ends_on,
        capacity=capacity,
        level=level,
        trainer_name=trainer_name.strip(),
        location=location.strip(),
        delivery_method=delivery_method,
        agreement=agreement,
        lecture_cost=lecture_cost,
        status=CohortStatus.PLANNED,
        request=request,
    )


@transaction.atomic
def _open_cohort(
    *,
    actor: Any,
    code: str,
    program: Any,
    semester: Any,
    name_ar: str,
    starts_on: date,
    ends_on: date,
    capacity: int,
    level: int | None,
    trainer_name: str,
    location: str,
    delivery_method: str,
    agreement: Any,
    lecture_cost: Decimal | None,
    status: str,
    request: Any,
) -> Any:
    from apps.operations.models import Cohort

    cohort = Cohort(
        code=code,
        program=program,
        semester=semester,
        level=level,
        name_ar=name_ar,
        starts_on=starts_on,
        ends_on=ends_on,
        capacity=capacity,
        trainer_name=trainer_name,
        location=location,
        delivery_method=delivery_method,
        agreement=agreement,
        status=status,
    )
    if lecture_cost is not None:
        cohort.lecture_cost = lecture_cost
    cohort.full_clean(exclude=["program", "semester", "agreement"])
    cohort.save()

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(cohort.pk),
        reference=code,
        summary_ar=f"فتح دفعة {code} — {program.name_ar}",
        actor=actor,
        changes={
            "program": program.code,
            "semester": semester.code,
            "level": level,
            "starts_on": starts_on.isoformat(),
            "ends_on": ends_on.isoformat(),
            "capacity": capacity,
            "agreement": agreement.agreement_number if agreement else None,
            "status": status,
        },
        request=request,
    )
    return cohort


def cohort_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    (code, label) pairs for a form — approved cohorts only.

    An enrolment on an unapproved cohort is refused by BR-013 anyway, so
    offering one in a dropdown would only produce a refusal the user could
    have been spared.
    """
    from apps.operations.models import Cohort
    from apps.operations.services import mohe_service

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    pairs: list[tuple[str, str]] = []
    for cohort in Cohort.objects.select_related("program").order_by("-starts_on"):
        if mohe_service.cohort_is_approved(cohort):
            pairs.append((cohort.code, f"{cohort.code} — {cohort.name_ar}"))
    return pairs


def get_cohort_instance(*, actor: Any, code: str, request: Any = None) -> Any:
    """The Cohort model object, for handing to another service (A-05)."""
    from apps.operations.models import Cohort

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return Cohort.objects.select_related("program", "agreement").get(code=code)


def program_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """(code, label) pairs of active programmes, for the cohort form."""
    from apps.catalog.models import Program

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return [
        (p.code, f"{p.code} — {p.name_ar}")
        for p in Program.objects.filter(is_active=True).order_by("code")
    ]


def semester_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """(code, label) pairs of semesters, newest first."""
    from apps.core.models import Semester

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return [(s.code, str(s)) for s in Semester.objects.order_by("-starts_on")]


__all__ = [
    "cohort_choices",
    "get_cohort",
    "get_cohort_instance",
    "list_cohorts",
    "open_cohort",
    "program_choices",
    "semester_choices",
]
