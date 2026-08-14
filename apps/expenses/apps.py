"""المصروفات — app shell. Models are built in Sprint 4."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExpensesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.expenses"
    label = "expenses"
    verbose_name = _("المصروفات")
