"""
Cashbox screens — receipts, taking payment, and the daily closing.

Views render and delegate. No business rule is decided here: the minimum
first payment (BR-020), who may void (BR-025), and who may certify a count
(BR-028) all live in the services and are shown to the user in the services'
own words.

**D-01 shows up as an absence.** The centre manager may VIEW the payments
screen and has no CREATE on ``payment-new``, so the "take payment" entry never
appears for them — and ``policy.require`` refuses the POST as well, for anyone
who arrives at the URL another way.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.cashbox.forms import ClosingForm, PaymentForm, ReconcileForm, VoidRequestForm
from apps.cashbox.services import closing_service, payment_service
from apps.core.services.settings_service import get_setting
from apps.operations.services import enrollment_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


def _parse_date(raw: str) -> date | None:
    """A filter date from the query string, or None when it is absent or junk."""
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Receipts (Screen.PAYMENTS)
# ---------------------------------------------------------------------------
def payments_view(request: HttpRequest) -> HttpResponse:
    rows = payment_service.list_receipts(
        actor=request.user,
        query=request.GET.get("q", "").strip(),
        on_date=_parse_date(request.GET.get("on", "").strip()),
        request=request,
    )
    return render(
        request,
        "cashbox/payments.html",
        {
            "title": _("الدفعات وسندات القبض"),
            "active_screen": Screen.PAYMENTS,
            "receipts": rows,
            "query": request.GET.get("q", ""),
            "on_date": request.GET.get("on", ""),
            "can_create": policy.is_allowed(request.user, Screen.PAYMENT_NEW, Action.CREATE),
        },
    )


@require_http_methods(["GET", "POST"])
def receipt_detail_view(request: HttpRequest, number: str) -> HttpResponse:
    try:
        receipt = payment_service.get_receipt(actor=request.user, number=number, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    form = VoidRequestForm(request.POST or None)
    if request.method == "POST":
        response = _handle_void(request, number, form)
        if response is not None:
            return response

    return render(
        request,
        "cashbox/receipt_detail.html",
        {
            "title": _("سند قبض"),
            "active_screen": Screen.PAYMENTS,
            "receipt": receipt,
            "form": form,
            "next_enrollment_code": request.GET.get("enrollment", "").strip(),
            # Δ-06 — the cashier ASKS (create) and the finance officer
            # DECIDES (void). Reversing these would let one person do both.
            "can_request_void": policy.is_allowed(request.user, Screen.PAYMENTS, Action.CREATE),
            "can_approve_void": policy.is_allowed(request.user, Screen.PAYMENTS, Action.VOID),
        },
    )


#: Δ-06 — asking for a void is a CREATE the cashier holds; approving one is
#: the VOID action only the finance officer holds. Checked before the service
#: so the wrong role gets a 403 rather than a message about a rule.
VOID_ACTIONS = {"request": Action.CREATE, "approve": Action.VOID}
CLOSING_ACTIONS = {"open": Action.CREATE, "reconcile": Action.APPROVE}


def _handle_void(request: HttpRequest, number: str, form: VoidRequestForm) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in VOID_ACTIONS:
        policy.require(request.user, Screen.PAYMENTS, VOID_ACTIONS[action], request=request)
    try:
        if action == "request" and form.is_valid():
            payment_service.request_void(
                actor=request.user,
                receipt=payment_service.receipt_instance(
                    actor=request.user, number=number, request=request
                ),
                reason_ar=form.cleaned_data["reason_ar"],
                request=request,
            )
            messages.success(request, _("سُجِّل طلب الإلغاء"))
        elif action == "approve":
            payment_service.approve_void(
                actor=request.user,
                void_record=payment_service.void_instance(
                    actor=request.user, number=number, request=request
                ),
                request=request,
            )
            messages.success(request, _("اعتُمد الإلغاء"))
        else:
            return None
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("لا يوجد طلب إلغاء على هذا السند"))
        return None
    return redirect("cashbox:receipt-detail", number=number)


# ---------------------------------------------------------------------------
# Taking payment (Screen.PAYMENT_NEW)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def payment_new_view(request: HttpRequest) -> HttpResponse:
    policy.require(request.user, Screen.PAYMENT_NEW, Action.VIEW, request=request)

    # Materialised once: the form is built from these and the Q-15 lookup below
    # checks the posted code against the same list, so the two cannot disagree.
    enrollment_choices = enrollment_service.payable_enrollment_choices(
        actor=request.user, request=request
    )
    form = PaymentForm(
        request.POST or None,
        enrollment_choices=enrollment_choices,
        method_choices=payment_service.payment_method_choices(),
    )

    minimum_first_payment = _minimum_first_payment_today()

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            enrollment = enrollment_service.payable_enrollment(
                actor=request.user, code=data["enrollment_code"], request=request
            )
            receipt = payment_service.take_payment(
                actor=request.user,
                enrollment=enrollment,
                amount=data["amount"],
                payment_method=payment_service.method_by_code(data["payment_method"]),
                received_on=data["received_on"],
                external_receipt_ref=data["external_receipt_ref"],
                breakdown_text_ar=data["breakdown_text_ar"],
                request=request,
            )
            messages.success(
                request, _("صدر سند القبض %(n)s") % {"n": receipt.internal_receipt_number}
            )
            receipt_url = reverse(
                "cashbox:receipt-detail", kwargs={"number": receipt.internal_receipt_number}
            )
            return redirect(f"{receipt_url}?{urlencode({'enrollment': enrollment.code})}")
        except DjangoValidationError as exc:
            messages.error(request, _message_of(exc))
        except ObjectDoesNotExist:
            messages.error(request, _("تسجيل أو طريقة دفع غير معروفة"))

    return render(
        request,
        "cashbox/payment_new.html",
        {
            "title": _("استيفاء دفعة"),
            "active_screen": Screen.PAYMENT_NEW,
            "form": form,
            # BR-020's figure, read rather than written on the screen. The
            # template printed «400» as prose while the rule is enforced from
            # this effective-dated setting, so changing the setting left the
            # page stating a number the system no longer refused below.
            "minimum_first_payment": minimum_first_payment,
            "minimum_breakdown_holds": minimum_first_payment == _BREAKDOWN_HOLDS_AT,
            # Q-15 — the selected diploma's own floor, when one was chosen and
            # it carries one. A field read, not a verdict: whether BR-020
            # applies to THIS payment is the service's call, on the receipt's
            # date and on whether a first payment was already taken.
            "selected_program_minimum": _selected_program_minimum(request, enrollment_choices),
        },
    )


def _selected_program_minimum(
    request: HttpRequest, enrollment_choices: list[tuple[str, str]]
) -> Decimal | None:
    """
    ``minimum_first_payment_override`` of the enrolment the reader picked.

    Only ever answers on a POST that came back — a GET has no selection, and
    guessing a programme's floor before one is chosen would be inventing a
    figure. The posted code is checked against the very list the form was built
    from before it is resolved, so raw POST input never reaches a lookup, and
    the enrolment is read through the same service accessor the save path uses.

    Returns None when nothing was selected, when the code is not one of the
    offered choices, when it resolves to nothing, or when the programme has no
    override — all of which mean the same thing on screen: say nothing extra.
    """
    if request.method != "POST":
        return None

    code = (request.POST.get("enrollment_code") or "").strip()
    offered = {offered_code for offered_code, _label in enrollment_choices}
    if not code or code not in offered:
        return None

    try:
        enrollment = enrollment_service.payable_enrollment(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        return None
    return enrollment.cohort.program.minimum_first_payment_override


#: The published split — 300 registration plus 100 for the first subject — is
#: the reasoning behind ONE value and holds for no other. Nothing in the system
#: records how a different minimum divides, so the breakdown is shown beside
#: this figure and dropped beside any other rather than guessed at.
_BREAKDOWN_HOLDS_AT = Decimal("400")


def _minimum_first_payment_today() -> Decimal | None:
    """
    The diploma minimum in effect TODAY, for display only.

    ``as_of`` is today because the reader has not chosen a payment date yet;
    the rule itself is checked in ``payment_service`` against the date on the
    receipt, and a diploma may carry its own higher override (Q-15). So this
    is what the screen SAYS, never what the save decides.

    Returns None when the setting is not configured, and the sentence is then
    left out entirely rather than guessing a figure.
    """
    return get_setting(
        payment_service.MIN_FIRST_PAYMENT_KEY, as_of=timezone.localdate(), default=None
    )


# ---------------------------------------------------------------------------
# Daily closing (Screen.CLOSING)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def closing_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CLOSING, Action.CREATE)
    form = None
    if can_create:
        form = ClosingForm(
            request.POST if request.POST.get("action") == "open" else None,
            cashier_choices=closing_service.cashier_choices(),
        )

    if request.method == "POST":
        response = _handle_closing(request, form)
        if response is not None:
            return response

    rows = closing_service.list_closings(
        actor=request.user, on_date=_parse_date(request.GET.get("on", "").strip()), request=request
    )
    return render(
        request,
        "cashbox/closing.html",
        {
            "title": _("الإقفال اليومي"),
            "active_screen": Screen.CLOSING,
            "closings": rows,
            "form": form,
            "reconcile_form": ReconcileForm(),
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.CLOSING, Action.APPROVE),
            "on_date": request.GET.get("on", ""),
        },
    )


def _handle_closing(request: HttpRequest, form: ClosingForm | None) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLOSING_ACTIONS:
        policy.require(request.user, Screen.CLOSING, CLOSING_ACTIONS[action], request=request)
    try:
        if action == "open":
            if form is None:
                raise PermissionDenied
            if not form.is_valid():
                return None
            data = form.cleaned_data
            closing_service.open_closing(
                actor=request.user,
                cashier=closing_service.cashier_instance(int(data["cashier_id"])),
                closing_date=data["closing_date"],
                counted_total=data["counted_total"],
                request=request,
            )
            messages.success(request, _("فُتح الإقفال"))
        elif action == "reconcile":
            reconcile_form = ReconcileForm(request.POST)
            reconcile_form.is_valid()
            closing_service.reconcile(
                actor=request.user,
                closing=closing_service.closing_instance(
                    actor=request.user, code=request.POST.get("code", ""), request=request
                ),
                variance_resolution_ar=reconcile_form.cleaned_data.get(
                    "variance_resolution_ar", ""
                ),
                request=request,
            )
            messages.success(request, _("اعتُمد الإقفال"))
        else:
            return None
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("cashbox:closing")
