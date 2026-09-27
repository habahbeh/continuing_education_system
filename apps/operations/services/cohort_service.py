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
from django.utils import timezone

from apps.core.display import text_of
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Cohort"

#: The stage a reader acts on, as opposed to the stored status.
#:
#: ``PLANNED`` means two different things — before the ministry file and after
#: its approval — because ``mohe_service`` returns an approved cohort to
#: PLANNED. The register is sorted and filtered by what comes next, so the
#: stage is computed once here (status + approval + the file on record) and
#: every screen reads the same answer. (key, label, chip tone)
STAGES: list[tuple[str, str, str]] = [
    ("NEEDS_FILE", "بانتظار الملف الوزاري", "warn"),
    ("AT_MOHE", "بانتظار قرار الوزارة", "info"),
    ("MOHE_REJECTED", "مرفوضة من الوزارة", "danger"),
    ("ENROLLABLE", "جاهزة للتسجيل", "ok"),
    ("RUNNING", "قيد التنفيذ", "ok"),
    ("COMPLETED", "مكتملة", ""),
    ("CANCELLED", "ملغاة لقلة التسجيل", "danger"),
]
STAGE_LOOK: dict[str, tuple[str, str]] = {k: (label, tone) for k, label, tone in STAGES}


def _stage(status: str, is_approved: bool, mohe_file: dict[str, Any] | None) -> str:
    """Which of ``STAGES`` this cohort is in — the reader's question, not the enum's."""
    from apps.operations.models import CohortStatus

    if status == CohortStatus.RUNNING:
        return "RUNNING"
    if status == CohortStatus.COMPLETED:
        return "COMPLETED"
    if status == CohortStatus.CANCELLED_LOW_ENROLLMENT:
        return "CANCELLED"
    if status == CohortStatus.MOHE_REJECTED:
        return "MOHE_REJECTED"
    if is_approved:
        return "ENROLLABLE"
    if status == CohortStatus.PENDING_MOHE or (mohe_file and mohe_file["status"] == "SUBMITTED"):
        return "AT_MOHE"
    return "NEEDS_FILE"


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
    from django.db.models import Count, Q

    from apps.operations.models import Cohort, EnrollmentStatus

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    counted = {EnrollmentStatus.CANCELLED, EnrollmentStatus.TRANSFERRED_OUT}
    # The count rides along in the same query — one cohort per row used to
    # mean one extra query per row for its enrolments. Same exclusion as
    # ``Cohort.enrolled_count``, grouped once for the whole page.
    queryset = Cohort.objects.select_related("program", "semester", "agreement__partner").annotate(
        live_enrollments=Count("enrollments", filter=~Q(enrollments__status__in=counted))
    )
    if query:
        # The catalogue links here with a PROGRAMME code («الدفعات» on a
        # programme row), so a register that searched its own code and name
        # only answered that link with an empty table.
        queryset = queryset.filter(
            Q(code__icontains=query)
            | Q(name_ar__icontains=query)
            | Q(program__code__icontains=query)
            | Q(program__name_ar__icontains=query)
            | Q(trainer_name__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if program_code:
        queryset = queryset.filter(program__code=program_code)

    # BR-013 in one read for the page: which cohorts hold a live approval.
    # The row says «معتمدة» or names the next step; asked cohort by cohort it
    # was one query per row.
    from apps.operations.models import MoheStatus, MoheSubmission

    approved = set(
        MoheSubmission.objects.filter(status=MoheStatus.APPROVED).values_list(
            "cohort_id", flat=True
        )
    )
    # The newest file per cohort, so the row can open it rather than offer
    # a second one. Ordered newest first; the first seen per cohort wins.
    latest_file: dict[int, dict[str, Any]] = {}
    for sub in MoheSubmission.objects.order_by("-pk").only("pk", "cohort_id", "status"):
        latest_file.setdefault(
            sub.cohort_id,
            {"id": sub.pk, "status": sub.status, "status_display": sub.get_status_display()},
        )

    rows: list[dict[str, Any]] = []
    for cohort in queryset.order_by("-starts_on", "code"):
        enrolled = cohort.live_enrollments
        file_row = latest_file.get(cohort.pk)
        stage = _stage(cohort.status, cohort.pk in approved, file_row)
        stage_label, stage_tone = STAGE_LOOK[stage]
        rows.append(
            {
                "code": cohort.code,
                "name_ar": cohort.name_ar,
                "program_code": cohort.program.code,
                "program_name": cohort.program.name_ar,
                "program_type": cohort.program.program_type,
                "program_type_display": cohort.program.get_program_type_display(),
                "is_mohe_approved": cohort.pk in approved,
                "mohe_file": file_row,
                "delivery_method_display": cohort.get_delivery_method_display(),
                "level": cohort.level,
                "semester": str(cohort.semester),
                "starts_on": cohort.starts_on,
                "ends_on": cohort.ends_on,
                "capacity": cohort.capacity,
                "enrolled_count": enrolled,
                "seats_left": max(cohort.capacity - enrolled, 0),
                "status": cohort.status,
                "status_display": cohort.get_status_display(),
                "stage": stage,
                "stage_display": stage_label,
                "stage_tone": stage_tone,
                "cancellation_reason_ar": cohort.cancellation_reason_ar,
                # What the reader may do next, decided here rather than by a
                # chain of ``{% if %}`` over the status in the template.
                "can_start": stage == "ENROLLABLE",
                "can_complete": stage == "RUNNING",
                "can_cancel": stage in ("NEEDS_FILE", "AT_MOHE", "MOHE_REJECTED", "ENROLLABLE", "RUNNING"),
                "can_edit_row": stage not in ("COMPLETED", "CANCELLED"),
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

    if ends_on <= starts_on:
        raise ValidationError("تاريخ الانتهاء يجب أن يلي تاريخ البداية.")
    if capacity <= 0:
        raise ValidationError("السعة يجب أن تكون أكبر من صفر.")
    code = code.strip()
    if code and Cohort.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الدفعة {code} مستعمل سلفاً.")

    try:
        program = Program.objects.get(code=program_code)
    except Program.DoesNotExist as exc:
        raise ValidationError(f"برنامج غير معروف: {program_code}") from exc

    try:
        semester = Semester.objects.get(code=semester_code)
    except Semester.DoesNotExist as exc:
        raise ValidationError(f"فصل غير معروف: {semester_code}") from exc

    # Nothing the operator would have to invent: a blank code is minted from
    # the programme (``CO-<program>-<n>``) and a blank name is the programme
    # and the term. Both stay editable on the form for whoever has a
    # convention of their own.
    if not code:
        code = next_cohort_code(program_code)
    if not name_ar.strip():
        name_ar = f"{program.name_ar} — {semester}"

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

        # Sprint 8F-1 — the list of agreements the form offers is a courtesy;
        # this is the control. ``open_cohort`` took any number that resolved,
        # so a stale page or a hand-built POST could place a cohort under a
        # contract that had lapsed or had never been made live, and every
        # claim drawn on it afterwards would be drawn on nothing.
        #
        # Asked of ``starts_on``, not of today: the question is whether the
        # contract covers the period this cohort RUNS in. A cohort starting
        # after its partner's agreement expires earns that partner nothing,
        # and finding that out at claim time is finding out too late.
        if not agreement.is_available_on(starts_on):
            raise ValidationError(
                f"الاتفاقية {agreement.agreement_number} غير متاحة لدفعة تبدأ في "
                f"{starts_on} — حالتها {agreement.get_status_display()} "
                f"وسريانها {agreement.valid_from} حتى {agreement.valid_to}."
            )

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


# ---------------------------------------------------------------------------
# The lifecycle (§3.3/12 gives the manager E and A)
#
# Four of six statuses were unreachable before this: nothing in the repository
# set RUNNING, COMPLETED, CANCELLED_LOW_ENROLLMENT or PENDING_MOHE, so a cohort
# that had ended still read «مخطَّطة» and a course that never filled could not
# be closed at all — while §5.3 makes cancelling it the very thing that opens a
# full refund, and BR-065 the thing that waives the transfer bound.
# ---------------------------------------------------------------------------
EDITABLE_FIELDS = (
    "starts_on",
    "ends_on",
    "capacity",
    "trainer_name",
    "location",
    "delivery_method",
    "agreement_number",
    "lecture_cost",
)


def _live_enrollments(cohort: Any) -> int:
    from apps.operations.models import EnrollmentStatus

    return cohort.enrollments.exclude(
        status__in=(EnrollmentStatus.CANCELLED, EnrollmentStatus.TRANSFERRED_OUT)
    ).count()


def _audit_cohort(*, actor: Any, cohort: Any, action: str, summary: str, changes: dict, request: Any) -> None:
    write_audit(
        action=action,
        entity_type=ENTITY,
        entity_id=str(cohort.pk),
        reference=cohort.code,
        summary_ar=summary,
        actor=actor,
        changes=changes,
        request=request,
    )


def update_cohort(*, actor: Any, cohort: Any, data: dict[str, Any], request: Any = None) -> Any:
    """
    Correct a cohort's period, capacity and who runs it.

    The programme, the level and the code are the cohort's identity — enrolments,
    ministry files and archives all name them — so they are not editable here. A
    finished or cancelled cohort is history and refuses the edit.
    """
    from apps.operations.models import CohortStatus
    from apps.partners.models import Agreement

    policy.require(actor, Screen.COHORTS, Action.EDIT, request=request)

    if cohort.status in (CohortStatus.COMPLETED, CohortStatus.CANCELLED_LOW_ENROLLMENT):
        raise ValidationError("الدفعة المكتملة أو الملغاة لا تُعدَّل — سجلها تاريخ.")

    data = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
    number = str(data.pop("agreement_number", "") or "").strip()
    if "agreement_number" in EDITABLE_FIELDS:
        if number:
            try:
                cohort.agreement = Agreement.objects.get(agreement_number=number)
            except Agreement.DoesNotExist:
                raise ValidationError(f"لا توجد اتفاقية بالرقم {number}.") from None
        else:
            cohort.agreement = None

    live = _live_enrollments(cohort)
    if "capacity" in data and int(data["capacity"]) < live:
        raise ValidationError(
            f"السعة {data['capacity']} أقل من التسجيلات القائمة ({live}) — لا تُخفَّض تحتها."
        )

    return _write_update(actor=actor, cohort=cohort, data=data, number=number, request=request)


@transaction.atomic
def _write_update(
    *, actor: Any, cohort: Any, data: dict[str, Any], number: str, request: Any
) -> Any:
    before = {field: str(getattr(cohort, field)) for field in data}
    for field, value in data.items():
        setattr(cohort, field, value)
    cohort.full_clean()
    cohort.save()
    _audit_cohort(
        actor=actor,
        cohort=cohort,
        action="UPDATE",
        summary=f"تعديل الدفعة {cohort.code}",
        changes={"before": before, "after": {f: str(getattr(cohort, f)) for f in data}, "agreement": number},
        request=request,
    )
    return cohort


def start_cohort(*, actor: Any, cohort: Any, request: Any = None) -> Any:
    """PLANNED → RUNNING, and only for a cohort the ministry has approved (BR-013)."""
    from apps.operations.models import CohortStatus
    from apps.operations.services import mohe_service

    policy.require(actor, Screen.COHORTS, Action.EDIT, request=request)

    if cohort.status == CohortStatus.RUNNING:
        raise ValidationError("الدفعة قيد التنفيذ سلفاً.")
    if cohort.status != CohortStatus.PLANNED:
        raise ValidationError("لا تبدأ إلا دفعة مخطَّطة معتمدة من الوزارة.")
    if not mohe_service.cohort_is_approved(cohort):
        raise ValidationError(
            f"الدفعة {cohort.code} لم تُعتمد من الوزارة — لا تبدأ قبل الاعتماد (BR-013)."
        )

    return _write_status(
        actor=actor,
        cohort=cohort,
        status=CohortStatus.RUNNING,
        summary=f"بدء تنفيذ الدفعة {cohort.code}",
        request=request,
    )


@transaction.atomic
def _write_status(
    *, actor: Any, cohort: Any, status: str, summary: str, request: Any, action: str = "UPDATE",
    extra: dict[str, Any] | None = None,
) -> Any:
    fields = ["status"]
    cohort.status = status
    if extra and "cancellation_reason_ar" in extra:
        cohort.cancellation_reason_ar = extra["cancellation_reason_ar"]
        fields.append("cancellation_reason_ar")
    cohort.save(update_fields=fields)
    _audit_cohort(
        actor=actor,
        cohort=cohort,
        action=action,
        summary=summary,
        changes={"status": status, **(extra or {})},
        request=request,
    )
    return cohort


def complete_cohort(*, actor: Any, cohort: Any, request: Any = None) -> Any:
    """RUNNING → COMPLETED. Clearance and certificates read from here on."""
    from apps.operations.models import CohortStatus

    policy.require(actor, Screen.COHORTS, Action.EDIT, request=request)

    if cohort.status != CohortStatus.RUNNING:
        raise ValidationError("لا تُنهى إلا دفعة قيد التنفيذ.")

    return _write_status(
        actor=actor,
        cohort=cohort,
        status=CohortStatus.COMPLETED,
        summary=f"إنهاء الدفعة {cohort.code}",
        request=request,
    )


def cancel_for_low_enrollment(
    *, actor: Any, cohort: Any, reason_ar: str, request: Any = None
) -> Any:
    """
    Cancel a cohort that did not fill (§5.3, BR-065).

    APPROVE, not EDIT: the cancellation is what justifies a full refund for
    everyone on the cohort and waives the course-field bound on a transfer, so
    it is a decision, not a correction. The reason is stored verbatim beside
    the status — a refund defended by "the cohort was cancelled" has to be able
    to show why.
    """
    from apps.operations.models import CohortStatus

    policy.require(actor, Screen.COHORTS, Action.APPROVE, request=request)

    reason_ar = (reason_ar or "").strip()
    if not reason_ar:
        raise ValidationError("الإلغاء يحتاج سبباً — هو سند الاسترداد الكامل (§5.3).")
    if cohort.status == CohortStatus.CANCELLED_LOW_ENROLLMENT:
        raise ValidationError("الدفعة ملغاة سلفاً.")
    if cohort.status == CohortStatus.COMPLETED:
        raise ValidationError("الدفعة المكتملة لا تُلغى.")

    live = _live_enrollments(cohort)
    return _write_status(
        actor=actor,
        cohort=cohort,
        status=CohortStatus.CANCELLED_LOW_ENROLLMENT,
        summary=f"إلغاء الدفعة {cohort.code} لقلة التسجيل — {reason_ar}",
        request=request,
        action="APPROVE",
        extra={"cancellation_reason_ar": reason_ar, "live_enrollments": live},
    )


def cancellation_impact(*, actor: Any, cohort: Any, request: Any = None) -> dict[str, Any]:
    """What cancelling this cohort would touch — stated before it is confirmed."""
    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return {"live_enrollments": _live_enrollments(cohort)}


def cohort_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    (code, label) pairs for a form — approved cohorts only.

    An enrolment on an unapproved cohort is refused by BR-013 anyway, so
    offering one in a dropdown would only produce a refusal the user could
    have been spared.
    """
    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    return [(row["code"], row["label"]) for row in _approved_cohort_rows()]


def enrollable_cohorts(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Approved cohorts as rows, grouped by programme type — for the quick
    enrolment dialog on the participants screen, which first asks «دورة أم
    دبلوم؟» and only then lists the cohorts of that kind.

    Same gate and same set as ``cohort_choices``; only the shape differs.
    """
    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    from apps.catalog.models import ProgramType

    rows = _approved_cohort_rows()
    return [
        {
            "type": value,
            "label": label,
            "cohorts": [row for row in rows if row["program_type"] == value],
        }
        for value, label in ProgramType.choices
        if any(row["program_type"] == value for row in rows)
    ]


def _approved_cohort_rows() -> list[dict[str, Any]]:
    """
    Every BR-013-approved cohort, newest first — two queries for the whole
    list. The approval used to be asked cohort by cohort (one query each);
    the set of approved cohort ids is read once instead.
    """
    from apps.operations.models import Cohort, MoheStatus, MoheSubmission

    approved = set(
        MoheSubmission.objects.filter(status=MoheStatus.APPROVED).values_list(
            "cohort_id", flat=True
        )
    )
    return [
        {
            "code": cohort.code,
            "label": f"{cohort.code} — {cohort.name_ar}",
            "program_name": cohort.program.name_ar,
            "program_type": cohort.program.program_type,
            "starts_on": cohort.starts_on,
        }
        for cohort in Cohort.objects.select_related("program").order_by("-starts_on")
        if cohort.pk in approved
    ]


def get_cohort_instance(*, actor: Any, code: str, request: Any = None) -> Any:
    """The Cohort model object, for handing to another service (A-05)."""
    from apps.operations.models import Cohort

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return Cohort.objects.select_related("program", "agreement").get(code=code)


def next_cohort_code(program_code: str) -> str:
    """``CO-<program>-<n>``, the first ``n`` not yet taken for that programme."""
    from apps.operations.models import Cohort

    prefix = f"CO-{program_code}-"
    taken = set(Cohort.objects.filter(code__startswith=prefix).values_list("code", flat=True))
    n = len(taken) + 1
    while f"CO-{program_code}-{n}" in taken:
        n += 1
    return f"CO-{program_code}-{n}"


def program_options(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Active programmes with what the open-cohort dialog needs to shape itself:
    the kind (to group the list), and whether the programme is levelled and
    how many levels (BR-007 — the level field is drawn only where it applies).
    """
    from apps.catalog.models import Program, PriceListItem
    from apps.catalog.services import pricing_service

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)

    # A programme with no price on the list in force cannot price an enrolment
    # (BR-008), so the dialog says so on the option rather than letting the
    # cohort be opened and the till discover it.
    try:
        price_list = pricing_service.effective_price_list(as_of=timezone.localdate())
        priced = set(
            PriceListItem.objects.filter(price_list=price_list).values_list(
                "program_id", flat=True
            )
        )
    except pricing_service.NoEffectivePriceListError:
        priced = set()

    return [
        {
            "code": p.code,
            "label": p.name_ar,
            "type": p.program_type,
            "type_label": p.get_program_type_display(),
            "is_leveled": p.is_leveled,
            "levels": list(range(1, (p.levels_count or 0) + 1)) if p.is_leveled else [],
            "is_priced": p.pk in priced,
        }
        for p in Program.objects.filter(is_active=True).order_by("program_type", "name_ar")
    ]


def semester_options(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """Semesters newest first, with their dates so the dialog can propose a period."""
    from apps.core.models import Semester

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    return [
        {
            "code": s.code,
            "label": str(s),
            "starts_on": s.starts_on,
            "ends_on": s.ends_on,
            "is_active": s.is_active,
        }
        for s in Semester.objects.order_by("-starts_on")
    ]


def status_choices() -> list[tuple[str, str]]:
    """(value, label) of every cohort status — for the register's filter."""
    from apps.operations.models import CohortStatus

    return list(CohortStatus.choices)


def field_suggestions(*, actor: Any, request: Any = None) -> dict[str, list[str]]:
    """Trainers and places already on record — offered, never required."""
    from apps.operations.models import Cohort

    policy.require(actor, Screen.COHORTS, Action.VIEW, request=request)
    trainers = Cohort.objects.exclude(trainer_name="").values_list("trainer_name", flat=True)
    places = Cohort.objects.exclude(location="").values_list("location", flat=True)
    return {
        "trainers": sorted(set(trainers)),
        "locations": sorted(set(places)),
    }


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
    "STAGES",
    "STAGE_LOOK",
    "cancel_for_low_enrollment",
    "cancellation_impact",
    "cohort_choices",
    "complete_cohort",
    "enrollable_cohorts",
    "field_suggestions",
    "get_cohort",
    "get_cohort_instance",
    "list_cohorts",
    "next_cohort_code",
    "open_cohort",
    "program_choices",
    "program_options",
    "semester_choices",
    "semester_options",
    "start_cohort",
    "status_choices",
    "update_cohort",
]
