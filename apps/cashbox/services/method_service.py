"""
Payment methods as reference data, behind the service (DATA_MODEL §8.7).

``PaymentMethod`` is a table and not a set of fixed choices on purpose: bank
transfer and card are future scope, and the client adding one must be a ROW,
not a migration. That intention was carried by the model and by nothing else —
the only way to write such a row was the Django admin, which writes straight to
the table and leaves no audit line behind.

Found by walking a fresh install: ``seed_payment_methods`` is a command, so a
brand-new database has no method at all, ``payment_service`` requires one, and
the till therefore could not take a single dinar until a developer ran a
command or opened the admin.

**The settings permission, and deliberately not the till's own.** A payment
method is a system setting: PERMISSIONS.md §3.7/35 gives the centre manager
``V E P`` over settings and the finance officer ``V``. Row 16 (the till) gives
the CASHIER ``C`` — over receipts, which is his job, and not over the
vocabulary receipts are written in. Hanging this screen off the till row would
have let whoever takes the cash invent the method it was taken by, and locked
the manager out of his own configuration. So both acts here — adding a method
and standing one down — ask for ``EDIT`` on settings: no new matrix cell, and
the authority stays where the document put it.
"""

from __future__ import annotations

from typing import Any

from django.db import models, transaction

from apps.cashbox.models import PaymentMethod
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "cashbox.PaymentMethod"

#: Configuring the vocabulary is a settings act, not a till act — see the module
#: docstring. Named here so the screen and the tests read the same decision.
SCREEN = Screen.SETTINGS


def method_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every method, with the count of receipts written against it.

    The count is what separates a method that may still be reworded from one the
    books already rest on — and it is the reason standing a method down is the
    only exit offered: a receipt whose method row had been deleted would be a
    receipt nobody can say how it was paid.
    """
    policy.require(actor, SCREEN, Action.VIEW, request=request)

    counts = {
        row["payment_method_id"]: row["n"]
        for row in _receipt_model().objects.values("payment_method_id").annotate(
            n=models.Count("id")
        )
    }
    return [
        {
            "code": method.code,
            "name_ar": method.name_ar,
            "is_active": method.is_active,
            "receipts": counts.get(method.pk, 0),
        }
        for method in PaymentMethod.objects.order_by("name_ar")
    ]


def _receipt_model() -> Any:
    from apps.cashbox.models import Receipt

    return Receipt


def create_method(*, actor: Any, data: dict[str, Any], request: Any = None) -> PaymentMethod:
    """A new method, audited — which the admin road never was."""
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    with transaction.atomic():
        method = PaymentMethod(**data)
        method.full_clean()
        method.save()
        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(method.pk),
            reference=method.code,
            summary_ar=f"إنشاء طريقة دفع — {method.name_ar}",
            actor=actor,
            request=request,
        )
    return method


def update_method(
    *, actor: Any, method: PaymentMethod, data: dict[str, Any], request: Any = None
) -> PaymentMethod:
    """Rename, or stand down. The code is not among the editable fields."""
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    before = {field: getattr(method, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(method, field, value)
        method.full_clean()
        method.save()
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(method.pk),
            reference=method.code,
            summary_ar=f"تعديل طريقة دفع — {method.name_ar}",
            actor=actor,
            changes={
                field: {"from": str(before[field]), "to": str(getattr(method, field))}
                for field in data
                if before[field] != getattr(method, field)
            },
            request=request,
        )
    return method


def method_instance(*, actor: Any, code: str, request: Any = None) -> PaymentMethod:
    """The row itself, for handing back into this module."""
    policy.require(actor, SCREEN, Action.VIEW, request=request)
    return PaymentMethod.objects.get(code=code)


__all__ = [
    "ENTITY",
    "SCREEN",
    "create_method",
    "method_instance",
    "method_rows",
    "update_method",
]
