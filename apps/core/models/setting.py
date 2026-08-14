"""
Effective-dated settings (DATA_MODEL §3.2, ADR-009, BR-086).

No business constant lives in code. Every threshold, fee, multiplier and
capability flag is a row here with a validity period, so that:

* changing a rule is an administrative act, not a release; and
* changing it never rewrites the past — a claim computed in July is still
  read with July's settings.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import SettingKey, ShortCode


class SettingValueType(models.TextChoices):
    DECIMAL = "DECIMAL", _("رقم عشري")
    INTEGER = "INTEGER", _("رقم صحيح")
    BOOLEAN = "BOOLEAN", _("منطقي")
    STRING = "STRING", _("نص")


class EffectiveSetting(models.Model):
    key = SettingKey(db_index=True, verbose_name=_("المفتاح"))
    value = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=_("القيمة"),
        help_text=_("NULL تعني «غير محدَّدة بعد» ولا تعني صفراً"),
    )
    value_type = ShortCode(choices=SettingValueType.choices, verbose_name=_("نوع القيمة"))

    effective_from = models.DateField(verbose_name=_("سارٍ من"))
    effective_to = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("سارٍ حتى"),
        help_text=_("فارغ = سارٍ حتى إشعار آخر"),
    )

    note = models.TextField(verbose_name=_("مبرر التغيير"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_settings",
        verbose_name=_("أنشأه"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("وقت الإنشاء"))

    class Meta:
        verbose_name = _("إعداد مؤرّخ")
        verbose_name_plural = _("الإعدادات المؤرّخة")
        ordering = ["key", "-effective_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="core_setting_period_valid",
            ),
            # One row per (key, effective_from). Full overlap prevention needs
            # range logic MySQL cannot express, so it is enforced in
            # settings_service.set_setting() and covered by a test.
            models.UniqueConstraint(
                fields=["key", "effective_from"],
                name="core_setting_unique_key_from",
            ),
        ]
        indexes = [
            models.Index(fields=["key", "effective_from"], name="core_setting_key_from_idx"),
            models.Index(fields=["key", "effective_to"], name="core_setting_key_to_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.key} = {self.value} ({self.effective_from})"
