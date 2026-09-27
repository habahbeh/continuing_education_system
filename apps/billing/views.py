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

from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.billing.forms import (
    CreditReturnForm,
    DiscountForm,
    ExtraFeeForm,
    OpeningBalanceProposeForm,
    RefundForm,
)
from apps.billing.services import (
    credit_service,
    discount_service,
    extra_fee_service,
    opening_balance_service,
    refund_service,
)
from apps.cashbox.services import payment_service
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
    """
    The discounts register and, beside it, the grant form.

    The form offers only enrolments a discount can still land on (the same
    tests the service applies), each carrying its tuition base and the
    cohort's split so the amount, the remainder and each party's share are
    previewed before the save. ``?enrollment=`` from the account page both
    narrows the register and preselects the form.
    """
    can_create = policy.is_allowed(request.user, Screen.DISCOUNTS, Action.CREATE)
    enrollment_code = request.GET.get("enrollment", "").strip()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if status not in discount_service.DISCOUNT_FILTERS:
        status = ""

    candidates = (
        discount_service.discountable_rows(actor=request.user, request=request)
        if can_create
        else []
    )
    form = DiscountForm(
        request.POST if request.POST.get("action") == "grant" else None,
        initial={"enrollment_code": enrollment_code} if enrollment_code else None,
        enrollment_choices=[(r["code"], f'{r["code"]} — {r["participant_name"]}') for r in candidates],
    )

    if request.method == "POST":
        response = _handle_discount(request, form)
        if response is not None:
            return response

    rows = discount_service.list_discounts(
        actor=request.user,
        enrollment_code=enrollment_code,
        query=query,
        status=status,
        request=request,
    )
    summary = discount_service.discounts_summary(rows)
    base_url = reverse("billing:discounts")
    keep = {k: v for k, v in (("q", query), ("enrollment", enrollment_code)) if v}

    def _tile(key: str, label: str, value: object, tone: str, icon: str) -> dict:
        params = dict(keep)
        if key and status != key:
            params["status"] = key
        qs = urlencode(params)
        return {
            "key": key,
            "label": label,
            "value": value,
            "tone": tone,
            "icon": icon,
            "on": bool(key) and status == key,
            "url": base_url + (f"?{qs}" if qs else ""),
        }

    tiles = [
        _tile("", _("الخصومات"), summary["count"], "info", "percent"),
        _tile("pending", _("بانتظار الاعتماد"), summary["pending"], "amber", "checks"),
        _tile("", _("حصة الجامعة"), summary["university"], "violet", "building"),
        _tile("", _("حصة الشريك"), summary["partner"], "teal", "swap"),
    ]
    tiles[0]["url"] = base_url + (f"?{urlencode(keep)}" if keep else "")
    tiles[2]["url"] = tiles[3]["url"] = tiles[0]["url"]

    return render(
        request,
        "billing/discounts.html",
        {
            "title": _("الخصومات"),
            "active_screen": Screen.DISCOUNTS,
            "discounts": rows,
            "summary": summary,
            "tiles": tiles,
            "candidates": candidates,
            "form": form,
            "query": query,
            "status": status,
            "enrollment_code": enrollment_code,
            "is_filtered": bool(query or status or enrollment_code),
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.DISCOUNTS, Action.APPROVE),
            "current_user_id": request.user.pk,
            "posted_action": request.POST.get("action", ""),
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
            messages.success(request, _("سُجِّل الخصم — يظهر في كشف حساب المشارك فوراً"))
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
    """
    Two registers and, beside them, the two forms on tabs.

    Each form offers only what its service would accept — enrolments with
    money collected for a refund, enrolments holding a credit for a return —
    and every option carries the figure the form previews. ``?enrollment=``
    from the account page preselects and opens the matching tab; ``?tab=``
    picks one outright.
    """
    today = timezone.localdate()
    can_create = policy.is_allowed(request.user, Screen.REFUNDS, Action.CREATE)
    enrollment_code = request.GET.get("enrollment", "").strip()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if status not in refund_service.REFUND_FILTERS:
        status = ""

    refundable = (
        refund_service.refundable_rows(actor=request.user, request=request) if can_create else []
    )
    creditable = (
        credit_service.creditable_rows(actor=request.user, request=request) if can_create else []
    )
    action = request.POST.get("action", "")
    refund_form = RefundForm(
        request.POST if action == "request" else None,
        initial={"enrollment_code": enrollment_code} if enrollment_code else None,
        enrollment_choices=[(r["code"], f'{r["code"]} — {r["participant_name"]}') for r in refundable],
    )
    credit_form = CreditReturnForm(
        request.POST if action == "return-credit" else None,
        initial={"enrollment_code": enrollment_code, "returned_on": today},
        enrollment_choices=[(r["code"], f'{r["code"]} — {r["participant_name"]}') for r in creditable],
    )

    if request.method == "POST":
        response = _handle_refund(request, refund_form, credit_form, refundable)
        if response is not None:
            return response

    # Which tab opens: the posted one, the asked one, or the one that can act
    # on the enrolment that brought the reader here.
    tab = request.POST.get("action", "") if request.method == "POST" else request.GET.get("tab", "")
    tab = {"return-credit": "credit", "credit": "credit"}.get(tab, "refund")
    if request.method == "GET" and enrollment_code and not request.GET.get("tab"):
        # A credit balance is the lighter, correct path when one exists.
        tab = "credit" if any(r["code"] == enrollment_code for r in creditable) else "refund"

    refunds = refund_service.list_refunds(
        actor=request.user,
        enrollment_code=enrollment_code,
        status=status,
        query=query,
        request=request,
    )
    summary = refund_service.refunds_summary(refunds)
    base_url = reverse("billing:refunds")
    keep = {k: v for k, v in (("q", query), ("enrollment", enrollment_code)) if v}

    def _tile(key: str, label: str, value: object, tone: str, icon: str) -> dict:
        params = dict(keep)
        if key and status != key:
            params["status"] = key
        qs = urlencode(params)
        return {
            "key": key,
            "label": label,
            "value": value,
            "tone": tone,
            "icon": icon,
            "on": bool(key) and status == key,
            "url": base_url + (f"?{qs}" if qs else ""),
        }

    tiles = [
        _tile("REQUESTED", _("بانتظار الاعتماد"), summary["requested"], "amber", "checks"),
        _tile("APPROVED", _("بانتظار التنفيذ"), summary["approved"], "info", "coins"),
        _tile("EXECUTED", _("المنفَّذ"), summary["executed_total"], "ok", "undo"),
        _tile("", _("استرجاع من الشريك"), summary["recovery_total"], "teal", "swap"),
    ]
    tiles[3]["url"] = base_url + (f"?{urlencode(keep)}" if keep else "")

    return render(
        request,
        "billing/refunds.html",
        {
            "title": _("الاستردادات وردّ الأرصدة"),
            "active_screen": Screen.REFUNDS,
            "refunds": refunds,
            "summary": summary,
            "tiles": tiles,
            "credit_returns": credit_service.list_credit_returns(
                actor=request.user, enrollment_code=enrollment_code, request=request
            ),
            "refund_form": refund_form,
            "credit_form": credit_form,
            "refundable": refundable,
            "creditable": creditable,
            "tab": tab,
            "query": query,
            "status": status,
            "enrollment_code": enrollment_code,
            "is_filtered": bool(query or status or enrollment_code),
            "today": today,
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.REFUNDS, Action.APPROVE),
            "can_execute": policy.is_allowed(request.user, Screen.REFUNDS, Action.EDIT),
            "current_user_id": request.user.pk,
            "posted_action": action,
            "posted_code": request.POST.get("code", ""),
        },
    )


def _handle_refund(
    request: HttpRequest,
    refund_form: RefundForm,
    credit_form: CreditReturnForm,
    refundable: list[dict],
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
            if data["refund_type"] == "FULL" or data["amount"] is None:
                # FULL = everything collected; the service's own ceiling.
                row = next((r for r in refundable if r["code"] == code), None)
                data["amount"] = row["paid"] if row else data["amount"]
            refund_service.request_refund(
                actor=request.user,
                enrollment=_enrollment(request, code),
                request=request,
                **data,
            )
            messages.success(request, _("سُجِّل طلب الاسترداد — بانتظار اعتماد مدير المركز"))
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
            messages.success(request, _("رُدّ الرصيد الدائن كاملاً"))
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
    refund = refund_service.get_refund(
        actor=request.user, code=request.POST.get("code", ""), request=request
    )
    if action == "approve":
        refund_service.approve_refund(actor=request.user, refund=refund, request=request)
        messages.success(request, _("اعتُمد الاسترداد — بانتظار التنفيذ من الموظف المالي"))
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
            actor=request.user,
            refund=refund,
            executed_on=timezone.localdate(),
            request=request,
        )
        messages.success(request, _("نُفِّذ الاسترداد وسُجِّل خروج المال"))


# ---------------------------------------------------------------------------
# Extra fees (Screen.EXTRA_FEES)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def extra_fees_view(request: HttpRequest) -> HttpResponse:
    """
    The register beside the charge form. The form's type cards carry the
    seeded amount and the sharing rule each type charges with, so the reader
    sees the figure and who bears it before the save; ``?enrollment=`` and
    ``?type=`` from other screens preselect.
    """
    today = timezone.localdate()
    can_create = policy.is_allowed(request.user, Screen.EXTRA_FEES, Action.CREATE)
    enrollment_code = request.GET.get("enrollment", "").strip()
    query = request.GET.get("q", "").strip()
    fee_type = request.GET.get("type", "").strip()
    if fee_type not in extra_fee_service.FEE_TYPE_NEEDS:
        fee_type = ""

    candidates = (
        extra_fee_service.chargeable_rows(actor=request.user, request=request) if can_create else []
    )
    form = ExtraFeeForm(
        request.POST or None,
        initial={"enrollment_code": enrollment_code, "charged_on": today, "fee_type": fee_type or None},
        enrollment_choices=[(r["code"], f'{r["code"]} — {r["participant_name"]}') for r in candidates],
    )

    if request.method == "POST":
        policy.require(request.user, Screen.EXTRA_FEES, Action.CREATE, request=request)
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
                messages.success(request, _("حُمِّل الرسم وصار بنداً في حساب المشارك"))
                return redirect("billing:extra-fees")
            except DjangoValidationError as exc:
                messages.error(request, _message_of(exc))
            except ObjectDoesNotExist:
                messages.error(request, _("تسجيل غير معروف"))

    fees = extra_fee_service.list_extra_fees(
        actor=request.user,
        enrollment_code=enrollment_code,
        fee_type=fee_type,
        query=query,
        request=request,
    )
    summary = extra_fee_service.fees_summary(
        extra_fee_service.list_extra_fees(
            actor=request.user, enrollment_code=enrollment_code, query=query, request=request
        )
        if fee_type
        else fees
    )
    base_url = reverse("billing:extra-fees")
    keep = {k: v for k, v in (("q", query), ("enrollment", enrollment_code)) if v}
    tiles = []
    for key, bucket in summary.items():
        params = dict(keep)
        if fee_type != key:
            params["type"] = key
        qs = urlencode(params)
        tone, icon = extra_fee_service.FEE_TYPE_LOOK[key]
        tiles.append(
            {
                "key": key,
                "label": bucket["label"],
                "value": bucket["count"],
                "foot": bucket["total"],
                "tone": tone,
                "icon": icon,
                "on": fee_type == key,
                "url": base_url + (f"?{qs}" if qs else ""),
            }
        )

    return render(
        request,
        "billing/extra_fees.html",
        {
            "title": _("الرسوم الإضافية"),
            "active_screen": Screen.EXTRA_FEES,
            "fees": fees,
            "tiles": tiles,
            "summary": summary,
            "form": form,
            "candidates": candidates,
            "type_cards": extra_fee_service.fee_type_cards(as_of=today),
            "query": query,
            "fee_type": fee_type,
            "enrollment_code": enrollment_code,
            "is_filtered": bool(query or fee_type or enrollment_code),
            "today": today,
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
    # Sprint 8D-3 — resolving a credit is the manager's act too. It writes no
    # ledger row, but it decides who ends up with money, which is the same
    # weight of decision.
    "carry-forward": Action.APPROVE,
    "refund-due": Action.APPROVE,
    # Sprint 8D-4 — the officer executes what the manager declared, the same
    # split ``Refund`` uses. EDIT, not APPROVE: paying is not deciding.
    "pay-refund": Action.EDIT,
    # Sprint 8D-5 — correcting a payout is the same hand that made it: the
    # officer who handed the cash over is the one who knows it went wrong.
    "reverse-refund": Action.EDIT,
}

#: Every business refusal this screen can meet, so a rule arrives as a message
#: carrying its own reference while a role violation stays a 403.
OPENING_BALANCE_REFUSALS = (
    opening_balance_service.AlreadyPostedError,
    opening_balance_service.AlreadyRefundedError,
    opening_balance_service.AlreadyResolvedError,
    opening_balance_service.MissingPayoutDetailsError,
    opening_balance_service.NotRefundDueError,
    opening_balance_service.NotReversibleError,
    opening_balance_service.NotACreditError,
    opening_balance_service.NotALaterRegistrationError,
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
    balance side by side. The tiles are the stage filter; every act on a row
    opens its own dialog; ``?row=`` from the archive fills the proposal with
    the archive row it comes from.
    """
    today = timezone.localdate()
    can_propose = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.CREATE)
    can_review = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.EDIT)
    can_decide = policy.is_allowed(request.user, Screen.OPENING_BALANCES, Action.APPROVE)

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    statuses = opening_balance_service.status_choices()
    if status not in {c for c, _l in statuses}:
        status = ""
    direction = request.GET.get("direction", "").strip()
    directions = opening_balance_service.direction_choices()
    if direction not in {c for c, _l in directions}:
        direction = ""

    row_id = _parse_int(request.GET.get("row", "")) or _parse_int(request.POST.get("source_row_id", ""))
    archive_row = (
        opening_balance_service.archive_row_summary(
            actor=request.user, source_row_id=row_id, request=request
        )
        if row_id and can_propose
        else None
    )
    propose_form = OpeningBalanceProposeForm(
        request.POST if request.POST.get("action") == "propose" else None,
        initial={
            "source_row_id": row_id or None,
            "legacy_number": archive_row["legacy_number"] if archive_row else "",
            "as_of": today,
            "direction": "RECEIVABLE",
        },
        direction_choices=directions,
    )

    if request.method == "POST":
        response = _handle_opening_balance(request, propose_form)
        if response is not None:
            return response

    common = {"actor": request.user, "direction": direction, "query": query, "request": request}
    rows = opening_balance_service.list_balances(status=status, **common)
    summary = opening_balance_service.balances_summary(
        opening_balance_service.list_balances(**common) if status else rows
    )
    # Every row a reviewer or decider may act on carries the enrolments it
    # could be attached to — drawn once per row, into its dialog.
    if can_review or can_decide:
        for r in rows:
            if r["status"] in ("DRAFT", "REVIEWED") or r["is_resolvable_credit"]:
                r["enrollment_options"] = opening_balance_service.enrollment_options(
                    actor=request.user,
                    balance=opening_balance_service.balance_instance(
                        actor=request.user, code=r["code"], request=request
                    ),
                    request=request,
                )

    base_url = reverse("billing:opening-balances")
    keep = {k: v for k, v in (("q", query), ("direction", direction)) if v}

    def _tile(key: str, label: str, value: object, foot: object, tone: str, icon: str) -> dict:
        params = dict(keep)
        if key and status != key:
            params["status"] = key
        qs = urlencode(params)
        return {
            "key": key,
            "label": label,
            "value": value,
            "foot": foot,
            "tone": tone,
            "icon": icon,
            "on": bool(key) and status == key,
            "url": base_url + (f"?{qs}" if qs else ""),
        }

    tiles = [
        _tile("DRAFT", _("مسودات تنتظر المراجعة"), summary["draft"], _("لا أثر مالي"), "amber", "doc"),
        _tile("REVIEWED", _("مُراجَعة تنتظر الاعتماد"), summary["reviewed"], _("لا أثر مالي"), "info", "checks"),
        _tile("APPROVED", _("معتمَدة تنتظر الترحيل"), summary["approved"], _("إذن بالترحيل لا ترحيل"), "violet", "swap"),
        _tile("REFUND_DUE", _("تنتظر الصرف نقداً"), summary["refund_due"], summary["refund_due_total"], "danger", "coins"),
    ]

    return render(
        request,
        "billing/opening_balances.html",
        {
            "title": _("الأرصدة الافتتاحية"),
            "active_screen": Screen.OPENING_BALANCES,
            "balances": rows,
            "summary": summary,
            "tiles": tiles,
            "payment_methods": payment_service.payment_method_choices(),
            "today": today,
            "statuses": statuses,
            "directions": directions,
            "query": query,
            "status": status,
            "direction": direction,
            "is_filtered": bool(query or status or direction),
            "form": propose_form if can_propose else None,
            "archive_row": archive_row,
            "can_propose": can_propose,
            "can_review": can_review,
            "can_decide": can_decide,
            "current_user_id": request.user.pk,
            "posted_action": request.POST.get("action", ""),
            "posted_code": request.POST.get("code", ""),
        },
    )


def _parse_int(raw: str) -> int | None:
    try:
        return int(raw) if raw and raw.strip() else None
    except ValueError:
        return None


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


def _movement_date(request: HttpRequest, field: str) -> date:
    """
    A cash date from the form, defaulting to today when the field is blank.

    Sprint 8D-4 hardcoded ``date.today()`` here, which meant a voucher paid
    last Tuesday and entered on Thursday was filed in the wrong day — and, if
    the week straddled a month end, the wrong period. The service validates
    what comes back: not in the future, not inside a closed period.
    """
    raw = request.POST.get(field, "").strip()
    if not raw:
        return timezone.localdate()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DjangoValidationError(f"تاريخ غير صالح: {raw}") from exc


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
            direction=data["direction"],
            amount=data["amount"],
            as_of=data["as_of"],
            description_ar=data["description_ar"],
            request=request,
        )
    else:
        opening_balance_service.propose_manually(
            actor=request.user,
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
    elif action == "carry-forward":
        code = request.POST.get("enrollment_code", "").strip()
        opening_balance_service.carry_forward(
            actor=request.user,
            balance=balance,
            enrollment=_enrollment(request, code) if code else None,
            note_ar=note,
            request=request,
        )
        messages.success(request, _("رُحّل الرصيد الدائن إلى التسجيل اللاحق — بلا سند قبض"))
    elif action == "refund-due":
        opening_balance_service.mark_refund_due(
            actor=request.user, balance=balance, note_ar=note, request=request
        )
        messages.success(request, _("سُجّل الرصيد مستحقاً للردّ نقداً — الصرف بسند حقيقي لاحقاً"))
    elif action == "pay-refund":
        from apps.cashbox.services import payment_service as cashbox

        payout = opening_balance_service.pay_refund_due(
            actor=request.user,
            balance=balance,
            amount=balance.amount,
            paid_on=_movement_date(request, "paid_on"),
            payment_method=cashbox.method_by_code(request.POST.get("payment_method", "").strip()),
            external_reference=request.POST.get("external_reference", ""),
            payee_name_ar=request.POST.get("payee_name_ar", ""),
            note_ar=note,
            request=request,
        )
        messages.success(
            request,
            _("صُرف الرصيد بسند %(ref)s — بلا سند قبض") % {"ref": payout.external_reference},
        )
    elif action == "reverse-refund":
        payout = opening_balance_service.reverse_refund_payout(
            actor=request.user,
            balance=balance,
            reversed_on=_movement_date(request, "reversed_on"),
            reason_ar=request.POST.get("reason_ar", ""),
            reversal_reference=request.POST.get("reversal_reference", ""),
            request=request,
        )
        messages.success(
            request,
            _("عُكس صرف السند %(ref)s — والرصيد عاد مستحقاً للردّ")
            % {"ref": payout.external_reference},
        )
    else:
        line = opening_balance_service.post(actor=request.user, balance=balance, request=request)
        messages.success(
            request,
            _("رُحّل الرصيد إلى الدفتر — بند رسم %(amount)s") % {"amount": line.gross_amount},
        )
    return redirect("billing:opening-balances")
