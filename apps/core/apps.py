"""Core app configuration."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = _("النواة")

    def ready(self) -> None:
        # Registers the boot-blocking system checks (ADR-004 hard rule 10).
        from apps.core import checks  # noqa: F401
