"""Financial period (DATA_MODEL §3.6, OPEN_QUESTIONS Q-18)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import ShortCode


class FinancialPeriodStatus(models.TextChoices):
    OPEN = "OPEN", _("مفتوحة")
    CLOSED = "CLOSED", _("مقفلة")


class FinancialPeriod(models.Model):
    """
    Without closed periods, anyone can post a backdated entry and silently
    change a report that has already been issued and approved. From Sprint 4
    every financial write validates that its date falls in an OPEN period
    (D-23).
    """

    starts_on = models.DateField(verbose_name=_("تبدأ في"))
    ends_on = models.DateField(verbose_name=_("تنتهي في"))
    status = ShortCode(
        choices=FinancialPeriodStatus.choices,
        default=FinancialPeriodStatus.OPEN,
        verbose_name=_("الحالة"),
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_financial_periods",
        verbose_name=_("أقفلها"),
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("وقت الإقفال"))

    class Meta:
        verbose_name = _("فترة مالية")
        verbose_name_plural = _("الفترات المالية")
        ordering = ["-starts_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="core_period_ends_after_starts",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="CLOSED")
                | (models.Q(closed_by__isnull=False) & models.Q(closed_at__isnull=False)),
                name="core_period_closed_requires_closer",
            ),
            models.UniqueConstraint(fields=["starts_on"], name="core_period_unique_start"),
        ]
        indexes = [
            models.Index(fields=["starts_on", "ends_on"], name="core_period_range_idx"),
            models.Index(fields=["status"], name="core_period_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.starts_on} — {self.ends_on} ({self.get_status_display()})"
