"""
People admin — closed to everyone except SYSTEM_ADMINISTRATOR (T-165, Δ-02).

The Django admin is a second door into every table. Leaving it on the default
`is_staff` check would mean the permission matrix guards the front door while
the back door answers to a different rule entirely.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.people.models import Role, User


class SystemAdministratorOnlyMixin:
    """Restrict a ModelAdmin to the system administrator role."""

    @staticmethod
    def _is_sysadmin(request: HttpRequest) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        # A superuser flag alone is not enough: the role is the documented
        # authority, and a stray superuser account must not bypass Δ-02.
        return getattr(user, "role", "") == Role.SYSTEM_ADMINISTRATOR

    def has_module_permission(self, request: HttpRequest) -> bool:
        return self._is_sysadmin(request)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._is_sysadmin(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return self._is_sysadmin(request)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._is_sysadmin(request)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._is_sysadmin(request)


@admin.register(User)
class UserAdmin(SystemAdministratorOnlyMixin, DjangoUserAdmin):
    list_display = ("username", "full_name_ar", "role", "department", "is_active", "locked_at")
    list_filter = ("role", "is_active", "is_staff")
    readonly_fields = ("failed_login_count", "locked_at")
    fieldsets = (
        *(DjangoUserAdmin.fieldsets or ()),
        (_("بيانات المركز"), {"fields": ("full_name_ar", "role", "department")}),
        (
            _("حالة الحساب (Q-12)"),
            {"fields": ("failed_login_count", "locked_at", "lock_reason")},
        ),
    )
    add_fieldsets = (
        *(DjangoUserAdmin.add_fieldsets or ()),
        (_("بيانات المركز"), {"fields": ("full_name_ar", "role", "department")}),
    )
