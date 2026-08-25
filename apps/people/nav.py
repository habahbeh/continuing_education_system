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
    """
    One menu entry, and the permission that earns it.

    ``action`` exists because not every screen's entry is a VIEW. Recording a
    partner is ``C`` on the partners screen — there is no PARTNER_NEW row in
    the matrix — so an entry filtered on VIEW would offer the finance officer
    and the audit account, who hold ``V P`` there, a link to a page that
    refuses them. VIEW remains the default because it is what almost every
    entry means.
    """

    screen: str
    route: str
    label: Any
    action: str = Action.VIEW
    #: Name of a symbol in the sidebar's inline sprite, minus the ``i-``.
    #: Presentation only — it earns no permission and decides no order. The
    #: default is a real symbol, so an entry added without one gets a neutral
    #: mark rather than an empty gap where every sibling has a glyph.
    icon: str = "dot"


@dataclass(frozen=True)
class NavGroup:
    title: Any
    items: tuple[NavItem, ...]


#: The tree as it stands after Sprint 8B waves 0–2. Screens whose route does
#: not exist yet are simply absent — an entry pointing at an unbuilt screen
#: would be a promise the system cannot keep.
NAV: tuple[NavGroup, ...] = (
    # Sprint 8K: the order follows the client-approved demo sidebar. Some
    # entries are informational landing pages, but every visible promise has a
    # real guarded route behind it.
    NavGroup(
        _("الرئيسية"),
        (
            NavItem(Screen.DASHBOARD, "operations:dashboard", _("لوحة المؤشرات"), icon="gauge"),
            NavItem(
                Screen.ENROLL_FLOW, "operations:enroll-flow", _("مسار التسجيل والدفع"), icon="route"
            ),
        ),
    ),
    NavGroup(
        _("المشاركون والتسجيل"),
        (
            NavItem(Screen.STUDENTS, "people:participants", _("المشاركون"), icon="users"),
            NavItem(
                Screen.STUDENT_NEW, "people:participant-new", _("طلب التحاق جديد"), icon="user-plus"
            ),
            NavItem(Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات"), icon="list"),
            NavItem(Screen.TRANSFERS, "operations:transfers", _("النقل بين الدورات"), icon="swap"),
            NavItem(
                Screen.SPECIAL_CASES, "operations:special-cases", _("الحالات الخاصة"), icon="branch"
            ),
        ),
    ),
    NavGroup(
        _("البرامج والأسعار"),
        (
            NavItem(Screen.PROGRAMS, "catalog:programs", _("الدبلومات التدريبية"), icon="cap"),
            NavItem(
                Screen.SHORT_COURSES, "catalog:short-courses", _("الدورات القصيرة"), icon="book"
            ),
            NavItem(
                Screen.ONLINE_COURSES,
                "catalog:online-courses",
                _("الدورات الأونلاين"),
                icon="globe",
            ),
            NavItem(Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة"), icon="calendar"),
            NavItem(
                Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة"), icon="tag"
            ),
            NavItem(Screen.MOHE, "operations:mohe", _("اعتماد الوزارة"), icon="stamp"),
            NavItem(
                Screen.MOHE_SUBMIT,
                "operations:mohe-submit",
                _("نموذج الإرسال للوزارة"),
                icon="upload",
            ),
        ),
    ),
    NavGroup(
        _("الشؤون المالية"),
        (
            NavItem(Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض"), icon="receipt"),
            NavItem(Screen.PAYMENT_NEW, "cashbox:payment-new", _("استيفاء دفعة"), icon="coins"),
            NavItem(Screen.CLOSING, "cashbox:closing", _("الإقفال اليومي"), icon="vault"),
            NavItem(Screen.DISCOUNTS, "billing:discounts", _("الخصومات"), icon="percent"),
            NavItem(Screen.REFUNDS, "billing:refunds", _("الاستردادات"), icon="undo"),
            NavItem(
                Screen.EXTRA_FEES, "billing:extra-fees", _("الرسوم الإضافية"), icon="plus-square"
            ),
            NavItem(Screen.EXPENSES, "expenses:expenses", _("المصروفات"), icon="wallet"),
            NavItem(
                Screen.OPENING_BALANCES,
                "billing:opening-balances",
                _("الأرصدة الافتتاحية"),
                icon="scale",
            ),
        ),
    ),
    NavGroup(
        _("الشركاء والمخالصات"),
        (
            NavItem(Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون"), icon="building"),
            NavItem(Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات"), icon="doc"),
            NavItem(
                Screen.AGREEMENT_NEW, "partners:agreement-new", _("محرّر اتفاقية"), icon="doc-plus"
            ),
            NavItem(
                Screen.ENTITLEMENT, "settlements:entitlement", _("استحقاق الشركاء"), icon="checks"
            ),
            NavItem(Screen.CLAIMS, "settlements:claims", _("المطالبات"), icon="coins"),
            NavItem(
                Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات"), icon="doc-check"
            ),
            NavItem(
                Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء"), icon="alert"
            ),
            NavItem(
                Screen.OBLIGATIONS, "settlements:absences", _("غيابات المدربين"), icon="user-off"
            ),
        ),
    ),
    NavGroup(
        _("الإنهاء والشهادات"),
        (
            NavItem(
                Screen.CLEARANCE, "operations:clearances", _("براءة الذمة"), icon="shield-check"
            ),
            NavItem(Screen.CERTIFICATES, "operations:certificates", _("الشهادات"), icon="award"),
        ),
    ),
    NavGroup(
        _("النظام"),
        (
            NavItem(Screen.REPORTS, "reporting:reports", _("التقارير"), icon="chart"),
            NavItem(Screen.USERS, "people:users", _("المستخدمون والصلاحيات"), icon="key"),
            NavItem(Screen.AUDIT, "people:audit", _("سجل التدقيق"), icon="clock"),
            NavItem(Screen.SETTINGS, "people:settings", _("الإعدادات"), icon="cog"),
            NavItem(
                Screen.MIGRATION, "datamigration:batches", _("ترحيل البيانات"), icon="database"
            ),
            NavItem(
                Screen.MIGRATION, "datamigration:links", _("ربط السجلات التاريخية"), icon="link"
            ),
            NavItem(Screen.SETTINGS, "people:coverage", _("مصفوفة تغطية المتطلبات"), icon="grid"),
            NavItem(Screen.SETTINGS, "people:future", _("النطاق المستقبلي"), icon="flag"),
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
            if item.action not in allowed_actions(role, item.screen):
                continue
            try:
                url = reverse(item.route)
            except NoReverseMatch:  # pragma: no cover - a route removed upstream
                continue
            items.append(
                {"screen": item.screen, "label": item.label, "url": url, "icon": item.icon}
            )
        if items:
            groups.append({"title": group.title, "items": items})
    return groups


def mark_active(groups: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    """
    Flag the ONE entry the current page belongs to (Sprint 8I).

    Longest matching prefix, and that is the whole subtlety. Two entries can
    share a Screen — «الشركاء المتعاقدون» (/partners/) and «شريك جديد»
    (/partners/new/) are both PARTNERS — so the template's old test on
    ``item.screen == active_screen`` highlighted both at once. Matching on the
    path alone is not enough either: an exact test leaves nothing lit on a
    detail page like /partners/PRT-1/, and a plain prefix test lights the
    parent AND the child on /partners/new/.

    Taking the longest prefix answers all three: the child wins on its own
    page, the parent wins on a detail page beneath it, and exactly one entry
    is ever marked.
    """
    candidates = [
        item["url"]
        for group in groups
        for item in group["items"]
        if path == item["url"] or path.startswith(item["url"])
    ]
    best = max(candidates, key=len) if candidates else None
    for group in groups:
        for item in group["items"]:
            item["is_active"] = item["url"] == best
    return groups


def navigation(request: Any) -> dict[str, Any]:
    """Context processor — every template gets the menu without asking."""
    groups = nav_for(getattr(request, "user", None))
    return {"nav_groups": mark_active(groups, getattr(request, "path", ""))}


__all__ = ["NAV", "NavGroup", "NavItem", "mark_active", "nav_for", "navigation"]
