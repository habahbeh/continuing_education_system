"""
Daily closing (BR-026 … BR-028).

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
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.billing.services.account_service import ZERO
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "cashbox.DailyClosing"


def system_total_for(*, cashier: Any, closing_date: date) -> tuple[Decimal, int]:
    """What the system says was taken — issued receipts only."""
    from apps.cashbox.models import Receipt, ReceiptStatus

    result = Receipt.objects.filter(
        cashier=cashier, received_on=closing_date, status=ReceiptStatus.ISSUED
    ).aggregate(total=Sum("amount"), count=Count("id"))
    return (result["total"] or ZERO), (result["count"] or 0)


def open_closing(
    *, actor: Any, cashier: Any, closing_date: date, counted_total: Decimal, request: Any = None
) -> Any:
    """Record the counted cash against the system total."""
    from apps.cashbox.models import ClosingStatus, DailyClosing

    policy.require(actor, Screen.CLOSING, Action.CREATE, request=request)

    system_total, count = system_total_for(cashier=cashier, closing_date=closing_date)
    variance = counted_total - system_total

    with transaction.atomic():
        closing = DailyClosing(
            code=f"CL-{closing_date:%Y%m%d}-{cashier.pk}",
            closing_date=closing_date,
            cashier=cashier,
            system_total=system_total,
            counted_total=counted_total,
            variance=variance,
            receipt_count=count,
            status=ClosingStatus.OPEN if variance == ZERO else ClosingStatus.VARIANCE_PENDING,
        )
        closing.full_clean(exclude=["cashier"])
        closing.save()

        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(closing.pk),
            reference=closing.code,
            summary_ar=f"إقفال يومي — النظام {system_total} · العدّ {counted_total}",
            actor=actor,
            changes={
                "system_total": str(system_total),
                "counted_total": str(counted_total),
                "variance": str(variance),
                "receipt_count": count,
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


__all__ = ["open_closing", "reconcile", "system_total_for"]
