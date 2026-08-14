"""
Explicit deny rules D-01 … D-31 (PERMISSIONS.md §4).

The demo defined `deny` lists and then never read them, so every documented
prohibition held **by accident** — the forbidden action simply happened to be
missing from the allow list. Adding one allow entry would have silently
dropped a control. This module exists so that never happens here: deny is a
first-class registry evaluated BEFORE any allow lookup, and a deny cannot be
cancelled by granting permission.

Enforcement levels
------------------
``SCREEN``  the rule is fully decidable from (role, screen, action). Evaluated
            at runtime by ``first_matching``. Enforced now, in this sprint.
``ENTITY``  the rule needs the object itself (its state, its creator, its
            date). The rule is REGISTERED now with the sprint that owns its
            entity, and its screen-level projection — where one exists — is
            enforced now. Full enforcement is DEFERRED, and the gate report
            says so rather than claiming a pass.
``STATIC``  the rule is guaranteed by ABSENCE, and there is nothing to evaluate
            at runtime: D-20 holds because no code path changes a session's
            role, D-31 because no role for the president exists. These are
            enforced by static tests (T-163, T-277) and are listed here so the
            full set of controls reads as one list rather than two.

Registering the deferred rules now is deliberate: the list of controls is a
design artefact, and a control that exists only as a sentence in a document
tends to be discovered late and implemented differently by each sprint.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apps.people.constants import READ_ONLY_ACTIONS, Action, Screen
from apps.people.models import Role

SCREEN = "SCREEN"
ENTITY = "ENTITY"
STATIC = "STATIC"


@dataclass(frozen=True)
class DenyRule:
    """One explicit prohibition from PERMISSIONS.md §4."""

    rule_id: str
    summary_ar: str
    level: str
    #: Roles the rule binds. Empty means EVERY role, including the manager.
    roles: frozenset[str] = field(default_factory=frozenset)
    #: Screens the rule covers. Empty means every screen.
    screens: frozenset[str] = field(default_factory=frozenset)
    #: Actions the rule forbids. Empty means every action.
    actions: frozenset[str] = field(default_factory=frozenset)
    #: Sprint that will enforce the entity-level half. None for SCREEN rules.
    deferred_to: str | None = None
    #: Optional extra condition on the object, used by ENTITY rules.
    predicate: Callable[[Any], bool] | None = None

    def matches(self, role: str, screen: str, action: str) -> bool:
        if self.roles and role not in self.roles:
            return False
        if self.screens and screen not in self.screens:
            return False
        return not (self.actions and action not in self.actions)


_ALL_ACTIONS = frozenset(Action.values)
_WRITE_ACTIONS = _ALL_ACTIONS - READ_ONLY_ACTIONS
_PARTNER_SCREENS = frozenset(
    {
        Screen.PARTNERS,
        Screen.AGREEMENTS,
        Screen.AGREEMENT_NEW,
        Screen.CLAIMS,
        Screen.SETTLEMENTS,
        Screen.ENTITLEMENT,
        Screen.OBLIGATIONS,
    }
)

RULES: tuple[DenyRule, ...] = (
    # --- Enforced in full, now -------------------------------------------
    DenyRule(
        "D-01",
        "مدير المركز لا يُنشئ سند قبض ولا يستوفي دفعة (BR-081)",
        SCREEN,
        roles=frozenset({Role.CENTER_MANAGER}),
        screens=frozenset({Screen.PAYMENT_NEW}),
    ),
    DenyRule(
        "D-02",
        "حساب التدقيق لا يكتب شيئاً — بلا استثناء (BR-082)",
        SCREEN,
        roles=frozenset({Role.AUDIT_ACCOUNT}),
        actions=_WRITE_ACTIONS,
    ),
    DenyRule(
        "D-03",
        "الصندوق لا يرى التقارير (BR-083)",
        SCREEN,
        roles=frozenset({Role.CASHIER}),
        screens=frozenset({Screen.REPORTS}),
    ),
    DenyRule(
        "D-04",
        "الصندوق لا يرى بيانات الشركاء ولا الاتفاقيات ولا المطالبات (BR-083)",
        SCREEN,
        roles=frozenset({Role.CASHIER}),
        screens=_PARTNER_SCREENS,
    ),
    DenyRule(
        "D-05",
        "الصندوق لا يُنشئ استرداداً ولا خصماً",
        SCREEN,
        roles=frozenset({Role.CASHIER}),
        screens=frozenset({Screen.REFUNDS, Screen.DISCOUNTS}),
    ),
    DenyRule(
        "D-06",
        "موظف التسجيل لا يقبض نقداً",
        SCREEN,
        roles=frozenset({Role.REGISTRATION_OFFICER}),
        screens=frozenset({Screen.PAYMENT_NEW}),
    ),
    DenyRule(
        "D-07",
        "موظف التسجيل لا يعتمد خصماً ولا يُنشئه (BR-030)",
        SCREEN,
        roles=frozenset({Role.REGISTRATION_OFFICER}),
        screens=frozenset({Screen.DISCOUNTS}),
    ),
    DenyRule(
        "D-08",
        "موظف التسجيل لا يصرف للشركاء ولا يرى مطالباتهم",
        SCREEN,
        roles=frozenset({Role.REGISTRATION_OFFICER}),
        screens=_PARTNER_SCREENS,
    ),
    DenyRule(
        "D-09",
        "موظف التسجيل لا يعدّل الأسعار",
        SCREEN,
        roles=frozenset({Role.REGISTRATION_OFFICER}),
        screens=frozenset({Screen.PRICELISTS}),
        actions=frozenset({Action.CREATE, Action.EDIT, Action.APPROVE}),
    ),
    DenyRule(
        "D-10",
        "موظف التسجيل لا يُنشئ استرداداً",
        SCREEN,
        roles=frozenset({Role.REGISTRATION_OFFICER}),
        screens=frozenset({Screen.REFUNDS}),
    ),
    DenyRule(
        "D-11",
        "لا أحد يعدّل أو يحذف قيداً في سجل التدقيق (BR-084)",
        SCREEN,
        screens=frozenset({Screen.AUDIT}),
        actions=frozenset({Action.CREATE, Action.EDIT, Action.APPROVE, Action.VOID}),
    ),
    DenyRule(
        "D-20",
        "لا تبديل دور على الجلسة الحالية",
        STATIC,
        # Nothing to match at runtime: the guarantee is that no code path
        # exists which changes a session's role. T-163 proves the absence;
        # user_service.set_role refuses self-assignment explicitly.
    ),
    DenyRule(
        "D-29",
        "المدير المالي لا يقبض ولا يُقفل ولا يُنشئ/يعتمد خصماً أو استرداداً "
        "أو رسماً إضافياً أو مصروفاً أو مطالبة (Q-14 · BR-099)",
        SCREEN,
        roles=frozenset({Role.FINANCE_MANAGER}),
        screens=frozenset(
            {
                Screen.PAYMENT_NEW,
                Screen.CLOSING,
                Screen.DISCOUNTS,
                Screen.REFUNDS,
                Screen.EXTRA_FEES,
                Screen.EXPENSES,
                Screen.CLAIMS,
                Screen.SETTLEMENTS,
                Screen.OPENING_BALANCES,
            }
        ),
    ),
    DenyRule(
        "D-31",
        "لا حساب ولا دور ولا سير موافقة داخلي لرئيس الجامعة (Q-14)",
        STATIC,
        # Enforced by absence: no Role member exists for the president, and no
        # model carries a status that waits for one. T-277 proves both.
    ),
    # --- Registered now, entity-level enforcement deferred ----------------
    DenyRule(
        "D-12",
        "لا أحد يعدّل مطالبة معتمدة (BR-051)",
        ENTITY,
        screens=frozenset({Screen.CLAIMS}),
        deferred_to="Sprint 5",
    ),
    DenyRule(
        "D-13",
        "لا أحد يعدّل سنداً صادراً (BR-025)",
        ENTITY,
        screens=frozenset({Screen.PAYMENTS}),
        deferred_to="Sprint 4",
    ),
    DenyRule(
        "D-14",
        "لا أحد يعدّل قائمة أسعار معتمدة (BR-008)",
        ENTITY,
        screens=frozenset({Screen.PRICELISTS}),
        deferred_to="Sprint 3",
    ),
    DenyRule(
        "D-15",
        "لا أحد يعدّل لقطة أسعار اتفاقية موقّعة (BR-042)",
        ENTITY,
        screens=frozenset({Screen.AGREEMENTS}),
        deferred_to="Sprint 5",
    ),
    DenyRule(
        "D-16",
        "لا أحد يعدّل إقفالاً مقفلاً (BR-026)",
        ENTITY,
        screens=frozenset({Screen.CLOSING}),
        deferred_to="Sprint 4",
    ),
    DenyRule(
        "D-17",
        "أمين الصندوق لا يعتمد إقفال يومه (BR-028)",
        ENTITY,
        roles=frozenset({Role.CASHIER}),
        screens=frozenset({Screen.CLOSING}),
        actions=frozenset({Action.APPROVE}),
        deferred_to="Sprint 4",
    ),
    DenyRule(
        "D-18",
        "لا أحد يعتمد ما أنشأه بنفسه (فصل المهام)",
        ENTITY,
        actions=frozenset({Action.APPROVE}),
        deferred_to="Sprint 4",
    ),
    DenyRule("D-19", "لا حذف فيزيائي لأي كيان مالي", ENTITY, deferred_to="Sprint 4"),
    DenyRule(
        "D-21",
        "لا تسجيل على دفعة غير معتمدة وزارياً (BR-013)",
        ENTITY,
        screens=frozenset({Screen.ENROLLMENTS}),
        deferred_to="Sprint 6",
    ),
    DenyRule(
        "D-22",
        "لا إصدار شهادة بلا براءة ذمة مكتملة (BR-075)",
        ENTITY,
        screens=frozenset({Screen.CERTIFICATES}),
        deferred_to="Sprint 7",
    ),
    DenyRule("D-23", "لا حركة مالية بتاريخ في فترة مقفلة", ENTITY, deferred_to="Sprint 4"),
    DenyRule(
        "D-24",
        "لا رصيد افتتاحي بلا مراجع ومعتمِد مختلفَين عن المنشئ (BR-094)",
        ENTITY,
        screens=frozenset({Screen.OPENING_BALANCES}),
        deferred_to="Sprint 8",
    ),
    DenyRule(
        "D-25",
        "لا إنشاء جماعي للأرصدة الافتتاحية (BR-094)",
        ENTITY,
        screens=frozenset({Screen.OPENING_BALANCES}),
        deferred_to="Sprint 8",
    ),
    DenyRule(
        "D-26",
        "لا كيان مالي إنتاجي من الأرشيف التاريخي (ADR-013)",
        ENTITY,
        screens=frozenset({Screen.MIGRATION}),
        deferred_to="Sprint 8",
    ),
    DenyRule(
        "D-27",
        "لا تعديل على سجل مؤرشف بعد الأرشفة (BR-087)",
        ENTITY,
        screens=frozenset({Screen.MIGRATION}),
        deferred_to="Sprint 8",
    ),
    DenyRule(
        "D-28",
        "لا احتساب التأمين إيراداً ولا إدخاله وعاء الشريك (BR-092)",
        ENTITY,
        deferred_to="Sprint 4",
    ),
    DenyRule(
        "D-30",
        "لا مصادقة ثانية على الخطوة المالية ممن أدّى المصادقة الأولى (Q-14)",
        ENTITY,
        screens=frozenset({Screen.CLEARANCE}),
        actions=frozenset({Action.APPROVE}),
        deferred_to="Sprint 7",
    ),
)

#: Runtime matchers — enforcement complete in this sprint.
SCREEN_RULES: tuple[DenyRule, ...] = tuple(r for r in RULES if r.level == SCREEN)

#: Registered but not yet fully enforceable, each naming the sprint that owns it.
DEFERRED_RULES: tuple[DenyRule, ...] = tuple(r for r in RULES if r.level == ENTITY)

#: Guaranteed by absence, proved by static tests. Never matched at runtime.
STATIC_RULES: tuple[DenyRule, ...] = tuple(r for r in RULES if r.level == STATIC)

BY_ID: dict[str, DenyRule] = {rule.rule_id: rule for rule in RULES}


def first_matching(role: str, screen: str, action: str) -> DenyRule | None:
    """
    Return the first SCREEN-level rule forbidding this combination.

    Only SCREEN rules participate. An ENTITY rule without its object cannot be
    evaluated, and guessing would either block legitimate work or — worse —
    create the impression of a control that is not actually running. A STATIC
    rule has nothing to evaluate at all.

    Order matters only for which id is REPORTED, not for the outcome: where
    two rules both forbid a combination (the audit account voiding an audit
    row is caught by D-02 before D-11), either answer is a correct refusal.
    """
    for rule in SCREEN_RULES:
        if rule.predicate is not None and not rule.predicate(None):
            continue
        if rule.matches(role, screen, action):
            return rule
    return None


__all__ = [
    "BY_ID",
    "DEFERRED_RULES",
    "ENTITY",
    "RULES",
    "SCREEN",
    "SCREEN_RULES",
    "STATIC",
    "STATIC_RULES",
    "DenyRule",
    "first_matching",
]
