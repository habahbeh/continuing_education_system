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
    #: Colour family for the group's icons — one of the ``tone`` names the
    #: stylesheet knows (brand, info, ok, warn, danger, violet, teal, amber).
    #: Presentation only: seven groups in one ink read as one forty-line
    #: column; a hue per group lets the eye find the section before the head
    #: reads its title.
    tone: str = "brand"


#: الشجرة مرتَّبةً بتسلسل عمل المركز لا بتقسيم تقني (بطلب العميل).
#:
#: **المعيار «كم مرّة» لا «ما نوعها».** الترتيب السابق كان يجمع الشاشات بحسب
#: موضوعها — البرامج مع الأسعار، المال مع المال — فكان الموظف الجديد يرى أربعين
#: مدخلاً في سبع مجموعات بلا ما يقول له أيّها أوّل. وأسوأ من ذلك أن «مسار
#: التسجيل والدفع» كان تحت «الرئيسية» فيُقرأ كبداية النظام، وهو ينفّذ §6.2 من
#: وثيقة المتطلبات وحدها — أي أنه يبدأ من اعتماد الوزارة، وهي الخطوة الخامسة.
#: والمركز نفسه تلخبط عندها.
#:
#: فالمجموعات الآن أربعٌ مرقَّمة بترتيب ما يفعله المركز فعلاً:
#:
#:   ① ما يُعرَّف مرّة واحدة عند التركيب
#:   ② ما يُعاد كل فصل وكل دورة جديدة — وأوّله فتح الفصل، لأن الأسعار مرتبطة
#:     بفصل (§4) ورقم المشارك يُبنى منه (BR-001)
#:   ③ ما يتكرّر لكل مشارك، من طلب الالتحاق إلى الشهادة
#:   ④ دورة الشركاء — كل أربعة أشهر أو نهاية الدورة (§3.1)، فهي لا يومية ولا
#:     سنوية وجمعُها مع أيّهما يكذب على قارئها
#:
#: و«المراجعة» بلا رقم عن قصد: ما فيها قراءةٌ لا عملٌ له موضع في التسلسل،
#: وترقيمها يجعلها خطوةً خامسة يظنّ الموظف أنها تنتظره.
#:
#: **ولا قرار صلاحية تغيّر.** كل مدخل يحمل `Screen` و`Action` اللذين كان
#: يحملهما؛ ما تغيّر موضعه في الشجرة. والمجموعة الخالية تُحذف مع عنوانها في
#: ``nav_for``، فمن لا يملك قسماً لا يرى رقمه معلَّقاً على فراغ.
#:
#: وشاشةٌ لا يوجد مسارها بعد تغيب — لا مدخل يشير إلى وعدٍ لا يستطيع النظام
#: الوفاء به.
NAV: tuple[NavGroup, ...] = (
    NavGroup(
        _("الرئيسية"),
        tone="brand",
        items=(
            NavItem(Screen.DASHBOARD, "operations:dashboard", _("لوحة المؤشرات"), icon="gauge"),
        ),
    ),
    # ① ما يُكتب مرّة واحدة: مفردات النظام ومَن يشغّله ومَن يتعاقد معه.
    NavGroup(
        _("① البيانات الأساسية"),
        tone="warn",
        items=(
            NavItem(Screen.SETTINGS, "people:settings", _("الإعدادات"), icon="cog"),
            NavItem(Screen.USERS, "people:users", _("المستخدمون والصلاحيات"), icon="key"),
            # المجال المعرفي قبل مجال الدورة: الثاني يُصنَّف تحت الأول، والبرنامج
            # لا يُحفَظ بلا مجال دورة (BR-061 · قيدٌ في قاعدة البيانات). فترتيب
            # القائمة هو ترتيب الإدخال. وتحمل صلاحية شاشة البرامج نفسها.
            NavItem(
                Screen.PROGRAMS,
                "catalog:knowledge-fields",
                _("المجالات المعرفية"),
                icon="globe",
            ),
            NavItem(Screen.PROGRAMS, "catalog:course-categories", _("مجالات الدورات"), icon="tag"),
            # طريقة الدفع مفردةٌ من مفردات النظام لا فعلٌ من أفعال الصندوق:
            # أمين الصندوق يكتب السند، ولا يخترع الطريقة التي كُتب بها.
            NavItem(Screen.SETTINGS, "cashbox:payment-methods", _("طرق الدفع"), icon="coins"),
            # تُحكم بصلاحية قوائم الأسعار نفسها: التأمين شرطٌ من شروط السعر،
            # ومن يضع المبلغ هو من يسمّي السياسة التي تحكمه (C-26).
            NavItem(
                Screen.PRICELISTS,
                "catalog:deposit-policies",
                _("سياسات التأمين"),
                icon="shield-check",
            ),
            # الشريك يُعرَّف مرّة؛ واتفاقيتُه تُكتب كل دفعة، فهي في ② (§3.4).
            NavItem(Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون"), icon="building"),
            NavItem(
                Screen.OPENING_BALANCES,
                "billing:opening-balances",
                _("الأرصدة الافتتاحية"),
                icon="scale",
            ),
            NavItem(
                Screen.MIGRATION, "datamigration:batches", _("ترحيل البيانات"), icon="database"
            ),
            NavItem(
                Screen.MIGRATION, "datamigration:links", _("ربط السجلات التاريخية"), icon="link"
            ),
        ),
    ),
    # ② ما يُعاد كل فصل، بترتيب §6.1 من وثيقة المتطلبات: البرنامج ثم سعره ثم
    # الدفعة ثم الملف الوزاري. وأوّله الفصل نفسه، وهو ما لم تكن له شاشة.
    NavGroup(
        _("② تجهيز الفصل"),
        tone="violet",
        items=(
            NavItem(Screen.SETTINGS, "people:semesters", _("الفصول الدراسية"), icon="calendar"),
            NavItem(
                Screen.SETTINGS,
                "people:financial-periods",
                _("الفترات المالية"),
                icon="vault",
            ),
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
            NavItem(
                Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة"), icon="tag"
            ),
            # §3.4 — رقم الاتفاقية لكل برنامج يتزايد كل دفعة، فالاتفاقية تُكتب
            # عند تجهيز الدورة لا مرّةً واحدة مع الشريك.
            NavItem(Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات"), icon="doc"),
            NavItem(
                Screen.AGREEMENT_NEW, "partners:agreement-new", _("محرّر اتفاقية"), icon="doc-plus"
            ),
            NavItem(Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة"), icon="calendar"),
            NavItem(
                Screen.MOHE_SUBMIT,
                "operations:mohe-submit",
                _("نموذج الإرسال للوزارة"),
                icon="upload",
            ),
            NavItem(Screen.MOHE, "operations:mohe", _("اعتماد الوزارة"), icon="stamp"),
        ),
    ),
    # ③ ما يتكرّر لكل مشارك، بترتيب §6.2: طلب الالتحاق ثم الدفع ثم الوصل ثم
    # الاعتماد — وحتى الشهادة، فبراءة الذمة والشهادة لكل مشارك أيضاً.
    NavGroup(
        _("③ العمل اليومي"),
        tone="info",
        items=(
            NavItem(
                Screen.ENROLL_FLOW, "operations:enroll-flow", _("مسار التسجيل والدفع"), icon="route"
            ),
            NavItem(
                Screen.STUDENT_NEW, "people:participant-new", _("طلب التحاق جديد"), icon="user-plus"
            ),
            NavItem(Screen.STUDENTS, "people:participants", _("المشاركون"), icon="users"),
            NavItem(Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات"), icon="list"),
            NavItem(Screen.PAYMENT_NEW, "cashbox:payment-new", _("استيفاء دفعة"), icon="coins"),
            NavItem(Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض"), icon="receipt"),
            NavItem(Screen.DISCOUNTS, "billing:discounts", _("الخصومات"), icon="percent"),
            NavItem(
                Screen.EXTRA_FEES, "billing:extra-fees", _("الرسوم الإضافية"), icon="plus-square"
            ),
            NavItem(Screen.REFUNDS, "billing:refunds", _("الاستردادات"), icon="undo"),
            NavItem(Screen.TRANSFERS, "operations:transfers", _("النقل بين الدورات"), icon="swap"),
            NavItem(
                Screen.SPECIAL_CASES, "operations:special-cases", _("الحالات الخاصة"), icon="branch"
            ),
            NavItem(Screen.EXPENSES, "expenses:expenses", _("المصروفات"), icon="wallet"),
            NavItem(Screen.CLOSING, "cashbox:closing", _("الإقفال اليومي"), icon="vault"),
            NavItem(
                Screen.CLEARANCE, "operations:clearances", _("براءة الذمة"), icon="shield-check"
            ),
            NavItem(Screen.CERTIFICATES, "operations:certificates", _("الشهادات"), icon="award"),
        ),
    ),
    # ④ كل أربعة أشهر أو نهاية الدورة (§3.1): استحقاقٌ يُحسب ثم مطالبةٌ تُعتمد
    # ثم مخالصةٌ تُصرف.
    NavGroup(
        _("④ دورة الشركاء"),
        tone="amber",
        items=(
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
    # بلا رقم عن قصد: قراءةٌ لا عمل، وترقيمها يجعلها خطوةً ينتظرها الموظف.
    NavGroup(
        _("المراجعة"),
        tone="teal",
        items=(
            NavItem(Screen.REPORTS, "reporting:reports", _("التقارير"), icon="chart"),
            NavItem(Screen.AUDIT, "people:audit", _("سجل التدقيق"), icon="clock"),
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
            groups.append({"title": group.title, "items": items, "tone": group.tone})
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


def active_entry(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    The one marked entry with its group — what the breadcrumb and the title
    icon are drawn from. Presentation only: nothing here grants anything the
    menu did not already show.
    """
    for group in groups:
        for item in group["items"]:
            if item.get("is_active"):
                return {
                    "label": item["label"],
                    "url": item["url"],
                    "icon": item["icon"],
                    "tone": group["tone"],
                    "group": group["title"],
                }
    return None


#: Code prefix → the register that answers a search for it, and what to call the
#: thing being looked for. Every record in this system carries a prefixed code
#: (BR-002 and the numbering service), so what someone types is enough to know
#: where it lives — no lookup, no second search box, no new query.
#:
#: The destination is offered ONLY if the reader's own menu already contains it:
#: the jump is a shortcut through the menu, never around it.
JUMP_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("R-", "cashbox:payments", _("سند قبض")),
    ("EN-", "operations:enrollments", _("تسجيل")),
    ("CO-", "operations:cohorts", _("دفعة")),
    ("PL-", "catalog:pricelists", _("قائمة أسعار")),
    ("RF-", "billing:refunds", _("طلب استرداد")),
    ("CR-", "billing:refunds", _("ردّ فائض")),
    ("EX-", "expenses:expenses", _("مصروف")),
    ("OB-", "billing:opening-balances", _("رصيد افتتاحي")),
    ("PO-", "billing:opening-balances", _("صرف رصيد")),
    ("TR-", "operations:transfers", _("نقل بين دورات")),
    ("DIP-", "catalog:programs", _("دبلوم")),
    ("SC-", "catalog:short-courses", _("دورة قصيرة")),
    ("ON-", "catalog:online-courses", _("دورة أونلاين")),
    ("OL-", "catalog:online-courses", _("دورة أونلاين")),
)


#: The participants register, which the prefix rule above cannot reach: a
#: participant number is built from the active semester and carries no prefix
#: at all (BR-001), so typing a perfectly correct ``202610001`` matched nothing
#: and the palette answered it with an empty list.
#:
#: Two ways in, and both go to the register's own search rather than to the
#: file URL: a number that does not exist has to come back as «لا مشارك يطابق
#: البحث» on a screen with a way out, not as a 404. The name entry is offered
#: only to a role that may search by name — the restricted roles search by
#: number alone (BR-101), so offering them a name box would be offering a box
#: that always answers «none».
JUMP_PARTICIPANTS = "people:participants"


def jump_targets(groups: list[dict[str, Any]], actor: Any = None) -> list[dict[str, str]]:
    """
    Where a typed CODE would go, limited to registers already in this menu.

    Returned as data for the template to match against what is typed; the
    matching itself is a string comparison in the browser, so a code someone
    remembers reaches its record without a round trip.

    ``match`` says what the browser compares against: ``prefix`` for a code,
    ``digits`` for a bare participant number, ``text`` for a name.
    """
    reachable = {item["url"] for group in groups for item in group["items"]}
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for prefix, route, what in JUMP_PREFIXES:
        try:
            url = reverse(route)
        except NoReverseMatch:  # pragma: no cover - a route removed upstream
            continue
        if url not in reachable or (prefix, url) in seen:
            continue
        seen.add((prefix, url))
        targets.append({"prefix": prefix, "url": url, "what": str(what), "match": "prefix"})
    targets.extend(_participant_jumps(reachable, actor))
    return targets


def _participant_jumps(reachable: set[str], actor: Any) -> list[dict[str, str]]:
    """The two prefix-less ways into the participants register — see above."""
    try:
        url = reverse(JUMP_PARTICIPANTS)
    except NoReverseMatch:  # pragma: no cover - a route removed upstream
        return []
    if url not in reachable:
        return []
    # Imported here, not at module import time: this module is loaded by a
    # context processor on every request, and the services it would pull in
    # carry the models with them.
    from apps.people.services.participant_service import RESTRICTED_ROLES

    jumps = [
        {"prefix": "", "url": url, "what": str(_("ملف مشارك")), "match": "digits"},
    ]
    if getattr(actor, "role", "") not in RESTRICTED_ROLES:
        jumps.append(
            {"prefix": "", "url": url, "what": str(_("مشارك بالاسم")), "match": "text"}
        )
    return jumps


def navigation(request: Any) -> dict[str, Any]:
    """Context processor — every template gets the menu without asking."""
    groups = mark_active(nav_for(getattr(request, "user", None)), getattr(request, "path", ""))
    return {
        "nav_groups": groups,
        "nav_active": active_entry(groups),
        "nav_jump_targets": jump_targets(groups, getattr(request, "user", None)),
    }


__all__ = [
    "JUMP_PARTICIPANTS",
    "JUMP_PREFIXES",
    "NAV",
    "NavGroup",
    "NavItem",
    "active_entry",
    "jump_targets",
    "mark_active",
    "nav_for",
    "navigation",
]
