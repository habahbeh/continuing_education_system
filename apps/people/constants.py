"""
Declared vocabulary for the permission engine (PERMISSIONS.md §1, §2, §3).

Screens and actions are CONSTANTS, not free strings. A typo in a free string
is a silent permission hole: `require(user, "payment-new", ...)` misspelled as
`"payment_new"` matches no deny rule and no allow entry, and "no entry" would
otherwise be indistinguishable from a screen nobody guarded.

The screen list is the 37 PRODUCTION screens of PERMISSIONS.md §3. Rows 37
(`coverage`) and 38 (`future`) are review tools that are never built
(SPEC.md §3.1) and are therefore absent here.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.people.models import ParticipantCategory, Role


class Action(models.TextChoices):
    """PERMISSIONS.md §1 — the six actions every matrix cell is scored on."""

    VIEW = "V", _("عرض")
    CREATE = "C", _("إنشاء")
    EDIT = "E", _("تعديل")
    APPROVE = "A", _("اعتماد")
    VOID = "X", _("إلغاء")
    PRINT = "P", _("طباعة/تصدير")


class Screen(models.TextChoices):
    """PERMISSIONS.md §3 — the 37 production screens, in document order."""

    # §3.1 الرئيسية
    DASHBOARD = "dashboard", _("لوحة المؤشرات")
    ENROLL_FLOW = "enroll-flow", _("مسار التسجيل والدفع")
    # §3.2 المشاركون والتسجيل
    STUDENTS = "students", _("المشاركون")
    STUDENT_NEW = "student-new", _("طلب التحاق جديد")
    ENROLLMENTS = "enrollments", _("التسجيلات")
    TRANSFERS = "transfers", _("النقل بين الدورات")
    TRANSFER_NEW = "transfer-new", _("طلب نقل جديد")
    SPECIAL_CASES = "special-cases", _("الحالات الخاصة")
    # §3.3 البرامج والأسعار
    PROGRAMS = "programs", _("الدبلومات التدريبية")
    SHORT_COURSES = "short-courses", _("الدورات القصيرة")
    ONLINE_COURSES = "online-courses", _("الدورات الأونلاين")
    COHORTS = "cohorts", _("الدفعات المُشغّلة")
    PRICELISTS = "pricelists", _("قوائم الأسعار")
    MOHE = "mohe", _("اعتماد الوزارة")
    MOHE_SUBMIT = "mohe-submit", _("نموذج الإرسال للوزارة")
    # §3.4 الشؤون المالية
    PAYMENTS = "payments", _("الدفعات وسندات القبض")
    PAYMENT_NEW = "payment-new", _("استيفاء دفعة")
    CLOSING = "closing", _("الإقفال اليومي")
    DISCOUNTS = "discounts", _("الخصومات")
    REFUNDS = "refunds", _("الاستردادات")
    EXTRA_FEES = "extra-fees", _("الرسوم الإضافية")
    EXPENSES = "expenses", _("المصروفات")
    # §3.5 الشركاء والاتفاقيات
    PARTNERS = "partners", _("الشركاء")
    AGREEMENTS = "agreements", _("الاتفاقيات")
    AGREEMENT_NEW = "agreement-new", _("محرّر اتفاقية")
    ENTITLEMENT = "entitlement", _("استحقاق الشركاء")
    CLAIMS = "claims", _("المطالبات")
    SETTLEMENTS = "settlements", _("المخالصات")
    OBLIGATIONS = "obligations", _("التزامات الشركاء")
    # §3.6 الإنهاء والشهادات
    CLEARANCE = "clearance", _("براءة الذمة")
    CERTIFICATES = "certificates", _("الشهادات")
    # §3.7 التقارير والنظام
    REPORTS = "reports", _("التقارير")
    USERS = "users", _("المستخدمون والصلاحيات")
    AUDIT = "audit", _("سجل التدقيق")
    SETTINGS = "settings", _("الإعدادات")
    MIGRATION = "migration", _("الأرشيف التاريخي")
    OPENING_BALANCES = "opening-balances", _("الأرصدة الافتتاحية")


#: The six BUSINESS roles scored in the matrix (PERMISSIONS.md §2).
#: SYSTEM_ADMINISTRATOR is a technical role and is deliberately NOT one of
#: them; its boundaries are declared separately in permissions.matrix and
#: tested by T-165 / T-273.
BUSINESS_ROLES: tuple[str, ...] = (
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
)

#: Read-only actions. Used by D-02 (the audit account writes nothing, ever).
READ_ONLY_ACTIONS: frozenset[str] = frozenset({Action.VIEW, Action.PRINT})

#: Presentation vocabularies, re-exported so views can render a dropdown
#: without importing the models module (A-05 forbids that, and rightly: the
#: rule is about views reaching for data, and the exception would erode it).
ROLE_CHOICES = Role.choices
PARTICIPANT_CATEGORY_CHOICES = ParticipantCategory.choices

#: The seven reports of SPEC.md. Access is per-report, not per-screen — see
#: REPORT_ACCESS in permissions.matrix (footnotes 23 and 31).
REPORT_NUMBERS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)


__all__ = [
    "BUSINESS_ROLES",
    "PARTICIPANT_CATEGORY_CHOICES",
    "READ_ONLY_ACTIONS",
    "REPORT_NUMBERS",
    "ROLE_CHOICES",
    "Action",
    "Screen",
]
