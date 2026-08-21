"""
Operational screens — cohorts, enrolments, and the landing dashboard.

Views render and delegate. Every permission question goes through
``policy.require``; nothing here decides anything by inspecting a role, and
A-05 keeps models out of this file entirely — every row on every screen comes
from a service function that projected it.

**Refusals are shown, not translated.** A service that refuses says why, with
the rule reference in the message (BR-013, BR-018, BR-020). These views catch
the exception and put that message on the screen unchanged. A friendlier
paraphrase would drop the reference the centre needs in order to act.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.billing.services import account_service
from apps.billing.services.account_service import ZERO
from apps.operations.forms import (
    CertificateDateForm,
    CertificateIssueForm,
    ClearanceCancelForm,
    ClearanceOpenForm,
    CohortForm,
    CreditReturnAtClearanceForm,
    CustodyForm,
    DepositSettlementForm,
    EnrollmentForm,
)
from apps.operations.services import (
    certificate_service,
    clearance_service,
    cohort_service,
    enrollment_service,
)
from apps.partners.services import partner_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.people.services import participant_service


def _message_of(exc: Exception) -> str:
    """The service's own words, flattened out of Django's error containers."""
    detail = getattr(exc, "messages", None)
    if detail:
        return " · ".join(str(m) for m in detail)
    return str(exc)


# ---------------------------------------------------------------------------
# Dashboard — the landing page after sign-in
# ---------------------------------------------------------------------------
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """
    Counters and nothing more.

    Deliberately NOT a report: §9's seven reports are Sprint 8C, and a
    dashboard that started answering "net income after partner shares" would
    be one of them wearing a different name.
    """
    policy.require(request.user, Screen.DASHBOARD, Action.VIEW, request=request)

    counts: dict[str, int] = {}
    if policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.VIEW):
        rows = enrollment_service.list_enrollments(actor=request.user, request=request)
        counts["enrollments"] = len(rows)
        counts["unsettled"] = sum(1 for r in rows if not r["is_settled"])
        counts["awaiting_voucher"] = sum(1 for r in rows if not r["voucher_received"])
    if policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW):
        counts["cohorts"] = len(cohort_service.list_cohorts(actor=request.user, request=request))

    return render(
        request,
        "operations/dashboard.html",
        {"title": _("لوحة المؤشرات"), "active_screen": Screen.DASHBOARD, "counts": counts},
    )


# ---------------------------------------------------------------------------
# Cohorts (Screen.COHORTS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def cohorts_view(request: HttpRequest) -> HttpResponse:
    rows = cohort_service.list_cohorts(
        actor=request.user,
        query=request.GET.get("q", "").strip(),
        status=request.GET.get("status", "").strip(),
        request=request,
    )
    can_create = policy.is_allowed(request.user, Screen.COHORTS, Action.CREATE)
    form = None
    if can_create:
        form = CohortForm(
            request.POST or None,
            program_choices=cohort_service.program_choices(actor=request.user, request=request),
            semester_choices=cohort_service.semester_choices(actor=request.user, request=request),
            agreement_choices=_agreement_choices(request),
        )

    if request.method == "POST":
        if form is None:
            raise PermissionDenied
        if form.is_valid():
            try:
                cohort_service.open_cohort(actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, _("فُتحت الدفعة"))
                return redirect("operations:cohorts")
            except DjangoValidationError as exc:
                messages.error(request, _message_of(exc))

    return render(
        request,
        "operations/cohorts.html",
        {
            "title": _("الدفعات المُشغّلة"),
            "active_screen": Screen.COHORTS,
            "cohorts": rows,
            "form": form,
            "can_create": can_create,
            "query": request.GET.get("q", ""),
        },
    )


def _agreement_choices(request: HttpRequest) -> list[tuple[str, str]]:
    """Empty rather than absent when the role may not see agreements."""
    if not policy.is_allowed(request.user, Screen.AGREEMENTS, Action.VIEW):
        return []
    return partner_service.agreement_choices(actor=request.user, request=request)


# ---------------------------------------------------------------------------
# Enrolments (Screen.ENROLLMENTS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def enrollments_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.CREATE)
    form = None
    if can_create:
        form = EnrollmentForm(
            request.POST or None,
            cohort_choices=cohort_service.cohort_choices(actor=request.user, request=request)
            if policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW)
            else [],
        )

    if request.method == "POST":
        if form is None:
            raise PermissionDenied
        if form.is_valid():
            response = _create_enrollment(request, form)
            if response is not None:
                return response

    rows = enrollment_service.list_enrollments(
        actor=request.user,
        query=request.GET.get("q", "").strip(),
        cohort_code=request.GET.get("cohort", "").strip(),
        status=request.GET.get("status", "").strip(),
        request=request,
    )
    return render(
        request,
        "operations/enrollments.html",
        {
            "title": _("التسجيلات"),
            "active_screen": Screen.ENROLLMENTS,
            "enrollments": rows,
            "form": form,
            "can_create": can_create,
            "can_edit": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.EDIT),
            "can_approve": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.APPROVE),
            "query": request.GET.get("q", ""),
        },
    )


def _create_enrollment(request: HttpRequest, form: EnrollmentForm) -> HttpResponse | None:
    data = form.cleaned_data
    try:
        participant = participant_service.participant_instance(
            actor=request.user, participant_number=data["participant_number"], request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا يوجد مشارك بهذا الرقم"))
        return None

    try:
        cohort = cohort_service.get_cohort_instance(
            actor=request.user, code=data["cohort_code"], request=request
        )
        enrollment_service.enroll_with_charges(
            actor=request.user,
            participant=participant,
            cohort=cohort,
            enrolled_on=data["enrolled_on"],
            code=data["code"],
            request=request,
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None

    messages.success(request, _("تم التسجيل وتحميل الرسوم"))
    return redirect("operations:enrollments")


@require_http_methods(["POST"])
def enrollment_action_view(request: HttpRequest, code: str) -> HttpResponse:
    """The two state changes the enrolment list offers (BR-018)."""
    action = request.POST.get("action", "")
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    try:
        if action == "voucher":
            enrollment_service.record_voucher(
                actor=request.user, enrollment=enrollment, request=request
            )
            messages.success(request, _("سُجِّل استلام الوصل"))
        elif action == "approve":
            enrollment_service.approve_enrollment(
                actor=request.user, enrollment=enrollment, request=request
            )
            messages.success(request, _("اعتُمد التسجيل"))
        else:
            messages.error(request, _("إجراء غير معروف"))
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))

    return redirect("operations:enrollments")


# ---------------------------------------------------------------------------
# The participant's account (Screen.ENROLLMENTS — §9 report 5 as a screen)
# ---------------------------------------------------------------------------
def account_view(request: HttpRequest, code: str) -> HttpResponse:
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=code, request=request
        )
        statement = account_service.account_statement(
            actor=request.user, enrollment=enrollment, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(
        request,
        "operations/account.html",
        {
            "title": _("كشف حساب المشارك"),
            "active_screen": Screen.ENROLLMENTS,
            "statement": statement,
        },
    )


# ---------------------------------------------------------------------------
# Clearance (Screen.CLEARANCE) — §6.4, form CS Fm 7.18 Rev A
# ---------------------------------------------------------------------------
#: Which permission each POST action needs. Two of the step-2 actions belong
#: to a DIFFERENT screen's permission: settling the deposit and returning a
#: credit are money movements gated by REFUNDS.CREATE, which the finance
#: officer holds and the finance manager does not. The finance manager
#: countersigns and nothing else — so the buttons are gated by what each one
#: actually needs, never by where it sits on the page.
CLEARANCE_ACTIONS = {
    "open": (Screen.CLEARANCE, Action.CREATE),
    "custody": (Screen.CLEARANCE, Action.APPROVE),
    "certify": (Screen.CLEARANCE, Action.APPROVE),
    "second-certify": (Screen.CLEARANCE, Action.APPROVE),
    "handover": (Screen.CLEARANCE, Action.APPROVE),
    "close": (Screen.CLEARANCE, Action.APPROVE),
    "cancel": (Screen.CLEARANCE, Action.APPROVE),
    "deposit": (Screen.REFUNDS, Action.CREATE),
    "forfeit": (Screen.REFUNDS, Action.CREATE),
    "return-credit": (Screen.REFUNDS, Action.CREATE),
}


@require_http_methods(["GET", "POST"])
def clearances_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CLEARANCE, Action.CREATE)
    form = None
    if can_create:
        form = ClearanceOpenForm(
            request.POST if request.POST.get("action") == "open" else None,
            enrollment_choices=clearance_service.clearable_enrollment_choices(
                actor=request.user, request=request
            ),
        )

    if request.method == "POST":
        response = _handle_clearance_open(request, form)
        if response is not None:
            return response

    return render(
        request,
        "operations/clearances.html",
        {
            "title": _("براءة الذمة"),
            "active_screen": Screen.CLEARANCE,
            "clearances": clearance_service.list_clearances(
                actor=request.user,
                status=request.GET.get("status", "").strip(),
                query=request.GET.get("q", "").strip(),
                request=request,
            ),
            "form": form,
            "can_create": can_create,
            "query": request.GET.get("q", ""),
        },
    )


def _handle_clearance_open(
    request: HttpRequest, form: ClearanceOpenForm | None
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLEARANCE_ACTIONS:
        screen, permission = CLEARANCE_ACTIONS[action]
        policy.require(request.user, screen, permission, request=request)
    if action != "open" or form is None or not form.is_valid():
        return None

    data = form.cleaned_data
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=data["enrollment_code"], request=request
        )
        clearance = clearance_service.open_clearance(
            actor=request.user,
            enrollment=enrollment,
            case_type=data["case_type"],
            opened_on=data["opened_on"],
            code=data["code"],
            request=request,
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("تسجيل غير معروف"))
        return None

    messages.success(request, _("فُتحت براءة الذمة"))
    return redirect("operations:clearance-detail", code=clearance.code)


@require_http_methods(["GET", "POST"])
def clearance_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    if request.method == "POST":
        response = _handle_clearance_step(request, code)
        if response is not None:
            return response

    try:
        clearance = clearance_service.get_clearance(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    role = getattr(request.user, "role", None)
    approves = policy.is_allowed(request.user, Screen.CLEARANCE, Action.APPROVE)
    is_live = clearance["status"] not in {"COMPLETED", "CANCELLED"}

    return render(
        request,
        "operations/clearance_detail.html",
        {
            "title": _("براءة ذمة"),
            "active_screen": Screen.CLEARANCE,
            "clearance": clearance,
            "custody_form": CustodyForm(),
            "cancel_form": ClearanceCancelForm(),
            "deposit_form": DepositSettlementForm(),
            "credit_form": CreditReturnAtClearanceForm(),
            # §6.4 — each step offered to the department it belongs to, and
            # only while the clearance is still live.
            "can_custody": approves and is_live and role == clearance["custody_role"],
            "can_certify": approves and is_live,
            "can_second_certify": approves
            and is_live
            and role == clearance["second_certifier_role"],
            "can_handover": approves and is_live and role == clearance["handover_role"],
            "can_close": approves and is_live,
            "can_settle_money": policy.is_allowed(request.user, Screen.REFUNDS, Action.CREATE)
            and is_live,
        },
    )


def _handle_clearance_step(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLEARANCE_ACTIONS:
        screen, permission = CLEARANCE_ACTIONS[action]
        policy.require(request.user, screen, permission, request=request)
    try:
        clearance = clearance_service.clearance_instance(
            actor=request.user, code=code, request=request
        )
        handled = _dispatch_clearance(request, clearance, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return redirect("operations:clearance-detail", code=code)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return redirect("operations:clearance-detail", code=code) if handled else None


def _dispatch_clearance(request: HttpRequest, clearance: Any, action: str) -> bool:
    """Run one clearance action. Returns whether anything was done."""
    from apps.billing.services import deposit_service

    if action == "custody":
        custody_form = CustodyForm(request.POST)
        if not custody_form.is_valid():
            messages.error(request, _("يجب ذكر العُهد المُسترجَعة"))
            return True
        clearance_service.complete_custody_step(
            actor=request.user,
            clearance=clearance,
            custody_items=custody_form.cleaned_data["items"],
            request=request,
        )
        messages.success(request, _("أُتمّت الخطوة الأولى"))
    elif action == "certify":
        clearance_service.certify_finance_step(
            actor=request.user, clearance=clearance, request=request
        )
        messages.success(request, _("تمّت المصادقة المالية الأولى"))
    elif action == "second-certify":
        clearance_service.second_certify_finance_step(
            actor=request.user, clearance=clearance, request=request
        )
        messages.success(request, _("تمّت المصادقة الثانية وأُغلقت الخطوة المالية"))
    elif action == "handover":
        clearance_service.complete_handover_step(
            actor=request.user, clearance=clearance, request=request
        )
        messages.success(request, _("سُجِّل تسليم الشهادة"))
    elif action == "close":
        clearance_service.close_clearance(actor=request.user, clearance=clearance, request=request)
        messages.success(request, _("أُغلقت براءة الذمة"))
    elif action == "cancel":
        cancel_form = ClearanceCancelForm(request.POST)
        if not cancel_form.is_valid():
            messages.error(request, _("سبب الإلغاء إلزامي"))
            return True
        clearance_service.cancel_clearance(
            actor=request.user,
            clearance=clearance,
            reason_ar=cancel_form.cleaned_data["reason_ar"],
            request=request,
        )
        messages.success(request, _("أُلغيت براءة الذمة"))
    elif action in {"deposit", "forfeit"}:
        _settle_deposit(request, clearance, action, deposit_service)
    elif action == "return-credit":
        credit_form = CreditReturnAtClearanceForm(request.POST)
        if not credit_form.is_valid():
            messages.error(request, _("بيانات ردّ الرصيد غير مكتملة"))
            return True
        clearance_service.return_credit_at_clearance(
            actor=request.user,
            clearance=clearance,
            returned_on=credit_form.cleaned_data["returned_on"],
            code=credit_form.cleaned_data["code"],
            request=request,
        )
        messages.success(request, _("رُدّ الرصيد الدائن"))
    else:
        return False
    return True


def _settle_deposit(
    request: HttpRequest, clearance: Any, action: str, deposit_service: Any
) -> None:
    form = DepositSettlementForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("بيانات تسوية التأمين غير مكتملة"))
        return
    data = form.cleaned_data
    if action == "deposit":
        deposit_service.return_deposit(
            actor=request.user,
            enrollment=clearance.enrollment,
            returned_on=data["returned_on"],
            deduction_amount=data["deduction_amount"] or ZERO,
            deduction_reason_ar=data["deduction_reason_ar"],
            request=request,
        )
        messages.success(request, _("أُعيد التأمين"))
    else:
        deposit_service.forfeit_deposit(
            actor=request.user,
            enrollment=clearance.enrollment,
            reason="CLEARANCE",
            justification_ar=data["deduction_reason_ar"],
            forfeited_on=data["returned_on"],
            request=request,
        )
        messages.success(request, _("صودر التأمين"))


# ---------------------------------------------------------------------------
# Certificates (Screen.CERTIFICATES) — §7
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def certificates_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CERTIFICATES, Action.CREATE)
    form = None
    if can_create:
        form = CertificateIssueForm(
            request.POST if request.POST.get("action") == "issue" else None,
            enrollment_choices=certificate_service.issuable_enrollment_choices(
                actor=request.user, request=request
            ),
            grade_choices=certificate_service.available_grades(as_of=timezone.now().date()),
        )

    if request.method == "POST":
        response = _handle_certificate(request, form)
        if response is not None:
            return response

    return render(
        request,
        "operations/certificates.html",
        {
            "title": _("الشهادات"),
            "active_screen": Screen.CERTIFICATES,
            "certificates": certificate_service.list_certificates(
                actor=request.user, query=request.GET.get("q", "").strip(), request=request
            ),
            "form": form,
            "date_form": CertificateDateForm(),
            "can_create": can_create,
            "can_print": policy.is_allowed(request.user, Screen.CERTIFICATES, Action.PRINT),
            "query": request.GET.get("q", ""),
        },
    )


def _handle_certificate(
    request: HttpRequest, form: CertificateIssueForm | None
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    permission = {
        "issue": Action.CREATE,
        "deliver": Action.CREATE,
        "replace": Action.CREATE,
        "reprint": Action.PRINT,
    }.get(action)
    if permission is None:
        return None
    policy.require(request.user, Screen.CERTIFICATES, permission, request=request)

    try:
        if action == "issue":
            if form is None or not form.is_valid():
                return None
            _issue_certificate(request, form)
        else:
            _certificate_transition(request, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
    return redirect("operations:certificates")


def _issue_certificate(request: HttpRequest, form: CertificateIssueForm) -> None:
    data = form.cleaned_data
    enrollment = enrollment_service.get_enrollment(
        actor=request.user, code=data["enrollment_code"], request=request
    )
    certificate = certificate_service.issue_certificate(
        actor=request.user,
        enrollment=enrollment,
        grade=data["grade"],
        issued_on=data["issued_on"],
        duration_text=data["duration_text"],
        training_hours=data["training_hours"],
        request=request,
    )
    messages.success(request, _("صدرت الشهادة %(n)s") % {"n": certificate.certificate_number})


def _certificate_transition(request: HttpRequest, action: str) -> None:
    certificate = certificate_service.certificate_instance(
        actor=request.user, number=request.POST.get("number", ""), request=request
    )
    if action == "reprint":
        certificate_service.record_reprint(
            actor=request.user, certificate=certificate, request=request
        )
        messages.success(request, _("سُجّلت إعادة الطباعة — الرقم كما هو"))
        return

    date_form = CertificateDateForm(request.POST)
    if not date_form.is_valid():
        messages.error(request, _("تاريخ غير صالح"))
        return
    on_date = date_form.cleaned_data["on_date"]

    if action == "deliver":
        certificate_service.deliver(
            actor=request.user, certificate=certificate, delivered_on=on_date, request=request
        )
        messages.success(request, _("سُلِّمت الشهادة"))
    else:
        replacement = certificate_service.issue_replacement(
            actor=request.user, original=certificate, issued_on=on_date, request=request
        )
        messages.success(request, _("صدر بدل الفاقد %(n)s") % {"n": replacement.certificate_number})
