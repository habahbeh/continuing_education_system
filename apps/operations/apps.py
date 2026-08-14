"""التشغيل ودورة حياة المشارك — app shell. Models are built in Sprint 6."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operations"
    label = "operations"
    verbose_name = _("التشغيل ودورة حياة المشارك")
