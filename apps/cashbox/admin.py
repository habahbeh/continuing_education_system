"""
Cashbox admin — the payment-method reference table (Sprint 8I).

**Written because the browser pass found the till unusable on a fresh
install.** ``PaymentMethod`` is a reference table by design: DATA_MODEL §8.7
says bank transfer and card must arrive as ROWS rather than as a migration.
But nothing in the system had ever created a row — no admin, no service, no
seed command — so ``payment_method_choices`` returned an empty list and the
"استيفاء دفعة" screen offered a dropdown with nothing in it. A fresh
installation could enrol a participant, raise their charges, and then never
take a single dinar.

Registered plainly rather than behind the permission matrix, following
``core/admin.py``: ``Semester``, ``FinancialPeriod`` and ``NumberSequence``
are the same kind of thing — infrastructure the installation is configured
with once, not a business screen a role operates daily. The matrix has no row
for "which payment methods exist", and inventing one would have been a
bigger change than the gap warranted.

Deleting is refused. Every ``Receipt`` carries a PROTECT key to its method, so
a delete is a database error waiting to happen; ``is_active`` retires a method
without rewriting the receipts that used it.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.cashbox.models import PaymentMethod


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name_ar")

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


__all__ = ["PaymentMethodAdmin"]
