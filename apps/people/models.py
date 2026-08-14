"""
Custom user model (DATA_MODEL §4.1).

Built in Sprint 1 because changing AUTH_USER_MODEL after the first migration
is painful. Sprint 1 delivered the model only. Sprint 2A adds the sixth
business role and the local-authentication state, per the resolved decisions:

* Q-12 — local accounts in v1. No SSO/AD/LDAP/SAML/2FA. The lockout counters
  below are account state, not a permission engine; the engine lives in
  apps/people/permissions/.
* Q-14 — FINANCE_MANAGER is an internal role with a deliberately narrow scope
  (BR-099). It is NOT a stronger FINANCE_OFFICER and inherits nothing.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import NameAr, ShortCode


class Role(models.TextChoices):
    """
    The five documented business roles, plus two roles resolved after the demo.

    PERMISSIONS.md §6 Δ-02: user and role administration is NOT a centre
    manager function; it belongs to a dedicated system administrator held by
    the IT department.

    PERMISSIONS.md §6 Δ-13 (Q-14): FINANCE_MANAGER is the internal role that
    performs the SECOND certification of the financial clearance step after
    the finance officer (BR-074). Its scope is approval and oversight only —
    see BR-099 and deny rule D-29.

    Not roles, deliberately (Q-14):
    * University President — an external approver represented by a mandatory
      text reference plus a mandatory attachment (D-31). No account, no role,
      no PENDING_APPROVAL state waiting for a login that will never happen.
    * "Accountant" — a job title in form CS Fm 7.18 Rev A, not a role code.
      The first clearance certification is performed by FINANCE_OFFICER.
    """

    CENTER_MANAGER = "CENTER_MANAGER", _("مدير المركز")
    REGISTRATION_OFFICER = "REGISTRATION_OFFICER", _("موظف التسجيل")
    FINANCE_OFFICER = "FINANCE_OFFICER", _("الموظف المالي")
    FINANCE_MANAGER = "FINANCE_MANAGER", _("المدير المالي")
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

    # --- Local authentication state (Q-12) --------------------------------
    # Counters, not policy: the thresholds themselves are EffectiveSettings
    # (`login_max_failed_attempts`, `session_idle_timeout_minutes`) because a
    # number in the code is a business constant in disguise (BR-086).
    failed_login_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("عدد المحاولات الفاشلة"),
    )
    locked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("تاريخ القفل"),
        help_text=_("يُقفل الحساب بعد تجاوز حد المحاولات الفاشلة (Q-12)"),
    )
    lock_reason = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("سبب القفل"),
    )

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined,misc]
        verbose_name = _("مستخدم")  # type: ignore[assignment]
        verbose_name_plural = _("المستخدمون")  # type: ignore[assignment]
        constraints = [
            # Derived from Role.values, not a second hand-written list. The
            # duplicate list is exactly how a seventh role gets added to the
            # choices and forgotten in the constraint — which MySQL would then
            # reject at runtime while Django considered it valid (T-270).
            #
            # Django freezes this list into the migration file at the moment
            # the migration is written. That is correct: a migration is a
            # historical record. The benefit of deriving is that changing Role
            # now PRODUCES a migration instead of silently diverging.
            models.CheckConstraint(
                condition=models.Q(role="") | models.Q(role__in=Role.values),
                name="people_user_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name_ar or self.get_username()

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None
