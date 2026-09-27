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

from datetime import date, timedelta
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

from apps.cashbox.forms import (
    PaymentMethodForm,
    ClosingForm,
    ExternalRefForm,
    PaymentForm,
    ReconcileForm,
    VoidRequestForm,
)
from apps.cashbox.services import closing_service, method_service, payment_service
from apps.core.pagination import PAGE_SIZE as _PAGE_SIZE
from apps.core.pagination import page_of
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
#: Quick date ranges on the register. «today» is the default on a bare
#: visit: the cashier's first question is «كم قبضت اليوم؟».
RANGE_CHOICES = ("today", "yesterday", "week", "month", "all", "custom")
#: مُعاد تصديره من `core.pagination` — الاختبارات تستورده من هنا، والقيمة لها بيتٌ واحد.
PAGE_SIZE = _PAGE_SIZE


def _range_bounds(range_key: str, today: date) -> tuple[date | None, date | None]:
    if range_key == "today":
        return today, today
    if range_key == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if range_key == "week":
        return today - timedelta(days=6), today
    if range_key == "month":
        return today.replace(day=1), today
    return None, None


def payments_view(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    method = request.GET.get("method", "").strip()
    since = _parse_date(request.GET.get("from", "").strip())
    until = _parse_date(request.GET.get("to", "").strip())
    on_date = _parse_date(request.GET.get("on", "").strip())  # kept for older links

    # A range button («اليوم»…) wins over the form's remembered range; a typed
    # date turns the range custom whatever else the form says.
    range_key = (request.GET.get("pick") or request.GET.get("range", "")).strip()
    if since or until:
        range_key = "custom" if not request.GET.get("pick") else range_key
    if range_key not in RANGE_CHOICES:
        # A link that names something (a search, a status, a date) gets the
        # whole register; a bare visit gets today.
        range_key = (
            "custom"
            if (since or until or on_date)
            else "all"
            if (query or status or method)
            else "today"
        )
    if range_key != "custom":
        since, until = _range_bounds(range_key, today)
        on_date = None
    if status not in payment_service.RECEIPT_FILTERS:
        status = ""

    rows = payment_service.list_receipts(
        actor=request.user,
        query=query,
        on_date=on_date,
        since=since,
        until=until,
        status=status,
        method=method,
        request=request,
    )
    summary = payment_service.receipts_summary(rows)

    page = page_of(rows, request.GET.get("page", ""))
    params = {
        k: v
        for k, v in (
            ("q", query),
            ("range", range_key),
            ("from", since.isoformat() if since and range_key == "custom" else ""),
            ("to", until.isoformat() if until and range_key == "custom" else ""),
            ("on", on_date.isoformat() if on_date else ""),
            ("status", status),
            ("method", method),
        )
        if v
    }
    # The cards are the status filter: click one to narrow, click it again
    # to widen. Counts are of the set the other filters left.
    unfiltered = (
        payment_service.receipts_summary(
            payment_service.list_receipts(
                actor=request.user,
                query=query,
                on_date=on_date,
                since=since,
                until=until,
                method=method,
                request=request,
            )
        )
        if status
        else summary
    )
    base = {k: v for k, v in params.items() if k != "status"}

    def _tile(key: str, label: str, value: object, tone: str, icon: str) -> dict:
        target = {**base, **({"status": key} if key and status != key else {})}
        return {
            "key": key,
            "label": label,
            "value": value,
            "tone": tone,
            "icon": icon,
            "on": status == key,
            "url": reverse("cashbox:payments") + ("?" + urlencode(target) if target else ""),
        }

    tiles = [
        _tile("", _("المقبوض في النطاق"), unfiltered["total"], "ok", "coins"),
        _tile("issued", _("سندات صادرة"), unfiltered["issued"], "info", "receipt"),
        _tile("unclosed", _("لم تدخل إقفالاً"), unfiltered["unclosed"], "amber", "vault"),
        _tile("void_pending", _("إلغاء معلّق"), unfiltered["void_pending"], "warn", "undo"),
        _tile("voided", _("ملغاة"), unfiltered["voided"], "danger", "user-off"),
    ]
    return render(
        request,
        "cashbox/payments.html",
        {
            "title": _("الدفعات وسندات القبض"),
            "active_screen": Screen.PAYMENTS,
            "receipts": page["rows"],
            "page": page,
            "summary": summary,
            "tiles": tiles,
            "query": query,
            "on_date": request.GET.get("on", ""),
            "range_key": range_key,
            "since": since,
            "until": until,
            "status": status,
            "method": method,
            "methods": _method_cards(),
            "ranges": (
                ("today", _("اليوم")),
                ("yesterday", _("أمس")),
                ("week", _("٧ أيام")),
                ("month", _("الشهر")),
                ("all", _("الكل")),
            ),
            "is_filtered": bool(query or status or method or range_key != "today"),
            "params": params,
            "params_qs": urlencode(params),
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
    voucher_form = ExternalRefForm(request.POST or None)
    if request.method == "POST":
        response = _handle_void(request, number, form, voucher_form)
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
            "voucher_form": voucher_form,
            "next_enrollment_code": request.GET.get("enrollment", "").strip(),
            "just_issued": request.GET.get("issued") == "1" and receipt["status"] == "ISSUED",
            # Δ-06 — the cashier ASKS (create) and the finance officer
            # DECIDES (void). Reversing these would let one person do both.
            "can_request_void": policy.is_allowed(request.user, Screen.PAYMENTS, Action.CREATE),
            # D-18 — whoever asked for the void never sees the approve control:
            # the service refuses them anyway, and a button that only ever
            # refuses is a trap, not an action.
            "can_approve_void": policy.is_allowed(request.user, Screen.PAYMENTS, Action.VOID)
            and receipt["void_requested_by_id"] != request.user.pk,
            "posted_action": request.POST.get("action", ""),
            "requested_void_myself": receipt["void_requested_by_id"] == request.user.pk,
            # The quick actions, each by the permission it really needs.
            "can_take_payment": policy.is_allowed(request.user, Screen.PAYMENT_NEW, Action.CREATE),
            "can_view_enrollments": policy.is_allowed(
                request.user, Screen.ENROLLMENTS, Action.VIEW
            ),
            "can_record_voucher": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.EDIT),
            # §5.2 — the voucher number is completed by whoever may take
            # payment (the till), only while the receipt still lacks one.
            "can_add_voucher": receipt["voucher_addable"]
            and policy.is_allowed(request.user, Screen.PAYMENT_NEW, Action.CREATE),
        },
    )


def receipt_print_view(request: HttpRequest, number: str) -> HttpResponse:
    """The receipt on paper — original for the participant, copy for the till."""
    try:
        document = payment_service.receipt_document(
            actor=request.user, number=number, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(
        request,
        "print/receipt.html",
        {"receipt": document, "auto_print": request.GET.get("auto") == "1"},
    )


#: Δ-06 — asking for a void is a CREATE the cashier holds; approving one is
#: the VOID action only the finance officer holds. Checked before the service
#: so the wrong role gets a 403 rather than a message about a rule.
VOID_ACTIONS = {
    "request": Action.CREATE,
    "approve": Action.VOID,
    "reject": Action.VOID,
}
#: §5.2 — completing the voucher number belongs to whoever may record the
#: payment itself (§3.4/17), checked on that screen's CREATE.
VOUCHER_ACTION = ("voucher", Screen.PAYMENT_NEW, Action.CREATE)
CLOSING_ACTIONS = {"open": Action.CREATE, "reconcile": Action.APPROVE}


def _handle_void(
    request: HttpRequest, number: str, form: VoidRequestForm, voucher_form: ExternalRefForm
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in VOID_ACTIONS:
        policy.require(request.user, Screen.PAYMENTS, VOID_ACTIONS[action], request=request)
    elif action == VOUCHER_ACTION[0]:
        policy.require(request.user, VOUCHER_ACTION[1], VOUCHER_ACTION[2], request=request)
    try:
        if action == "voucher" and voucher_form.is_valid():
            payment_service.record_external_ref(
                actor=request.user,
                receipt=payment_service.receipt_instance(
                    actor=request.user, number=number, request=request
                ),
                external_receipt_ref=voucher_form.cleaned_data["external_receipt_ref"],
                request=request,
            )
            messages.success(request, _("سُجِّل رقم سند الدائرة المالية على السند"))
        elif action == "request" and form.is_valid():
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
        elif action == "reject" and form.is_valid():
            payment_service.reject_void(
                actor=request.user,
                void_record=payment_service.void_instance(
                    actor=request.user, number=number, request=request
                ),
                reason_ar=form.cleaned_data["reason_ar"],
                request=request,
            )
            messages.success(request, _("رُفض طلب الإلغاء وبقي السند صادراً"))
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
#: «300 تسجيل + 100 أول مادة» explains 400 and no other figure (BR-020).
_BREAKDOWN_HOLDS_AT = Decimal("400.000")

#: Tone and icon per payment-method code; anything else draws the wallet.
_METHOD_LOOK = {
    "CASH": ("ok", "coins"),
    "CHEQUE": ("info", "doc-check"),
    "VISA": ("violet", "wallet"),
    "CARD": ("violet", "wallet"),
    "TRANSFER": ("teal", "swap"),
}


@require_http_methods(["GET", "POST"])
def payment_new_view(request: HttpRequest) -> HttpResponse:
    """
    The till, in three steps on one screen: who pays, on which enrolment,
    how much. Arrives already filled from ``?enrollment=`` (the account, the
    register, the clearance) or from ``?participant=``; a bare visit starts
    at the search box.

    Every rule is the service's — BR-020 is read through the function that
    enforces it, and the posted enrolment is checked against the cards the
    reader was actually shown.
    """
    policy.require(request.user, Screen.PAYMENT_NEW, Action.VIEW, request=request)

    today = timezone.localdate()
    source = request.POST if request.method == "POST" else request.GET
    participant_number = (
        source.get("participant_number") or source.get("participant") or ""
    ).strip()
    preselected = (request.GET.get("enrollment") or "").strip()

    # A link that names the enrolment names the participant with it.
    if preselected and not participant_number:
        try:
            enrollment = enrollment_service.payable_enrollment(
                actor=request.user, code=preselected, request=request
            )
            participant_number = enrollment.participant.participant_number
        except ObjectDoesNotExist:
            messages.error(request, _("التسجيل %(code)s غير معروف") % {"code": preselected})
            preselected = ""

    account = None
    if participant_number:
        account = enrollment_service.payable_participant_account(
            actor=request.user, participant_number=participant_number, request=request
        )
        if account is None:
            messages.error(request, _("لا مشارك بالرقم %(n)s") % {"n": participant_number})
            participant_number = ""

    rows = account["enrollments"] if account else []
    if preselected and preselected not in {r["code"] for r in rows}:
        preselected = ""  # settled, cancelled, or someone else's
    if not preselected and len(rows) == 1:
        preselected = rows[0]["code"]
    selected = next((r for r in rows if r["code"] == preselected), None)

    form = PaymentForm(
        request.POST or None,
        initial={
            "participant_number": participant_number,
            "enrollment_code": preselected,
            "breakdown_text_ar": selected["default_text"] if selected else "",
        }
        if request.method == "GET"
        else None,
        enrollment_choices=[(r["code"], r["cohort_name"]) for r in rows],
        method_choices=payment_service.payment_method_choices(),
        voucher_required=payment_service.external_ref_required(as_of=today),
    )

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
                received_on=today,  # dated the day it is issued, never typed
                external_receipt_ref=data["external_receipt_ref"],
                breakdown_text_ar=data["breakdown_text_ar"],
                request=request,
            )
            # The receipt page greets the issue itself, with the print button
            # in hand — a text message alone left the cashier a click short.
            receipt_url = reverse(
                "cashbox:receipt-detail", kwargs={"number": receipt.internal_receipt_number}
            )
            query = urlencode({"enrollment": enrollment.code, "issued": "1"})
            return redirect(f"{receipt_url}?{query}")
        except DjangoValidationError as exc:
            messages.error(request, _message_of(exc))
        except ObjectDoesNotExist:
            messages.error(request, _("تسجيل أو طريقة دفع غير معروفة"))

    if request.method == "POST":
        posted = (request.POST.get("enrollment_code") or "").strip()
        selected = next((r for r in rows if r["code"] == posted), None) or selected
        preselected = selected["code"] if selected else preselected

    query = (request.GET.get("q") or "").strip()
    general_minimum = _general_minimum(today)
    methods = _method_cards()
    return render(
        request,
        "cashbox/payment_new.html",
        {
            "title": _("استيفاء دفعة"),
            "active_screen": Screen.PAYMENT_NEW,
            "form": form,
            "voucher_required": payment_service.external_ref_required(as_of=today),
            "today": today,
            "query": query,
            "matches": (
                enrollment_service.payable_participant_search(
                    actor=request.user, query=query, request=request
                )
                if query and not account
                else []
            ),
            "recent": (
                enrollment_service.recent_payable_rows(actor=request.user, request=request)
                if not account
                else []
            ),
            "account": account,
            "rows": rows,
            "selected": selected,
            "preselected": preselected,
            "methods": methods,
            "default_method": methods[0]["code"] if methods else "",
            "can_view_account": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.VIEW),
            "can_view_enrollments": policy.is_allowed(
                request.user, Screen.ENROLLMENTS, Action.VIEW
            ),
            # BR-020, for the reader: the general figure the setting carries
            # today, and the selected programme's own floor when it has one.
            # ``take_payment`` still decides, on the receipt's date.
            "minimum_first_payment": general_minimum,
            "minimum_breakdown_holds": general_minimum == _BREAKDOWN_HOLDS_AT,
            "selected_program_minimum": _selected_program_minimum(selected),
        },
    )


def _general_minimum(as_of: date) -> Decimal | None:
    """The effective-dated BR-020 figure, or None when it is not configured."""
    configured = get_setting(payment_service.MIN_FIRST_PAYMENT_KEY, as_of=as_of, default=None)
    return Decimal(str(configured)) if configured is not None else None


def _selected_program_minimum(selected: dict | None) -> Decimal | None:
    """
    The selected diploma's own floor (Q-15), read for display from the row
    the service built. Whether it applies to this payment is not decided
    here; ``payment_service.first_payment_minimum`` answered that already.
    """
    if not selected or not selected["is_first_payment"]:
        return None
    return selected["program_minimum_override"]


def _method_cards() -> list[dict[str, str]]:
    cards = []
    for code, name in payment_service.payment_method_choices():
        tone, icon = _METHOD_LOOK.get(code.upper(), ("brand", "wallet"))
        cards.append({"code": code, "name": name, "tone": tone, "icon": icon})
    return cards


# ---------------------------------------------------------------------------
# Daily closing (Screen.CLOSING)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def closing_view(request: HttpRequest) -> HttpResponse:
    """
    The daily closing: the queue of cashier-days still open, the form that
    closes one (with the system's side shown live before anything is
    typed), and the register of closings with their approval.
    """
    today = timezone.localdate()
    can_create = policy.is_allowed(request.user, Screen.CLOSING, Action.CREATE)
    can_approve = policy.is_allowed(request.user, Screen.CLOSING, Action.APPROVE)

    open_days = closing_service.open_days(actor=request.user, request=request)
    # A queue row («أقفل هذا اليوم») arrives with the cashier and the date; a
    # bare visit starts on the newest open day rather than on a blank form.
    picked_on = _parse_date(request.GET.get("on", "").strip())
    picked_cashier = request.GET.get("cashier", "").strip()
    if not picked_cashier and not picked_on and open_days:
        picked_cashier = str(open_days[0]["cashier_id"])
        picked_on = open_days[0]["closing_date"]
    initial = {"closing_date": picked_on or today, "cashier_id": picked_cashier}
    form = None
    if can_create:
        form = ClosingForm(
            request.POST if request.POST.get("action") == "open" else None,
            initial=initial,
            cashier_choices=closing_service.cashier_choices(),
        )

    if request.method == "POST":
        response = _handle_closing(request, form)
        if response is not None:
            return response

    status = request.GET.get("status", "").strip()
    rows = closing_service.list_closings(actor=request.user, request=request)
    counts = {
        "open_days": len(open_days),
        "pending": sum(1 for r in rows if r["status"] != "RECONCILED"),
        "variance": sum(1 for r in rows if r["status"] == "VARIANCE_PENDING"),
        "month": sum(
            1
            for r in rows
            if r["status"] == "RECONCILED"
            and r["closing_date"].strftime("%Y%m") == today.strftime("%Y%m")
        ),
    }
    if status == "pending":
        rows = [r for r in rows if r["status"] != "RECONCILED"]
    elif status == "variance":
        rows = [r for r in rows if r["status"] == "VARIANCE_PENDING"]
    elif status == "month":
        rows = [
            r
            for r in rows
            if r["status"] == "RECONCILED"
            and r["closing_date"].strftime("%Y%m") == today.strftime("%Y%m")
        ]
    else:
        status = ""

    def _tile(key: str, label: str, value: int, tone: str, icon: str) -> dict:
        return {
            "key": key,
            "label": label,
            "value": value,
            "tone": tone,
            "icon": icon,
            "on": status == key,
            "url": reverse("cashbox:closing") + (f"?status={key}" if key and status != key else ""),
        }

    tiles = [
        _tile("open", _("أيام بلا إقفال"), counts["open_days"], "amber", "clock"),
        _tile("pending", _("بانتظار الاعتماد"), counts["pending"], "info", "checks"),
        _tile("variance", _("فروق قيد المطابقة"), counts["variance"], "danger", "alert"),
        _tile("month", _("مُقفل هذا الشهر"), counts["month"], "ok", "vault"),
    ]
    # The «أيام بلا إقفال» card scrolls to the queue rather than filtering.
    tiles[0]["url"] = reverse("cashbox:closing") + "#open-days"
    tiles[0]["on"] = False

    return render(
        request,
        "cashbox/closing.html",
        {
            "title": _("الإقفال اليومي"),
            "active_screen": Screen.CLOSING,
            "closings": rows,
            "open_days": open_days,
            "tiles": tiles,
            "status": status,
            "form": form,
            "reconcile_form": ReconcileForm(),
            "can_create": can_create,
            "can_approve": can_approve,
            "vouchers_required": closing_service.vouchers_required(as_of=today),
            "today": today,
            "posted_action": request.POST.get("action", ""),
            "posted_code": request.POST.get("code", ""),
            "user_id": request.user.pk,
        },
    )


def _same_month(a: date, b: date) -> bool:
    return (a.year, a.month) == (b.year, b.month)


def closing_preview_view(request: HttpRequest) -> HttpResponse:
    """
    The system's side of one cashier-day, drawn into the open form as the
    cashier and the date are chosen (htmx): the total, the split by method,
    and the receipts still without a voucher number.
    """
    policy.require(request.user, Screen.CLOSING, Action.VIEW, request=request)

    closing_date = _parse_date(request.GET.get("on", "").strip())
    cashier_raw = request.GET.get("cashier", "").strip()
    summary = None
    cashier = None
    if closing_date and cashier_raw.isdigit():
        try:
            cashier = closing_service.cashier_instance(int(cashier_raw))
        except ObjectDoesNotExist:
            cashier = None
    if cashier is not None and closing_date is not None:
        summary = closing_service.day_summary(cashier=cashier, closing_date=closing_date)
    return render(
        request,
        "cashbox/_closing_preview.html",
        {
            "summary": summary,
            "closing_date": closing_date,
            "vouchers_required": closing_service.vouchers_required(
                as_of=closing_date or timezone.localdate()
            ),
        },
    )


def closing_print_view(request: HttpRequest, code: str) -> HttpResponse:
    """The closing on paper — §9 report 6, signed by hand."""
    try:
        document = closing_service.closing_document(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    return render(request, "print/closing.html", {"closing": document})


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
            cashier = closing_service.cashier_instance(int(data["cashier_id"]))
            closing = closing_service.open_closing(
                actor=request.user,
                cashier=cashier,
                closing_date=data["closing_date"],
                counted_total=data["counted_total"],
                request=request,
            )
            # Sequential closing: a backlog is worked one day at a time, but
            # without hunting for the next one — the form lands on it.
            remaining = [
                d
                for d in closing_service.open_days(actor=request.user, request=request)
                if d["cashier_id"] == cashier.pk
            ]
            if remaining:
                nxt = remaining[0]
                messages.success(
                    request,
                    _("فُتح الإقفال %(code)s — التالي لنفس الصندوق: %(day)s")
                    % {"code": closing.code, "day": nxt["closing_date"].strftime("%Y/%m/%d")},
                )
                query = urlencode(
                    {"cashier": nxt["cashier_id"], "on": nxt["closing_date"].isoformat()}
                )
                return redirect(f"{reverse('cashbox:closing')}?{query}#close-day")
            messages.success(request, _("فُتح الإقفال %(code)s") % {"code": closing.code})
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


@require_http_methods(["GET", "POST"])
def payment_methods_view(request: HttpRequest) -> HttpResponse:
    """
    The vocabulary every receipt is written in — نقداً، حوالة، بطاقة.

    ``PaymentMethod`` is a table rather than fixed choices precisely so the
    client can add one without a migration, and that intention had no screen
    behind it: the rows could be written from the Django admin alone, which
    leaves no audit line. On a brand-new database there was no method at all,
    ``payment_service`` requires one, and so the till could not take a single
    dinar until someone ran a management command.

    The SETTINGS permission, not the till's own — the service's docstring says
    why at length: the cashier writes receipts, he does not invent the methods
    they are written in. No new matrix cell.
    """
    screen = method_service.SCREEN
    policy.require(request.user, screen, Action.VIEW, request=request)

    can_edit = policy.is_allowed(request.user, screen, Action.EDIT)

    posted = request.method == "POST"
    action = request.POST.get("action", "create") if posted else ""
    # Bound only for the act that owns it: on a GET ``request.POST`` is an empty
    # QueryDict — data, not nothing — and binding it would report every required
    # field missing before the reader had typed a character.
    form = PaymentMethodForm(request.POST if action == "create" else None)

    if posted:
        policy.require(request.user, screen, Action.EDIT, request=request)
        if action == "toggle":
            response = _toggle_payment_method(request)
            if response is not None:
                return response
        elif form.is_valid():
            response = _create_payment_method(request, form)
            if response is not None:
                return response
        else:
            messages.error(request, _("راجع حقول الطريقة."))

    rows = method_service.method_rows(actor=request.user, request=request)
    return render(
        request,
        "cashbox/payment_methods.html",
        {
            "title": _("طرق الدفع"),
            "active_screen": screen,
            "rows": rows,
            "form": form,
            "can_edit": can_edit,
            "posted_action": action,
            "active_count": sum(1 for row in rows if row["is_active"]),
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
        },
    )


def _create_payment_method(request: HttpRequest, form: PaymentMethodForm) -> HttpResponse | None:
    from django.db import IntegrityError

    try:
        method = method_service.create_method(
            actor=request.user, data=dict(form.cleaned_data), request=request
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لطريقة أخرى — اختر رمزاً غيره.")
            % {"code": form.cleaned_data["code"]},
        )
        return None

    messages.success(request, _("أُضيفت الطريقة %(name)s") % {"name": method.name_ar})
    return redirect("cashbox:payment-methods")


def _toggle_payment_method(request: HttpRequest) -> HttpResponse | None:
    """
    Stand a method down, or bring it back — never a delete.

    Receipts point at this row, and a receipt whose method had been deleted is a
    receipt nobody can say how it was paid. Standing it down takes it out of the
    till's choices and leaves every receipt already written still readable.
    """
    code = request.POST.get("code", "").strip()
    try:
        method = method_service.method_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا طريقة بالرمز %(code)s") % {"code": code})
        return None

    method_service.update_method(
        actor=request.user,
        method=method,
        data={"is_active": not method.is_active},
        request=request,
    )
    messages.success(
        request,
        _("أُعيدت الطريقة %(name)s") % {"name": method.name_ar}
        if not method.is_active
        else _("أُوقفت الطريقة %(name)s") % {"name": method.name_ar},
    )
    return redirect("cashbox:payment-methods")
