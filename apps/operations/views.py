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

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.billing.services import account_service
from apps.operations.forms import CohortForm, EnrollmentForm
from apps.operations.services import cohort_service, enrollment_service
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
