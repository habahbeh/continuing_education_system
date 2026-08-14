"""الكتالوج والأسعار — app shell. Models are built in Sprint 3."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"
    verbose_name = _("الكتالوج والأسعار")
