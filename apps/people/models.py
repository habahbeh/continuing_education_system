"""
Custom user model (DATA_MODEL §4.1).

Built in Sprint 1 because changing AUTH_USER_MODEL after the first migration
is painful. Sprint 1 delivers the model ONLY — no permission engine, no deny
rules, no custom login screens. Those are Sprint 2 (Q-12, Q-14).
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import NameAr, ShortCode


class Role(models.TextChoices):
    """
    The five documented roles plus the system administrator.

    PERMISSIONS.md §6 Δ-02: user and role administration is NOT a centre
    manager function; it belongs to a dedicated system administrator held by
    the IT department.

    A sixth internal role, FINANCE_MANAGER, is pending Q-14 and is deliberately
    NOT added here yet.
    """

    CENTER_MANAGER = "CENTER_MANAGER", _("مدير المركز")
    REGISTRATION_OFFICER = "REGISTRATION_OFFICER", _("موظف التسجيل")
    FINANCE_OFFICER = "FINANCE_OFFICER", _("الموظف المالي")
    CASHIER = "CASHIER", _("الصندوق")
    AUDIT_ACCOUNT = "AUDIT_ACCOUNT", _("حساب التدقيق")
    SYSTEM_ADMINISTRATOR = "SYSTEM_ADMINISTRATOR", _("مدير النظام")


class User(AbstractUser):
    full_name_ar = NameAr(blank=True, verbose_name=_("الاسم بالعربية"))
    role = ShortCode(
        choices=Role.choices,
        blank=True,
        verbose_name=_("الدور"),
        help_text=_("الدور يأتي من المصادقة — لا واجهة تغيّره على الجلسة (D-20)"),
    )
    department = models.CharField(max_length=150, blank=True, verbose_name=_("الدائرة"))

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined,misc]
        verbose_name = _("مستخدم")  # type: ignore[assignment]
        verbose_name_plural = _("المستخدمون")  # type: ignore[assignment]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role="")
                | models.Q(
                    role__in=[
                        "CENTER_MANAGER",
                        "REGISTRATION_OFFICER",
                        "FINANCE_OFFICER",
                        "CASHIER",
                        "AUDIT_ACCOUNT",
                        "SYSTEM_ADMINISTRATOR",
                    ]
                ),
                name="people_user_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name_ar or self.get_username()
