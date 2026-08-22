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

from apps.billing.forms import (
    CreditReturnForm,
    DiscountForm,
    ExtraFeeForm,
    OpeningBalanceProposeForm,
    OpeningBalanceReviewForm,
    RefundForm,
)
from apps.billing.services import (
    credit_service,
    discount_service,
    extra_fee_service,
    opening_balance_service,
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


# ---------------------------------------------------------------------------
# Opening balances (Screen.OPENING_BALANCES) — Sprint 8D-2
# ---------------------------------------------------------------------------
#: Which permission each step needs, checked before the service runs. Note
#: that ``post`` needs APPROVE and not CREATE: writing the ledger row is the
#: manager's act, and the officer who proposed the balance may not perform it.
OPENING_BALANCE_ACTIONS = {
    "propose": Action.CREATE,
    "review": Action.EDIT,
    "approve": Action.APPROVE,
    "reject": Action.APPROVE,
    "post": Action.APPROVE,
}

#: Every business refusal this screen can meet, so a rule arrives as a message
#: carrying its own reference while a role violation stays a 403.
OPENING_BALANCE_REFUSALS = (
    opening_balance_service.AlreadyPostedError,
    opening_balance_service.CreditNotPostableError,
    opening_balance_service.NoEnrollmentError,
    opening_balance_service.OpeningBalanceStateError,
    opening_balance_service.SeparationOfDutiesError,
)


@require_http_methods(["GET", "POST"])
def opening_balances_view(request: HttpRequest) -> HttpResponse:
    """
    The four-hand workflow on one page (BR-094).

    Deliberately one screen rather than four: the whole point of D-24 is that
    a reader can see who proposed, who reviewed and who approved a given
    balance side by side. Splitting the steps across screens would hide the
    separation the rule exists to create.
    """
    can_propose = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.CREATE)
    can_review = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.EDIT)
    can_decide = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.APPROVE)

    propose_form = OpeningBalanceProposeForm(
        request.POST if request.POST.get("action") == "propose" else None,
        direction_choices=opening_balance_service.direction_choices(),
    )

    if request.method == "POST":
        response = _handle_opening_balance(request, propose_form)
        if response is not None:
            return response

    return render(
        request,
        "billing/opening_balances.html",
        {
            "title": _("الأرصدة الافتتاحية"),
            "active_screen": Screen.OPENING_BALANCES,
            "balances": opening_balance_service.list_balances(
                actor=request.user,
                status=request.GET.get("status", "").strip(),
                direction=request.GET.get("direction", "").strip(),
                request=request,
            ),
            "totals": opening_balance_service.totals(actor=request.user, request=request),
            "statuses": opening_balance_service.status_choices(),
            "directions": opening_balance_service.direction_choices(),
            "form": propose_form if can_propose else None,
            "review_form": OpeningBalanceReviewForm() if can_review else None,
            "can_propose": can_propose,
            "can_review": can_review,
            "can_decide": can_decide,
            "current_user_id": request.user.pk,
        },
    )


def _handle_opening_balance(
    request: HttpRequest, propose_form: OpeningBalanceProposeForm
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in OPENING_BALANCE_ACTIONS:
        return None
    policy.require(
        request.user, Screen.OPENING_BALANCES, OPENING_BALANCE_ACTIONS[action], request=request
    )

    try:
        if action == "propose":
            return _propose_opening_balance(request, propose_form)
        return _advance_opening_balance(request, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    except OPENING_BALANCE_REFUSALS as exc:
        messages.error(request, str(exc))
        return None


def _propose_opening_balance(
    request: HttpRequest, form: OpeningBalanceProposeForm
) -> HttpResponse | None:
    if not form.is_valid():
        return None
    data = form.cleaned_data
    row_id = data["source_row_id"]

    if row_id:
        from apps.datamigration.services import read_service

        historical = read_service.historical_enrollment_for(
            actor=request.user, source_row_id=row_id, request=request
        )
        opening_balance_service.propose_from_archive(
            actor=request.user,
            historical_enrollment=historical,
            code=data["code"],
            direction=data["direction"],
            amount=data["amount"],
            as_of=data["as_of"],
            description_ar=data["description_ar"],
            request=request,
        )
    else:
        opening_balance_service.propose_manually(
            actor=request.user,
            code=data["code"],
            direction=data["direction"],
            amount=data["amount"],
            as_of=data["as_of"],
            description_ar=data["description_ar"],
            legacy_number=data["legacy_number"],
            request=request,
        )

    messages.success(request, _("سُجّل الاقتراح — لا أثر في الدفتر حتى الترحيل"))
    return redirect("billing:opening-balances")


def _advance_opening_balance(request: HttpRequest, action: str) -> HttpResponse | None:
    balance = opening_balance_service.balance_instance(
        actor=request.user, code=request.POST.get("code", ""), request=request
    )
    note = request.POST.get("note_ar", "")

    if action == "review":
        enrollment_code = request.POST.get("enrollment_code", "").strip()
        opening_balance_service.review(
            actor=request.user,
            balance=balance,
            enrollment=_enrollment(request, enrollment_code) if enrollment_code else None,
            note_ar=note,
            request=request,
        )
        messages.success(request, _("رُوجع الرصيد — ولم يتحرك شيء في الدفتر"))
    elif action == "approve":
        opening_balance_service.approve(
            actor=request.user, balance=balance, note_ar=note, request=request
        )
        messages.success(request, _("اعتُمد الرصيد — الاعتماد إذن بالترحيل لا ترحيل"))
    elif action == "reject":
        opening_balance_service.reject(
            actor=request.user, balance=balance, note_ar=note, request=request
        )
        messages.success(request, _("رُفض الرصيد"))
    else:
        line = opening_balance_service.post(actor=request.user, balance=balance, request=request)
        messages.success(
            request,
            _("رُحّل الرصيد إلى الدفتر — بند رسم %(amount)s") % {"amount": line.gross_amount},
        )
    return redirect("billing:opening-balances")
