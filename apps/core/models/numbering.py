"""
Central numbering counters (DATA_MODEL §3.3, ADR-011).

Every user-visible number in the system comes from here: participant numbers
(BR-001), receipt numbers (Q-03), certificate numbers (BR-076), agreement
numbers (BR-043).

The demo derived sequences from ``array.length + 1``, which collides under
concurrency and after deletion. Production uses a locked row inside the same
transaction that creates the record, so a failed transaction rolls the counter
back and leaves no gap.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import ShortCode


class NumberSequence(models.Model):
    scope = ShortCode(
        verbose_name=_("النطاق"),
        help_text=_("participant · receipt · certificate · agreement"),
    )
    partition = ShortCode(
        verbose_name=_("التقسيم"),
        help_text=_("مثال: 2026 أو 2026-5 أو legacy-2018"),
    )
    next_value = models.BigIntegerField(default=1, verbose_name=_("القيمة التالية"))
    padding = models.PositiveSmallIntegerField(default=4, verbose_name=_("عدد الخانات"))
    is_gapless = models.BooleanField(
        default=True,
        verbose_name=_("بلا فجوات"),
        help_text=_("الأرقام المالية بلا فجوات — الفجوة شبهة تدقيق"),
    )

    class Meta:
        verbose_name = _("عدّاد ترقيم")
        verbose_name_plural = _("عدّادات الترقيم")
        ordering = ["scope", "partition"]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "partition"],
                name="core_sequence_unique_scope_partition",
            ),
            models.CheckConstraint(
                condition=models.Q(next_value__gte=1),
                name="core_sequence_next_value_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(padding__gte=1) & models.Q(padding__lte=12),
                name="core_sequence_padding_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope}/{self.partition} → {self.next_value}"
