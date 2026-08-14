"""Core admin — AuditEvent is strictly read-only (ADR-010, D-11)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.core.models import AuditEvent, EffectiveSetting, FinancialPeriod, NumberSequence, Semester


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """
    Read-only by construction. The audit trail is append-only; the admin must
    not offer an edit path that the model would reject anyway (D-11).
    """

    list_display = ("occurred_at", "actor", "actor_role", "action", "entity_type", "reference")
    list_filter = ("action", "entity_type", "actor_role")
    search_fields = ("reference", "summary_ar", "entity_id")
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "type_code", "starts_on", "ends_on", "is_active")
    list_filter = ("is_active", "type_code")


@admin.register(EffectiveSetting)
class EffectiveSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "value_type", "effective_from", "effective_to")
    list_filter = ("value_type", "key")
    search_fields = ("key",)


@admin.register(NumberSequence)
class NumberSequenceAdmin(admin.ModelAdmin):
    list_display = ("scope", "partition", "next_value", "padding", "is_gapless")
    list_filter = ("scope", "is_gapless")


@admin.register(FinancialPeriod)
class FinancialPeriodAdmin(admin.ModelAdmin):
    list_display = ("starts_on", "ends_on", "status", "closed_by", "closed_at")
    list_filter = ("status",)
