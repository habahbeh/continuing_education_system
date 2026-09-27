"""
Daily closing (BR-026 … BR-028) — «مطابقة المقبوض بالوصولات» (§5.2).

The money is taken at the university's finance department, which issues its
own voucher; the centre records the payment and the voucher number. The
day's reconciliation is therefore between two independent sources: what the
centre recorded against what the finance department vouched for — a receipt
recorded without a voucher number is the discrepancy this screen exists to
catch. ``counted_total`` is the total of the vouchers in hand.

Two controls, both also enforced by the database because both are the kind
someone is tempted to wave through at the end of a long day:

* **BR-027** — a till may close with a difference, but never a silent one. A
  reconciled closing with a variance must carry a written resolution.
* **BR-028** — whoever held the cash does not sign off on the count. The
  constraint ``approved_by <> cashier`` makes it impossible on every path,
  not merely absent from the screen.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.billing.services.account_service import ZERO
from apps.core.display import person_name
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "cashbox.DailyClosing"


VOUCHERS_REQUIRED_KEY = "closing_requires_vouchers"


def system_total_for(*, cashier: Any, closing_date: date) -> tuple[Decimal, int]:
    """What the system says was taken — issued receipts only."""
    from apps.cashbox.models import Receipt, ReceiptStatus

    result = Receipt.objects.filter(
        cashier=cashier, received_on=closing_date, status=ReceiptStatus.ISSUED
    ).aggregate(total=Sum("amount"), count=Count("id"))
    return (result["total"] or ZERO), (result["count"] or 0)


def day_summary(*, cashier: Any, closing_date: date) -> dict[str, Any]:
    """
    One cashier's day before it is closed: the system total, the split by
    payment method (for the record — one method is live today, §10 names
    the rest as future), and every receipt recorded without the finance
    department's voucher number.
    """
    from apps.cashbox.models import Receipt, ReceiptStatus

    receipts = list(
        Receipt.objects.filter(
            cashier=cashier, received_on=closing_date, status=ReceiptStatus.ISSUED
        )
        .select_related("participant", "payment_method")
        .order_by("id")
    )
    by_method: dict[str, dict[str, Any]] = {}
    for r in receipts:
        slot = by_method.setdefault(
            r.payment_method.code,
            {
                "code": r.payment_method.code,
                "name": r.payment_method.name_ar,
                "amount": ZERO,
                "count": 0,
            },
        )
        slot["amount"] += r.amount
        slot["count"] += 1
    unvouched = [
        {
            "number": r.internal_receipt_number,
            "participant_name": r.participant.name_ar,
            "amount": r.amount,
        }
        for r in receipts
        if not r.external_receipt_ref.strip()
    ]
    total = sum((r.amount for r in receipts), ZERO)
    return {
        "total": total,
        "count": len(receipts),
        "by_method": sorted(by_method.values(), key=lambda m: -m["amount"]),
        "unvouched": unvouched,
        "unvouched_count": len(unvouched),
        "unvouched_total": sum((u["amount"] for u in unvouched), ZERO),
        "vouched_total": total - sum((u["amount"] for u in unvouched), ZERO),
    }


def vouchers_required(*, as_of: date) -> bool:
    from apps.core.services.settings_service import get_setting

    return bool(get_setting(VOUCHERS_REQUIRED_KEY, as_of=as_of, default=True))


def open_days(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Cashier-days with issued receipts and no closing yet — the queue this
    screen is for. Newest first.
    """
    from apps.cashbox.models import DailyClosing, Receipt, ReceiptStatus

    policy.require(actor, Screen.CLOSING, Action.VIEW, request=request)

    closed = {
        (c.cashier_id, c.closing_date)
        for c in DailyClosing.objects.only("cashier_id", "closing_date")
    }
    rows = (
        Receipt.objects.filter(status=ReceiptStatus.ISSUED, daily_closing__isnull=True)
        .values("cashier_id", "cashier__username", "cashier__full_name_ar", "received_on")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-received_on", "cashier__username")
    )
    days = []
    for r in rows:
        if (r["cashier_id"], r["received_on"]) in closed:
            continue
        days.append(
            {
                "cashier_id": r["cashier_id"],
                "cashier": r["cashier__full_name_ar"] or r["cashier__username"],
                "closing_date": r["received_on"],
                "total": r["total"] or ZERO,
                "count": r["count"],
            }
        )
    return days


def open_closing(
    *, actor: Any, cashier: Any, closing_date: date, counted_total: Decimal, request: Any = None
) -> Any:
    """
    Record the vouchers in hand against the system total.

    ``counted_total`` is the sum of the finance department's vouchers for the
    day. The closing opens clean only when that matches the system and every
    receipt carries a voucher number (when the setting requires it); either
    gap leaves it «فرق قيد المطابقة» until someone explains it (BR-027).
    """
    from apps.cashbox.models import ClosingStatus, DailyClosing

    policy.require(actor, Screen.CLOSING, Action.CREATE, request=request)

    if DailyClosing.objects.filter(cashier=cashier, closing_date=closing_date).exists():
        raise ValidationError(
            f"يوم {closing_date:%Y/%m/%d} لهذا الصندوق مُقفل أو قيد المطابقة بالفعل (BR-026)."
        )

    summary = day_summary(cashier=cashier, closing_date=closing_date)
    system_total, count = summary["total"], summary["count"]
    if count == 0:
        raise ValidationError(
            f"لا سندات صادرة لهذا الصندوق في {closing_date:%Y/%m/%d}؛ لا شيء يُقفل."
        )
    variance = counted_total - system_total
    unvouched = summary["unvouched_count"] if vouchers_required(as_of=closing_date) else 0
    clean = variance == ZERO and unvouched == 0

    with transaction.atomic():
        closing = DailyClosing(
            code=f"CL-{closing_date:%Y%m%d}-{cashier.pk}",
            closing_date=closing_date,
            cashier=cashier,
            system_total=system_total,
            counted_total=counted_total,
            variance=variance,
            receipt_count=count,
            unvouched_count=summary["unvouched_count"],
            opened_by=actor if getattr(actor, "pk", None) else None,
            status=ClosingStatus.OPEN if clean else ClosingStatus.VARIANCE_PENDING,
        )
        closing.full_clean(exclude=["cashier"])
        closing.save()

        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(closing.pk),
            reference=closing.code,
            summary_ar=f"إقفال يومي — النظام {system_total} · الوصولات {counted_total}",
            actor=actor,
            changes={
                "system_total": str(system_total),
                "counted_total": str(counted_total),
                "variance": str(variance),
                "receipt_count": count,
                "unvouched_count": summary["unvouched_count"],
            },
            request=request,
        )
    return closing


def reconcile(
    *, actor: Any, closing: Any, variance_resolution_ar: str = "", request: Any = None
) -> Any:
    """
    Approve and close the day.

    BR-028 is checked here for a readable message and by the database for
    everything else: the person who took the money cannot be the person who
    certifies how much of it there was.
    """
    from apps.cashbox.models import ClosingStatus, Receipt, ReceiptStatus

    policy.require(actor, Screen.CLOSING, Action.APPROVE, request=request)

    if closing.cashier_id == getattr(actor, "pk", None):
        raise PermissionDenied("لا يجوز لأمين الصندوق اعتماد إقفال يومه (BR-028) — الاعتماد لغيره.")
    if closing.variance != ZERO and not variance_resolution_ar.strip():
        raise ValidationError(
            f"الإقفال بفرق {closing.variance} يتطلب تسوية مكتوبة قبل الإغلاق (BR-027)."
        )
    if (
        closing.unvouched_count
        and vouchers_required(as_of=closing.closing_date)
        and not variance_resolution_ar.strip()
    ):
        raise ValidationError(
            f"{closing.unvouched_count} من سندات اليوم بلا رقم سند من الدائرة المالية؛ "
            "يُستكمل الرقم على السند أو تُكتب تسوية قبل الاعتماد (§5.2 · BR-027)."
        )

    with transaction.atomic():
        closing.status = ClosingStatus.RECONCILED
        closing.variance_resolution_ar = variance_resolution_ar.strip()
        closing.approved_by = actor
        closing.approved_at = timezone.now()
        closing.full_clean(exclude=["cashier", "approved_by"])
        closing.save()

        # Attach the day's receipts so the closing is reproducible later.
        Receipt.objects.filter(
            cashier=closing.cashier,
            received_on=closing.closing_date,
            status=ReceiptStatus.ISSUED,
            daily_closing__isnull=True,
        ).update(daily_closing=closing)

        write_audit(
            action="APPROVE",
            entity_type=ENTITY,
            entity_id=str(closing.pk),
            reference=closing.code,
            summary_ar=f"اعتماد إقفال يومي — فرق {closing.variance}",
            actor=actor,
            changes={
                "variance": str(closing.variance),
                "resolution": variance_resolution_ar.strip() or None,
            },
            request=request,
        )
    return closing


def list_closings(
    *,
    actor: Any,
    on_date: date | None = None,
    since: date | None = None,
    until: date | None = None,
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Daily closings as rows, newest first (§9 report 6).

    Sprint 8L — ``since``/``until`` beside ``on_date``, named as
    ``list_receipts`` names them. §9.6 asks for «إجمالي القبض اليومي»: the
    daily is the ROW, not the report. A supervisor reconciling a week reads
    seven rows, and was having to open seven pages to do it.

    Neither filter is an oracle: ``closing_date`` is printed on every row this
    caller already receives, so narrowing by it reports nothing the row
    withholds (A-10).
    """
    from apps.cashbox.models import DailyClosing

    policy.require(actor, Screen.CLOSING, Action.VIEW, request=request)

    queryset = DailyClosing.objects.select_related("cashier", "approved_by", "opened_by")
    if on_date is not None:
        queryset = queryset.filter(closing_date=on_date)
    if since is not None:
        queryset = queryset.filter(closing_date__gte=since)
    if until is not None:
        queryset = queryset.filter(closing_date__lte=until)

    return [
        {
            "code": c.code,
            "closing_date": c.closing_date,
            "cashier": person_name(c.cashier),
            "cashier_id": c.cashier_id,
            "system_total": c.system_total,
            "counted_total": c.counted_total,
            "variance": c.variance,
            "receipt_count": c.receipt_count,
            "status": c.status,
            "status_display": c.get_status_display(),
            "variance_resolution_ar": c.variance_resolution_ar,
            "approved_by": person_name(c.approved_by),
            "approved_at": c.approved_at,
            "opened_by": person_name(c.opened_by) if c.opened_by_id else "",
            "opened_at": c.opened_at,
            "unvouched_count": c.unvouched_count,
            "is_clean": c.variance == ZERO and c.unvouched_count == 0,
        }
        for c in queryset.order_by("-closing_date", "-id")
    ]


def cashier_choices() -> list[tuple[str, str]]:
    """Users who may hold a till, for the closing form."""
    from apps.cashbox.models import Receipt
    from apps.people.models import Role, User

    with_receipts = Receipt.objects.values_list("cashier_id", flat=True).distinct()
    users = User.objects.filter(
        models.Q(role=Role.CASHIER, is_active=True) | models.Q(pk__in=with_receipts)
    ).order_by("username")
    return [(str(u.pk), u.full_name_ar or u.get_username()) for u in users]


def cashier_instance(user_id: int) -> Any:
    """The cashier a closing belongs to."""
    from apps.people.models import User

    return User.objects.get(pk=user_id)


def closing_instance(*, actor: Any, code: str, request: Any = None) -> Any:
    """The DailyClosing object, for handing back into this module (A-05)."""
    from apps.cashbox.models import DailyClosing

    policy.require(actor, Screen.CLOSING, Action.VIEW, request=request)
    return DailyClosing.objects.get(code=code)


def closing_document(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """The closing on paper: the row, the day's receipts, the letterhead."""
    from apps.cashbox.services import payment_service
    from apps.core.services import document_settings

    closing = closing_instance(actor=actor, code=code, request=request)
    row = next(
        r
        for r in list_closings(actor=actor, on_date=closing.closing_date, request=request)
        if r["code"] == code
    )
    receipts = payment_service.list_receipts(
        actor=actor,
        on_date=closing.closing_date,
        cashier_id=closing.cashier_id,
        status="issued",
        request=request,
    )
    return {
        **row,
        "receipts": receipts,
        "summary": day_summary(cashier=closing.cashier, closing_date=closing.closing_date),
        "chrome": document_settings.chrome(as_of=closing.closing_date),
    }


__all__ = [
    "cashier_choices",
    "cashier_instance",
    "closing_document",
    "closing_instance",
    "day_summary",
    "list_closings",
    "open_closing",
    "open_days",
    "reconcile",
    "system_total_for",
    "vouchers_required",
]
