"""
Billing screens — discounts, refunds and credit returns, extra fees.

The three screens where Sprint 8A's rules meet a user. None of them re-states
a rule: the discount screen does not know that §5.1 confines a discount to
tuition or that Sprint 8A refuses one after collection, the refund screen does
not know that BR-034 needs two external documents. They call the service and
show what it says.

**Refunds and credit returns share a screen and not a form.** §5.3's refund
reverses revenue and needs the president; BR-071's credit return hands back an
overpayment that was never revenue and needs no such thing. Putting them on
one form would invite the heavy process onto the light case — or worse, the
reverse. They sit side by side so the difference is visible.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.billing.forms import CreditReturnForm, DiscountForm, ExtraFeeForm, RefundForm
from apps.billing.services import (
    credit_service,
    discount_service,
    extra_fee_service,
    refund_service,
)
from apps.operations.services import enrollment_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


def _enrollment_choices(request: HttpRequest) -> list[tuple[str, str]]:
    if not policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.VIEW):
        return []
    return enrollment_service.enrollment_choices(actor=request.user, request=request)


def _enrollment(request: HttpRequest, code: str) -> object:
    return enrollment_service.get_enrollment(actor=request.user, code=code, request=request)


# ---------------------------------------------------------------------------
# Discounts (Screen.DISCOUNTS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def discounts_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.DISCOUNTS, Action.CREATE)
    form = DiscountForm(
        request.POST if request.POST.get("action") == "grant" else None,
        enrollment_choices=_enrollment_choices(request),
    )

    if request.method == "POST":
        response = _handle_discount(request, form)
        if response is not None:
            return response

    rows = discount_service.list_discounts(
        actor=request.user,
        enrollment_code=request.GET.get("enrollment", "").strip(),
        request=request,
    )
    return render(
        request,
        "billing/discounts.html",
        {
            "title": _("الخصومات"),
            "active_screen": Screen.DISCOUNTS,
            "discounts": rows,
            "form": form,
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.DISCOUNTS, Action.APPROVE),
            "current_user_id": request.user.pk,
        },
    )


#: Which permission each POST action on these screens needs. Checked BEFORE
#: the service call so a ROLE violation becomes a 403, while a BUSINESS rule
#: — including the ones services raise as PermissionDenied, such as BR-028 —
#: becomes a message the operator can act on. Collapsing the two would either
#: hide a refusal or turn a rule into a dead end.
DISCOUNT_ACTIONS = {"grant": Action.CREATE, "approve": Action.APPROVE}
REFUND_ACTIONS = {
    "request": Action.CREATE,
    "return-credit": Action.CREATE,
    "approve": Action.APPROVE,
    "reject": Action.APPROVE,
    "execute": Action.EDIT,
}


def _handle_discount(request: HttpRequest, form: DiscountForm) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in DISCOUNT_ACTIONS:
        policy.require(request.user, Screen.DISCOUNTS, DISCOUNT_ACTIONS[action], request=request)
    try:
        if action == "grant":
            if not form.is_valid():
                return None
            data = dict(form.cleaned_data)
            code = data.pop("enrollment_code")
            discount_service.grant_discount(
                actor=request.user,
                enrollment=_enrollment(request, code),
                request=request,
                **data,
            )
            messages.success(request, _("سُجِّل الخصم"))
        elif action == "approve":
            discount_service.approve_discount(
                actor=request.user,
                discount=discount_service.get_discount(
                    actor=request.user, discount_id=int(request.POST["id"]), request=request
                ),
                request=request,
            )
            messages.success(request, _("اعتُمد الخصم"))
        else:
            return None
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except (ObjectDoesNotExist, KeyError, ValueError):
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("billing:discounts")


# ---------------------------------------------------------------------------
# Refunds and credit returns (Screen.REFUNDS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def refunds_view(request: HttpRequest) -> HttpResponse:
    choices = _enrollment_choices(request)
    action = request.POST.get("action", "")
    refund_form = RefundForm(
        request.POST if action == "request" else None, enrollment_choices=choices
    )
    credit_form = CreditReturnForm(
        request.POST if action == "return-credit" else None, enrollment_choices=choices
    )

    if request.method == "POST":
        response = _handle_refund(request, refund_form, credit_form)
        if response is not None:
            return response

    return render(
        request,
        "billing/refunds.html",
        {
            "title": _("الاستردادات وردّ الأرصدة"),
            "active_screen": Screen.REFUNDS,
            "refunds": refund_service.list_refunds(actor=request.user, request=request),
            "credit_returns": credit_service.list_credit_returns(
                actor=request.user, request=request
            ),
            "refund_form": refund_form,
            "credit_form": credit_form,
            "can_create": policy.is_allowed(request.user, Screen.REFUNDS, Action.CREATE),
            "can_approve": policy.is_allowed(request.user, Screen.REFUNDS, Action.APPROVE),
            "can_execute": policy.is_allowed(request.user, Screen.REFUNDS, Action.EDIT),
            "current_user_id": request.user.pk,
        },
    )


def _handle_refund(
    request: HttpRequest, refund_form: RefundForm, credit_form: CreditReturnForm
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in REFUND_ACTIONS:
        policy.require(request.user, Screen.REFUNDS, REFUND_ACTIONS[action], request=request)
    try:
        if action == "request":
            if not refund_form.is_valid():
                return None
            data = dict(refund_form.cleaned_data)
            code = data.pop("enrollment_code")
            refund_service.request_refund(
                actor=request.user,
                enrollment=_enrollment(request, code),
                request=request,
                **data,
            )
            messages.success(request, _("سُجِّل طلب الاسترداد"))
        elif action == "return-credit":
            if not credit_form.is_valid():
                return None
            data = dict(credit_form.cleaned_data)
            code = data.pop("enrollment_code")
            credit_service.return_credit(
                actor=request.user,
                enrollment=_enrollment(request, code),
                request=request,
                **data,
            )
            messages.success(request, _("رُدّ الرصيد الدائن"))
        elif action in {"approve", "reject", "execute"}:
            _refund_transition(request, action)
        else:
            return None
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("billing:refunds")


def _refund_transition(request: HttpRequest, action: str) -> None:
    from datetime import date

    refund = refund_service.get_refund(
        actor=request.user, code=request.POST.get("code", ""), request=request
    )
    if action == "approve":
        refund_service.approve_refund(actor=request.user, refund=refund, request=request)
        messages.success(request, _("اعتُمد الاسترداد"))
    elif action == "reject":
        refund_service.reject_refund(
            actor=request.user,
            refund=refund,
            reason_ar=request.POST.get("reason_ar", ""),
            request=request,
        )
        messages.success(request, _("رُفض الاسترداد"))
    else:
        refund_service.execute_refund(
            actor=request.user, refund=refund, executed_on=date.today(), request=request
        )
        messages.success(request, _("نُفِّذ الاسترداد"))


# ---------------------------------------------------------------------------
# Extra fees (Screen.EXTRA_FEES)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def extra_fees_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.EXTRA_FEES, Action.CREATE)
    form = ExtraFeeForm(request.POST or None, enrollment_choices=_enrollment_choices(request))

    if request.method == "POST":
        if not can_create:
            raise PermissionDenied
        if form.is_valid():
            data = dict(form.cleaned_data)
            code = data.pop("enrollment_code")
            try:
                extra_fee_service.charge_extra_fee(
                    actor=request.user,
                    enrollment=_enrollment(request, code),
                    request=request,
                    **data,
                )
                messages.success(request, _("حُمِّل الرسم"))
                return redirect("billing:extra-fees")
            except DjangoValidationError as exc:
                messages.error(request, _message_of(exc))
            except ObjectDoesNotExist:
                messages.error(request, _("تسجيل غير معروف"))

    return render(
        request,
        "billing/extra_fees.html",
        {
            "title": _("الرسوم الإضافية"),
            "active_screen": Screen.EXTRA_FEES,
            "fees": extra_fee_service.list_extra_fees(actor=request.user, request=request),
            "form": form,
            "can_create": can_create,
        },
    )
