"""
Programme and price-list lifecycle (BR-006 … BR-008, D-14).

Two controls live here, and both are the kind the demo left to good intentions:

**BR-006 — a diploma's subject prices must reconcile with its course fee.**
``Σ subject.price == course_fee − consumables``. It cannot be a CHECK
constraint because it aggregates across rows, so it is enforced at approval
and re-checkable afterwards by a management command.

**D-14 — an approved price list is immutable.** Not "the UI hides the edit
button": every write path refuses, and approving a new list archives the
previous one so history stays intact (BR-008).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.catalog.models import (
    CourseCategory,
    DepositPolicy,
    KnowledgeField,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
    RegistrationFeeRule,
    Subject,
)
from apps.people.constants import PARTICIPANT_CATEGORY_CHOICES
from apps.catalog.services import pricing_service
from apps.core.exceptions import ImmutableRecordError
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

PROGRAM_ENTITY = "catalog.Program"
PRICE_LIST_ENTITY = "catalog.PriceList"
CATEGORY_ENTITY = "catalog.CourseCategory"
FIELD_ENTITY = "catalog.KnowledgeField"
DEPOSIT_ENTITY = "catalog.DepositPolicy"

#: Deposit policies are governed by the price-list row (PERMISSIONS.md §3.3/13,
#: ``V C E P`` for the centre manager). A deliberate reuse and not a shortcut: a
#: deposit is a term of the price a programme carries, C-26 pairs the amount with
#: the policy on the very same price item, and whoever may set the amount must be
#: able to name the policy or the pair can never be completed. Inventing a matrix
#: row for it would have moved an authority the document already placed.
DEPOSIT_SCREEN = Screen.PRICELISTS

#: Which screen governs a programme, by type (PERMISSIONS.md §3.3 rows 9–11).
SCREEN_BY_TYPE: dict[str, str] = {
    ProgramType.DIPLOMA: Screen.PROGRAMS,
    ProgramType.SHORT_COURSE: Screen.SHORT_COURSES,
    ProgramType.ONLINE_COURSE: Screen.ONLINE_COURSES,
}


def screen_for(program_type: str) -> str:
    return SCREEN_BY_TYPE.get(program_type, Screen.PROGRAMS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BR-006 — the subject-price reconciliation
# ---------------------------------------------------------------------------
def subject_price_variance(program: Program, course_fee: Decimal) -> Decimal:
    """
    ``Σ subjects − (course_fee − consumables)``. Zero means reconciled.

    Returned as a signed number so the screen can say "short by 10" rather
    than "invalid", which is the difference between a message someone can act
    on and one they escalate.
    """
    expected = Decimal(course_fee) - Decimal(program.consumables_per_student)
    return pricing_service.subject_price_total(program) - expected


def check_subject_prices(program: Program, course_fee: Decimal) -> None:
    """BR-006 — raise unless the subject prices reconcile."""
    variance = subject_price_variance(program, course_fee)
    if variance != 0:
        raise ValidationError(
            f"مجموع أسعار المواد لا يطابق رسوم الدورة — الفرق {variance:+} دينار. "
            f"(BR-006: مجموع المواد = رسوم الدورة − المستهلكات)"
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_programs(*, actor: Any, program_type: str, request: Any = None) -> Any:
    policy.require(actor, screen_for(program_type), Action.VIEW, request=request)
    return (
        Program.objects.filter(program_type=program_type)
        .select_related("course_category", "knowledge_field")
        .order_by("name_ar")
    )


#: Cohort statuses that count as «جارية» on the programme row.
_LIVE_COHORT_STATUSES = ("PLANNED", "PENDING_MOHE", "RUNNING")


def _price_in_force(program: Program, price_list: Any) -> Decimal | None:
    """The course fee on the list in force (level 1 for a levelled programme), or None."""
    if price_list is None:
        return None
    item = (
        PriceListItem.objects.filter(program=program, price_list=price_list)
        .order_by("level_key")
        .first()
    )
    return item.course_fee if item else None


def _items_in_force(price_list: Any, program_ids: list[int]) -> dict[int, list[PriceListItem]]:
    """Every priced level of every programme on the list in force — one query, keyed by programme."""
    items: dict[int, list[PriceListItem]] = {}
    if price_list is None or not program_ids:
        return items
    for item in PriceListItem.objects.filter(price_list=price_list, program_id__in=program_ids).order_by(
        "program_id", "level_key"
    ):
        items.setdefault(item.program_id, []).append(item)
    return items


def _fee_rules_in_force(price_list: Any, program_ids: list[int]) -> dict[int, list[RegistrationFeeRule]]:
    """Programme-specific registration-fee rules on the list in force (T-096..T-099), keyed by programme."""
    rules: dict[int, list[RegistrationFeeRule]] = {}
    if price_list is None or not program_ids:
        return rules
    for rule in RegistrationFeeRule.objects.filter(
        price_list=price_list, program_id__in=program_ids
    ).order_by("program_id", "participant_category"):
        rules.setdefault(rule.program_id, []).append(rule)
    return rules


def _fee_note(program_type: str, rules: list[RegistrationFeeRule]) -> str:
    """
    One line for the row about the registration fee.

    An online course carries none at all and never can (BR-010, structural in
    ``pricing_service``), so the row says so whatever rows a price list holds.
    """
    if program_type == ProgramType.ONLINE_COURSE:
        return "بلا رسوم تسجيل (BR-010)"
    if not rules:
        return ""
    if all(r.fee is None for r in rules):
        return "بلا رسم تسجيل"
    return "رسم تسجيل خاص"


def program_rows(
    *,
    actor: Any,
    program_type: str,
    query: str = "",
    category: str = "",
    active: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Programmes as rows for the catalogue screen: the row answers the reader's
    first questions — what does it cost on the list in force, does it have
    live cohorts, and (for a diploma) do its subjects reconcile (BR-006).
    """
    from django.db.models import Count, Q
    from django.utils import timezone as _tz

    policy.require(actor, screen_for(program_type), Action.VIEW, request=request)

    try:
        price_list = pricing_service.effective_price_list(as_of=_tz.localdate())
    except pricing_service.NoEffectivePriceListError:
        price_list = None

    queryset = (
        Program.objects.filter(program_type=program_type)
        .select_related("course_category", "knowledge_field")
        # ``distinct`` on every count: cohorts and subjects are two joins on
        # the same row, and without it a diploma with four subjects and one
        # cohort would report four cohorts.
        .annotate(
            live_cohorts=Count(
                "cohorts", filter=Q(cohorts__status__in=_LIVE_COHORT_STATUSES), distinct=True
            ),
            cohort_count=Count("cohorts", distinct=True),
            subject_count=Count("subjects", distinct=True),
        )
        .order_by("name_ar")
    )
    if query.strip():
        q = query.strip()
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(name_ar__icontains=q)
            | Q(name_en__icontains=q)
            | Q(specialization__icontains=q)
            | Q(course_category__name_ar__icontains=q)
        )
    if category:
        queryset = queryset.filter(course_category__code=category)
    if active == "yes":
        queryset = queryset.filter(is_active=True)
    elif active == "no":
        queryset = queryset.filter(is_active=False)

    programs = list(queryset)
    items_by_program = _items_in_force(price_list, [p.pk for p in programs])
    rules_by_program = _fee_rules_in_force(price_list, [p.pk for p in programs])

    rows = []
    for p in programs:
        items = items_by_program.get(p.pk, [])
        fee = items[0].course_fee if items else None
        subject_total = pricing_service.subject_price_total(p) if p.subject_count else None
        variance = (
            subject_price_variance(p, fee)
            if p.program_type == ProgramType.DIPLOMA and fee is not None and subject_total is not None
            else None
        )
        rows.append(
            {
                "code": p.code,
                "name_ar": p.name_ar,
                "name_en": p.name_en,
                "program_type": p.program_type,
                "category": p.course_category.name_ar if p.course_category_id else "",
                "category_code": p.course_category.code if p.course_category_id else "",
                "field": p.knowledge_field.name_ar if p.knowledge_field_id else "",
                "specialization": p.specialization,
                "training_hours": p.training_hours,
                "is_leveled": p.is_leveled,
                "levels_count": p.levels_count,
                "consumables": p.consumables_per_student,
                "min_first_payment": p.minimum_first_payment_override,
                "is_active": p.is_active,
                "price": fee,
                "price_list_code": price_list.code if price_list and fee is not None else "",
                "levels_priced": len(items),
                "deposit": items[0].deposit_amount if items else None,
                "fee_note": _fee_note(p.program_type, rules_by_program.get(p.pk, [])),
                "subject_count": p.subject_count,
                "subject_total": subject_total,
                "variance": variance,
                "live_cohorts": p.live_cohorts,
                "cohort_count": p.cohort_count,
            }
        )
    return rows


def programs_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts over already-projected rows — the screen's tiles."""
    return {
        "active": sum(1 for r in rows if r["is_active"]),
        "inactive": sum(1 for r in rows if not r["is_active"]),
        "with_live_cohorts": sum(1 for r in rows if r["live_cohorts"]),
        "unpriced": sum(1 for r in rows if r["price"] is None),
        "unreconciled": sum(1 for r in rows if r["variance"] not in (None, 0)),
    }


def resolve_lookups(data: dict[str, Any]) -> dict[str, Any]:
    """Replace the ``knowledge_field`` / ``course_category`` codes with instances (or None)."""
    field_code = data.pop("knowledge_field", "")
    category_code = data.pop("course_category", "")
    data["knowledge_field"] = KnowledgeField.objects.get(code=field_code) if field_code else None
    data["course_category"] = (
        CourseCategory.objects.get(code=category_code) if category_code else None
    )
    return data


def category_choices() -> list[tuple[str, str]]:
    return [(c.code, c.name_ar) for c in CourseCategory.objects.filter(is_active=True).order_by("name_ar")]


def field_choices() -> list[tuple[str, str]]:
    return [(f.code, f.name_ar) for f in KnowledgeField.objects.filter(is_active=True).order_by("name_ar")]


def program_cohorts(*, actor: Any, program: Program, request: Any = None) -> list[dict[str, Any]]:
    """The programme's cohorts, newest first, for its detail page."""
    policy.require(actor, screen_for(program.program_type), Action.VIEW, request=request)
    return [
        {
            "code": c.code,
            "name_ar": c.name_ar or c.code,
            "status": c.status,
            "status_display": c.get_status_display(),
            "starts_on": c.starts_on,
            "enrollment_count": c.enrollments.count(),
        }
        for c in program.cohorts.all().order_by("-starts_on", "-id")[:20]
    ]


def set_program_active(
    *, actor: Any, program: Program, active: bool, request: Any = None
) -> Program:
    """
    Retire or reinstate a programme. Retiring one with live cohorts is
    refused: the cohorts keep running on a programme nobody can find.
    """
    policy.require(actor, screen_for(program.program_type), Action.EDIT, request=request)
    if not active and program.cohorts.filter(status__in=_LIVE_COHORT_STATUSES).exists():
        raise ValidationError(
            f"للبرنامج «{program.name_ar}» دفعات جارية — يُوقَف بعد اكتمالها أو إلغائها."
        )
    return update_program(actor=actor, program=program, data={"is_active": active}, request=request)


def add_subject(
    *, actor: Any, program: Program, data: dict[str, Any], request: Any = None
) -> Subject:
    """A diploma subject — sequence assigned at the end unless given (BR-007: zero is a price)."""
    policy.require(actor, screen_for(program.program_type), Action.EDIT, request=request)
    if program.program_type != ProgramType.DIPLOMA:
        raise ValidationError("المواد للدبلومات وحدها (§2.3).")
    data = dict(data)
    if not data.get("sequence"):
        last = program.subjects.order_by("-sequence").values_list("sequence", flat=True).first()
        data["sequence"] = (last or 0) + 1
    with transaction.atomic():
        subject = Subject(program=program, **data)
        subject.full_clean()
        subject.save()
        write_audit(
            action="UPDATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"إضافة مادة — {subject.name_ar} ({subject.price})",
            actor=actor,
            changes={"subject": subject.name_ar, "price": str(subject.price), "hours": subject.training_hours},
            request=request,
        )
    return subject


def update_subject(
    *, actor: Any, subject: Subject, data: dict[str, Any], request: Any = None
) -> Subject:
    policy.require(actor, screen_for(subject.program.program_type), Action.EDIT, request=request)
    before = {f: getattr(subject, f) for f in data}
    with transaction.atomic():
        for f, v in data.items():
            setattr(subject, f, v)
        subject.full_clean()
        subject.save()
        write_audit(
            action="UPDATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(subject.program_id),
            reference=subject.program.code,
            summary_ar=f"تعديل مادة — {subject.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(subject, f))}
                for f in data
                if before[f] != getattr(subject, f)
            },
            request=request,
        )
    return subject


def remove_subject(*, actor: Any, subject: Subject, request: Any = None) -> None:
    """
    Remove a subject — only while the programme has no cohorts: once a cohort
    exists the subjects are what enrolments and claims were built on.
    """
    program = subject.program
    policy.require(actor, screen_for(program.program_type), Action.EDIT, request=request)
    if program.cohorts.exists():
        raise ValidationError(
            f"للبرنامج «{program.name_ar}» دفعات؛ لا تُحذف مادة بعد ذلك — يُعدَّل سعرها أو تُصفَّر (BR-007)."
        )
    with transaction.atomic():
        name = subject.name_ar
        subject.delete()
        write_audit(
            action="UPDATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"حذف مادة — {name}",
            actor=actor,
            changes={"removed_subject": name},
            request=request,
        )


def subject_instance(*, actor: Any, program: Program, subject_id: int, request: Any = None) -> Subject:
    policy.require(actor, screen_for(program.program_type), Action.VIEW, request=request)
    return Subject.objects.get(pk=subject_id, program=program)


def reconciliation(*, program: Program) -> dict[str, Any]:
    """BR-006 as the detail page states it: Σ subjects vs (list fee − consumables)."""
    from django.utils import timezone as _tz

    try:
        price_list = pricing_service.effective_price_list(as_of=_tz.localdate())
    except pricing_service.NoEffectivePriceListError:
        price_list = None
    fee = _price_in_force(program, price_list)
    total = pricing_service.subject_price_total(program)
    variance = subject_price_variance(program, fee) if fee is not None else None
    return {
        "subject_total": total,
        "fee": fee,
        "price_list_code": price_list.code if price_list and fee is not None else "",
        "consumables": program.consumables_per_student,
        "expected": (fee - program.consumables_per_student) if fee is not None else None,
        "variance": variance,
        "ok": variance == 0 if variance is not None else None,
    }


def pricing_in_force(*, program: Program) -> dict[str, Any]:
    """
    What the programme costs today, as the detail page states it: every priced
    level with its deposit (BR-096), and the registration fee per participant
    category — the programme's own rule where one exists (T-096..T-099), the
    general rule otherwise. ``fee`` is None where NO fee is charged.

    An online course is the exception the screen must not get wrong: it carries
    no registration fee at all (BR-010), which ``pricing_service`` enforces
    structurally, so no category is listed for it however the price list is
    populated. Printing the general 50/15 there would be the screen telling the
    reader to collect money the system will never charge.
    """
    try:
        price_list = pricing_service.effective_price_list(as_of=timezone.localdate())
    except pricing_service.NoEffectivePriceListError:
        return {
            "price_list_code": "",
            "levels": [],
            "registration_fees": [],
            "no_registration_fee": program.program_type == ProgramType.ONLINE_COURSE,
        }

    levels = [
        {
            "level_key": item.level_key,
            "course_fee": item.course_fee,
            "deposit": item.deposit_amount,
        }
        for item in _items_in_force(price_list, [program.pk]).get(program.pk, [])
    ]
    if program.program_type == ProgramType.ONLINE_COURSE:
        return {
            "price_list_code": price_list.code,
            "levels": levels,
            "registration_fees": [],
            "no_registration_fee": True,
        }

    general = {
        r.participant_category: r
        for r in RegistrationFeeRule.objects.filter(price_list=price_list, program__isnull=True)
    }
    specific = {r.participant_category: r for r in _fee_rules_in_force(price_list, [program.pk]).get(program.pk, [])}
    registration_fees = []
    for category, label in PARTICIPANT_CATEGORY_CHOICES:
        rule = specific.get(category) or general.get(category)
        if rule is None:
            continue
        registration_fees.append(
            {
                "category": category,
                "category_display": label,
                "fee": rule.fee,
                "is_exception": category in specific,
                "note": rule.exception_note_ar if category in specific else "",
            }
        )
    return {
        "price_list_code": price_list.code,
        "levels": levels,
        "registration_fees": registration_fees,
        "no_registration_fee": False,
    }


def same_category_programs(*, program: Program) -> list[dict[str, Any]]:
    """
    The other active short courses in the programme's field — the set BR-061
    permits a transfer between. Empty for anything but a categorised course.
    """
    if not program.course_category_id:
        return []
    return [
        {"code": p.code, "name_ar": p.name_ar, "training_hours": p.training_hours}
        for p in Program.objects.filter(
            course_category_id=program.course_category_id, program_type=program.program_type, is_active=True
        )
        .exclude(pk=program.pk)
        .order_by("name_ar")
    ]


def get_program(*, actor: Any, code: str, request: Any = None) -> Program:
    program = Program.objects.select_related("course_category").get(code=code)
    policy.require(actor, screen_for(program.program_type), Action.VIEW, request=request)
    return program


def list_price_lists(*, actor: Any, request: Any = None) -> Any:
    policy.require(actor, Screen.PRICELISTS, Action.VIEW, request=request)
    return PriceList.objects.select_related("semester").order_by("-effective_from")


def price_list_status_choices() -> list[tuple[str, str]]:
    """(value, label) of every price-list state — for the register's filter."""
    return list(PriceListStatus.choices)


def unpriced_active_programs(
    *, actor: Any, price_list: PriceList, request: Any = None
) -> list[dict[str, str]]:
    """
    Active programmes this list leaves without an item — a pure read.

    A list that prices nine of ten programmes is not "almost complete": the
    tenth cannot take an enrolment at all (BR-008), and the gap belongs to the
    list that caused it rather than to the till that discovers it.
    """
    policy.require(actor, Screen.PRICELISTS, Action.VIEW, request=request)
    priced = set(
        PriceListItem.objects.filter(price_list=price_list).values_list("program_id", flat=True)
    )
    return [
        {"code": p.code, "name_ar": p.name_ar}
        for p in Program.objects.filter(is_active=True).order_by("code")
        if p.pk not in priced
    ]


def get_price_list(*, actor: Any, code: str, request: Any = None) -> PriceList:
    policy.require(actor, Screen.PRICELISTS, Action.VIEW, request=request)
    return PriceList.objects.select_related("semester").get(code=code)


# ---------------------------------------------------------------------------
# Course categories — the field a transfer is judged against (BR-061)
# ---------------------------------------------------------------------------
#: The programme screens' own permission, deliberately: a course category is
#: not a screen of its own in §3 — it is the catalogue reference data the
#: person who defines programmes maintains. Reusing PROGRAMS adds no cell to
#: the matrix and invents no authority that the document never granted.
CATEGORY_SCREEN = Screen.PROGRAMS


def category_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every category, with the count of programmes standing on it.

    The count is the whole reason this is a screen rather than a list: a
    category nothing uses may be renamed or stood down freely, and one that
    forty programmes depend on may not. The reader cannot know which without
    being told, so they are told.
    """
    policy.require(actor, CATEGORY_SCREEN, Action.VIEW, request=request)

    counts = {
        row["course_category_id"]: row["n"]
        for row in Program.objects.values("course_category_id").annotate(n=models.Count("id"))
    }
    return [
        {
            "code": category.code,
            "name_ar": category.name_ar,
            "is_active": category.is_active,
            "programs": counts.get(category.pk, 0),
        }
        for category in CourseCategory.objects.order_by("name_ar")
    ]


def create_course_category(
    *, actor: Any, data: dict[str, Any], request: Any = None
) -> CourseCategory:
    """
    A new category, through the service — never the ORM and never the admin.

    It was reachable only from the Django admin, which writes the row straight
    to the table: no audit line, so nobody could say afterwards who added the
    field that a transfer refusal now rests on.
    """
    policy.require(actor, CATEGORY_SCREEN, Action.CREATE, request=request)

    with transaction.atomic():
        category = CourseCategory(**data)
        category.full_clean()
        category.save()
        write_audit(
            action="CREATE",
            entity_type=CATEGORY_ENTITY,
            entity_id=str(category.pk),
            reference=category.code,
            summary_ar=f"إنشاء مجال دورة — {category.name_ar}",
            actor=actor,
            request=request,
        )
    return category


def update_course_category(
    *, actor: Any, category: CourseCategory, data: dict[str, Any], request: Any = None
) -> CourseCategory:
    """Rename, or stand down. The code is NOT among the editable fields."""
    policy.require(actor, CATEGORY_SCREEN, Action.EDIT, request=request)

    before = {field: getattr(category, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(category, field, value)
        category.full_clean()
        category.save()
        write_audit(
            action="UPDATE",
            entity_type=CATEGORY_ENTITY,
            entity_id=str(category.pk),
            reference=category.code,
            summary_ar=f"تعديل مجال دورة — {category.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(category, f))}
                for f in data
                if before[f] != getattr(category, f)
            },
            request=request,
        )
    return category


def category_instance(*, actor: Any, code: str, request: Any = None) -> CourseCategory:
    """The row itself, for handing back into this module."""
    policy.require(actor, CATEGORY_SCREEN, Action.VIEW, request=request)
    return CourseCategory.objects.get(code=code)


# ---------------------------------------------------------------------------
# Knowledge fields — the ministry's own classification (§9 of the brief)
# ---------------------------------------------------------------------------
#: The same permission as the categories beside them, and for the same reason:
#: this is catalogue reference data, not a screen §3 ever named.
FIELD_SCREEN = Screen.PROGRAMS


def knowledge_field_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every field, with the count of programmes declaring it.

    Unlike a course category, this one governs nothing: it is the ministry's
    classification, copied onto the submission file because the ministry asks
    for it. The count is here for the same reason it is there — so a reader
    standing one down knows what still points at it.
    """
    policy.require(actor, FIELD_SCREEN, Action.VIEW, request=request)

    counts = {
        row["knowledge_field_id"]: row["n"]
        for row in Program.objects.values("knowledge_field_id").annotate(n=models.Count("id"))
    }
    return [
        {
            "code": field.code,
            "name_ar": field.name_ar,
            "is_active": field.is_active,
            "programs": counts.get(field.pk, 0),
        }
        for field in KnowledgeField.objects.order_by("name_ar")
    ]


def create_knowledge_field(
    *, actor: Any, data: dict[str, Any], request: Any = None
) -> KnowledgeField:
    """A new field, through the service — so the row carries a name and a date."""
    policy.require(actor, FIELD_SCREEN, Action.CREATE, request=request)

    with transaction.atomic():
        field = KnowledgeField(**data)
        field.full_clean()
        field.save()
        write_audit(
            action="CREATE",
            entity_type=FIELD_ENTITY,
            entity_id=str(field.pk),
            reference=field.code,
            summary_ar=f"إنشاء مجال معرفي — {field.name_ar}",
            actor=actor,
            request=request,
        )
    return field


def update_knowledge_field(
    *, actor: Any, field: KnowledgeField, data: dict[str, Any], request: Any = None
) -> KnowledgeField:
    """Rename, or stand down. The code stays as the ministry knows it."""
    policy.require(actor, FIELD_SCREEN, Action.EDIT, request=request)

    before = {name: getattr(field, name) for name in data}
    with transaction.atomic():
        for name, value in data.items():
            setattr(field, name, value)
        field.full_clean()
        field.save()
        write_audit(
            action="UPDATE",
            entity_type=FIELD_ENTITY,
            entity_id=str(field.pk),
            reference=field.code,
            summary_ar=f"تعديل مجال معرفي — {field.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(field, f))}
                for f in data
                if before[f] != getattr(field, f)
            },
            request=request,
        )
    return field


def knowledge_field_instance(*, actor: Any, code: str, request: Any = None) -> KnowledgeField:
    """The row itself, for handing back into this module."""
    policy.require(actor, FIELD_SCREEN, Action.VIEW, request=request)
    return KnowledgeField.objects.get(code=code)


# ---------------------------------------------------------------------------
# Programmes
# ---------------------------------------------------------------------------
def create_program(*, actor: Any, data: dict[str, Any], request: Any = None) -> Program:
    policy.require(actor, screen_for(data.get("program_type", "")), Action.CREATE, request=request)

    with transaction.atomic():
        program = Program(**data)
        program.full_clean()
        program.save()
        write_audit(
            action="CREATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"إنشاء برنامج — {program.name_ar}",
            actor=actor,
            changes={"program_type": program.program_type},
            request=request,
        )
    return program


def update_program(
    *, actor: Any, program: Program, data: dict[str, Any], request: Any = None
) -> Program:
    policy.require(actor, screen_for(program.program_type), Action.EDIT, request=request)

    before = {field: getattr(program, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(program, field, value)
        program.full_clean()
        program.save()
        write_audit(
            action="UPDATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"تعديل برنامج — {program.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(program, f))}
                for f in data
                if before[f] != getattr(program, f)
            },
            request=request,
        )
    return program


def approve_program(
    *, actor: Any, program: Program, course_fee: Decimal, request: Any = None
) -> Program:
    """
    BR-006 gate (T-089, T-090).

    A diploma is approved only when its subjects add up. The refusal names the
    shortfall in dinars, because "the prices do not reconcile" sends someone
    hunting and "short by 10" sends them to the row.
    """
    policy.require(actor, screen_for(program.program_type), Action.APPROVE, request=request)

    if program.program_type == ProgramType.DIPLOMA:
        try:
            check_subject_prices(program, course_fee)
        except ValidationError as exc:
            write_audit(
                action="DENIED_ATTEMPT",
                entity_type=PROGRAM_ENTITY,
                entity_id=str(program.pk),
                reference=program.code,
                summary_ar=f"منع اعتماد برنامج — {exc.messages[0]}",
                actor=actor,
                denial_rule="BR-006",
                request=request,
            )
            raise

    with transaction.atomic():
        program.is_active = True
        program.save(update_fields=["is_active"])
        write_audit(
            action="APPROVE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"اعتماد برنامج — {program.name_ar}",
            actor=actor,
            changes={"course_fee": str(course_fee)},
            request=request,
        )
    return program


# ---------------------------------------------------------------------------
# Price lists
# ---------------------------------------------------------------------------
def _refuse_if_frozen(price_list: PriceList) -> None:
    """D-14 — nobody edits an approved or archived list. Not even the manager."""
    if price_list.is_frozen:
        raise ImmutableRecordError(
            f"قائمة الأسعار {price_list.code} بحالة {price_list.status} — "
            "غير قابلة للتعديل (D-14 · BR-008). التغيير بإصدار قائمة جديدة."
        )


def semester_choices() -> list[tuple[str, str]]:
    """
    The semesters a price list may belong to, newest first (A-05).

    Projected here rather than read in the view, which may not import a model:
    a screen that reaches for ``Semester`` today reaches for a queryset
    tomorrow, and the architecture test refuses the first step for that reason.
    """
    from apps.core.models import Semester

    return [
        (str(semester.pk), f"{semester.code} — {semester.name_ar}")
        for semester in Semester.objects.order_by("-starts_on")
    ]


def unpriced_program_choices(*, price_list: PriceList) -> list[tuple[str, str]]:
    """Programmes this list has not priced yet — one item per programme."""
    priced = set(price_list.items.values_list("program_id", flat=True))
    return [
        (str(program.pk), f"{program.code} — {program.name_ar}")
        for program in Program.objects.order_by("name_ar")
        if program.pk not in priced
    ]


def resolve_price_list_data(data: dict[str, Any]) -> dict[str, Any]:
    """``semester`` arrives from a form as a primary key; give back the row."""
    from apps.core.models import Semester

    resolved = dict(data)
    resolved["semester"] = Semester.objects.get(pk=resolved["semester"])
    return resolved


def resolve_price_item_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    Same for ``program`` and for the deposit policy, and the empty deposit stays
    ``None``, never zero — BR-096 keeps «no deposit at all» distinct from «a
    deposit of nothing», and C-26 pairs the amount with the policy either way.
    """
    resolved = dict(data)
    resolved["program"] = Program.objects.get(pk=resolved["program"])
    named = resolved.get("deposit_policy") or ""
    resolved["deposit_policy"] = DepositPolicy.objects.get(pk=named) if named else None
    return resolved


def create_price_list(*, actor: Any, data: dict[str, Any], request: Any = None) -> PriceList:
    policy.require(actor, Screen.PRICELISTS, Action.CREATE, request=request)

    with transaction.atomic():
        price_list = PriceList(**data)
        price_list.full_clean()
        price_list.save()
        write_audit(
            action="CREATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"إنشاء قائمة أسعار — {price_list.name_ar}",
            actor=actor,
            request=request,
        )
    return price_list


def update_price_list(
    *, actor: Any, price_list: PriceList, data: dict[str, Any], request: Any = None
) -> PriceList:
    """
    Correct a DRAFT list. ``_refuse_if_frozen`` is what keeps it to a draft.

    A list carries a semester, two dates and a decision reference, all typed by
    hand — and until now a mistyped one could not be corrected or removed from
    any screen or any service. A date a month out meant no list was in force
    and no enrolment could be priced, with nothing to do about it but edit the
    table. A draft is not evidence yet; it is a document being written, and a
    document being written must be correctable.

    The approved one is a different matter entirely and stays untouchable
    (D-14 · BR-008): the change there is a NEW list that supersedes it.
    """
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    before = {field: getattr(price_list, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(price_list, field, value)
        price_list.full_clean()
        price_list.save()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"تعديل مسودة قائمة أسعار — {price_list.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(price_list, f))}
                for f in data
                if before[f] != getattr(price_list, f)
            },
            request=request,
        )
    return price_list


def remove_program_pricing(
    *, actor: Any, price_list: PriceList, program_code: str, request: Any = None
) -> int:
    """
    Take one programme's price off a DRAFT list, levels and all.

    The screen shows a programme's price as ONE row even when a levelled course
    carries an item per level, so the act has to match the row the reader is
    looking at: removing «الإنجليزية» means removing its eight level items, not
    the first of them.

    A wrong figure can be corrected in place; a programme that should never
    have been priced on this list has to go, and on a draft nothing depends on
    it yet. On an approved list this is refused with everything else (D-14).
    """
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    items = list(
        PriceListItem.objects.select_related("program").filter(
            price_list=price_list, program__code=program_code
        )
    )
    if not items:
        raise PriceListItem.DoesNotExist(
            f"لا بند للبرنامج {program_code} على القائمة {price_list.code}."
        )

    name = items[0].program.name_ar
    fees = ", ".join(str(item.course_fee) for item in items)
    with transaction.atomic():
        removed = PriceListItem.objects.filter(pk__in=[item.pk for item in items]).delete()[0]
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"حذف سعر {name} من مسودة القائمة",
            actor=actor,
            changes={"program": program_code, "course_fee": fees, "items": str(removed)},
            request=request,
        )
    return int(removed)


def deposit_policy_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every deposit policy, with the count of price items standing on it.

    The count is what tells the reader which policy may be reworded and which
    one carries live pricing behind it — the same reason the category screen
    counts its programmes.
    """
    policy.require(actor, DEPOSIT_SCREEN, Action.VIEW, request=request)

    counts = {
        row["deposit_policy_id"]: row["n"]
        for row in PriceListItem.objects.values("deposit_policy_id").annotate(n=models.Count("id"))
    }
    known = {value for value, _label in deposit_forfeit_choices()}
    return [
        {
            "code": row.code,
            "name_ar": row.name_ar,
            "is_required": row.is_required,
            "refund_trigger": row.refund_trigger,
            "forfeit_on": list(row.forfeit_on or []),
            # Split for the editor: the twelve statuses have checkboxes and
            # anything else has the free field beside them. Split HERE because
            # the template may not decide which of a stored list is «known» —
            # and a policy opened for editing and saved unchanged would have
            # silently dropped «CONFIRMED» if it had nowhere to be drawn.
            "forfeit_known": [value for value in (row.forfeit_on or []) if value in known],
            "forfeit_extra_text": "، ".join(
                str(value) for value in (row.forfeit_on or []) if value not in known
            ),
            "is_taxable": row.is_taxable,
            "allows_partial_deduction": row.allows_partial_deduction,
            "claim_deadline_days": row.claim_deadline_days,
            "notes_ar": row.notes_ar,
            "is_active": row.is_active,
            "items": counts.get(row.pk, 0),
        }
        for row in DepositPolicy.objects.order_by("name_ar")
    ]


def create_deposit_policy(*, actor: Any, data: dict[str, Any], request: Any = None) -> DepositPolicy:
    """
    A new policy, through the service — never the admin.

    Reachable only from the Django admin until now, which writes past the audit
    trail: a deposit forfeited months later rests on the wording of this row,
    and nobody could say who wrote it. A fresh install had no policy at all, so
    no programme could carry a deposit and C-26 made the amount unenterable too.
    """
    policy.require(actor, DEPOSIT_SCREEN, Action.CREATE, request=request)

    with transaction.atomic():
        row = DepositPolicy(**data)
        row.full_clean()
        row.save()
        write_audit(
            action="CREATE",
            entity_type=DEPOSIT_ENTITY,
            entity_id=str(row.pk),
            reference=row.code,
            summary_ar=f"إنشاء سياسة تأمين — {row.name_ar}",
            actor=actor,
            changes={
                "refund_trigger": row.refund_trigger,
                "forfeit_on": list(row.forfeit_on or []),
                "is_required": row.is_required,
            },
            request=request,
        )
    return row


def update_deposit_policy(
    *, actor: Any, deposit_policy: DepositPolicy, data: dict[str, Any], request: Any = None
) -> DepositPolicy:
    """Reword or stand down. The code is not among the editable fields."""
    policy.require(actor, DEPOSIT_SCREEN, Action.EDIT, request=request)

    before = {field: getattr(deposit_policy, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(deposit_policy, field, value)
        deposit_policy.full_clean()
        deposit_policy.save()
        write_audit(
            action="UPDATE",
            entity_type=DEPOSIT_ENTITY,
            entity_id=str(deposit_policy.pk),
            reference=deposit_policy.code,
            summary_ar=f"تعديل سياسة تأمين — {deposit_policy.name_ar}",
            actor=actor,
            changes={
                field: {"from": str(before[field]), "to": str(getattr(deposit_policy, field))}
                for field in data
                if before[field] != getattr(deposit_policy, field)
            },
            request=request,
        )
    return deposit_policy


def deposit_policy_instance(*, actor: Any, code: str, request: Any = None) -> DepositPolicy:
    """The row itself, for handing back into this module."""
    policy.require(actor, DEPOSIT_SCREEN, Action.VIEW, request=request)
    return DepositPolicy.objects.get(code=code)


def deposit_policy_choices() -> list[tuple[str, str]]:
    """
    Active policies, for the price item that must name one beside its amount.

    ``C-26`` refuses an amount without a policy and a policy without an amount,
    so this list is what makes a deposit enterable at all. Stood-down policies
    are left out: they are history for the items that already carry them, not a
    choice for a new one.
    """
    return [
        (str(row.pk), f"{row.code} — {row.name_ar}")
        for row in DepositPolicy.objects.filter(is_active=True).order_by("name_ar")
    ]


def deposit_forfeit_choices() -> list[tuple[str, str]]:
    """
    The enrolment states a deposit may be forfeited in (BR-097) — as SUGGESTIONS.

    These were offered as the only choices at first, on the reasoning that a
    status typed by hand never matches anything. The reasoning was wrong about
    this field: the client's own documented policy (``DEP-ENG-GEN``, 2026-08-15)
    forfeits on ``CONFIRMED``, which is not one of the twelve enrolment statuses
    at all — it names the moment the participant confirmed, not a stored state.
    A closed list therefore could not express the single policy the client had
    actually specified, and the screen would have refused the data the system
    already held.

    So Q-30 stays open here as it does on ``refund_trigger``: these twelve are
    the easy, typo-free path, and ``forfeit_other`` carries anything else.
    """
    from apps.operations.models import EnrollmentStatus

    return [(str(value), str(label)) for value, label in EnrollmentStatus.choices]


def deposit_forfeit_in_use() -> list[str]:
    """
    Every forfeit value already stored, for the free field's suggestions.

    What stops ``ON_CONFIRM`` and ``CONFIRMED`` living side by side is not a
    constraint — Q-30 forbids one — but showing the writer what the centre
    already says. The twelve statuses are left out: they have their own
    checkboxes, and offering them twice invites the same value stored two ways.
    """
    from apps.operations.models import EnrollmentStatus

    known = {str(value) for value, _label in EnrollmentStatus.choices}
    seen: dict[str, None] = {}
    for stored in DepositPolicy.objects.values_list("forfeit_on", flat=True):
        for value in stored or []:
            text = str(value).strip()
            if text and text not in known:
                seen.setdefault(text, None)
    return sorted(seen)


def fee_rule_program_choices(*, price_list: PriceList) -> list[tuple[str, str]]:
    """
    The scope a fee rule may take: every programme, or none of them.

    The empty first choice is the GENERAL rule (``program = NULL``) and it is
    named rather than left blank, because «كل البرامج» and «لم أختر بعد» look
    identical in a blank option and mean opposite things — one charges every
    programme in the category, the other charges nothing.

    Online courses are left out: BR-010 says they carry no registration fee at
    all, structurally, so a rule naming one would be a row the resolver never
    reads.
    """
    return [("", "— القاعدة العامة لكل البرامج —")] + [
        (str(program.pk), f"{program.code} — {program.name_ar}")
        for program in Program.objects.exclude(program_type=ProgramType.ONLINE_COURSE).order_by(
            "name_ar"
        )
    ]


def resolve_fee_rule_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    ``program`` arrives as a primary key or as the empty general scope.

    ``fee`` is left exactly as the form gave it: ``None`` means NO registration
    fee is charged and is not the same row as a fee of zero (BR-009 · C-27), so
    it is never defaulted here.
    """
    resolved = dict(data)
    program_id = resolved.get("program") or ""
    resolved["program"] = Program.objects.get(pk=program_id) if program_id else None
    return resolved


def add_fee_rule(
    *, actor: Any, price_list: PriceList, data: dict[str, Any], request: Any = None
) -> RegistrationFeeRule:
    """
    One registration-fee rule on a DRAFT list (BR-009).

    Until now these rules existed in the data model, were read by the pricing
    engine on every single enrolment, and could be entered from nowhere but the
    Django admin. A list approved without them is a list that prices NOTHING:
    ``resolve_registration_fee`` finds neither a specific rule nor a general
    one and refuses, so the first enrolment of the term fails at a screen that
    has no way to answer — and D-14 forbids adding the rule afterwards.

    The scope is what makes the rule readable: ``program = None`` is the
    general rule for a category, and a rule naming a programme beats it. Both
    are stored here; which of the two applies is the resolver's decision, not
    this function's.
    """
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    with transaction.atomic():
        rule = RegistrationFeeRule(price_list=price_list, **data)
        # ``program_key`` is what C-27 is keyed on, and ``save()`` is what
        # computes it — which is one step too late for ``full_clean()``: the
        # uniqueness check would read the default 0 and refuse a PROGRAMME rule
        # as a duplicate of the general one for the same category, making
        # BR-009's most-specific-wins unreachable from any screen. The admin
        # never met this because a ModelForm excludes non-editable fields from
        # its validation and so skipped the check entirely.
        rule.program_key = rule.program_id or 0
        rule.full_clean()
        rule.save()
        scope = rule.program.name_ar if rule.program is not None else "كل البرامج"
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"إضافة قاعدة رسم تسجيل — {scope} / {rule.participant_category}",
            actor=actor,
            changes={
                "program": rule.program.code if rule.program is not None else None,
                "participant_category": rule.participant_category,
                # NULL survives the audit line too: «بلا رسوم» is the fact
                # recorded, and "0" would record a different one.
                "fee": str(rule.fee) if rule.fee is not None else None,
            },
            request=request,
        )
    return rule


def remove_fee_rule(
    *, actor: Any, price_list: PriceList, rule_id: str, request: Any = None
) -> None:
    """Take one rule off a DRAFT list — a mistyped rule must be correctable."""
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    rule = RegistrationFeeRule.objects.select_related("program").filter(
        price_list=price_list, pk=rule_id
    ).first()
    if rule is None:
        raise RegistrationFeeRule.DoesNotExist(
            f"لا قاعدة رسم بهذا المعرّف على القائمة {price_list.code}."
        )

    scope = rule.program.name_ar if rule.program is not None else "كل البرامج"
    with transaction.atomic():
        rule.delete()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"حذف قاعدة رسم تسجيل — {scope} / {rule.participant_category}",
            actor=actor,
            changes={"participant_category": rule.participant_category},
            request=request,
        )


def categories_without_a_fee_rule(*, price_list: PriceList) -> list[tuple[str, str]]:
    """
    The participant categories this list cannot price at all yet (BR-009).

    A read, not a refusal: a list carrying only online courses legitimately
    needs no rule (BR-010), so the screen WARNS with this and the service does
    not block. What it stops is an approval that freezes a list which would
    refuse the first enrolment typed against it.
    """
    covered = set(
        RegistrationFeeRule.objects.filter(price_list=price_list, program__isnull=True).values_list(
            "participant_category", flat=True
        )
    )
    return [
        (str(category), str(label))
        for category, label in PARTICIPANT_CATEGORY_CHOICES
        if category not in covered
    ]


def add_price_item(
    *, actor: Any, price_list: PriceList, data: dict[str, Any], request: Any = None
) -> PriceListItem:
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    with transaction.atomic():
        item = PriceListItem(price_list=price_list, **data)
        item.full_clean()
        item.save()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"إضافة بند سعر — {item.program.name_ar}",
            actor=actor,
            changes={
                "program": item.program.code,
                "course_fee": str(item.course_fee),
                "deposit_amount": str(item.deposit_amount) if item.deposit_amount else None,
            },
            request=request,
        )
    return item


def update_price_item(
    *, actor: Any, item: PriceListItem, data: dict[str, Any], request: Any = None
) -> PriceListItem:
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(item.price_list)

    with transaction.atomic():
        for field, value in data.items():
            setattr(item, field, value)
        item.full_clean()
        item.save()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(item.price_list_id),
            reference=item.price_list.code,
            summary_ar=f"تعديل بند سعر — {item.program.name_ar}",
            actor=actor,
            request=request,
        )
    return item


def record_external_approval(
    *,
    actor: Any,
    price_list: PriceList,
    approved_by_text: str,
    decision_reference: str,
    request: Any = None,
) -> PriceList:
    """
    Record the president's approval of a list, archiving the previous one (BR-008).

    **This is deliberately not an APPROVE permission.** PERMISSIONS.md row 13
    gives the centre manager ``V C E P`` on price lists and withholds ``A``,
    because footnote 8 says he RECOMMENDS and the university president
    approves — outside the system entirely (D-31). Nobody holds an internal
    approval right over a price list, and adding one to the matrix to make
    this function fit would have quietly moved the authority.

    So the act here is recording an external decision, which is an edit. The
    control is not who clicks it but that the approver's name and the decision
    reference are MANDATORY: they are the only evidence the approval happened.
    """
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    if not approved_by_text.strip() or not decision_reference.strip():
        raise ValidationError(
            "اعتماد القائمة يتطلب جهة الاعتماد ومرجع القرار — "
            "الاعتماد خارج النظام ولا دليل عليه سواهما (BR-008 · D-31)."
        )

    with transaction.atomic():
        superseded = (
            PriceList.objects.select_for_update()
            .filter(semester=price_list.semester, status=PriceListStatus.APPROVED)
            .exclude(pk=price_list.pk)
        )
        archived_codes = [old.code for old in superseded]
        for old in superseded:
            old.status = PriceListStatus.ARCHIVED
            old.archived_at = timezone.now()
            old.save()
            write_audit(
                action="UPDATE",
                entity_type=PRICE_LIST_ENTITY,
                entity_id=str(old.pk),
                reference=old.code,
                summary_ar=f"أرشفة قائمة أسعار — استبدلتها {price_list.code}",
                actor=actor,
                request=request,
            )

        price_list.status = PriceListStatus.APPROVED
        price_list.approved_by_text = approved_by_text.strip()
        price_list.decision_reference = decision_reference.strip()
        price_list.approved_at = timezone.now()
        price_list.save()

        write_audit(
            action="APPROVE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"اعتماد قائمة أسعار — {price_list.name_ar}",
            actor=actor,
            changes={
                "approved_by": approved_by_text.strip(),
                "decision_reference": decision_reference.strip(),
                "archived": archived_codes,
            },
            request=request,
        )
    return price_list


__all__ = [
    "add_fee_rule",
    "create_deposit_policy",
    "deposit_forfeit_choices",
    "deposit_forfeit_in_use",
    "deposit_policy_choices",
    "deposit_policy_instance",
    "deposit_policy_rows",
    "update_deposit_policy",
    "add_price_item",
    "categories_without_a_fee_rule",
    "fee_rule_program_choices",
    "remove_fee_rule",
    "resolve_fee_rule_data",
    "add_subject",
    "approve_program",
    "category_choices",
    "check_subject_prices",
    "create_price_list",
    "create_program",
    "field_choices",
    "get_price_list",
    "get_program",
    "list_price_lists",
    "list_programs",
    "program_cohorts",
    "price_list_status_choices",
    "pricing_in_force",
    "program_rows",
    "programs_summary",
    "reconciliation",
    "record_external_approval",
    "remove_subject",
    "resolve_lookups",
    "same_category_programs",
    "screen_for",
    "set_program_active",
    "subject_instance",
    "subject_price_variance",
    "unpriced_active_programs",
    "update_price_item",
    "update_program",
    "update_subject",
]
