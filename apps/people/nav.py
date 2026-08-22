"""
The navigation tree, filtered by what the signed-in role may actually see.

Until Sprint 8B the system had no navigation at all: ``base.html`` carried a
title bar and a footer, and the eight screens that existed were reachable only
by typing their URL. This module is the menu, and it is built from the
permission matrix rather than beside it.

**One source of truth.** An entry appears when
``matrix.allowed_actions(role, screen)`` grants VIEW — never because a
template asked ``if user.role ==``. A permission change therefore moves the
menu automatically, and a menu that disagreed with the policy engine is not
expressible.

**Hiding is courtesy, not security.** Every view still calls
``policy.require``. A user who guesses a URL for a hidden screen is refused
there, with a DENIED_ATTEMPT row (BR-085) — the menu simply spares them the
trip. That is why the audit account, which may VIEW everything and change
nothing, sees a full menu and no action buttons anywhere behind it.

Entries name a URL by route rather than by path, so a moved route breaks the
build at ``reverse`` instead of silently rendering a dead link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.people.constants import Action, Screen
from apps.people.permissions.matrix import allowed_actions


@dataclass(frozen=True)
class NavItem:
    screen: str
    route: str
    label: Any


@dataclass(frozen=True)
class NavGroup:
    title: Any
    items: tuple[NavItem, ...]


#: The tree as it stands after Sprint 8B waves 0–2. Screens whose route does
#: not exist yet are simply absent — an entry pointing at an unbuilt screen
#: would be a promise the system cannot keep.
NAV: tuple[NavGroup, ...] = (
    NavGroup(
        _("المشاركون والتسجيل"),
        (
            NavItem(Screen.STUDENTS, "people:participants", _("المشاركون")),
            NavItem(Screen.STUDENT_NEW, "people:participant-new", _("طلب التحاق جديد")),
            NavItem(Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
            NavItem(Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
        ),
    ),
    NavGroup(
        _("البرامج والأسعار"),
        (
            NavItem(Screen.PROGRAMS, "catalog:programs", _("الدبلومات التدريبية")),
            NavItem(Screen.SHORT_COURSES, "catalog:short-courses", _("الدورات القصيرة")),
            NavItem(Screen.ONLINE_COURSES, "catalog:online-courses", _("الدورات الأونلاين")),
            NavItem(Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار")),
        ),
    ),
    NavGroup(
        _("الشؤون المالية"),
        (
            NavItem(Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),
            NavItem(Screen.PAYMENT_NEW, "cashbox:payment-new", _("استيفاء دفعة")),
            NavItem(Screen.CLOSING, "cashbox:closing", _("الإقفال اليومي")),
            NavItem(Screen.DISCOUNTS, "billing:discounts", _("الخصومات")),
            NavItem(Screen.REFUNDS, "billing:refunds", _("الاستردادات وردّ الأرصدة")),
            NavItem(Screen.EXTRA_FEES, "billing:extra-fees", _("الرسوم الإضافية")),
            NavItem(Screen.EXPENSES, "expenses:expenses", _("المصروفات")),
            NavItem(Screen.OPENING_BALANCES, "billing:opening-balances", _("الأرصدة الافتتاحية")),
        ),
    ),
    NavGroup(
        _("الشركاء والمخالصات"),
        (
            NavItem(Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون")),
            NavItem(Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),
            NavItem(Screen.CLAIMS, "settlements:claims", _("المطالبات")),
            NavItem(Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات")),
            NavItem(Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
            NavItem(Screen.OBLIGATIONS, "settlements:absences", _("غيابات المدربين")),
        ),
    ),
    NavGroup(
        _("الإنهاء والشهادات"),
        (
            NavItem(Screen.CLEARANCE, "operations:clearances", _("براءة الذمة")),
            NavItem(Screen.CERTIFICATES, "operations:certificates", _("الشهادات")),
        ),
    ),
    NavGroup(
        _("النظام"),
        (
            NavItem(Screen.REPORTS, "reporting:reports", _("التقارير")),
            NavItem(Screen.MIGRATION, "datamigration:batches", _("الأرشيف التاريخي")),
            NavItem(Screen.MIGRATION, "datamigration:links", _("ربط السجلات التاريخية")),
            NavItem(Screen.USERS, "people:users", _("المستخدمون والصلاحيات")),
            NavItem(Screen.AUDIT, "people:audit", _("سجل التدقيق")),
        ),
    ),
)


def nav_for(user: Any) -> list[dict[str, Any]]:
    """
    The menu this user should see — groups with nothing visible are dropped.

    An empty group heading over no links reads as a missing screen rather
    than as a screen the role may not have, so the group goes with its items.
    """
    role = getattr(user, "role", None)
    if not role or not getattr(user, "is_authenticated", False):
        return []

    groups: list[dict[str, Any]] = []
    for group in NAV:
        items = []
        for item in group.items:
            if Action.VIEW not in allowed_actions(role, item.screen):
                continue
            try:
                url = reverse(item.route)
            except NoReverseMatch:  # pragma: no cover - a route removed upstream
                continue
            items.append({"screen": item.screen, "label": item.label, "url": url})
        if items:
            groups.append({"title": group.title, "items": items})
    return groups


def navigation(request: Any) -> dict[str, Any]:
    """Context processor — every template gets the menu without asking."""
    return {"nav_groups": nav_for(getattr(request, "user", None))}


__all__ = ["NAV", "NavGroup", "NavItem", "nav_for", "navigation"]
