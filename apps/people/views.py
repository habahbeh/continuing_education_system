"""
Authentication and user-administration views (Q-12, Δ-02).

Views render and delegate. Every permission question is answered by
``permissions.policy.require`` — never by an ``if user.role ==`` in here, and
never by hiding a button in a template. A-05 keeps this honest: views may not
import models directly.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.pagination import page_of
from apps.core.services import branding_service
from apps.operations.management.commands.seed_demo_all import DEMO_DATABASES
from apps.people.constants import (
    PARTICIPANT_CATEGORY_CHOICES,
    ROLE_CHOICES,
    Action,
    Screen,
)
from apps.people.forms import FinancialPeriodForm, LoginForm, SemesterForm
from apps.people.participant_forms import ParticipantEditForm, ParticipantForm
from apps.people.permissions import policy
from apps.people.services import (
    audit_query_service,
    auth_service,
    fiscal_period_service,
    participant_numbering,
    participant_service,
    reference_data,
    semester_service,
    user_service,
)
from apps.people.services.participant_numbering import NoActiveSemesterError


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = auth_service.attempt_login(
                request,
                form.cleaned_data["username"],
                form.cleaned_data["password"],
            )
        except auth_service.AccountLockedError:
            return redirect("people:locked")

        if user is not None:
            return redirect(settings.LOGIN_REDIRECT_URL)

        # One message for every failure mode. Telling the visitor which half
        # was wrong tells them which usernames exist.
        messages.error(request, _("اسم المستخدم أو كلمة المرور غير صحيحة"))

    return render(request, "people/login.html", {"form": form})


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> HttpResponse:
    auth_service.perform_logout(request)
    return redirect("people:login")


def locked_view(request: HttpRequest) -> HttpResponse:
    return render(request, "people/locked.html", status=403)


def session_expired_view(request: HttpRequest) -> HttpResponse:
    return render(request, "people/session_expired.html", status=440)


def users_view(request: HttpRequest) -> HttpResponse:
    """
    The users screen (PERMISSIONS.md row 33).

    CENTER_MANAGER and AUDIT_ACCOUNT read it; SYSTEM_ADMINISTRATOR administers
    it. There is no control here that changes the current session's role, at
    any privilege level (D-20).
    """
    policy.require(request.user, Screen.USERS, Action.VIEW, request=request)
    return render(
        request,
        "people/users.html",
        {
            "users": user_service.list_users(),
            "can_edit": policy.is_allowed(request.user, Screen.USERS, Action.EDIT),
            "roles": ROLE_CHOICES,
        },
    )


@require_http_methods(["POST"])
def user_action_view(request: HttpRequest) -> HttpResponse:
    """
    System-administrator actions on a user account (Δ-02).

    Each branch delegates to a service that runs its own policy check — this
    view chooses which operation, never whether it is allowed.
    """
    action = request.POST.get("action", "")
    target = user_service.get_user(pk=request.POST.get("user_id", ""))

    try:
        if action == "set_role":
            user_service.set_role(
                actor=request.user,
                target=target,
                role=request.POST.get("role", ""),
                request=request,
            )
            messages.success(request, _("تم تحديث الدور"))
        elif action == "toggle_active":
            user_service.set_active(
                actor=request.user,
                target=target,
                is_active=not target.is_active,
                request=request,
            )
            messages.success(request, _("تم تحديث حالة الحساب"))
        elif action == "unlock":
            auth_service.unlock_account(
                target=target,
                actor=request.user,
                reason=request.POST.get("reason", ""),
                request=request,
            )
            messages.success(request, _("تم فكّ قفل الحساب"))
        else:
            messages.error(request, _("إجراء غير معروف"))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("people:users")


# ---------------------------------------------------------------------------
# Participants (PERMISSIONS.md rows 3 and 4)
# ---------------------------------------------------------------------------
#: The registry is paged rather than capped. Fifty is the register's own page
#: size (``cashbox.views.PAGE_SIZE``), and the two screens are read the same
#: way, so they page the same way.
PARTICIPANTS_PAGE_SIZE = 50

#: The tone and glyph each category wears on its filter card. Kept beside the
#: view rather than in the template because the template may not look a value
#: up by a variable key, and beside the labels rather than inside them because
#: a colour is presentation and the label is the glossary's word.
_CATEGORY_TILES: dict[str, tuple[str, str]] = {
    "UNIVERSITY": ("info", "cap"),
    "CENTER": ("violet", "building"),
    "EMPLOYEE": ("teal", "users"),
}


def _query_string(path: str, params: dict[str, Any]) -> str:
    """``path`` with the given query, and with no «?» when there is none."""
    encoded = urlencode({key: value for key, value in params.items() if value})
    return f"{path}?{encoded}" if encoded else path


def participants_view(request: HttpRequest) -> HttpResponse:
    """
    The participants list.

    Rows arrive already projected to the caller's permitted field set — a
    restricted role's forbidden fields are absent from the response, not merely
    unrendered (BR-101, T-283).

    The page is built in this order for a reason: the whole filtered set is
    read and counted first, so every number on screen is a number about the
    registry; only then is a page of fifty rows cut, and only that page is
    decorated with labels and with its newest enrolment. Decorating the whole
    set would mean reading the enrolments of thousands of people to print
    fifty.
    """
    # Trimmed before anything is done with them, and trimmed ONCE: the service
    # used to strip the search term while the page re-displayed the raw one, so
    # a box holding two spaces drew the "filtered results" banner over an
    # unfiltered list. And a category outside the glossary is not a filter at
    # all — obeying it would answer «no such participants» about a category
    # that does not exist, which reads like an answer about the registry.
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if category not in {value for value, _label in PARTICIPANT_CATEGORY_CHOICES}:
        category = ""

    columns = participant_service.visible_fields_for(request.user)
    # And a category the reader may not SEE is not a filter either. The service
    # already refuses to narrow by a withheld field — a filter reports the field
    # as surely as printing it (BR-101) — so the same request returns the same
    # set with the parameter and without it. The page used to keep the word
    # anyway: a link copied from the manager drew «الفئة: طالب جامعة/خرّيج» and
    # «إلغاء التصفية» over the cashier's UNFILTERED sixteen rows. That is the
    # blank-search bug above wearing another hat, and it is worse, because it
    # also tells the cashier a category label the projection withheld.
    if "category" not in columns:
        category = ""

    rows = participant_service.list_participants(
        actor=request.user,
        query=query,
        category=category,
        request=request,
    )

    labels = dict(PARTICIPANT_CATEGORY_CHOICES)
    # The filter cards count the set the OTHER filter left, so «طالب مركز: ٨٧»
    # stays the answer to "and how many of these are centre students" while a
    # category is selected — the same rule the register's status cards follow.
    # Drawn only for a role that sees the category field: a card is a filter,
    # and a filter on a withheld field reports it (BR-101).
    tiles: list[dict[str, Any]] = []
    if "category" in columns:
        scope = (
            rows
            if not category
            else participant_service.list_participants(
                actor=request.user, query=query, request=request
            )
        )
        tally: Counter[str] = Counter(str(row["category"]) for row in scope)
        base = {"q": query} if query else {}
        tiles.append(
            {
                "label": _("كل المشاركين"),
                "value": len(scope),
                "tone": "brand" if not category else "",
                "icon": "users",
                "on": not category,
                "url": _query_string(reverse("people:participants"), base),
            }
        )
        for value, label in PARTICIPANT_CATEGORY_CHOICES:
            tone, icon = _CATEGORY_TILES.get(str(value), ("info", "users"))
            selected = value == category
            tiles.append(
                {
                    "label": label,
                    "value": tally.get(str(value), 0),
                    "tone": tone,
                    "icon": icon,
                    "on": selected,
                    # Clicking the selected card again widens the list back —
                    # a card that only ever narrows is a trap on a screen with
                    # no other way out of its own filter.
                    "url": _query_string(
                        reverse("people:participants"),
                        base if selected else {**base, "category": value},
                    ),
                }
            )

    page = page_of(rows, request.GET.get("page", ""), size=PARTICIPANTS_PAGE_SIZE)

    # Presentation only, and only over the page the reader is actually looking
    # at. ``category`` was reaching the page as its stored code — a registry
    # that says UNIVERSITY at a client instead of «طالب جامعة/خرّيج». The label
    # is attached to rows that ALREADY carry the field, so a restricted role
    # gains no key it did not have (BR-101).
    #
    # City and qualification are settings-driven lists (reference_data), so
    # their words come from the same place the admission form offers them.
    cities = dict(reference_data.cities())
    qualifications = dict(reference_data.qualifications())
    for row in page["rows"]:
        if "category" in row:
            row["category_display"] = labels.get(row["category"], row["category"])
        if "city" in row:
            row["city_display"] = cities.get(row["city"], row["city"])
        if "qualification" in row:
            row["qualification_display"] = qualifications.get(
                row["qualification"], row["qualification"]
            )

    # The quick view names each participant's newest enrolment. One query for
    # the page, gated by the enrolments screen's own permission (see the
    # service); a role without it gets no ``latest`` key and the dialog says
    # so. Lazy import: ``people`` stays free of ``operations`` at import time.
    from apps.operations.services.participant_hub_service import latest_enrollments

    latest = latest_enrollments(
        actor=request.user,
        participant_numbers=[row["participant_number"] for row in page["rows"]],
        request=request,
    )
    for row in page["rows"]:
        if row["participant_number"] in latest:
            row["latest"] = latest[row["participant_number"]]

    # The quick-enrolment dialog: «دورة أم دبلوم؟» then the approved cohorts
    # of that kind. Drawn only for a reader who may CREATE an enrolment —
    # the same gate the dialog's POST enforces — and only when there is at
    # least one approved cohort to offer.
    enroll_groups: list[dict[str, Any]] = []
    if policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.CREATE) and policy.is_allowed(
        request.user, Screen.COHORTS, Action.VIEW
    ):
        from apps.operations.services.cohort_service import enrollable_cohorts

        enroll_groups = enrollable_cohorts(actor=request.user, request=request)

    return render(
        request,
        "people/participants.html",
        {
            "participants": page["rows"],
            "page": page,
            # Everything but ``page``, so a pager link keeps the filters.
            "params_qs": urlencode({k: v for k, v in (("q", query), ("category", category)) if v}),
            "columns": columns,
            # The empty row has to span the table this role actually gets:
            # number, name, registrations, actions, plus whichever optional
            # columns survived the projection.
            "column_count": 4 + sum(1 for name in ("category", "phone") if name in columns),
            "tiles": tiles,
            "categories": PARTICIPANT_CATEGORY_CHOICES,
            "query": query,
            "selected_category": category,
            "selected_category_label": labels.get(category, ""),
            "is_filtered": bool(query or category),
            "can_create": policy.is_allowed(request.user, Screen.STUDENT_NEW, Action.CREATE),
            "can_edit": policy.is_allowed(request.user, Screen.STUDENTS, Action.EDIT),
            "enroll_groups": enroll_groups,
            "today": timezone.localdate(),
        },
    )


def participant_detail_view(request: HttpRequest, number: str) -> HttpResponse:
    try:
        rows = participant_service.get_participant_display(
            actor=request.user, participant_number=number, request=request
        )
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد مشارك بهذا الرقم")) from None

    values = {key: value for key, _label, value in rows}

    # The operational half of the file — enrolments, balances, clearances,
    # certificates, the next step — read through the services that own them.
    # Imported here and not at the top: ``people`` stays free of ``operations``
    # at import time, the way the rest of this app is.
    from apps.operations.services.participant_hub_service import participant_hub

    hub = participant_hub(
        actor=request.user, participant_number=values["participant_number"], request=request
    )
    return render(
        request,
        "people/participant_detail.html",
        {
            "rows": rows,
            "values": values,
            "number": values["participant_number"],
            "name_ar": values["name_ar"],
            "hub": hub,
            "can_edit": policy.is_allowed(request.user, Screen.STUDENTS, Action.EDIT),
        },
    )


#: The admission form's twenty fields, grouped for reading (SPEC §6).
#:
#: Presentation only. The names, their order inside a group, whether any of
#: them is required and what ``clean()`` does with them are all untouched —
#: this decides which card a field is drawn in and nothing else. Every field
#: appears exactly once, and a test compares this map against the form so a
#: field added later cannot go missing from the page by being forgotten here.
PARTICIPANT_FORM_SECTIONS: dict[str, list[str]] = {
    "identity": ["category", "registered_on"],
    "personal": [
        "name_ar",
        "name_en",
        "date_of_birth",
        "gender",
        "nationality",
        "id_document_type",
        "id_document_number",
    ],
    # BR-005 — drawn beside the warning that asks for it, and nowhere else.
    # It used to stand in the identity card of every new application, asking
    # «سبب المتابعة رغم التكرار» before anything had been repeated; its own
    # help text admitted it («يُملأ عند تأكيد المتابعة»).
    "duplicate_reason": ["duplicate_override_reason"],
    "contact": ["city", "phone", "po_box", "email"],
    "background": ["qualification", "employer"],
    "consent": [
        "is_exempt",
        "exemption_approval_ref",
        "exemption_approval_date",
        "no_refund_pledge_accepted",
    ],
    # القسم الخامس مقسوم ثلاثاً لا لأن الحقول تغيّرت، بل ليُكشف وسطُه وحده:
    # حقلا الموافقة يظهران لحظة التأشير بالإعفاء لا بعد أن يرفض الحفظ. كلٌّ
    # منها يُرسَم بـ `_form.html` نفسه، فلا يتكرّر حقل ولا معرّف في الصفحة.
    "consent_flag": ["is_exempt"],
    "consent_exemption": ["exemption_approval_ref", "exemption_approval_date"],
    "consent_pledge": ["no_refund_pledge_accepted"],
}

#: (key, Arabic title) in the order the cards are drawn. The section strip
#: needs a title per key and the template cannot look one up by a variable —
#: and a page 2052px tall on a tablet needs something other than scrolling to
#: say where the reader is and where the red is.
PARTICIPANT_SECTION_TITLES: tuple[tuple[str, Any], ...] = (
    ("identity", _("الفئة وتاريخ التسجيل")),
    ("personal", _("البيانات الشخصية ووثيقة الهوية")),
    ("contact", _("الاتصال والعنوان")),
    ("background", _("المؤهل والعمل")),
    ("consent", _("الإعفاء والتعهّد")),
)


@require_http_methods(["GET", "POST"])
def participant_new_view(request: HttpRequest) -> HttpResponse:
    """
    The admission form.

    Three things this view decides that the template cannot:

    **Who may save.** The matrix grants the audit account VIEW on this screen
    and withholds CREATE (requirements §8 — «حساب التدقيق: قراءة فقط»). The
    screen was handing that account a fillable form and a save button, and the
    save was refused by the service and written to the audit trail as a
    DENIED_ATTEMPT — a refusal the screen itself had invited (polish rules
    §3.4). ``can_create`` is the same flag the participants list already uses.

    **Whether saving is possible at all.** BR-001 allocates the permanent
    number from the active semester, so with no active semester nothing can be
    saved. That used to be discovered after twenty-two fields had been filled
    in. It is discovered here, before the form is drawn.

    **Which record a duplicate refers to.** BR-005 refuses with the matching
    participant's number in the message. The number alone means copying it and
    searching for it; the matching rows are read back — a pure read, through
    the service's own lookup — so the page can offer the file as a link.
    """
    policy.require(request.user, Screen.STUDENT_NEW, Action.VIEW, request=request)
    can_create = policy.is_allowed(request.user, Screen.STUDENT_NEW, Action.CREATE)

    # BR-001 — asked before the form is drawn, not after it is filled in.
    active_semester: Any = None
    semester_error = ""
    try:
        active_semester = participant_numbering.active_semester()
    except NoActiveSemesterError as exc:
        semester_error = str(exc)

    # ``request.POST or None`` treated an empty body as "not submitted", so a
    # POST carrying nothing re-rendered a pristine form and said nothing at
    # all. The method is what decides whether the form is bound.
    posted = request.method == "POST" and can_create
    form = ParticipantForm(request.POST if posted else None)
    duplicates: list[dict[str, str]] = []

    if posted and form.is_valid():
        try:
            participant = participant_service.create_participant(
                actor=request.user,
                data=form.to_service_data(),
                duplicate_override_reason=form.cleaned_data["duplicate_override_reason"],
                request=request,
            )
        except DjangoValidationError as exc:
            _apply_errors(form, exc)
        except NoActiveSemesterError as exc:
            # BR-001 — no silent fallback to today's year.
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("تم إنشاء المشارك برقم %(number)s") % {"number": participant.participant_number},
            )
            return redirect("people:participant-detail", number=participant.participant_number)

    # BR-005 — name the record the refusal is about, as something to open.
    # ``find_identity_duplicates`` takes no actor and writes nothing: a pure
    # read, which is the only kind of service call presentation may make.
    #
    # Asked of the DOCUMENT that was submitted, not of the field that happened
    # to be rejected. It used to be asked only when ``id_document_number``
    # carried the service's own refusal, and that made the warning disappear at
    # the worst moment: a clerk refused for the duplicate types the documented
    # reason, then leaves the pledge unticked and saves again. The form is now
    # rejected by ``clean()`` BEFORE the service is reached, so no duplicate is
    # reported — the banner vanishes, the reason field is not drawn, and the
    # sentence they wrote is gone with it, silently. Nothing about the
    # duplicate had changed; only which field complained first.
    if form.is_bound:
        raw = form.data.get("id_document_type", ""), form.data.get("id_document_number", "")
        if all(raw):
            duplicates = [
                {"number": row.participant_number, "name": row.name_ar}
                for row in participant_service.find_identity_duplicates(
                    id_document_type=raw[0], id_document_number=raw[1]
                )
            ]

    # The strip across the top: one entry per card, carrying how many of its
    # own fields were rejected.
    nav_sections = [
        {
            "n": index,
            "key": key,
            "title": title,
            "errors": sum(
                1 for name in PARTICIPANT_FORM_SECTIONS[key] if form.is_bound and form[name].errors
            ),
        }
        for index, (key, title) in enumerate(PARTICIPANT_SECTION_TITLES, start=1)
    ]

    return render(
        request,
        "people/participant_new.html",
        {
            "form": form,
            "sections": PARTICIPANT_FORM_SECTIONS,
            "nav_sections": nav_sections,
            "can_create": can_create,
            "active_semester": active_semester,
            "semester_error": semester_error,
            "duplicates": duplicates,
            # The summary at the top of a page that is two screens tall: the
            # reader must not have to hunt five cards for the red.
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


@require_http_methods(["GET", "POST"])
def participant_edit_view(request: HttpRequest, number: str) -> HttpResponse:
    policy.require(request.user, Screen.STUDENTS, Action.EDIT, request=request)

    try:
        participant = participant_service.get_editable(
            actor=request.user, participant_number=number, request=request
        )
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد مشارك بهذا الرقم")) from None

    # Only the fields that exist on the model — the form also carries the
    # BR-005 override reason, which is an answer to a question, not a stored value.
    initial = {
        field: getattr(participant, field)
        for field in ParticipantEditForm.base_fields
        if hasattr(participant, field)
    }
    form = ParticipantEditForm(request.POST or None, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            participant_service.update_participant(
                actor=request.user,
                participant=participant,
                data=form.to_service_data(),
                request=request,
            )
        except DjangoValidationError as exc:
            _apply_errors(form, exc)
        else:
            messages.success(request, _("تم حفظ التعديلات"))
            return redirect("people:participant-detail", number=number)

    return render(request, "people/participant_edit.html", {"form": form, "number": number})


def _apply_errors(form: ParticipantForm, exc: DjangoValidationError) -> None:
    """Surface a service-layer ValidationError next to the field it concerns."""
    for field, messages_list in getattr(exc, "message_dict", {"__all__": [str(exc)]}).items():
        for message in messages_list:
            form.add_error(field if field in form.fields else None, message)


# ---------------------------------------------------------------------------
# Audit trail (PERMISSIONS.md row 34) — read only, D-11
# ---------------------------------------------------------------------------
@require_http_methods(["GET"])
def audit_view(request: HttpRequest) -> HttpResponse:
    """
    Browsing the audit trail.

    GET only, by decorator. BR-084 makes the log append-only for every role
    without exception, and Sprint 1 removed UPDATE and DELETE from the
    application database user — this screen simply offers nothing to try.
    """
    events = audit_query_service.browse_audit(
        actor=request.user,
        action=request.GET.get("action", "").strip(),
        actor_username=request.GET.get("actor", "").strip(),
        entity_type=request.GET.get("entity", "").strip(),
        request=request,
    )
    return render(
        request,
        "core/audit.html",
        {
            "events": events,
            "actions": audit_query_service.distinct_actions(),
            "filters": {
                "action": request.GET.get("action", ""),
                "actor": request.GET.get("actor", ""),
                "entity": request.GET.get("entity", ""),
            },
        },
    )


# ---------------------------------------------------------------------------
# Sprint 8K — the three read-only system screens the demo sidebar shows
# ---------------------------------------------------------------------------
# They live here, beside users and the audit trail, because a guarded view has
# to ask ``policy`` — and ADR-008 forbids ``apps.core`` from knowing any
# business app, the permission engine included. The Setting model stays in
# core, where infrastructure belongs; only the screen over it moves.
def settings_view(request: HttpRequest) -> HttpResponse:
    """
    What the centre can change without a release — read only, for now.

    ADR-009 · BR-086: no business constant lives in code. Every threshold, fee
    and multiplier is an effective-dated row, so changing a rule is an
    administrative act and changing it never rewrites the past — a claim
    computed in July is still read with July's settings.

    That is exactly why no edit form appears here for a SETTING. Editing a
    setting means writing a NEW value with a validity period, not overwriting
    the standing one; a plain form would quietly rewrite history and make last
    month's claim re-read with this month's rate. §3.7/35 does grant the centre
    manager EDIT on this screen — the grant is real and the screen for it is
    not built.

    Sprint 8L — the identity block IS built, and is the first thing on this
    screen a role may change. It writes a ``core.BrandAsset`` row the same way
    a setting change writes a row: dated, reasoned, superseding rather than
    overwriting. It does not rewrite the past either, and it holds a FILE,
    which is why it needed a model of its own rather than a 255-character
    setting value.

    Values are deliberately absent. They are effective-dated and read at the
    moment they are used, so printing one here would show a number that is only
    accidentally today's. The keys and what they govern are the stable part.
    """
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)

    # Sprint 8L — the two demo buttons own their own POSTs. Without this branch
    # the identity block would bind the brand form to them and answer «اختر
    # ملفاً» to a request that carried no file and never meant to.
    demo_action = request.POST.get("action", "") if request.method == "POST" else ""
    if demo_action in ("seed-demo", "reset-demo"):
        response = _run_demo_seed(request, reset=demo_action == "reset-demo")
        if response is not None:
            return response

    brand_form = _handle_brand_upload(request) if not demo_action else BrandAssetFormBlank()
    if isinstance(brand_form, HttpResponse):
        return brand_form

    groups = [
        {
            "title": _("حدود ورسوم مالية"),
            "keys": [
                ("diploma_minimum_first_payment", _("أدنى دفعة أولى للدبلوم (BR-020).")),
                ("registration_fee_center_default", _("رسم تسجيل طالب المركز (BR-009).")),
                ("registration_fee_university_default", _("رسم تسجيل الطالب الجامعي (BR-009).")),
                ("subject_repeat_fee", _("رسم إعادة المادة، ويُقسم مناصفةً (BR-037).")),
                (
                    "certificate_replacement_fee",
                    _("بدل فاقد الشهادة، وهو للمركز بالكامل (BR-038)."),
                ),
                ("money_display_dp", _("خانات عرض المبالغ؛ التخزين يبقى بثلاث خانات (Q-04).")),
            ],
        },
        {
            "title": _("مهل ودورة حياة التسجيل"),
            "keys": [
                ("transfer_lecture_limit", _("مهلة النقل بعدد المحاضرات (BR-062).")),
                ("dismissal_fail_limit", _("عدد المواد الراسبة الذي يوجب الفصل (BR-067).")),
                ("payment_overdue_days", _("متى يُعدّ المشارك متأخراً عن الدفع (Q-16 — مفتوح).")),
                ("mohe_deadline_alert_days", _("التنبيه قبل انتهاء المهلة الوزارية (BR-015).")),
            ],
        },
        {
            "title": _("الشركاء والغياب"),
            "keys": [
                ("trainer_absence_multiplier", _("مضاعف غرامة غياب المدرّس (BR-057).")),
                ("trainer_absence_replace_limit", _("عدد الغيابات الذي يجيز الاستبدال (BR-058).")),
                (
                    "partner_base_mode",
                    _("أساس احتساب الشريك: قبل الضريبة أم بعدها (Q-28 — مفتوح)."),
                ),
                ("partner_offset_scope", _("نطاق خصم التزامات الشريك (Q-08 — مفتوح).")),
            ],
        },
        {
            "title": _("الدخول والجلسة"),
            "keys": [
                ("session_idle_timeout_minutes", _("مدة الخمول التي تُنهي الجلسة (Q-12).")),
                ("login_max_failed_attempts", _("عدد المحاولات الفاشلة قبل قفل الحساب (Q-12).")),
                (
                    "login_lockout_requires_admin_unlock",
                    _("فكّ القفل بيد مدير النظام بسبب موثّق، لا تلقائياً بمرور الوقت (Q-12)."),
                ),
            ],
        },
        {
            "title": _("المشاركون والوثائق"),
            "keys": [
                (
                    "identity_document_uniqueness_mode",
                    _("تكرار وثيقة الهوية: تنبيه مع المتابعة بسبب موثّق، أو منع (BR-005)."),
                ),
                ("participant_qualifications", _("قائمة المؤهلات المعتمدة.")),
                ("participant_cities", _("قائمة المدن المعتمدة.")),
                ("certificate_grades", _("قائمة التقديرات التي تُقبل على الشهادة (BR-078).")),
                ("document_university_ar", _("اسم الجامعة في ترويسة المستندات.")),
                ("clearance_form_title_ar", _("عنوان نموذج براءة الذمة.")),
                ("certificate_title_ar", _("عنوان الشهادة.")),
            ],
        },
    ]

    open_decisions = [
        ("Q-28", _("أساس احتساب الشريك — قبل الضريبة أم بعدها؟"), "partner_base_mode"),
        ("Q-08", _("خصم التزامات الشريك: على مستوى الشريك أم الاتفاقية؟"), "partner_offset_scope"),
        ("Q-16", _("بعد كم يوم يُعدّ المشارك متأخراً عن الدفع؟"), "payment_overdue_days"),
        (
            "Q-05",
            _("تكرار وثيقة الهوية: تنبيه أم منع؟"),
            "identity_document_uniqueness_mode",
        ),
    ]

    return render(
        request,
        "core/settings.html",
        {
            "title": _("الإعدادات"),
            "active_screen": Screen.SETTINGS,
            "groups": groups,
            "open_decisions": open_decisions,
            "brand_form": brand_form,
            "brand_slots": branding_service.slot_state(as_of=date.today()),
            "may_edit_brand": policy.is_allowed(request.user, Screen.SETTINGS, Action.EDIT),
            # بيانات العرض: الاسم يُعرَض ليعرف من يضغط على أي قاعدة يضغط،
            # والمحو يُعرَض فقط إن كانت قاعدةَ عرض — فزرٌّ استعماله مرفوض لا
            # يُرسَم (قواعد التحسين §3.4 · BR-085).
            "demo_database": connection.settings_dict["NAME"],
            "demo_resettable": connection.settings_dict["NAME"] in DEMO_DATABASES,
        },
    )


def _handle_brand_upload(request: HttpRequest) -> Any:
    """
    The identity block's POST, or a blank form for the page to draw.

    Kept beside the screen rather than inside ``branding_service`` because the
    permission question is the SCREEN's: core may not ask ``policy`` (A-03),
    and the matrix cell being spent here is §3.7/35's EDIT on this screen.
    """
    from apps.core.forms import BrandAssetForm

    if request.method != "POST":
        return BrandAssetForm()

    policy.require(request.user, Screen.SETTINGS, Action.EDIT, request=request)
    form = BrandAssetForm(request.POST, request.FILES)
    if not form.is_valid():
        return form

    try:
        branding_service.set_asset(
            actor=request.user,
            slot=form.cleaned_data["slot"],
            upload=form.cleaned_data["file"],
            effective_from=date.today(),
            note=form.cleaned_data["note"],
            alt_text_ar=form.cleaned_data["alt_text_ar"],
            request=request,
        )
    except DjangoValidationError as refusal:
        form.add_error("file", refusal)
        return form

    messages.success(request, _("حُدِّثت هوية المؤسسة. الشعار الجديد يظهر في الشاشات والمستندات."))
    return redirect("people:settings")


def BrandAssetFormBlank() -> Any:  # noqa: N802 - a factory named like the class it returns
    """An unbound identity form, for a POST that was not the identity block's."""
    from apps.core.forms import BrandAssetForm

    return BrandAssetForm()


def _run_demo_seed(request: HttpRequest, *, reset: bool) -> HttpResponse | None:
    """
    The two demo buttons, behind the settings EDIT cell.

    **Why a screen at all.** The seeds are management commands, and whoever
    stands in front of the client half an hour before a demo does not open a
    terminal. The commands stay the implementation — this only calls them, so
    there is one seeding road and not two that drift.

    **The refusal is the command's, not this view's.** ``seed_demo_all``
    refuses ``--reset`` on any database not named in ``DEMO_DATABASES``, and
    that refusal arrives here as ``CommandError`` and is shown. Re-deciding it
    here would be a second rule to keep in step with the first.

    **A reset signs the reader out.** ``flush`` truncates the session table and
    the user rows with it, so the redirect lands on the login page and the
    rebuilt accounts carry the seed's password. The dialog says so before it
    happens; this is the part a confirmation exists for.
    """
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    policy.require(request.user, Screen.SETTINGS, Action.EDIT, request=request)

    database = connection.settings_dict["NAME"]
    if reset and request.POST.get("confirm", "").strip() != database:
        messages.error(
            request,
            _("لم يُطابق اسم القاعدة — لم يُمسّ شيء. اكتب %(name)s للتأكيد.")
            % {"name": database},
        )
        return redirect("people:settings")

    out = StringIO()
    try:
        if reset:
            call_command("seed_demo_all", "--reset", "--noinput", stdout=out, stderr=out)
        else:
            call_command("seed_demo_all", stdout=out, stderr=out)
    except CommandError as refused:
        messages.error(request, str(refused))
        return redirect("people:settings")
    except Exception as failed:  # pragma: no cover - a seed that broke mid-way
        messages.error(
            request,
            _("تعثّر الزرع: %(why)s — والمخرَج في الطرفية أوضح، شغّله منها.")
            % {"why": failed},
        )
        return redirect("people:settings")

    # The command's own tally is the report: it names every empty register, and
    # an empty register is an empty screen in front of the client.
    empty = [line.strip() for line in out.getvalue().splitlines() if "سجلات فارغة" in line]
    what = _("إعادة بناء بيانات العرض") if reset else _("ملء بيانات العرض")
    messages.success(request, _("تمّ %(what)s.") % {"what": what})
    if empty:
        messages.warning(request, empty[0])
    if reset:
        messages.info(request, _("أُعيد بناء الحسابات — سجّل الدخول من جديد."))
    return redirect("people:settings")


#: The four ways a demo promise can be kept, and the chip each earns. Kept as
#: data so the legend on the page and the rows in it cannot drift apart.
_DONE = ("منفذ", "ok")
_GUIDED = ("إرشادي", "brand")
_READONLY = ("قراءة فقط", "info")
_PENDING = ("بانتظار قرار", "warn")
_FUTURE = ("نطاق مستقبلي", "")

_OPERATIONAL = "تشغيلية"
_INFORMATIONAL = "إرشادية"
_READ = "قراءة فقط"

#: (demo sidebar label, route, kind, status, note) — in the demo's own order,
#: js/app.js ``const NAV`` flattened. All 36, none omitted and none invented.
_COVERAGE_ROWS: tuple[tuple[str, str, str, tuple[str, str], str], ...] = (
    (
        "لوحة المؤشرات",
        "operations:dashboard",
        _OPERATIONAL,
        _DONE,
        "مؤشرات تُقرأ من الحركة الفعلية، ولكل دور منها ما تسمح به صلاحيته.",
    ),
    (
        "مسار التسجيل والدفع",
        "operations:enroll-flow",
        _INFORMATIONAL,
        _GUIDED,
        "ثماني مراحل تشرح الرحلة وتربط بالشاشات التي تنفّذها؛ لا يُنفَّذ من الصفحة شيء.",
    ),
    (
        "المشاركون",
        "people:participants",
        _OPERATIONAL,
        _DONE,
        "بحث وإضافة وتعديل، والرقم الجامعي يُولَّد بقاعدته ولا يُصحَّح لاحقاً (BR-001).",
    ),
    (
        "طلب التحاق جديد",
        "people:participant-new",
        _OPERATIONAL,
        _DONE,
        "نموذج الالتحاق بفئات المشاركين الثلاث.",
    ),
    (
        "التسجيلات",
        "operations:enrollments",
        _OPERATIONAL,
        _DONE,
        "إنشاء واعتماد، خلف بوابتَي اعتماد الوزارة وتسجيل الوصل (BR-013 · BR-018).",
    ),
    (
        "النقل بين الدورات",
        "operations:transfers",
        _OPERATIONAL,
        _DONE,
        "طلب وفحص شروط وتنفيذ؛ المال ينتقل بقيد عكسي لا بتعديل (BR-060 … BR-066).",
    ),
    (
        "الحالات الخاصة",
        "operations:special-cases",
        _INFORMATIONAL,
        _GUIDED,
        "القواعد والخدمات منفّذة (BR-067 … BR-071)، والصفحة تشرح الأنواع الستة. "
        "شاشة الإدخال التفصيلية غير مبنية بعد.",
    ),
    (
        "الدبلومات التدريبية",
        "catalog:programs",
        _OPERATIONAL,
        _DONE,
        "بناء الدبلومات وموادها وربطها بأسعارها.",
    ),
    (
        "الدورات القصيرة",
        "catalog:short-courses",
        _OPERATIONAL,
        _DONE,
        "كتالوج الدورات القصيرة وأسعارها.",
    ),
    (
        "الدورات الأونلاين",
        "catalog:online-courses",
        _OPERATIONAL,
        _DONE,
        "كتالوج الدورات الأونلاين وأسعارها.",
    ),
    (
        "الدفعات المُشغّلة",
        "operations:cohorts",
        _OPERATIONAL,
        _DONE,
        "فتح الدفعات وربطها بالبرنامج والفصل.",
    ),
    (
        "قوائم الأسعار المؤرّخة",
        "catalog:pricelists",
        _OPERATIONAL,
        _DONE,
        "قوائم بتواريخ سريان؛ والمعتمدة منها لا تُعدَّل (BR-008).",
    ),
    (
        "اعتماد الوزارة",
        "operations:mohe",
        _OPERATIONAL,
        _DONE,
        "ملف الوزارة، وهو البوابة التي لا يمر التسجيل قبلها (BR-013 … BR-016).",
    ),
    (
        "الدفعات وسندات القبض",
        "cashbox:payments",
        _OPERATIONAL,
        _DONE,
        "السندات وتخصيصاتها؛ والإلغاء يكتب قيداً عكسياً ولا يحذف (BR-025).",
    ),
    (
        "استيفاء دفعة",
        "cashbox:payment-new",
        _OPERATIONAL,
        _DONE,
        "القبض والتوزيع المخزَّن، وحدّ الدفعة الأولى للدبلوم (BR-020 · BR-022).",
    ),
    (
        "الإقفال اليومي",
        "cashbox:closing",
        _OPERATIONAL,
        _DONE,
        "العدّ والتسوية، ومن قبض لا يعتمد العدّ (BR-027 · BR-028).",
    ),
    (
        "الخصومات",
        "billing:discounts",
        _OPERATIONAL,
        _DONE,
        "إنشاء واعتماد بمرجع موافقة مسجَّل (BR-030).",
    ),
    (
        "الاستردادات",
        "billing:refunds",
        _OPERATIONAL,
        _DONE,
        "طلب وتنفيذ، ومن ينفّذ الاسترداد لا يعتمده (BR-034).",
    ),
    (
        "الرسوم الإضافية",
        "billing:extra-fees",
        _OPERATIONAL,
        _DONE,
        "رسم إعادة المادة وبدل فاقد الشهادة وما شابههما (BR-037 · BR-038).",
    ),
    ("المصروفات", "expenses:expenses", _OPERATIONAL, _DONE, "تسجيل المصروفات واعتمادها."),
    (
        "الشركاء المتعاقدون",
        "partners:partners",
        _OPERATIONAL,
        _DONE,
        "سجل الشركاء؛ و«شريك جديد» زرّ على الشاشة نفسها.",
    ),
    (
        "الاتفاقيات",
        "partners:agreements",
        _OPERATIONAL,
        _DONE,
        "الاتفاقيات ولقطات أسعارها؛ والموقّعة منها لا تُعدَّل (BR-042).",
    ),
    (
        "محرّر اتفاقية",
        "partners:agreement-new",
        _OPERATIONAL,
        _DONE,
        "تسجيل اتفاقية موقّعة بنموذج احتسابها واستثناءاتها.",
    ),
    (
        "استحقاق الشركاء",
        "settlements:entitlement",
        _INFORMATIONAL,
        _GUIDED,
        "صفحة دليل تشرح السلسلة كاملة؛ والحساب الفعلي يجري على شاشة المطالبات. "
        "أساس الاحتساب — قبل الضريبة أم بعدها — فرضية مسجَّلة (Q-28).",
    ),
    (
        "المطالبات",
        "settlements:claims",
        _OPERATIONAL,
        _DONE,
        "بناء المطالبة واعتمادها؛ والمعتمدة لا يعدّلها أحد (BR-051).",
    ),
    (
        "المخالصات",
        "settlements:settlements",
        _OPERATIONAL,
        _DONE,
        "المخالصة والتوقيع النهائي على الفترة.",
    ),
    (
        "التزامات الشركاء",
        "settlements:obligations",
        _OPERATIONAL,
        _PENDING,
        "الشاشة تعمل وتخصم من مطالبة لاحقة (BR-036). نطاق الخصم — على مستوى الشريك "
        "أم الاتفاقية — فرضية مسجَّلة بانتظار قرار العميل (Q-08).",
    ),
    (
        "براءة الذمة",
        "operations:clearances",
        _OPERATIONAL,
        _DONE,
        "ثلاث خطوات وتوقيعان، والرصيد صفر في الاتجاهين (BR-073 · BR-074).",
    ),
    (
        "الشهادات",
        "operations:certificates",
        _OPERATIONAL,
        _DONE,
        "لا شهادة بلا براءة ذمة مكتملة (BR-075)، والتقدير من قائمة معتمدة (BR-078).",
    ),
    ("التقارير", "reporting:reports", _READ, _DONE, "سبعة تقارير، ولكل دور ما يُسمح له منها."),
    ("المستخدمون والصلاحيات", "people:users", _OPERATIONAL, _DONE, "إدارة الحسابات والأدوار."),
    (
        "سجل التدقيق",
        "people:audit",
        _READ,
        _DONE,
        "مكتمل، وقراءةٌ فقط بحكم القاعدة: لا يعدّله أحد ولا يحذفه (BR-084).",
    ),
    (
        "الإعدادات",
        "people:settings",
        _READ,
        _READONLY,
        "تعرض المفاتيح والقرارات المفتوحة. التعديل يعني كتابة قيمة جديدة بتاريخ "
        "سريان (BR-086)، وشاشته لم تُبنَ في هذه المرحلة.",
    ),
    (
        "ترحيل البيانات",
        "datamigration:batches",
        _OPERATIONAL,
        _DONE,
        "استيراد وتدقيق وأرشفة، معزولة عن الدفتر المالي بحكم معماري (A-04).",
    ),
    ("مصفوفة تغطية المتطلبات", "people:coverage", _READ, _DONE, "هذه الصفحة."),
    (
        "النطاق المستقبلي",
        "people:future",
        _READ,
        _DONE,
        "تُوثّق البنود خارج النطاق الحالي وسبب تأجيل كل واحد منها.",
    ),
)

#: Screens this system has and the demo's sidebar never showed. Kept apart from
#: the parity table on purpose: mixing them in would inflate the coverage count
#: with things nobody asked to see covered.
_EXTRA_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "نموذج الإرسال للوزارة",
        "operations:mohe-submit",
        "إضافة في النظام الحقيقي",
        "موظف التسجيل يجهّز الملف ومدير المركز يرسله (§3.3/15).",
    ),
    (
        "الأرصدة الافتتاحية",
        "billing:opening-balances",
        "إضافة في النظام الحقيقي",
        "أرصدة ما قبل النظام: إنشاء ثم مراجعة من شخص آخر ثم اعتماد (BR-094).",
    ),
    (
        "غيابات المدربين",
        "settlements:absences",
        "إضافة في النظام الحقيقي",
        "غرامة غياب المدرّس واستثناؤها الخطي (BR-057 · BR-058).",
    ),
    (
        "ربط السجلات التاريخية",
        "datamigration:links",
        "إضافة في النظام الحقيقي",
        "ربط اسم مؤرشف بمشارك قائم — حكم هوية بيد موظف التسجيل.",
    ),
    (
        "طلب نقل جديد",
        "operations:transfer-new",
        "تُفتح من شاشتها الأم",
        "خارج القائمة الجانبية مطابقةً للديمو؛ تُفتح بزرّ على شاشة النقل.",
    ),
    (
        "شريك جديد",
        "partners:partner-new",
        "تُفتح من شاشتها الأم",
        "خارج القائمة الجانبية مطابقةً للديمو؛ تُفتح بزرّ على شاشة الشركاء.",
    ),
)


def coverage_view(request: HttpRequest) -> HttpResponse:
    """
    The delivery-review page: every demo sidebar promise against its real address.

    All 36 entries of the demo's own ``NAV`` are listed in the demo's own order,
    and each names the route that answers it. ``reverse()`` runs on every one of
    them, so a renamed or deleted route breaks this page loudly instead of
    leaving a claim on screen that is no longer true.

    **The honest headline is that nothing is missing.** No sidebar item resolves
    to a future-scope placeholder — three resolve to guided pages rather than
    data-entry forms, and those three say so in their own words and here. What
    IS deferred is features inside screens, and that belongs on النطاق المستقبلي,
    not to be smuggled into this count.
    """
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)

    # The constants above are data; ``_`` here is the non-lazy gettext this
    # module imports, so the wording is resolved per request rather than frozen
    # at import time.
    rows = [
        {
            "demo": _(demo),
            "path": reverse(route),
            "kind": _(kind),
            "status": _(status[0]),
            "chip": status[1],
            "note": _(note),
        }
        for demo, route, kind, status, note in _COVERAGE_ROWS
    ]
    extras = [
        {"screen": _(screen), "path": reverse(route), "tag": _(tag), "note": _(note)}
        for screen, route, tag, note in _EXTRA_ROWS
    ]

    counts = [
        (_(label), chip, sum(1 for r in rows if r["status"] == _(label)))
        for label, chip in (_DONE, _GUIDED, _READONLY, _PENDING, _FUTURE)
    ]

    return render(
        request,
        "core/coverage.html",
        {
            "title": _("مصفوفة تغطية المتطلبات"),
            "active_screen": Screen.SETTINGS,
            "rows": rows,
            "extras": extras,
            "counts": counts,
            "total": len(rows),
        },
    )


def future_view(request: HttpRequest) -> HttpResponse:
    """
    Documented scope that is not built — so it is not read as something missing.

    Every row is grounded: README §2.4 carries the demo's own avoid/defer table,
    and the rest come from the rules that say so out loud — BR-078 on grades,
    BR-086 on settings, Q-13 on attachments. Nothing here is a defect and
    nothing here carries a date; the four open questions are decisions waiting
    on the client, each reversible by a setting rather than a migration.
    """
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)

    deferred = [
        {
            "item": _("تنزيل المرفقات"),
            "today": _("المرفق يُرفع وتُحسب بصمته ويُعرض اسمه وحجمه."),
            "why": _(
                "سياسة الاحتفاظ وحدود الحجم وفحص الفيروسات وصلاحية التنزيل سؤال "
                "قائم بذاته (Q-13)، ولم يُفتح رابط تنزيل قبل أن يُجاب."
            ),
        },
        {
            "item": _("منع التسجيل بعد المهلة"),
            "today": _("المهلة تُعرض على الشاشة (BR-019)."),
            "why": _("المنع الفعلي بها على الأسماء الجديدة لم يُفعَّل بعد."),
        },
        {
            "item": _("الدفعات الجزئية للشركاء"),
            "today": _("المخالصة تتم على الفترة كاملة."),
            "why": _("خارج النطاق المعتمد حتى الآن."),
        },
        {
            "item": _("إعادة تصميم الإقفال اليومي"),
            "today": _("الإقفال يعمل بقواعده الحالية (BR-026 … BR-028)."),
            "why": _("إعادة التصميم خارج النطاق المعتمد حتى الآن."),
        },
        {
            "item": _("تعديل اتفاقية سارية أو إلغاؤها"),
            "today": _("التصحيح يكون بملحق يحلّ محلّ الاتفاقية."),
            "why": _(
                "قرار تصميم لا نقص: تعديل اتفاقية موقّعة يغيّر أساس مطالبات "
                "قد تكون خُتمت بتوقيع (BR-042)."
            ),
        },
        {
            "item": _("وحدة العلامات والامتحانات"),
            "today": _("التقدير يُدخله مُصدر الشهادة ويُتحقق من قائمة معتمدة."),
            "why": _("لا وحدة علامات ولا كيان امتحانات في هذا النطاق (BR-078)."),
        },
        {
            "item": _("وحدة الحضور"),
            "today": _("عدّاد المحاضرات مصدره الإدخال اليدوي."),
            "why": _("النموذج يعرف مصدراً ثانياً «من وحدة الحضور» لم يُبنَ بعد."),
        },
        {
            "item": _("شاشة تعديل الإعدادات"),
            "today": _("الإعدادات تُقرأ من الشاشة وتُزرع بأمر إداري."),
            "why": _(
                "التعديل يجب أن يُنشئ قيمة جديدة بتاريخ سريان لا أن يستبدل القائمة "
                "(BR-086)، وهذه شاشة تُبنى بقواعدها لا بحقل نصّي."
            ),
        },
        {
            "item": _("التكامل التقني مع نظام الوزارة"),
            "today": _("رفع الأسماء إلى نظام الوزارة إدخال يدوي مزدوج."),
            "why": _("لا يوجد تكامل تقني مباشر في هذا النطاق؛ الإدخال اليدوي مقصود."),
        },
    ]

    open_questions = [
        ("Q-28", _("أساس احتساب الشريك: قبل الضريبة أم بعدها؟")),
        ("Q-08", _("خصم التزامات الشريك: على مستوى الشريك أم الاتفاقية؟")),
        ("Q-09", _("الدفعة المقدّمة للشريك: كيف تُسترد عن غير المؤهّلين؟")),
        ("Q-16", _("بعد كم يوم يُعدّ المشارك متأخراً عن الدفع؟")),
    ]

    return render(
        request,
        "core/future.html",
        {
            "title": _("النطاق المستقبلي"),
            "active_screen": Screen.SETTINGS,
            "deferred": deferred,
            "open_questions": open_questions,
        },
    )


# ---------------------------------------------------------------------------
# الفصل الدراسي والفترة المالية — أوّل خطوتين في النظام، وكانتا بلا شاشة
# ---------------------------------------------------------------------------
def _refusal_text(exc: Exception) -> str:
    """A service refusal as one readable line, whatever shape it arrived in."""
    detail = getattr(exc, "messages", None)
    return " · ".join(str(message) for message in detail) if detail else str(exc)


def semesters_view(request: HttpRequest) -> HttpResponse:
    """
    The academic semesters, and the act that names the current one.

    **The first step in the whole system, and it had no screen.** BR-001 builds
    every participant number out of the ACTIVE semester's year and type digit,
    ``participant_numbering.active_semester`` raises when none is flagged, and
    the refusal the registrar meets says «عرّف الفصل الحالي أولاً» — pointing at
    a page that did not exist. A fresh install therefore could not register one
    participant until somebody ran ``manage.py seed_semester`` or opened the
    Django admin, which writes past the audit trail.

    The SETTINGS permission, not a new matrix row — ``semester_service`` says
    why at length. Everything that writes asks for ``EDIT``; the finance
    officer and the audit account read.
    """
    screen = semester_service.SCREEN
    policy.require(request.user, screen, Action.VIEW, request=request)

    can_edit = policy.is_allowed(request.user, screen, Action.EDIT)
    types = semester_service.semester_type_choices()

    posted = request.method == "POST"
    action = request.POST.get("action", "create") if posted else ""
    # Bound only for the act that owns it: on a GET ``request.POST`` is an empty
    # QueryDict — data, not nothing — and binding it would report every required
    # field missing before the reader had typed a character.
    form = SemesterForm(
        request.POST if action == "create" else None, type_choices=types
    )
    edit_form = SemesterForm(
        request.POST if action == "edit" else None, type_choices=types, editing=True
    )
    editing_code = request.POST.get("code", "") if action == "edit" else ""

    if posted:
        policy.require(request.user, screen, Action.EDIT, request=request)
        response = _semester_post(request, action, form, edit_form, editing_code)
        if response is not None:
            return response

    rows = semester_service.semester_rows(actor=request.user, request=request)
    current = next((row for row in rows if row["is_active"]), None)
    page = page_of(rows, request.GET.get("page", ""))
    return render(
        request,
        "people/semesters.html",
        {
            "title": _("الفصول الدراسية"),
            "active_screen": screen,
            "rows": page["rows"],
            "page": page,
            "current": current,
            "tiles": [
                {
                    "label": _("فصول معرَّفة"),
                    "count": len(rows),
                    "tone": "brand",
                    "icon": "calendar",
                },
                {
                    "label": _("أرقام صدرت"),
                    "count": sum(row["numbers"] + row["center_numbers"] for row in rows),
                    "tone": "info",
                    "icon": "users",
                },
                {
                    "label": _("بتواريخ متقاطعة"),
                    "count": sum(1 for row in rows if row["overlaps"]),
                    "tone": "amber",
                    "icon": "alert",
                },
            ],
            "form": form,
            "edit_form": edit_form,
            "editing_code": editing_code,
            "can_edit": can_edit,
            "posted_action": action,
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


def _semester_post(
    request: HttpRequest,
    action: str,
    form: SemesterForm,
    edit_form: SemesterForm,
    editing_code: str,
) -> HttpResponse | None:
    """The three acts this screen carries. ``None`` means «re-render as is»."""
    if action == "activate":
        return _activate_semester(request)
    if action == "edit":
        if not edit_form.is_valid():
            messages.error(request, _("راجع حقول الفصل."))
            return None
        return _update_semester(request, editing_code, edit_form)
    if not form.is_valid():
        messages.error(request, _("راجع حقول الفصل."))
        return None
    return _create_semester(request, form)


def _create_semester(request: HttpRequest, form: SemesterForm) -> HttpResponse | None:
    from django.db import IntegrityError

    data = dict(form.cleaned_data)
    data["type_code"] = int(data["type_code"])
    try:
        semester = semester_service.create_semester(
            actor=request.user, data=data, request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _refusal_text(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لفصل آخر — اختر رمزاً غيره.")
            % {"code": data.get("code", "")},
        )
        return None

    messages.success(
        request,
        _("أُضيف الفصل %(name)s%(current)s")
        % {
            "name": semester.name_ar,
            "current": _(" — وهو الفصل الحالي الآن.") if semester.is_active else "",
        },
    )
    return redirect("people:semesters")


def _update_semester(
    request: HttpRequest, code: str, form: SemesterForm
) -> HttpResponse | None:
    data = dict(form.cleaned_data)
    data["type_code"] = int(data["type_code"])
    try:
        semester = semester_service.semester_instance(
            actor=request.user, code=code, request=request
        )
        semester_service.update_semester(
            actor=request.user, semester=semester, data=data, request=request
        )
    except ObjectDoesNotExist:
        raise Http404("لا فصل بهذا الرمز") from None
    except DjangoValidationError as exc:
        messages.error(request, _refusal_text(exc))
        return None

    messages.success(request, _("حُفظت تعديلات الفصل %(code)s") % {"code": code})
    return redirect("people:semesters")


def _activate_semester(request: HttpRequest) -> HttpResponse | None:
    code = request.POST.get("code", "")
    try:
        semester = semester_service.semester_instance(
            actor=request.user, code=code, request=request
        )
        semester_service.activate_semester(
            actor=request.user, semester=semester, request=request
        )
    except ObjectDoesNotExist:
        raise Http404("لا فصل بهذا الرمز") from None
    except DjangoValidationError as exc:
        messages.error(request, _refusal_text(exc))
        return None

    messages.success(
        request,
        _("صار %(name)s هو الفصل الحالي — وأرقام المشاركين الجديدة تُبنى منه.")
        % {"name": semester.name_ar},
    )
    return redirect("people:semesters")


def financial_periods_view(request: HttpRequest) -> HttpResponse:
    """
    The financial periods, and the act that signs one off (D-23).

    **The guard existed and had nothing to guard.** Nine money movements ask
    ``period_service.require_open`` whether their date falls in a closed
    period; no screen could open a period or close one, so on a live database
    every month stays open forever and a backdated receipt walks into a month
    whose report was already issued. The admin was the only road, and it leaves
    no audit row behind.

    Re-opening is deliberately absent — ``fiscal_period_service`` says why. The
    confirmation therefore names what the close is about to refuse, because
    afterwards is too late to ask.
    """
    screen = fiscal_period_service.SCREEN
    policy.require(request.user, screen, Action.VIEW, request=request)

    can_edit = policy.is_allowed(request.user, screen, Action.EDIT)

    posted = request.method == "POST"
    action = request.POST.get("action", "create") if posted else ""
    suggested = fiscal_period_service.suggested_range()
    form = FinancialPeriodForm(
        request.POST if action == "create" else None, initial=suggested
    )

    if posted:
        policy.require(request.user, screen, Action.EDIT, request=request)
        if action == "close":
            response = _close_financial_period(request)
        elif form.is_valid():
            response = _create_financial_period(request, form)
        else:
            messages.error(request, _("راجع تاريخي الفترة."))
            response = None
        if response is not None:
            return response

    rows = fiscal_period_service.period_rows(actor=request.user, request=request)
    page = page_of(rows, request.GET.get("page", ""))
    return render(
        request,
        "people/financial_periods.html",
        {
            "title": _("الفترات المالية"),
            "active_screen": screen,
            "rows": page["rows"],
            "page": page,
            "today_covered": fiscal_period_service.today_is_covered(
                actor=request.user, request=request
            ),
            "refuses": fiscal_period_service.REFUSES,
            "tiles": [
                {
                    "label": _("مفتوحة"),
                    "count": sum(1 for row in rows if row["is_open"]),
                    "tone": "ok",
                    "icon": "calendar",
                },
                {
                    "label": _("مقفلة"),
                    "count": sum(1 for row in rows if not row["is_open"]),
                    "tone": "info",
                    "icon": "vault",
                },
                {
                    "label": _("أيام غير مغطّاة"),
                    "count": sum(1 for row in rows if row["gap_before"]),
                    "tone": "amber",
                    "icon": "alert",
                },
            ],
            "form": form,
            "can_edit": can_edit,
            "posted_action": action,
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


def _create_financial_period(
    request: HttpRequest, form: FinancialPeriodForm
) -> HttpResponse | None:
    from django.db import IntegrityError

    try:
        period = fiscal_period_service.create_period(
            actor=request.user, data=dict(form.cleaned_data), request=request
        )
    except DjangoValidationError as exc:
        # Field errors land beside their field; anything else becomes a message.
        if getattr(exc, "error_dict", None):
            for field, errors in exc.message_dict.items():
                for error in errors:
                    form.add_error(field if field in form.fields else None, error)
        else:
            messages.error(request, _refusal_text(exc))
        return None
    except IntegrityError:
        messages.error(request, _("فترةٌ أخرى تبدأ في اليوم نفسه — اختر تاريخ بدايةٍ غيره."))
        return None

    messages.success(
        request,
        _("فُتحت الفترة %(from)s — %(to)s")
        % {"from": period.starts_on, "to": period.ends_on},
    )
    return redirect("people:financial-periods")


def _close_financial_period(request: HttpRequest) -> HttpResponse | None:
    try:
        period = fiscal_period_service.period_instance(
            actor=request.user, period_id=request.POST.get("period", "0"), request=request
        )
        fiscal_period_service.close_period(
            actor=request.user, period=period, request=request
        )
    except (ObjectDoesNotExist, ValueError):
        raise Http404("لا فترة مالية بهذا المعرّف") from None
    except DjangoValidationError as exc:
        messages.error(request, _refusal_text(exc))
        return None

    messages.success(
        request,
        _("أُقفلت الفترة %(from)s — %(to)s، ولا تُقيَّد فيها حركة مالية بعد الآن (D-23).")
        % {"from": period.starts_on, "to": period.ends_on},
    )
    return redirect("people:financial-periods")
