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

from datetime import date
from typing import Any

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
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
    HandoverForm,
    MoheAttachmentForm,
    MoheDecisionForm,
    MoheResubmissionForm,
    MoheSendForm,
    MoheSubmissionForm,
    TransferExecuteForm,
    TransferRejectForm,
    TransferRequestForm,
)
from apps.operations.services import (
    certificate_service,
    clearance_service,
    cohort_service,
    enrollment_service,
    mohe_service,
    transfer_service,
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
            "custody_form": CustodyForm(
                initial={
                    "items": "\n".join(clearance_service.standard_custody_items(as_of=date.today()))
                }
            ),
            "handover_form": HandoverForm(),
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
        handover_form = HandoverForm(request.POST)
        if not handover_form.is_valid():
            messages.error(request, _("اسم مستلم الشهادة إلزامي"))
            return True
        clearance_service.complete_handover_step(
            actor=request.user,
            clearance=clearance,
            participant_ack_name=handover_form.cleaned_data["participant_ack_name"],
            request=request,
        )
        messages.success(request, _("سُجِّل تسليم الشهادة وإقرار المستلِم"))
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


# ---------------------------------------------------------------------------
# Printed documents (Sprint 8C-1)
# ---------------------------------------------------------------------------
# ⚠️ Requirements-based output. The centre's own blank forms were not in the
# client folder, so these follow §6.4 and §7 and carry a printed marker saying
# so until someone verifies them and flips ``document_mode``.
#
# The print route runs the SAME permission check as the screen it prints. A
# document route that read more loosely than its screen would be a way around
# the matrix wearing a printer icon.
def clearance_print_view(request: HttpRequest, code: str) -> HttpResponse:
    """
    The clearance form (§6.4).

    Printable at any stage, deliberately: the form is a checklist a
    participant is handed at the counter, and WORKFLOWS §6.3 wants what is
    still outstanding visible. Completion is shown, not required.
    """
    try:
        document = clearance_service.clearance_document(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(request, "print/clearance_form.html", {"clearance": document})


def certificate_print_view(request: HttpRequest, number: str) -> HttpResponse:
    """
    The certificate, or a replacement (§7).

    BR-075 is not re-checked here and does not need to be: a Certificate row
    only exists because ``issue_certificate`` found a COMPLETED clearance, and
    a replacement only because ``issue_replacement`` found an original whose
    fee had been collected. Printing reads what those refusals already
    permitted — it cannot conjure a certificate that was never issued.
    """
    try:
        document = certificate_service.certificate_document(
            actor=request.user, number=number, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(request, "print/certificate.html", {"certificate": document})


# ---------------------------------------------------------------------------
# The ministry file (Sprint 8G)
# ---------------------------------------------------------------------------
#: Which permission each POST on the MOHE screens needs, checked before the
#: service so a role violation is a 403 and a rule refusal is a message. The
#: split is §3.3's own: the registration officer drafts and attaches, the
#: centre manager sends and records the decision (footnote ⁹).
MOHE_ACTIONS: dict[str, tuple[str, str]] = {
    "attach": (Screen.MOHE_SUBMIT, Action.EDIT),
    "send": (Screen.MOHE_SUBMIT, Action.APPROVE),
    "approve": (Screen.MOHE, Action.APPROVE),
    "reject": (Screen.MOHE, Action.APPROVE),
    "resubmit": (Screen.MOHE_SUBMIT, Action.CREATE),
}


@require_http_methods(["GET"])
def mohe_view(request: HttpRequest) -> HttpResponse:
    """§3.3/14 — every ministry file, whatever its status."""
    return render(
        request,
        "operations/mohe.html",
        {
            "title": _("اعتماد الوزارة"),
            "active_screen": Screen.MOHE,
            "submissions": mohe_service.list_submissions(
                actor=request.user,
                status=request.GET.get("status", "").strip(),
                query=request.GET.get("q", "").strip(),
                request=request,
            ),
            "query": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "can_open_file": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE),
        },
    )


@require_http_methods(["GET", "POST"])
def mohe_submit_view(request: HttpRequest) -> HttpResponse:
    """
    §3.3/15 — open a ministry file for a cohort.

    Opens on VIEW and submits on CREATE. The row gives the audit account ``V``
    and withholds ``C``, so the editor is readable by the role that reads
    everything and fillable only by the two that draft.
    """
    policy.require(request.user, Screen.MOHE_SUBMIT, Action.VIEW, request=request)

    form = MoheSubmissionForm(
        request.POST or None,
        cohort_choices=mohe_service.submittable_cohort_choices(actor=request.user, request=request),
    )

    if request.method == "POST":
        policy.require(request.user, Screen.MOHE_SUBMIT, Action.CREATE, request=request)
        if form.is_valid():
            try:
                cohort = cohort_service.get_cohort_instance(
                    actor=request.user, code=form.cleaned_data["cohort_code"], request=request
                )
                submission = mohe_service.create_submission(
                    actor=request.user, cohort=cohort, data=form.content(), request=request
                )
            except (DjangoValidationError, ObjectDoesNotExist) as exc:
                messages.error(request, _message_of(exc))
            else:
                messages.success(
                    request,
                    _("فُتح ملف وزاري للدفعة %(code)s — أرفق المستندين ثم أرسله")
                    % {"code": cohort.code},
                )
                return redirect("operations:mohe-detail", submission_id=submission.pk)

    return render(
        request,
        "operations/mohe_submit.html",
        {
            "title": _("نموذج الإرسال للوزارة"),
            "active_screen": Screen.MOHE_SUBMIT,
            "form": form,
            "can_create": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE),
        },
    )


@require_http_methods(["GET", "POST"])
def mohe_detail_view(request: HttpRequest, submission_id: int) -> HttpResponse:
    """
    One file: its contents, its documents, and whichever act comes next.

    Part of §3.3/14 rather than a screen of its own — the same permission row
    as the listing it opens from, the way the clearance detail sits under the
    clearance screen.
    """
    if request.method == "POST":
        response = _handle_mohe_action(request, submission_id)
        if response is not None:
            return response

    try:
        submission = mohe_service.get_submission(
            actor=request.user, submission_id=submission_id, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب وزاري بهذا الرقم")) from exc

    may_draft = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.EDIT)
    may_send = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.APPROVE)
    may_decide = policy.is_allowed(request.user, Screen.MOHE, Action.APPROVE)

    return render(
        request,
        "operations/mohe_detail.html",
        {
            "title": _("الطلب الوزاري"),
            "active_screen": Screen.MOHE,
            "submission": submission,
            "attachment_form": MoheAttachmentForm(
                purpose_choices=[
                    (p["purpose"], p["label"]) for p in submission["required_purposes"]
                ]
            ),
            "send_form": MoheSendForm(initial={"submitted_on": timezone.localdate()}),
            "decision_form": MoheDecisionForm(initial={"decided_on": timezone.localdate()}),
            "resubmission_form": MoheResubmissionForm(initial=submission["content"]),
            # BR-016 — the send button appears only when both documents are in.
            "can_attach": may_draft and submission["status"] == "DRAFT",
            "can_send": may_send and submission["is_sendable"],
            "send_blocked_by_documents": may_send
            and submission["status"] == "DRAFT"
            and bool(submission["missing_attachments"]),
            "can_decide": may_decide and submission["is_decidable"],
            "can_resubmit": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE)
            and submission["is_resubmittable"],
        },
    )


def _handle_mohe_action(request: HttpRequest, submission_id: int) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in MOHE_ACTIONS:
        return None

    screen, permission = MOHE_ACTIONS[action]
    policy.require(request.user, screen, permission, request=request)

    try:
        submission = mohe_service.submission_instance(
            actor=request.user, submission_id=submission_id, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب وزاري بهذا الرقم")) from exc

    try:
        created = _run_mohe_action(request, action, submission)
    except (DjangoValidationError, PermissionDenied) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        messages.error(request, _message_of(exc))
        return redirect("operations:mohe-detail", submission_id=submission_id)

    if created is not None:
        return redirect("operations:mohe-detail", submission_id=created)
    return redirect("operations:mohe-detail", submission_id=submission_id)


def _run_mohe_action(request: HttpRequest, action: str, submission: Any) -> int | None:
    """Perform one act and report the id to land on — a new one for a resubmission."""
    if action == "attach":
        attachment_form = MoheAttachmentForm(
            request.POST,
            request.FILES,
            purpose_choices=[(p, str(p)) for p in mohe_service.REQUIRED_ATTACHMENTS],
        )
        if not attachment_form.is_valid():
            messages.error(request, _("اختر نوع المستند وملفاً صالحاً."))
            return None
        mohe_service.attach_document(
            actor=request.user,
            submission=submission,
            purpose=attachment_form.cleaned_data["purpose"],
            upload=attachment_form.cleaned_data["upload"],
            request=request,
        )
        messages.success(request, _("أُرفق المستند."))
        return None

    if action == "send":
        send_form = MoheSendForm(request.POST)
        if not send_form.is_valid():
            messages.error(request, _("تاريخ الإرسال مطلوب."))
            return None
        mohe_service.submit_to_mohe(
            actor=request.user,
            submission=submission,
            submitted_on=send_form.cleaned_data["submitted_on"],
            request=request,
        )
        messages.success(request, _("أُرسل الطلب إلى الوزارة."))
        return None

    if action in {"approve", "reject"}:
        decision_form = MoheDecisionForm(request.POST)
        if not decision_form.is_valid():
            messages.error(request, _("تاريخ القرار مطلوب."))
            return None
        data = decision_form.cleaned_data
        mohe_service.record_decision(
            actor=request.user,
            submission=submission,
            approved=action == "approve",
            decided_on=data["decided_on"],
            mohe_course_number=data["mohe_course_number"],
            registration_deadline=data["registration_deadline"],
            rejection_reason_ar=data["rejection_reason_ar"],
            request=request,
        )
        messages.success(
            request,
            _("سُجّل الاعتماد الوزاري.") if action == "approve" else _("سُجّل الرفض الوزاري."),
        )
        return None

    # resubmit — a NEW file answering the rejection, which stays readable.
    resubmission_form = MoheResubmissionForm(request.POST)
    if not resubmission_form.is_valid():
        messages.error(request, _("راجع حقول النموذج."))
        return None
    fresh = mohe_service.resubmit(
        actor=request.user,
        rejected=submission,
        data=resubmission_form.content(),
        request=request,
    )
    messages.success(request, _("فُتح ملف جديد يردّ على الرفض — أرفق المستندين ثم أرسله."))
    return int(fresh.pk)


# ---------------------------------------------------------------------------
# Transfers (Sprint 8H)
# ---------------------------------------------------------------------------
#: §3.2/6's own split, which is why three different people appear on one
#: record: the registration officer opens the request (``C`` on §3.2/7), the
#: centre manager recommends or refuses it (``A``), and finance settles and
#: executes it (``E`` — footnote ⁶, the fee-difference settlement). The
#: manager holds no ``E`` here and finance holds no ``A``; neither can do the
#: other's step.
TRANSFER_ACTIONS: dict[str, tuple[str, str]] = {
    "recommend": (Screen.TRANSFERS, Action.APPROVE),
    "reject": (Screen.TRANSFERS, Action.APPROVE),
    "execute": (Screen.TRANSFERS, Action.EDIT),
}


@require_http_methods(["GET"])
def transfers_view(request: HttpRequest) -> HttpResponse:
    """§3.2/6 — every transfer, whatever stage it has reached."""
    return render(
        request,
        "operations/transfers.html",
        {
            "title": _("النقل بين الدورات"),
            "active_screen": Screen.TRANSFERS,
            "transfers": transfer_service.list_transfers(
                actor=request.user,
                status=request.GET.get("status", "").strip(),
                query=request.GET.get("q", "").strip(),
                request=request,
            ),
            "query": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "can_request": policy.is_allowed(request.user, Screen.TRANSFER_NEW, Action.CREATE),
        },
    )


@require_http_methods(["GET", "POST"])
def transfer_new_view(request: HttpRequest) -> HttpResponse:
    """
    §3.2/7 — raise a transfer request.

    Two POSTs, and the difference matters. ``preview`` runs the rule engine
    and the BR-064 arithmetic and writes nothing, so the operator sees the
    refusal — or the difference the participant will owe — before committing
    anybody to it. ``submit`` is the request itself.

    Opens on VIEW and submits on CREATE, so the audit account can read the
    form it may not file.
    """
    policy.require(request.user, Screen.TRANSFER_NEW, Action.VIEW, request=request)

    form = TransferRequestForm(
        request.POST or None,
        enrollment_choices=transfer_service.transferable_enrollment_choices(
            actor=request.user, request=request
        ),
        cohort_choices=transfer_service.destination_cohort_choices(
            actor=request.user, request=request
        ),
        reason_choices=transfer_service.reason_choices(),
        initial={"requested_on": timezone.localdate()},
    )
    preview = None

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "submit":
            policy.require(request.user, Screen.TRANSFER_NEW, Action.CREATE, request=request)
        if form.is_valid():
            if action == "submit":
                created = _submit_transfer(request, form)
                if created is not None:
                    return redirect("operations:transfer-detail", code=created)
            else:
                preview = _preview_transfer(request, form)

    return render(
        request,
        "operations/transfer_new.html",
        {
            "title": _("طلب نقل جديد"),
            "active_screen": Screen.TRANSFER_NEW,
            "form": form,
            "preview": preview,
            "can_create": policy.is_allowed(request.user, Screen.TRANSFER_NEW, Action.CREATE),
        },
    )


def _transfer_inputs(request: HttpRequest, form: TransferRequestForm) -> dict[str, Any]:
    """
    Resolve the two records and settle who granted any waiver.

    The waiver's approver is checked against §3.2/6's ``A`` rather than taken
    on trust from whoever filled the form. C-12 is precisely about this: the
    demo granted the waiver on a dropdown pick with nobody's name on it, and
    ``request_transfer`` still accepts whatever ``waiver_by`` it is handed —
    so the screen refuses a registrar's self-granted exception here, with a
    DENIED_ATTEMPT, instead of storing one.
    """
    data = form.cleaned_data
    waiver_by = None
    if data.get("grant_category_waiver"):
        policy.require(request.user, Screen.TRANSFERS, Action.APPROVE, request=request)
        waiver_by = request.user

    return {
        "from_enrollment": enrollment_service.get_enrollment(
            actor=request.user, code=data["from_enrollment_code"], request=request
        ),
        "to_cohort": cohort_service.get_cohort_instance(
            actor=request.user, code=data["to_cohort_code"], request=request
        ),
        "reason": data["reason"],
        "waiver_by": waiver_by,
        "waiver_reason_ar": data.get("category_waiver_reason_ar", ""),
    }


def _preview_transfer(request: HttpRequest, form: TransferRequestForm) -> dict[str, Any] | None:
    try:
        inputs = _transfer_inputs(request, form)
    except ObjectDoesNotExist as exc:
        messages.error(request, _message_of(exc))
        return None

    return transfer_service.preview_transfer(
        actor=request.user,
        as_of=form.cleaned_data["requested_on"],
        request=request,
        **inputs,
    )


def _submit_transfer(request: HttpRequest, form: TransferRequestForm) -> str | None:
    try:
        inputs = _transfer_inputs(request, form)
        transfer = transfer_service.request_transfer(
            actor=request.user,
            requested_on=form.cleaned_data["requested_on"],
            code=form.cleaned_data["code"],
            request=request,
            **inputs,
        )
    except ObjectDoesNotExist as exc:
        messages.error(request, _message_of(exc))
        return None
    except (DjangoValidationError, *transfer_service.PRICING_ERRORS) as exc:
        # The rule engine's own words, with the reference the centre needs —
        # and the pricing refusals beside them, which are plain Exceptions
        # and answered with a 500 until Sprint 8I found it in the browser.
        messages.error(request, _message_of(exc))
        return None

    messages.success(
        request, _("سُجّل طلب النقل %(code)s — بانتظار تنسيب المدير") % {"code": transfer.code}
    )
    return str(transfer.code)


@require_http_methods(["GET", "POST"])
def transfer_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """§3.2/6 — one transfer, its frozen evidence, and the step it awaits."""
    if request.method == "POST":
        response = _handle_transfer_action(request, code)
        if response is not None:
            return response

    try:
        transfer = transfer_service.get_transfer(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب نقل بهذا الرمز")) from exc

    decides = policy.is_allowed(request.user, Screen.TRANSFERS, Action.APPROVE)
    settles = policy.is_allowed(request.user, Screen.TRANSFERS, Action.EDIT)

    return render(
        request,
        "operations/transfer_detail.html",
        {
            "title": _("طلب نقل"),
            "active_screen": Screen.TRANSFERS,
            "transfer": transfer,
            "reject_form": TransferRejectForm(),
            "execute_form": TransferExecuteForm(initial={"executed_on": timezone.localdate()}),
            "can_recommend": decides and transfer["awaits_manager"],
            "can_reject": decides and not transfer["is_closed"],
            "can_execute": settles and transfer["awaits_finance"],
        },
    )


def _handle_transfer_action(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in TRANSFER_ACTIONS:
        return None

    screen, permission = TRANSFER_ACTIONS[action]
    policy.require(request.user, screen, permission, request=request)

    try:
        transfer = transfer_service.transfer_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب نقل بهذا الرمز")) from exc

    try:
        _run_transfer_action(request, action, transfer)
    except (DjangoValidationError, *transfer_service.PRICING_ERRORS) as exc:
        # Executing re-prices the target course, so the same pricing refusals
        # reach here. A settlement that cannot be priced is a message, not a
        # traceback.
        messages.error(request, _message_of(exc))

    return redirect("operations:transfer-detail", code=code)


def _run_transfer_action(request: HttpRequest, action: str, transfer: Any) -> None:
    if action == "recommend":
        transfer_service.manager_recommend(actor=request.user, transfer=transfer, request=request)
        messages.success(request, _("نُسّب الطلب — بانتظار التسوية المالية."))
        return

    if action == "reject":
        form = TransferRejectForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("سبب الرفض إلزامي."))
            return
        transfer_service.reject_transfer(
            actor=request.user,
            transfer=transfer,
            reason_ar=form.cleaned_data["reason_ar"],
            request=request,
        )
        messages.success(request, _("رُفض طلب النقل."))
        return

    execute_form = TransferExecuteForm(request.POST)
    if not execute_form.is_valid():
        messages.error(request, _("تاريخ التنفيذ ورمز التسجيل الجديد مطلوبان."))
        return
    transfer_service.execute_transfer(
        actor=request.user,
        transfer=transfer,
        executed_on=execute_form.cleaned_data["executed_on"],
        new_code=execute_form.cleaned_data["new_code"],
        request=request,
    )
    messages.success(request, _("نُفِّذ النقل وسُوّيت الرسوم."))


# ---------------------------------------------------------------------------
# Sprint 8K — demo-parity guided screens
# ---------------------------------------------------------------------------
def enroll_flow_view(request: HttpRequest) -> HttpResponse:
    """
    The registration-and-payment guide — WORKFLOWS §1 … §7, read only.

    This screen executes nothing. It exists so a new employee can see the whole
    journey, from the admission request to the certificate, and learn which
    rule stops them at each stage rather than discovering it as a refusal.

    **Nothing here is invented.** Every stage names the rules that actually run
    underneath it, so the guide cannot drift from the system: BR-013 · BR-018
    on the enrolment, BR-022 · BR-025 on the money, BR-027 · BR-028 on the
    closing, BR-060 … BR-066 on transfers, BR-067 … BR-071 on the special
    cases, BR-073 · BR-074 on the clearance and BR-075 on the certificate.

    **Why the links are filtered.** Four roles may open this page and none may
    open every screen it describes — §3.4/17 keeps the manager, the registrar
    and the audit account out of cash collection, and D-01 makes BR-081 an
    explicit deny for the manager rather than a missing grant. A stage whose
    screen the reader may not VIEW keeps its whole explanation and loses only
    its button; offering it would send someone following the guide as written
    into a refusal and a DENIED_ATTEMPT row (BR-085).
    """
    policy.require(request.user, Screen.ENROLL_FLOW, Action.VIEW, request=request)

    def _links(*candidates: tuple[str, str, Any]) -> list[dict[str, Any]]:
        """The subset of a stage's screens this particular reader may open."""
        return [
            {"url": reverse(route), "label": label}
            for screen, route, label in candidates
            if policy.is_allowed(request.user, screen, Action.VIEW)
        ]

    stages: list[dict[str, Any]] = [
        {
            "number": 1,
            "title": _("طلب التحاق جديد"),
            "what": _(
                "تُدخل بيانات المشارك وفئته ومؤهله، ويولّد النظام رقمه الجامعي: "
                "السنة (٤) + رمز النوع (١) + التسلسل (٤)."
            ),
            "who": _("موظف التسجيل، أو مدير المركز"),
            "before": _("لا شيء — هنا يبدأ المسار."),
            "after": _("إنشاء التسجيل على دفعة معتمدة."),
            "rules": [
                _(
                    "الرقم الجامعي دائم ولا يُصحَّح لاحقاً (BR-001)، ولذلك يرفض "
                    "النظام توليده إذا لم يكن هناك فصل نشط بدل أن يخمّن السنة."
                ),
            ],
            "links": _links(
                (Screen.STUDENT_NEW, "people:participant-new", _("افتح طلب التحاق جديد")),
            ),
        },
        {
            "number": 2,
            "title": _("التسجيل ومتابعته"),
            "what": _(
                "يُربط المشارك بدفعة مُشغّلة، وتُحتسب رسومه، ثم يُعتمد التسجيل فيصبح "
                "نافذاً ويظهر في كشوف الدفعة."
            ),
            "who": _("موظف التسجيل يُنشئ ويعدّل، ومدير المركز يعتمد"),
            "before": _("طلب التحاق مُسجَّل، ودفعة اعتمدتها الوزارة."),
            "after": _("استيفاء الدفعة في الصندوق."),
            "rules": [
                _("لا تسجيل على دفعة لم تعتمدها الوزارة (BR-013 · D-21)."),
                _(
                    "لا يُعتمد التسجيل قبل تسجيل الوصل (BR-018) — وهذا قيد في قاعدة "
                    "البيانات أيضاً، لا رسالة على الشاشة فقط."
                ),
            ],
            "links": _links(
                (Screen.ENROLLMENTS, "operations:enrollments", _("افتح التسجيلات")),
            ),
        },
        {
            "number": 3,
            "title": _("استيفاء دفعة"),
            "what": _(
                "يُقبض المبلغ ويوزّعه النظام على بنود الرسوم ويخزّن التوزيع، فيبقى "
                "مجموع التخصيصات مساوياً لقيمة السند بالفلس."
            ),
            "who": _("أمين الصندوق، أو الموظف المالي"),
            "before": _("تسجيل قائم للمشارك."),
            "after": _("صدور سند القبض وتسليمه للمشارك."),
            "rules": [
                _(
                    "مدير المركز لا يستوفي دفعة ولا يُنشئ سند قبض (BR-081 · D-01)، "
                    "وموظف التسجيل لا يقبض نقداً — منعٌ صريح لا نقصٌ في الصلاحية."
                ),
                _("التوزيع على البنود مُخزَّن لا محسوباً عند كل قراءة (BR-022)."),
            ],
            "links": _links(
                (Screen.PAYMENT_NEW, "cashbox:payment-new", _("افتح استيفاء دفعة")),
            ),
        },
        {
            "number": 4,
            "title": _("الدفعات وسندات القبض"),
            "what": _("سجل السندات الصادرة: قيمها وتخصيصاتها وحالتها، ومنه يُطلب إلغاء سند ويُعتمد."),
            "who": _("الصندوق يطلب الإلغاء، والموظف المالي يعتمده"),
            "before": _("استيفاء دفعة."),
            "after": _("اعتماد التسجيل (BR-018)، ثم الإقفال اليومي."),
            "rules": [
                _(
                    "لا أحد يعدّل سنداً صادراً (BR-025). الإلغاء يكتب تخصيصات عكسية "
                    "ويُبقي السند الأصلي برقمه وقيمته — فيُقرأ الأمر كتاريخ، لا كرقم "
                    "تغيّر وحده."
                ),
            ],
            "links": _links(
                (Screen.PAYMENTS, "cashbox:payments", _("افتح الدفعات وسندات القبض")),
            ),
        },
        {
            "number": 5,
            "title": _("النقل أو الحالة الخاصة"),
            "optional": True,
            "what": _(
                "مسار جانبي يُسلك عند الحاجة وحدها: نقل بين دورتين، أو حالة موثّقة "
                "مثل التأجيل أو الإحلال أو الفصل."
            ),
            "who": _("موظف التسجيل يفتح الطلب، ومدير المركز يعتمده"),
            "before": _("تسجيل قائم."),
            "after": _("العودة إلى المسار الطبيعي عند نقطة التوقف."),
            "rules": [
                _(
                    "النقل لا يعدّل قيداً مالياً ولا يحذفه: البنود تُلغى وتُعاد على "
                    "التسجيل الجديد، والتخصيصات تنتقل كزوج عكسي (BR-060 … BR-066)."
                ),
                _(
                    "لكل حالة خاصة أثر مالي مكتوب بالكلمات لا بالحالة وحدها "
                    "(BR-067 … BR-071) — الرصيد الدائن يُنشأ هنا ويُردّ عند براءة الذمة."
                ),
            ],
            "links": _links(
                (Screen.TRANSFERS, "operations:transfers", _("افتح النقل بين الدورات")),
                (Screen.SPECIAL_CASES, "operations:special-cases", _("افتح الحالات الخاصة")),
            ),
        },
        {
            "number": 6,
            "title": _("الإقفال اليومي"),
            "what": _(
                "يُعدّ الصندوق نقده في آخر اليوم، ويُقارَن المعدود بالمستحق، ثم يعتمد الإقفال شخصٌ آخر."
            ),
            "who": _("أمين الصندوق يُنشئ ويُدخل المعدود، والموظف المالي يعتمد"),
            "before": _("قبض اليوم."),
            "after": _("يوم جديد، وأرقام يمكن أن تُبنى عليها التقارير."),
            "rules": [
                _(
                    "قد يُقفل الصندوق بفرق، لكن لا يُقفل بفرق صامت: الفرق يحتاج "
                    "تسوية مكتوبة (BR-027)."
                ),
                _(
                    "من قبض المال لا يوقّع على عدّه (BR-028) — قيدٌ في قاعدة البيانات "
                    "يجعل الأمر مستحيلاً على كل المسارات، لا مجرد زر مخفي."
                ),
            ],
            "links": _links(
                (Screen.CLOSING, "cashbox:closing", _("افتح الإقفال اليومي")),
            ),
        },
        {
            "number": 7,
            "title": _("براءة الذمة"),
            "what": _(
                "ثلاث خطوات لا يُعاد ترتيبها على النموذج CS Fm 7.18 Rev A: يستردّ "
                "المركز عهدته، ثم تتحقق المالية من الحساب، ثم يُسلَّم المشارك شهادته."
            ),
            "who": _("المركز في الخطوتين الأولى والثالثة، والمالية في الثانية"),
            "before": _("انتهاء الدراسة وتسوية الحساب."),
            "after": _("إصدار الشهادة."),
            "rules": [
                _(
                    "الرصيد يجب أن يكون صفراً في الاتجاهين: الرصيد الدائن يوقف "
                    "البراءة كما يوقفها الدَّين تماماً (BR-073 · C-09)."
                ),
                _(
                    "الخطوة المالية تحتاج توقيعين من شخصين مختلفين — الموظف المالي "
                    "أولاً ثم المدير المالي (BR-074)."
                ),
            ],
            "links": _links(
                (Screen.CLEARANCE, "operations:clearances", _("افتح براءة الذمة")),
            ),
        },
        {
            "number": 8,
            "title": _("الشهادة"),
            "what": _(
                "تُصدر الشهادة برقمها ويُدخَل التقدير، وتبقى إعادة الطباعة نفس "
                "الشهادة على ورق جديد بالرقم ذاته."
            ),
            "who": _("مدير المركز"),
            "before": _("براءة ذمة مكتملة."),
            "after": _("نهاية المسار."),
            "rules": [
                _("لا شهادة بلا براءة ذمة مكتملة (BR-075)."),
                _(
                    "لا وحدة علامات في النظام ولا كيان امتحانات (BR-078): التقدير "
                    "يُدخله مُصدر الشهادة ويُتحقق من قائمة التقديرات المعتمدة."
                ),
            ],
            "links": _links(
                (Screen.CERTIFICATES, "operations:certificates", _("افتح الشهادات")),
            ),
        },
    ]

    return render(
        request,
        "operations/enroll_flow.html",
        {
            "title": _("مسار التسجيل والدفع"),
            "active_screen": Screen.ENROLL_FLOW,
            "stages": stages,
        },
    )


def special_cases_view(request: HttpRequest) -> HttpResponse:
    """
    The six documented exceptions to the ordinary lifecycle (DATA_MODEL §7.6).

    The rules run today — ``special_case_service`` enforces BR-067 … BR-071 and
    the database carries the dismissal constraint — but no data-entry screen
    exists yet, so this page teaches the types and says so plainly instead of
    drawing a form that would refuse everyone.

    Two of the six types are honest about a gap: CANCELLATION and
    CREDIT_TRANSFER are valid ``SpecialCaseType`` values with a CheckConstraint
    behind them and no service that creates one. Listing them as though they
    worked would be the fake functionality this sprint exists to avoid.
    """
    policy.require(request.user, Screen.SPECIAL_CASES, Action.VIEW, request=request)

    built = _("مسار مبني")
    declared = _("نوع مُعرَّف — بلا خدمة تُنشئه بعد")
    cases = [
        {
            "label": _("إلغاء"),
            "state": declared,
            "built": False,
            "what": _(
                "إلغاء التسجيل قبل أن يبدأ أثره. النوع محفوظ في النموذج ومحمي "
                "بقيد في قاعدة البيانات، ولا توجد خدمة تُنشئ حالة من هذا النوع بعد."
            ),
        },
        {
            "label": _("فصل"),
            "state": built,
            "built": True,
            "what": _(
                "قرار إداري لا يُتخذ على كلام: مرجع القرار إلزامي في الخدمة وفي "
                "قاعدة البيانات معاً (BR-067). لا استرداد يتبع الفصل، والشريك لا "
                "يستحق عنه شيئاً (BR-045)، وأي رصيد متبقٍ يبقى ديناً يمنع براءة "
                "الذمة (BR-068)."
            ),
        },
        {
            "label": _("ترحيل لدفعة لاحقة"),
            "state": built,
            "built": True,
            "what": _(
                "ينتقل المشارك وماله معاً إلى دفعة لاحقة (BR-069). المال ينتقل كما "
                "ينتقل في النقل: تخصيص عكسي على التسجيل القديم ومثله على الجديد — "
                "لا يُعدَّل قيد ولا يُحذف."
            ),
        },
        {
            "label": _("إحلال"),
            "state": built,
            "built": True,
            "what": _(
                "بديل يأخذ مقعداً شاغراً بانسحاب موثّق، وبلا رسم تسجيل ثانٍ "
                "(BR-070): المقعد دُفع عنه إدارياً مرة، وتحصيل الرسم مجدداً يُحاسب "
                "المركز على عمله مرتين."
            ),
        },
        {
            "label": _("نقل رصيد"),
            "state": declared,
            "built": False,
            "what": _(
                "نقل رصيد بين تسجيلين. النوع مُعرَّف في النموذج، ولا توجد خدمة "
                "تُنشئ حالة من هذا النوع بعد."
            ),
        },
        {
            "label": _("رصيد دائن"),
            "state": built,
            "built": True,
            "what": _(
                "يجعل الرصيد الدائن حالةً لها صاحب بدل أن يبقى رقماً سالباً "
                "(BR-071). يُنشأ عن ترحيل أو إحلال أو نقل أرخص، ويُردّ عند براءة "
                "الذمة — فالبراءة لا تُغلق ورصيد المشارك غير صفر (BR-073)."
            ),
        },
    ]

    statuses = [
        (_("قائمة"), _("سُجِّلت ولم تُسوَّ بعد.")),
        (_("مسوّاة"), _("انتهى أثرها المالي والإداري.")),
        (_("ملغاة"), _("أُلغيت الحالة نفسها، ويبقى أثرها في سجل التدقيق.")),
    ]

    links = [
        {"url": reverse(route), "label": label}
        for screen, route, label in (
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.TRANSFERS, "operations:transfers", _("النقل بين الدورات")),
            (Screen.CLEARANCE, "operations:clearances", _("براءة الذمة")),
        )
        if policy.is_allowed(request.user, screen, Action.VIEW)
    ]

    return render(
        request,
        "operations/special_cases.html",
        {
            "title": _("الحالات الخاصة"),
            "active_screen": Screen.SPECIAL_CASES,
            "cases": cases,
            "statuses": statuses,
            "links": links,
        },
    )
