"""People admin."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.people.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "full_name_ar", "role", "department", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = (
        *(DjangoUserAdmin.fieldsets or ()),
        (_("بيانات المركز"), {"fields": ("full_name_ar", "role", "department")}),
    )
    add_fieldsets = (
        *(DjangoUserAdmin.add_fieldsets or ()),
        (_("بيانات المركز"), {"fields": ("full_name_ar", "role", "department")}),
    )
