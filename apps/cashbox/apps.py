"""الصندوق — ما تم قبضه — app shell. Models are built in Sprint 4."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CashboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cashbox"
    label = "cashbox"
    verbose_name = _("الصندوق — ما تم قبضه")
