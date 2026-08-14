"""التقارير السبعة — app shell. Models are built in Sprint 9."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    label = "reporting"
    verbose_name = _("التقارير السبعة")
