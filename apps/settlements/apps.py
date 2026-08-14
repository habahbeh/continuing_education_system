"""التسويات مع الشركاء — app shell. Models are built in Sprint 5."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SettlementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settlements"
    label = "settlements"
    verbose_name = _("التسويات مع الشركاء")
