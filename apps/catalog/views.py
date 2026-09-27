"""
Catalogue screens (PERMISSIONS.md §3.3 rows 9–13).

Four screens, three of which are the same list filtered by programme type —
diplomas, short courses and online courses are separate rows in the matrix
because they carry different permissions, not because they are different
things. Keeping one view and one template means a permission change lands in
one place.

Views render and delegate. Every permission question goes through
``policy.require``; nothing here decides anything by inspecting a role.
"""

from __future__ import annotations

from typing import Any

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.catalog.forms import (
    CourseCategoryForm,
    KnowledgeFieldForm,
    DepositPolicyForm,
    FeeRuleForm,
    PriceItemForm,
    PriceListApprovalForm,
    PriceListForm,
    ProgramForm,
    SubjectForm,
)
from apps.core.pagination import page_of
from apps.catalog.services import catalog_service, pricing_service
from apps.core.exceptions import ImmutableRecordError
from apps.people.constants import PARTICIPANT_CATEGORY_CHOICES, Action, Screen
from apps.people.permissions import policy

#: Programme type → the screen that governs it (PERMISSIONS.md §3.3).
TYPE_BY_SCREEN: dict[str, str] = {
    Screen.PROGRAMS: "DIPLOMA",
    Screen.SHORT_COURSES: "SHORT_COURSE",
    Screen.ONLINE_COURSES: "ONLINE_COURSE",
}

#: …and the list it came from, so a detail page can offer the way back. The
#: reader already passed this screen's VIEW gate to be here, so the link
#: cannot send them into a refusal.
LIST_ROUTE_BY_SCREEN: dict[str, str] = {
    Screen.PROGRAMS: "catalog:programs",
    Screen.SHORT_COURSES: "catalog:short-courses",
    Screen.ONLINE_COURSES: "catalog:online-courses",
}


#: What each of the three catalogue screens is FOR, in the reader's words.
#: Kept here beside ``TYPE_BY_SCREEN`` so the three rows of §3.3 that differ
#: only by programme type read differently on screen as well.
SUBTITLE_BY_SCREEN: dict[str, Any] = {
    Screen.PROGRAMS: _(
        "الدبلومات المعرَّفة في الكتالوج: الرمز والمجال والساعات والمواد وحالة التفعيل. "
        "الأسعار تُقرأ من قوائم الأسعار المؤرّخة، والتشغيل يبدأ بدفعة على برنامج معرَّف."
    ),
    Screen.SHORT_COURSES: _(
        "الدورات القصيرة المعرَّفة في الكتالوج. مجال الدورة ليس وصفاً: هو ما يحدّ "
        "النقل المسموح بين دورتين (BR-061). الأسعار والعربون واستثناءات رسم التسجيل "
        "تُقرأ من قوائم الأسعار المؤرّخة."
    ),
    Screen.ONLINE_COURSES: _(
        "الدورات الأونلاين المعرَّفة في الكتالوج: الرمز والساعات وحالة التفعيل. "
        "الدورة الأونلاين بلا رسوم تسجيل (BR-010)، وسعرها يُقرأ من قوائم الأسعار "
        "المؤرّخة؛ قسمة إيرادها تحكمها اتفاقية الشريك لا الكتالوج."
    ),
}

#: The code pattern the centre follows for each type — a hint in the dialog,
#: not a rule: the code is the manager's to choose.
CODE_HINT_BY_TYPE: dict[str, str] = {
    "DIPLOMA": "DIP-…",
    "SHORT_COURSE": "SC-…",
    "ONLINE_COURSE": "OL-…",
}


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


def _program_data(form: ProgramForm) -> dict[str, Any]:
    """Form → model fields; the lookup codes are resolved by the service (ADR-008)."""
    return catalog_service.resolve_lookups(dict(form.cleaned_data))


def _program_list(request: HttpRequest, screen: str, title: str) -> HttpResponse:
    """
    The catalogue list for one programme type, with the live search, the
    filters and — for the manager — the «برنامج جديد» dialog (§3.3/9-11).
    """
    policy.require(request.user, screen, Action.VIEW, request=request)
    program_type = TYPE_BY_SCREEN[screen]
    can_create = policy.is_allowed(request.user, screen, Action.CREATE)

    form = ProgramForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "create" else None,
        program_type=program_type,
        field_choices=catalog_service.field_choices(),
        category_choices=catalog_service.category_choices(),
    )
    if request.method == "POST":
        policy.require(request.user, screen, Action.CREATE, request=request)
        if request.POST.get("action") == "create" and form.is_valid():
            try:
                program = catalog_service.create_program(
                    actor=request.user,
                    data={**_program_data(form), "program_type": program_type},
                    request=request,
                )
                messages.success(request, _("أُنشئ البرنامج %(name)s") % {"name": program.name_ar})
                return redirect("catalog:program-detail", code=program.code)
            except (DjangoValidationError, PermissionDenied) as exc:
                messages.error(request, _message_of(exc))

    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    active = request.GET.get("active", "").strip()
    if active not in ("yes", "no"):
        active = ""
    rows = catalog_service.program_rows(
        actor=request.user,
        program_type=program_type,
        query=query,
        category=category,
        active=active,
        request=request,
    )
    summary = catalog_service.programs_summary(
        catalog_service.program_rows(
            actor=request.user, program_type=program_type, query=query, category=category, request=request
        )
        if active
        else rows
    )
    base_url = reverse(LIST_ROUTE_BY_SCREEN[screen])
    keep = {k: v for k, v in (("q", query), ("category", category)) if v}

    def _tile(key: str, label: str, value: int, tone: str, icon: str) -> dict:
        params = dict(keep)
        if key and active != key:
            params["active"] = key
        qs = urlencode(params)
        return {
            "key": key,
            "label": label,
            "value": value,
            "tone": tone,
            "icon": icon,
            "on": bool(key) and active == key,
            "url": base_url + (f"?{qs}" if qs else ""),
        }

    tiles = [
        _tile("yes", _("نشط"), summary["active"], "ok", "checks"),
        _tile("no", _("غير نشط"), summary["inactive"], "amber", "clock"),
        _tile("", _("بدفعات جارية"), summary["with_live_cohorts"], "info", "users"),
        _tile("", _("بلا سعر ساري"), summary["unpriced"], "danger", "tag"),
    ]

    return render(
        request,
        "catalog/programs.html",
        {
            "title": title,
            "subtitle": SUBTITLE_BY_SCREEN[screen],
            "programs": rows,
            "summary": summary,
            "tiles": tiles,
            "categories": catalog_service.category_choices(),
            "query": query,
            "category": category,
            "active": active,
            "is_filtered": bool(query or category or active),
            "screen": screen,
            "is_diploma": program_type == "DIPLOMA",
            "form": form if can_create else None,
            "code_hint": CODE_HINT_BY_TYPE[program_type],
            "is_short_course": program_type == "SHORT_COURSE",
            "is_online": program_type == "ONLINE_COURSE",
            # The course field bounds a transfer, and transfers are the short
            # courses' alone — elsewhere the column, the filter and the field
            # would all be permanently empty.
            "shows_category": program_type == "SHORT_COURSE",
            "posted_action": request.POST.get("action", ""),
            "can_create": can_create,
            "can_edit": policy.is_allowed(request.user, screen, Action.EDIT),
            "can_approve": policy.is_allowed(request.user, screen, Action.APPROVE),
        },
    )


@require_http_methods(["GET", "POST"])
def diplomas_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.PROGRAMS, _("الدبلومات التدريبية"))


@require_http_methods(["GET", "POST"])
def short_courses_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.SHORT_COURSES, _("الدورات القصيرة"))


@require_http_methods(["GET", "POST"])
def online_courses_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.ONLINE_COURSES, _("الدورات الأونلاين"))


@require_http_methods(["GET", "POST"])
def program_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """
    One programme, with its subjects and the BR-006 reconciliation.

    The variance is shown whether or not it reconciles: a diploma that is
    short by ten dinars should say so on the screen, not only when someone
    tries to approve it. The manager edits the card, adds or edits subjects,
    approves (BR-006) and retires the programme from here — each in a dialog.
    """
    try:
        program = catalog_service.get_program(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد برنامج بهذا الرمز")) from None

    screen = catalog_service.screen_for(program.program_type)
    can_edit = policy.is_allowed(request.user, screen, Action.EDIT)
    can_approve = policy.is_allowed(request.user, screen, Action.APPROVE)
    action = request.POST.get("action", "") if request.method == "POST" else ""

    edit_form = ProgramForm(
        request.POST if action == "update" else None,
        program_type=program.program_type,
        field_choices=catalog_service.field_choices(),
        category_choices=catalog_service.category_choices(),
        editing=True,
        initial={
            "code": program.code,
            "name_ar": program.name_ar,
            "name_en": program.name_en,
            "knowledge_field": program.knowledge_field.code if program.knowledge_field_id else "",
            "course_category": program.course_category.code if program.course_category_id else "",
            "specialization": program.specialization,
            "training_hours": program.training_hours,
            "is_leveled": program.is_leveled,
            "levels_count": program.levels_count,
            "consumables_per_student": program.consumables_per_student,
            "minimum_first_payment_override": program.minimum_first_payment_override,
        },
    )
    subject_form = SubjectForm(request.POST if action in ("add-subject", "update-subject") else None)

    if request.method == "POST":
        response = _handle_program_action(request, program, screen, action, edit_form, subject_form)
        if response is not None:
            return response

    return render(
        request,
        "catalog/program_detail.html",
        {
            "program": program,
            "subjects": program.subjects.all(),
            "subject_total": pricing_service.subject_price_total(program),
            "reconciliation": catalog_service.reconciliation(program=program),
            "cohorts": catalog_service.program_cohorts(actor=request.user, program=program, request=request),
            "pricing": catalog_service.pricing_in_force(program=program),
            "siblings": catalog_service.same_category_programs(program=program),
            "is_short_course": screen == Screen.SHORT_COURSES,
            "can_transfer": policy.is_allowed(request.user, Screen.TRANSFERS, Action.VIEW),
            "code_hint": CODE_HINT_BY_TYPE.get(program.program_type, ""),
            "shows_category": program.program_type == "SHORT_COURSE",
            "is_online": program.program_type == "ONLINE_COURSE",
            "screen": screen,
            "list_url": reverse(LIST_ROUTE_BY_SCREEN[screen]),
            "list_label": Screen(screen).label,
            "expects_subjects": screen == Screen.PROGRAMS,
            "edit_form": edit_form,
            # الرمز يُعرض ولا يُعدَّل: هو المرجع الذي تُسمّى به القائمة في كل
            # مكان، وإعادة تسميته بعد خروجه من الشاشة تكسر ما يشير إليه.
            "edit_fields": (
                "name_ar",
                "semester",
                "issued_on",
                "effective_from",
                "proposed_by_text",
            ),
            "subject_form": subject_form,
            "posted_action": action,
            "posted_subject": request.POST.get("subject_id", ""),
            "can_edit": can_edit,
            "can_approve": can_approve,
        },
    )


PROGRAM_ACTIONS = {
    "update": Action.EDIT,
    "add-subject": Action.EDIT,
    "update-subject": Action.EDIT,
    "remove-subject": Action.EDIT,
    "toggle-active": Action.EDIT,
    "approve": Action.APPROVE,
}


def _handle_program_action(
    request: HttpRequest,
    program: Any,
    screen: str,
    action: str,
    edit_form: ProgramForm,
    subject_form: SubjectForm,
) -> HttpResponse | None:
    if action not in PROGRAM_ACTIONS:
        return None
    policy.require(request.user, screen, PROGRAM_ACTIONS[action], request=request)
    try:
        if action == "update":
            if not edit_form.is_valid():
                return None
            data = _program_data(edit_form)
            data.pop("code", None)
            catalog_service.update_program(actor=request.user, program=program, data=data, request=request)
            messages.success(request, _("حُفظت بيانات البرنامج"))
        elif action == "add-subject":
            if not subject_form.is_valid():
                return None
            catalog_service.add_subject(
                actor=request.user, program=program, data=subject_form.cleaned_data, request=request
            )
            messages.success(request, _("أُضيفت المادة"))
        elif action == "update-subject":
            if not subject_form.is_valid():
                return None
            subject = catalog_service.subject_instance(
                actor=request.user, program=program, subject_id=int(request.POST.get("subject_id", "0")), request=request
            )
            catalog_service.update_subject(
                actor=request.user, subject=subject, data=subject_form.cleaned_data, request=request
            )
            messages.success(request, _("حُفظت المادة"))
        elif action == "remove-subject":
            subject = catalog_service.subject_instance(
                actor=request.user, program=program, subject_id=int(request.POST.get("subject_id", "0")), request=request
            )
            catalog_service.remove_subject(actor=request.user, subject=subject, request=request)
            messages.success(request, _("حُذفت المادة"))
        elif action == "toggle-active":
            catalog_service.set_program_active(
                actor=request.user, program=program, active=not program.is_active, request=request
            )
            messages.success(request, _("فُعِّل البرنامج") if not program.is_active else _("أُوقف البرنامج"))
        elif action == "approve":
            fee = catalog_service.reconciliation(program=program)["fee"]
            if fee is None:
                raise DjangoValidationError("لا سعر سارياً لهذا البرنامج — يُسعَّر على قائمة معتمدة قبل الاعتماد (BR-008).")
            catalog_service.approve_program(
                actor=request.user, program=program, course_fee=fee, request=request
            )
            messages.success(request, _("اعتُمد البرنامج — مواده تطابق رسومه (BR-006)"))
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except (ObjectDoesNotExist, ValueError):
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("catalog:program-detail", code=program.code)


def _price_list_filters(query: str, status: str, rows: list[Any]) -> list[tuple[str, str]]:
    """The filters this request is narrowing by, named for the reader by LABEL.

    The stored code is never printed: a banner that said «الحالة: APPROVED»
    would be the screen talking to itself.
    """
    active: list[tuple[str, str]] = []
    if query:
        active.append((_("بحث"), query))
    if status:
        # Read off the drawn rows first; a filter matching nothing has no row to
        # read from, so the service's own vocabulary answers instead (A-05 keeps
        # the models out of here).
        labels = {str(row.status): str(row.get_status_display()) for row in rows}
        labels.update(dict(catalog_service.price_list_status_choices()))
        active.append((_("الحالة"), labels.get(status, status)))
    return active


def _price_item_rows(items: Any) -> list[dict[str, Any]]:
    """
    Items as rows, with the repeated levels of one programme folded into one.

    A levelled course carries an item per level, and English 1–8 priced the same
    printed eight identical rows — a wall of the same number that says nothing
    the first row did not. Levels are folded ONLY when every one of them agrees
    on the fee, the deposit, the policy and the note; a level priced differently
    is the thing the reader came for and keeps its own row.
    """
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for item in items:
        key = (
            item.program_id,
            item.course_fee,
            item.deposit_amount,
            item.deposit_policy_id,
            item.notes,
        )
        row = grouped.get(key)
        if row is None:
            row = {
                "program_code": item.program.code,
                "program_name": item.program.name_ar,
                "course_fee": item.course_fee,
                "deposit_amount": item.deposit_amount,
                "deposit_policy": item.deposit_policy.name_ar if item.deposit_policy_id else "",
                "notes": item.notes,
                "levels": 0,
                "level_from": item.level,
                "level_to": item.level,
            }
            grouped[key] = row
            order.append(key)
        row["levels"] += 1
        if item.level is not None:
            row["level_from"] = min(filter(None, (row["level_from"], item.level)))
            row["level_to"] = max(row["level_to"] or item.level, item.level)
    return [grouped[key] for key in order]


def _matching_rules(price_list: Any, query: str) -> list[Any]:
    """
    Fee rules, narrowed by programme when the reader asks — keeping the general
    ones, because the specific rule BEATS the general one rather than replacing
    the answer (BR-009). Hiding the general half would make the screen say no
    fee is charged where one plainly is.
    """
    rules = price_list.registration_fee_rules.select_related("program")
    if query:
        rules = rules.filter(
            Q(program__code__icontains=query)
            | Q(program__name_ar__icontains=query)
            | Q(program__isnull=True)
        )
    return list(rules)


@require_http_methods(["GET", "POST"])
def price_lists_view(request: HttpRequest) -> HttpResponse:
    """
    The register of dated price lists — what was issued, when it takes effect,
    and which one is in force TODAY.

    That last one is the reader's first question and the screen used to leave it
    to be guessed from the ordering. It is answered by the same pure read the
    pricing engine uses (``effective_price_list``), so the screen cannot say one
    thing while an enrolment is priced by another; BR-012 still settles a real
    pricing by the date of the event, which is why the chip says «اليوم».
    """
    policy.require(request.user, Screen.PRICELISTS, Action.VIEW, request=request)

    # The register listed and offered no way in: a screen that says «لا قوائم
    # أسعار معرَّفة بعد» and gives no route to make one is an empty state that
    # explains without pointing (§8), and the only way to a first list was the
    # Django admin. ``create_price_list`` had been written and had no caller.
    can_create = policy.is_allowed(request.user, Screen.PRICELISTS, Action.CREATE)
    new_form = _price_list_form(request.POST if request.method == "POST" else None)

    if request.method == "POST":
        # The permission BEFORE the shape. A reader with no CREATE who posts an
        # incomplete form must be refused for what they are, not handed a field
        # error that implies the act would have been allowed had they filled it
        # in — and the attempt belongs in the audit trail (BR-085).
        policy.require(request.user, Screen.PRICELISTS, Action.CREATE, request=request)
        if new_form.is_valid():
            created = _create_price_list(request, new_form)
            if created is not None:
                return created
        else:
            messages.error(request, _("راجع حقول النموذج."))

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    # Materialised once: the template iterates this list and the chips below are
    # counted off the same rows, so the two cannot disagree.
    price_lists = list(catalog_service.list_price_lists(actor=request.user, request=request))
    today = timezone.localdate()
    try:
        in_force = pricing_service.effective_price_list(as_of=today).code
    except pricing_service.NoEffectivePriceListError:
        in_force = ""

    if query:
        needle = query.lower()
        price_lists = [
            pl
            for pl in price_lists
            if needle in pl.code.lower()
            or needle in pl.name_ar.lower()
            or needle in str(pl.semester).lower()
            or needle in (pl.decision_reference or "").lower()
        ]
    if status:
        price_lists = [pl for pl in price_lists if str(pl.status) == status]

    # Tallied off the rows rather than queried again, and keyed by the raw
    # status so the template can tone the chip the way every other list does.
    # Encounter order is the queryset's order — newest effective date first —
    # and a state nobody is in gets no chip rather than a zero.
    tally: dict[tuple[str, str], int] = {}
    for price_list in price_lists:
        key = (str(price_list.status), str(price_list.get_status_display()))
        tally[key] = tally.get(key, 0) + 1

    # The strip counts the WHOLE register, not the filtered slice: a tile that
    # shrank with the filter it opens would count itself. Counted off rows the
    # service already handed over — no second query.
    everything = (
        price_lists
        if not (query or status)
        else list(catalog_service.list_price_lists(actor=request.user, request=request))
    )
    base = reverse("catalog:pricelists")

    def _tile(label: str, value: Any, code: str, tone: str, icon: str) -> dict[str, Any]:
        return {
            "label": label,
            "value": value,
            "url": f"{base}?status={code}" if code else base,
            "tone": tone,
            "icon": icon,
            "on": bool(code) and status == code,
        }

    by_status: dict[str, int] = {}
    for price_list in everything:
        by_status[str(price_list.status)] = by_status.get(str(price_list.status), 0) + 1
    tiles = [
        {
            "label": _("سارية اليوم"),
            "value": in_force or _("لا شيء"),
            "url": base,
            "tone": "ok" if in_force else "danger",
            "icon": "checks",
            "on": False,
            "is_code": bool(in_force),
        },
        _tile(_("معتمدة"), by_status.get("APPROVED", 0), "APPROVED", "ok", "stamp"),
        _tile(_("مسودة"), by_status.get("DRAFT", 0), "DRAFT", "amber", "clock"),
        _tile(_("مؤرشفة"), by_status.get("ARCHIVED", 0), "ARCHIVED", "info", "database"),
    ]

    return render(
        request,
        "catalog/pricelists.html",
        {
            "price_lists": price_lists,
            "counts": [
                {"status": status_code, "label": label, "count": count}
                for (status_code, label), count in tally.items()
            ],
            "query": query,
            "status": status,
            "status_choices": catalog_service.price_list_status_choices(),
            "is_filtered": bool(query or status),
            "in_force_code": in_force,
            "today": today,
            "tiles": tiles,
            "active_filters": _price_list_filters(query, status, price_lists),
            # Row 13 grants no APPROVE to anyone: the president approves
            # outside the system (footnote 8, D-31). Recording that decision is
            # an edit, which is why this asks for EDIT. Kept in the context —
            # and pinned by tests — though this screen still draws no act:
            # a button with no service behind it is worse than none (§2.2).
            "can_record_approval": policy.is_allowed(request.user, Screen.PRICELISTS, Action.EDIT),
            "can_create": can_create,
            "new_form": new_form,
            "posted_create": request.method == "POST",
        },
    )


def _price_list_form(data: Any) -> PriceListForm:
    """The semesters a list may belong to — projected by the service (A-05)."""
    return PriceListForm(data, semester_choices=catalog_service.semester_choices())


def _create_price_list(request: HttpRequest, form: PriceListForm) -> HttpResponse | None:
    try:
        data = catalog_service.resolve_price_list_data(dict(form.cleaned_data))
    except ObjectDoesNotExist:
        messages.error(request, _("لا فصل بهذا الرمز."))
        return None

    try:
        price_list = catalog_service.create_price_list(
            actor=request.user, data=data, request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لقائمة أخرى — اختر رمزاً غيره.")
            % {"code": data["code"]},
        )
        return None

    messages.success(
        request,
        _("أُنشئت القائمة %(code)s — مسودة. أضف بنودها ثم سجّل قرار الاعتماد.")
        % {"code": price_list.code},
    )
    return redirect("catalog:pricelist-detail", code=price_list.code)


@require_http_methods(["GET", "POST"])
def price_list_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """
    One dated list: its identity, its priced items, and its fee rules.

    Two absences are load-bearing here and are projected as such rather than
    left for a truthiness test in the template. A deposit of NULL means the
    programme carries no deposit at all (BR-096), and a fee of NULL means no
    registration fee is charged (BR-009, and the JCPA/PMP courses of T-098) —
    neither is a zero, and ``{% if amount %}`` cannot tell the difference.
    """
    try:
        price_list = catalog_service.get_price_list(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist:
        raise Http404(_("لا توجد قائمة أسعار بهذا الرمز")) from None

    # The two acts a draft list needs, and neither existed on any screen: a
    # list with no items prices nothing, and a list nobody approved may not be
    # used at all. Both services were written and had no caller.
    editable = policy.is_allowed(request.user, Screen.PRICELISTS, Action.EDIT)
    item_form = _price_item_form(request, price_list, None)
    # تعديل المسودة: الرمز خارج الحقول عمداً — هو ما يُشار به إلى القائمة في كل
    # مكان، وتغييره بعد كتابته يعني إعادة تسمية مرجعٍ قد يكون قد خرج من الشاشة.
    edit_form = _price_list_form(None)
    edit_form.initial = {
        "code": price_list.code,
        "name_ar": price_list.name_ar,
        "semester": str(price_list.semester_id),
        "issued_on": price_list.issued_on,
        "effective_from": price_list.effective_from,
        "proposed_by_text": price_list.proposed_by_text,
    }
    rule_form = _fee_rule_form(price_list, None)
    approval_form = PriceListApprovalForm(
        initial={
            "approved_by_text": price_list.approved_by_text,
            "decision_reference": price_list.decision_reference,
        }
    )

    if request.method == "POST":
        # Same order here: EDIT is what both acts need, asked before the shape.
        policy.require(request.user, Screen.PRICELISTS, Action.EDIT, request=request)
        action = request.POST.get("action", "")
        if action == "approve":
            approval_form = PriceListApprovalForm(request.POST)
            response = _record_price_list_approval(request, price_list, approval_form)
            if response is not None:
                return response
        elif action == "edit":
            edit_form = _price_list_form(request.POST)
            if edit_form.is_valid():
                response = _update_price_list(request, price_list, edit_form)
                if response is not None:
                    return response
            else:
                messages.error(request, _("راجع حقول القائمة."))
        elif action == "remove-item":
            response = _remove_price_item(request, price_list)
            if response is not None:
                return response
        elif action == "fee-rule":
            rule_form = _fee_rule_form(price_list, request.POST)
            if rule_form.is_valid():
                response = _add_fee_rule(request, price_list, rule_form)
                if response is not None:
                    return response
            else:
                messages.error(request, _("راجع حقول قاعدة الرسم."))
        elif action == "remove-rule":
            response = _remove_fee_rule(request, price_list)
            if response is not None:
                return response
        else:
            item_form = _price_item_form(request, price_list, request.POST)
            if item_form.is_valid():
                response = _add_price_item(request, price_list, item_form)
                if response is not None:
                    return response
            else:
                messages.error(request, _("راجع حقول البند."))

    # The category is stored as a bare code, so the row has no
    # ``get_..._display``. ``constants`` re-exports the vocabulary precisely so
    # a view can name it without importing the models module (A-05).
    category_labels = dict(PARTICIPANT_CATEGORY_CHOICES)

    query = request.GET.get("q", "").strip()
    rule_query = request.GET.get("rq", "").strip()
    items = price_list.items.select_related("program", "deposit_policy")
    if query:
        items = items.filter(
            Q(program__code__icontains=query) | Q(program__name_ar__icontains=query)
        )
    item_rows = _price_item_rows(items)

    today = timezone.localdate()
    try:
        in_force = pricing_service.effective_price_list(as_of=today).code
    except pricing_service.NoEffectivePriceListError:
        in_force = ""

    rules = _matching_rules(price_list, rule_query)

    return render(
        request,
        "catalog/pricelist_detail.html",
        {
            "price_list": price_list,
            "items": item_rows,
            # A column that is empty in every row costs width and gives nothing;
            # it is drawn only where some item actually carries a note.
            "has_notes": any(row["notes"] for row in item_rows),
            # A programme search that matched no rule OF ITS OWN still shows the
            # general rules — true, and silent about it until now.
            "rules_are_general_only": bool(rule_query)
            and not any(rule.program_id for rule in rules),
            "item_count": sum(row["levels"] for row in item_rows),
            "program_count": len(item_rows),
            "query": query,
            "rule_query": rule_query,
            "is_filtered": bool(query),
            "in_force_code": in_force,
            "today": today,
            # A list that leaves an active programme without an item cannot
            # price an enrolment on it (BR-008), and that is a property of the
            # LIST — read here rather than discovered later at the till.
            "unpriced": catalog_service.unpriced_active_programs(
                actor=request.user, price_list=price_list, request=request
            ),
            # Projected to a row the template can render without deciding
            # anything: the scope of the rule, the category BY NAME, and the
            # fee left as ``None`` where none is charged.
            "fee_rules": [
                {
                    "id": rule.pk,
                    "program": rule.program,
                    "category": rule.participant_category,
                    "category_display": category_labels.get(
                        rule.participant_category, rule.participant_category
                    ),
                    "fee": rule.fee,
                    "note": rule.exception_note_ar,
                }
                for rule in rules
            ],
            "can_edit": editable and not price_list.is_frozen,
            "item_form": item_form,
            "rule_form": rule_form,
            # The rules are what the pricing engine reads on EVERY enrolment,
            # and a list frozen without them refuses the first one typed
            # against it with nothing the screen can do about it (D-14). So the
            # gap is named here, beside the approval, not discovered at the
            # till: these are the categories no general rule covers yet.
            "uncovered_categories": catalog_service.categories_without_a_fee_rule(
                price_list=price_list
            ),
            "rule_count": price_list.registration_fee_rules.count(),
            "approval_form": approval_form,
            "edit_form": edit_form,
            # An approved list is evidence, not a draft: the services refuse
            # every edit on it (D-14), so the screen offers none either.
            "is_frozen": price_list.is_frozen,
        },
    )


def _fee_rule_form(price_list: Any, data: Any) -> FeeRuleForm:
    """Scopes and categories projected by the service — no model here (A-05)."""
    return FeeRuleForm(
        data,
        program_choices=catalog_service.fee_rule_program_choices(price_list=price_list),
        category_choices=[(str(code), str(label)) for code, label in PARTICIPANT_CATEGORY_CHOICES],
    )


def _add_fee_rule(
    request: HttpRequest, price_list: Any, form: FeeRuleForm
) -> HttpResponse | None:
    try:
        data = catalog_service.resolve_fee_rule_data(dict(form.cleaned_data))
    except ObjectDoesNotExist:
        messages.error(request, _("لا برنامج بهذا الرمز."))
        return None

    try:
        rule = catalog_service.add_fee_rule(
            actor=request.user, price_list=price_list, data=data, request=request
        )
    except (DjangoValidationError, ImmutableRecordError) as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        # C-27 keeps one rule per (list, programme, category); a second one
        # would leave the resolver choosing arbitrarily between them.
        messages.error(
            request, _("توجد قاعدة لهذا النطاق والفئة سلفاً — احذفها أو عدّلها بدل إضافة ثانية.")
        )
        return None

    scope = rule.program.name_ar if rule.program is not None else _("كل البرامج")
    messages.success(request, _("أُضيفت قاعدة رسم — %(scope)s") % {"scope": scope})
    return redirect("catalog:pricelist-detail", code=price_list.code)


def _remove_fee_rule(request: HttpRequest, price_list: Any) -> HttpResponse | None:
    try:
        catalog_service.remove_fee_rule(
            actor=request.user,
            price_list=price_list,
            rule_id=request.POST.get("rule", ""),
            request=request,
        )
    except ImmutableRecordError as exc:
        messages.error(request, _message_of(exc))
        return None
    except (ObjectDoesNotExist, ValueError):
        messages.error(request, _("لا قاعدة رسم بهذا المعرّف على هذه القائمة."))
        return None

    messages.success(request, _("حُذفت قاعدة الرسم من المسودة."))
    return redirect("catalog:pricelist-detail", code=price_list.code)


def _price_item_form(request: HttpRequest, price_list: Any, data: Any) -> PriceItemForm:
    """Only programmes this list has not priced yet — the rest would collide."""
    return PriceItemForm(
        data,
        program_choices=catalog_service.unpriced_program_choices(price_list=price_list),
        deposit_policy_choices=catalog_service.deposit_policy_choices(),
    )


def _add_price_item(
    request: HttpRequest, price_list: Any, form: PriceItemForm
) -> HttpResponse | None:
    try:
        data = catalog_service.resolve_price_item_data(dict(form.cleaned_data))
    except ObjectDoesNotExist:
        messages.error(request, _("لا برنامج بهذا الرمز."))
        return None

    try:
        catalog_service.add_price_item(
            actor=request.user, price_list=price_list, data=data, request=request
        )
    except (DjangoValidationError, ImmutableRecordError) as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(
        request, _("أُضيف سعر %(name)s") % {"name": data["program"].name_ar}
    )
    return redirect("catalog:pricelist-detail", code=price_list.code)


def _record_price_list_approval(
    request: HttpRequest, price_list: Any, form: PriceListApprovalForm
) -> HttpResponse | None:
    """
    D-31 — the president approves outside the system; this records that he did.

    The two fields are mandatory in the service and in the form, because they
    are the only evidence the approval ever happened.
    """
    if not form.is_valid():
        messages.error(request, _("اعتماد القائمة يتطلب جهة الاعتماد ومرجع القرار."))
        return None

    try:
        catalog_service.record_external_approval(
            actor=request.user,
            price_list=price_list,
            approved_by_text=form.cleaned_data["approved_by_text"],
            decision_reference=form.cleaned_data["decision_reference"],
            request=request,
        )
    except (DjangoValidationError, ImmutableRecordError) as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(
        request,
        _("سُجّل اعتماد القائمة %(code)s — صارت سارية، وأُرشفت سابقتها.")
        % {"code": price_list.code},
    )
    return redirect("catalog:pricelist-detail", code=price_list.code)


# ---------------------------------------------------------------------------
# Course categories (Sprint 8L) — the reference data a transfer is judged on
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
@require_http_methods(["GET", "POST"])
def deposit_policies_view(request: HttpRequest) -> HttpResponse:
    """
    Refundable-deposit policies, with a form to write one.

    The table existed, the pricing engine read it, ``C-26`` pairs a deposit
    amount with a policy on every price item — and there was no screen. Worse:
    the price-item form carried the AMOUNT and no policy field, so the
    constraint refused every deposit anyone tried to enter. A fresh install
    therefore could not price a single programme with a deposit, and the only
    road to a policy was the Django admin, which writes past the audit trail
    that a forfeited deposit is later defended with.

    The price-list permission, deliberately (see ``DEPOSIT_SCREEN``): a deposit
    is a term of the price, and whoever may set the amount must be able to name
    the policy. No new matrix cell and no new row.
    """
    screen = catalog_service.DEPOSIT_SCREEN
    policy.require(request.user, screen, Action.VIEW, request=request)

    can_create = policy.is_allowed(request.user, screen, Action.CREATE)
    can_edit = policy.is_allowed(request.user, screen, Action.EDIT)

    posted = request.method == "POST"
    action = request.POST.get("action", "create") if posted else ""
    # Bound only for the act that OWNS this form. On a GET ``request.POST`` is an
    # empty QueryDict — data, not nothing — so binding it would fill ``errors``
    # with every required field and open the dialog on sight.
    form = _deposit_policy_form(request.POST if action == "create" else None)
    edit_form = _deposit_policy_form(
        request.POST if action == "edit" else None, editing=True
    )
    editing_code = request.POST.get("code", "") if action == "edit" else ""

    if posted:
        policy.require(
            request.user,
            screen,
            Action.CREATE if action == "create" else Action.EDIT,
            request=request,
        )
        if action == "toggle":
            response = _toggle_deposit_policy(request)
            if response is not None:
                return response
        elif action == "edit":
            if edit_form.is_valid():
                response = _update_deposit_policy(request, editing_code, edit_form)
                if response is not None:
                    return response
            else:
                messages.error(request, _("راجع حقول السياسة."))
        elif form.is_valid():
            response = _create_deposit_policy(request, form)
            if response is not None:
                return response
        else:
            messages.error(request, _("راجع حقول السياسة."))

    all_rows = catalog_service.deposit_policy_rows(actor=request.user, request=request)
    query = request.GET.get("q", "").strip()
    state = request.GET.get("state", "").strip()
    rows = all_rows
    if query:
        needle = query.lower()
        rows = [
            row
            for row in rows
            if needle in row["code"].lower()
            or needle in row["name_ar"].lower()
            or needle in row["refund_trigger"].lower()
        ]
    # Tallied off the WHOLE set, not the filtered one: a card that counted the
    # rows already narrowed would answer «how many are active» with «the ones
    # you can see», and pressing it would then change its own number.
    tiles = [
        {
            "label": _("نشطة"),
            "count": sum(1 for row in all_rows if row["is_active"]),
            "state": "active",
            "tone": "ok",
            "icon": "shield-check",
        },
        {
            "label": _("مُعطَّلة"),
            "count": sum(1 for row in all_rows if not row["is_active"]),
            "state": "off",
            "tone": "amber",
            "icon": "user-off",
        },
        {
            "label": _("عليها تسعير"),
            "count": sum(1 for row in all_rows if row["items"]),
            "state": "used",
            "tone": "info",
            "icon": "tag",
        },
    ]
    if state == "active":
        rows = [row for row in rows if row["is_active"]]
    elif state == "off":
        rows = [row for row in rows if not row["is_active"]]
    elif state == "used":
        rows = [row for row in rows if row["items"]]

    page = page_of(rows, request.GET.get("page", ""))
    return render(
        request,
        "catalog/deposit_policies.html",
        {
            "title": _("سياسات التأمين"),
            "active_screen": screen,
            "rows": page["rows"],
            "page": page,
            "tiles": tiles,
            "state": state,
            # Everything but ``page``, so a pager link keeps the filters.
            "params_qs": urlencode(
                {key: value for key, value in (("q", query), ("state", state)) if value}
            ),
            "query": query,
            "is_filtered": bool(query or state),
            "form": form,
            "edit_form": edit_form,
            "editing_code": editing_code,
            # The forfeit statuses are drawn by the template as a fieldset of
            # their own, so the shared partial is told which fields to render
            # around it (§ ``partials/_form.html`` and its ``only``).
            "fields_top": DEPOSIT_FIELDS_TOP,
            "fields_bottom": DEPOSIT_FIELDS_BOTTOM,
            # What other policies already forfeit on, offered to the free field:
            # Q-30 forbids a constraint, so what stops «CONFIRMED» and
            # «ON_CONFIRM» living side by side is showing the writer what the
            # centre already says.
            "forfeit_seen": catalog_service.deposit_forfeit_in_use(),
            "can_create": can_create,
            "can_edit": can_edit,
            "posted_action": action,
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


#: Drawn before and after the forfeit-status fieldset. The list is here rather
#: than in the template because the template may not decide what a form holds.
DEPOSIT_FIELDS_TOP = ("code", "name_ar", "is_required", "refund_trigger")
DEPOSIT_FIELDS_BOTTOM = (
    "is_taxable",
    "allows_partial_deduction",
    "claim_deadline_days",
    "notes_ar",
    "is_active",
)


def _deposit_data(form: DepositPolicyForm) -> dict[str, Any]:
    """
    The form's answer as the MODEL's fields.

    ``forfeit_other`` is a second control over one stored column, not a column
    of its own: the form merged it into ``forfeit_on`` in ``clean``, and passing
    it on would hand ``DepositPolicy(**data)`` a keyword it has never had.
    """
    data = dict(form.cleaned_data)
    data.pop("forfeit_other", None)
    data["forfeit_on"] = list(data.get("forfeit_on") or [])
    return data


def _update_deposit_policy(
    request: HttpRequest, code: str, form: DepositPolicyForm
) -> HttpResponse | None:
    """
    Correct a policy in place.

    Everything but the code: a mistyped deadline, a trigger spelt two ways, a
    note that turned out to explain nothing — none of these could be corrected
    from any screen, and the policy is not frozen evidence the way an approved
    price list is (D-14 governs the LIST, not the terms a deposit is held on).
    A row nobody may fix is a row that gets worked around by adding a second.
    """
    try:
        row = catalog_service.deposit_policy_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا سياسة بالرمز %(code)s") % {"code": code})
        return None

    data = _deposit_data(form)
    try:
        catalog_service.update_deposit_policy(
            actor=request.user, deposit_policy=row, data=data, request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(request, _("حُفظت السياسة %(name)s") % {"name": row.name_ar})
    return redirect("catalog:deposit-policies")


def _deposit_policy_form(data: Any, *, editing: bool = False) -> DepositPolicyForm:
    """The forfeit statuses come from the operations vocabulary via the service."""
    return DepositPolicyForm(
        data,
        forfeit_choices=catalog_service.deposit_forfeit_choices(),
        editing=editing,
    )


def _create_deposit_policy(
    request: HttpRequest, form: DepositPolicyForm
) -> HttpResponse | None:
    data = _deposit_data(form)
    try:
        row = catalog_service.create_deposit_policy(
            actor=request.user, data=data, request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لسياسة أخرى — اختر رمزاً غيره.")
            % {"code": data["code"]},
        )
        return None

    messages.success(request, _("أُضيفت السياسة %(name)s") % {"name": row.name_ar})
    return redirect("catalog:deposit-policies")


def _toggle_deposit_policy(request: HttpRequest) -> HttpResponse | None:
    """
    Stand a policy down, or bring it back. Never a delete.

    A policy price items already point at is history for them; deleting it
    would leave a deposit nobody can explain the terms of. Standing it down
    takes it out of the choices a NEW item is offered and leaves the old ones
    readable — which is what ``deposit_policy_choices`` filters on.
    """
    code = request.POST.get("code", "").strip()
    try:
        row = catalog_service.deposit_policy_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا سياسة بالرمز %(code)s") % {"code": code})
        return None

    catalog_service.update_deposit_policy(
        actor=request.user,
        deposit_policy=row,
        data={"is_active": not row.is_active},
        request=request,
    )
    messages.success(
        request,
        _("أُعيدت السياسة %(name)s") % {"name": row.name_ar}
        if not row.is_active
        else _("أُوقفت السياسة %(name)s") % {"name": row.name_ar},
    )
    return redirect("catalog:deposit-policies")


def course_categories_view(request: HttpRequest) -> HttpResponse:
    """
    The catalogue's own list of course fields, with a form to add one.

    It had no screen at all. The rows existed, ``Program`` refuses a short
    course without one (BR-061's own database constraint), and the only way to
    create one was the Django admin — which writes straight to the table, so
    nobody could say afterwards who added the field a transfer refusal now
    rests on. This screen puts the write behind the service, which audits it.

    The programme screens' permission, not a new one: a category is the
    catalogue reference data of whoever defines programmes. No new matrix cell.
    """
    policy.require(request.user, catalog_service.CATEGORY_SCREEN, Action.VIEW, request=request)

    can_create = policy.is_allowed(
        request.user, catalog_service.CATEGORY_SCREEN, Action.CREATE
    )
    can_edit = policy.is_allowed(request.user, catalog_service.CATEGORY_SCREEN, Action.EDIT)

    posted = request.method == "POST"
    form = CourseCategoryForm(request.POST if posted else None)

    if posted:
        action = request.POST.get("action", "create")
        if action == "toggle":
            response = _toggle_category(request)
            if response is not None:
                return response
        elif form.is_valid():
            response = _create_category(request, form)
            if response is not None:
                return response

    rows = catalog_service.category_rows(actor=request.user, request=request)
    return render(
        request,
        "catalog/course_categories.html",
        {
            "title": _("مجالات الدورات"),
            "active_screen": catalog_service.CATEGORY_SCREEN,
            "rows": rows,
            "form": form,
            "can_create": can_create,
            "can_edit": can_edit,
            # Named at the top so a refusal is read rather than hunted for.
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


def _create_category(request: HttpRequest, form: CourseCategoryForm) -> HttpResponse | None:
    try:
        category = catalog_service.create_course_category(
            actor=request.user, data=dict(form.cleaned_data), request=request
        )
    except DjangoValidationError as exc:
        # A duplicate code arrives here, in the service's own words.
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لمجال آخر — اختر رمزاً غيره.")
            % {"code": form.cleaned_data["code"]},
        )
        return None

    messages.success(
        request, _("أُضيف المجال %(name)s") % {"name": category.name_ar}
    )
    return redirect("catalog:course-categories")


def _toggle_category(request: HttpRequest) -> HttpResponse | None:
    """Stand a category down, or bring it back. Never a delete."""
    code = request.POST.get("code", "").strip()
    try:
        category = catalog_service.category_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا مجال بالرمز %(code)s") % {"code": code})
        return None

    catalog_service.update_course_category(
        actor=request.user,
        category=category,
        data={"is_active": not category.is_active},
        request=request,
    )
    messages.success(
        request,
        _("%(name)s — %(state)s")
        % {
            "name": category.name_ar,
            "state": _("نشط") if category.is_active else _("مُعطَّل"),
        },
    )
    return redirect("catalog:course-categories")


# ---------------------------------------------------------------------------
# Knowledge fields (Sprint 8L) — what the ministry is told, not what we judge
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def knowledge_fields_view(request: HttpRequest) -> HttpResponse:
    """
    The ministry's classification, maintained from a screen rather than the
    admin. It governs nothing in this system — it is copied onto the
    submission file because §9 of the brief lists it among what the ministry
    is sent — and that is precisely why it had no screen: nothing broke
    without one, so the gap stayed invisible until a client had to install
    from nothing.
    """
    policy.require(request.user, catalog_service.FIELD_SCREEN, Action.VIEW, request=request)

    can_create = policy.is_allowed(request.user, catalog_service.FIELD_SCREEN, Action.CREATE)
    can_edit = policy.is_allowed(request.user, catalog_service.FIELD_SCREEN, Action.EDIT)

    posted = request.method == "POST"
    form = KnowledgeFieldForm(request.POST if posted else None)

    if posted:
        if request.POST.get("action", "create") == "toggle":
            response = _toggle_knowledge_field(request)
            if response is not None:
                return response
        elif form.is_valid():
            response = _create_knowledge_field(request, form)
            if response is not None:
                return response

    return render(
        request,
        "catalog/knowledge_fields.html",
        {
            "title": _("المجالات المعرفية"),
            "active_screen": catalog_service.FIELD_SCREEN,
            "rows": catalog_service.knowledge_field_rows(actor=request.user, request=request),
            "form": form,
            "can_create": can_create,
            "can_edit": can_edit,
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


def _create_knowledge_field(
    request: HttpRequest, form: KnowledgeFieldForm
) -> HttpResponse | None:
    try:
        field = catalog_service.create_knowledge_field(
            actor=request.user, data=dict(form.cleaned_data), request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لمجال معرفي آخر — اختر رمزاً غيره.")
            % {"code": form.cleaned_data["code"]},
        )
        return None

    messages.success(request, _("أُضيف المجال المعرفي %(name)s") % {"name": field.name_ar})
    return redirect("catalog:knowledge-fields")


def _toggle_knowledge_field(request: HttpRequest) -> HttpResponse | None:
    code = request.POST.get("code", "").strip()
    try:
        field = catalog_service.knowledge_field_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا مجال معرفي بالرمز %(code)s") % {"code": code})
        return None

    catalog_service.update_knowledge_field(
        actor=request.user,
        field=field,
        data={"is_active": not field.is_active},
        request=request,
    )
    messages.success(
        request,
        _("%(name)s — %(state)s")
        % {
            "name": field.name_ar,
            "state": _("نشط") if field.is_active else _("مُعطَّل"),
        },
    )
    return redirect("catalog:knowledge-fields")


def _update_price_list(
    request: HttpRequest, price_list: Any, form: PriceListForm
) -> HttpResponse | None:
    """Correct a draft. The code is not among the fields: it is the reference."""
    try:
        data = catalog_service.resolve_price_list_data(dict(form.cleaned_data))
    except ObjectDoesNotExist:
        messages.error(request, _("لا فصل بهذا الرمز."))
        return None

    data.pop("code", None)
    try:
        catalog_service.update_price_list(
            actor=request.user, price_list=price_list, data=data, request=request
        )
    except (DjangoValidationError, ImmutableRecordError) as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(request, _("حُفظت تعديلات المسودة."))
    return redirect("catalog:pricelist-detail", code=price_list.code)


def _remove_price_item(request: HttpRequest, price_list: Any) -> HttpResponse | None:
    """The row the reader sees is a programme, so the act removes a programme."""
    code = request.POST.get("program", "").strip()
    try:
        catalog_service.remove_program_pricing(
            actor=request.user, price_list=price_list, program_code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا بند لهذا البرنامج على القائمة."))
        return None
    except ImmutableRecordError as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(request, _("حُذف سعر %(code)s من المسودة.") % {"code": code})
    return redirect("catalog:pricelist-detail", code=price_list.code)
