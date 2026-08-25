"""
Authentication and user-administration views (Q-12, Δ-02).

Views render and delegate. Every permission question is answered by
``permissions.policy.require`` — never by an ``if user.role ==`` in here, and
never by hiding a button in a template. A-05 keeps this honest: views may not
import models directly.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.people.constants import (
    PARTICIPANT_CATEGORY_CHOICES,
    ROLE_CHOICES,
    Action,
    Screen,
)
from apps.people.forms import LoginForm
from apps.people.participant_forms import ParticipantEditForm, ParticipantForm
from apps.people.permissions import policy
from apps.people.services import (
    audit_query_service,
    auth_service,
    participant_service,
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
def participants_view(request: HttpRequest) -> HttpResponse:
    """
    The participants list.

    Rows arrive already projected to the caller's permitted field set — a
    restricted role's forbidden fields are absent from the response, not merely
    unrendered (BR-101, T-283).
    """
    rows = participant_service.list_participants(
        actor=request.user,
        query=request.GET.get("q", "").strip(),
        category=request.GET.get("category", "").strip(),
        request=request,
    )
    return render(
        request,
        "people/participants.html",
        {
            "participants": rows,
            "columns": participant_service.visible_fields_for(request.user),
            "categories": PARTICIPANT_CATEGORY_CHOICES,
            "query": request.GET.get("q", ""),
            "selected_category": request.GET.get("category", ""),
            "can_create": policy.is_allowed(request.user, Screen.STUDENT_NEW, Action.CREATE),
            "can_edit": policy.is_allowed(request.user, Screen.STUDENTS, Action.EDIT),
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
    return render(
        request,
        "people/participant_detail.html",
        {
            "rows": rows,
            "number": values["participant_number"],
            "name_ar": values["name_ar"],
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
        "duplicate_override_reason",
    ],
    "contact": ["city", "phone", "po_box", "email"],
    "background": ["qualification", "employer"],
    "consent": [
        "is_exempt",
        "exemption_approval_ref",
        "exemption_approval_date",
        "no_refund_pledge_accepted",
    ],
}


@require_http_methods(["GET", "POST"])
def participant_new_view(request: HttpRequest) -> HttpResponse:
    policy.require(request.user, Screen.STUDENT_NEW, Action.VIEW, request=request)

    form = ParticipantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
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

    return render(
        request,
        "people/participant_new.html",
        {"form": form, "sections": PARTICIPANT_FORM_SECTIONS},
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

    That is exactly why no edit form appears here. Editing a setting means
    writing a NEW value with a validity period, not overwriting the standing
    one; a plain form would quietly rewrite history and make last month's claim
    re-read with this month's rate. §3.7/35 does grant the centre manager EDIT
    on this screen — the grant is real and the screen for it is not built.

    Values are deliberately absent. They are effective-dated and read at the
    moment they are used, so printing one here would show a number that is only
    accidentally today's. The keys and what they govern are the stable part.
    """
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)

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
        },
    )


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
