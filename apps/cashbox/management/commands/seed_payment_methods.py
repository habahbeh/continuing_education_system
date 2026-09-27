"""
Seed the three payment methods the till offers — نقد، شيك، فيزا.

``PaymentMethod`` is a reference table on purpose (DATA_MODEL §8.7): a new
method is a row, never a migration. This command writes those rows for a
fresh install and is idempotent — a row that exists keeps its name.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

METHODS = (
    ("CASH", "نقد"),
    ("CHEQUE", "شيك"),
    ("VISA", "فيزا"),
)


class Command(BaseCommand):
    help = "ينشئ طرق الدفع الثلاث (نقد، شيك، فيزا) إن لم تكن موجودة"

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.cashbox.models import PaymentMethod

        created = 0
        for code, name_ar in METHODS:
            _, was_created = PaymentMethod.objects.get_or_create(
                code=code, defaults={"name_ar": name_ar, "is_active": True}
            )
            created += int(was_created)
        self.stdout.write(f"تم: {created} مُنشأ · {len(METHODS) - created} قائم لم يُمسّ.")
