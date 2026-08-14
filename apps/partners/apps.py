"""الشركاء والاتفاقيات — app shell. Models are built in Sprint 5."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PartnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.partners"
    label = "partners"
    verbose_name = _("الشركاء والاتفاقيات")
