"""
Deny-rule enforcement — T-151 … T-158, T-271 … T-273, T-277.

The defect these guard against is specific and documented (PERMISSIONS.md
§0.1): the demo defined deny lists and never read them, so every prohibition
held only because the action happened to be absent from the allow list. These
tests assert the prohibitions hold *because a rule refused them*, which is a
different claim and the only one worth making.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

from apps.core.models import AuditEvent
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions import deny_rules, policy
from apps.people.permissions.matrix import ALLOW, allowed_actions

pytestmark = pytest.mark.django_db


def _user(role: str, username: str | None = None) -> User:
    return User.objects.create_user(
        username=username or f"probe.{role.lower()}",
        password="probe-password-1234",
        role=role,
    )


# ---------------------------------------------------------------------------
# T-151 — deny is evaluated first, and structurally cannot be overridden
# ---------------------------------------------------------------------------
def test_t151_granting_permission_cannot_cancel_an_explicit_deny(monkeypatch) -> None:
    """
    The heart of BR-080.

    Grant the centre manager every action on payment-new — the exact mistake a
    future developer makes — and D-01 must still refuse. If this test ever
    fails, every documented prohibition has become accidental again.
    """
    manager = _user(Role.CENTER_MANAGER)
    assert not policy.evaluate(manager, Screen.PAYMENT_NEW, Action.CREATE).allowed

    patched = dict(ALLOW)
    patched[(Screen.PAYMENT_NEW, Role.CENTER_MANAGER)] = frozenset(Action.values)
    monkeypatch.setattr("apps.people.permissions.matrix.ALLOW", patched)

    decision = policy.evaluate(manager, Screen.PAYMENT_NEW, Action.CREATE)
    assert not decision.allowed, "an allow entry overrode an explicit deny — BR-080 is broken"
    assert decision.denial_rule == "D-01"


def test_t151_deny_precedes_allow_for_every_screen_rule() -> None:
    """
    No SCREEN rule is shadowed by a matrix grant anywhere.

    STATIC rules (D-20, D-31) are excluded: they have no runtime matcher
    because they are guaranteed by absence, and their own tests prove it.
    """
    conflicts = []
    for rule in deny_rules.SCREEN_RULES:
        roles = rule.roles or {r for _s, r in ALLOW}
        screens = rule.screens or set(Screen.values)
        actions = rule.actions or set(Action.values)
        for role in roles:
            for screen in screens:
                for action in actions:
                    if action in allowed_actions(role, screen):
                        decision = policy.evaluate(_probe(role), screen, action)
                        if decision.allowed:
                            conflicts.append(f"{rule.rule_id}: {role}/{screen}/{action}")
    assert not conflicts, "deny rules overridden by the allow matrix: " + ", ".join(conflicts)


def _probe(role: str) -> User:
    return User(username="probe", role=role, is_active=True)


# ---------------------------------------------------------------------------
# T-152 … T-158 — the documented prohibitions, by rule
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rule_id", "role", "screen", "action"),
    [
        ("D-01", Role.CENTER_MANAGER, Screen.PAYMENT_NEW, Action.CREATE),
        ("D-03", Role.CASHIER, Screen.REPORTS, Action.VIEW),
        ("D-04", Role.CASHIER, Screen.PARTNERS, Action.VIEW),
        ("D-04", Role.CASHIER, Screen.CLAIMS, Action.VIEW),
        ("D-05", Role.CASHIER, Screen.REFUNDS, Action.CREATE),
        ("D-06", Role.REGISTRATION_OFFICER, Screen.PAYMENT_NEW, Action.CREATE),
        ("D-07", Role.REGISTRATION_OFFICER, Screen.DISCOUNTS, Action.CREATE),
        ("D-08", Role.REGISTRATION_OFFICER, Screen.CLAIMS, Action.VIEW),
        ("D-09", Role.REGISTRATION_OFFICER, Screen.PRICELISTS, Action.EDIT),
        ("D-10", Role.REGISTRATION_OFFICER, Screen.REFUNDS, Action.CREATE),
        ("D-11", Role.CENTER_MANAGER, Screen.AUDIT, Action.EDIT),
        ("D-11", Role.FINANCE_OFFICER, Screen.AUDIT, Action.VOID),
        # The audit account is caught by D-02 first — a stricter rule reaching
        # the same refusal. Asserted here so the precedence is deliberate.
        ("D-02", Role.AUDIT_ACCOUNT, Screen.AUDIT, Action.VOID),
    ],
)
def test_documented_prohibition_is_refused_by_its_rule(
    rule_id: str, role: str, screen: str, action: str
) -> None:
    decision = policy.evaluate(_probe(role), screen, action)
    assert not decision.allowed
    assert decision.denial_rule == rule_id, (
        f"{role}/{screen}/{action} was refused by {decision.denial_rule}, "
        f"but {rule_id} is the rule that should have caught it"
    )


def test_t154_audit_account_writes_nothing_anywhere() -> None:
    """D-02 — every write action, every screen, no exception."""
    auditor = _probe(Role.AUDIT_ACCOUNT)
    for screen in Screen.values:
        for action in (Action.CREATE, Action.EDIT, Action.APPROVE, Action.VOID):
            decision = policy.evaluate(auditor, screen, action)
            assert not decision.allowed, f"audit account may {action} on {screen}"


def test_t155_audit_account_can_view_and_export() -> None:
    """A read-only account that cannot read is not read-only, it is broken."""
    auditor = _probe(Role.AUDIT_ACCOUNT)
    assert policy.evaluate(auditor, Screen.PAYMENTS, Action.VIEW).allowed
    assert policy.evaluate(auditor, Screen.AUDIT, Action.PRINT).allowed


# ---------------------------------------------------------------------------
# T-271 … T-273 — the new role's boundaries (Q-14, BR-099, D-29)
# ---------------------------------------------------------------------------
FIM_FORBIDDEN = [
    Screen.PAYMENT_NEW,
    Screen.CLOSING,
    Screen.DISCOUNTS,
    Screen.REFUNDS,
    Screen.EXTRA_FEES,
    Screen.EXPENSES,
    Screen.CLAIMS,
    Screen.SETTLEMENTS,
    Screen.OPENING_BALANCES,
]


@pytest.mark.parametrize("screen", FIM_FORBIDDEN)
@pytest.mark.parametrize("action", list(Action.values))
def test_t271_finance_manager_is_refused_every_operational_path(screen: str, action: str) -> None:
    decision = policy.evaluate(_probe(Role.FINANCE_MANAGER), screen, action)
    assert not decision.allowed, f"finance manager may {action} on {screen} — D-29 breached"
    assert decision.denial_rule == "D-29"


def test_t271_denial_is_audited_with_its_rule(seeded_settings: None) -> None:
    manager = _user(Role.FINANCE_MANAGER, "fim.denied")
    with pytest.raises(PermissionDenied):
        policy.require(manager, Screen.PAYMENT_NEW, Action.CREATE)

    event = AuditEvent.objects.order_by("-id").first()
    assert event is not None
    assert event.action == "DENIED_ATTEMPT"
    assert event.denial_rule == "D-29"
    assert event.actor_role == Role.FINANCE_MANAGER
    assert event.entity_type == Screen.PAYMENT_NEW


def test_t273_finance_manager_does_not_inherit_finance_officer() -> None:
    """
    Q-14 / BR-099 — tested explicitly, not inferred from the matrix.

    The risk this guards is not a coding mistake but a reasoning mistake:
    "he is the manager, so he should have at least what the officer has".
    That sentence, acted on, dissolves the separation of duties the role was
    created to strengthen.
    """
    fim = _probe(Role.FINANCE_MANAGER)
    fin = _probe(Role.FINANCE_OFFICER)

    fim_grants = {
        (s, a) for s in Screen.values for a in Action.values if policy.evaluate(fim, s, a).allowed
    }
    fin_grants = {
        (s, a) for s in Screen.values for a in Action.values if policy.evaluate(fin, s, a).allowed
    }

    assert not fin_grants.issubset(fim_grants), "finance manager absorbed the officer's rights"
    assert not fim_grants.issubset(fin_grants) or fim_grants != fin_grants
    # The one thing he has that the officer does not is the second signature.
    assert (Screen.CLEARANCE, Action.APPROVE) in fim_grants
    # And the officer keeps operational work the manager must never hold.
    assert (Screen.PAYMENT_NEW, Action.CREATE) in fin_grants
    assert (Screen.PAYMENT_NEW, Action.CREATE) not in fim_grants


def test_finance_manager_keeps_only_his_documented_grants() -> None:
    """The whole of his authority, enumerated. Growth here needs a decision."""
    fim = _probe(Role.FINANCE_MANAGER)
    grants = {
        (s, a) for s in Screen.values for a in Action.values if policy.evaluate(fim, s, a).allowed
    }
    assert grants == {
        (Screen.DASHBOARD, Action.VIEW),
        (Screen.STUDENTS, Action.VIEW),
        (Screen.ENROLLMENTS, Action.VIEW),
        (Screen.PAYMENTS, Action.VIEW),
        (Screen.PAYMENTS, Action.PRINT),
        (Screen.CLEARANCE, Action.VIEW),
        (Screen.CLEARANCE, Action.APPROVE),
        (Screen.CLEARANCE, Action.PRINT),
        (Screen.REPORTS, Action.VIEW),
        (Screen.REPORTS, Action.PRINT),
        (Screen.AUDIT, Action.VIEW),
        (Screen.AUDIT, Action.PRINT),
    }


# ---------------------------------------------------------------------------
# T-277 — the president is not a role (D-31)
# ---------------------------------------------------------------------------
def test_t277_no_role_exists_for_the_university_president() -> None:
    forbidden = {"PRESIDENT", "UNIVERSITY_PRESIDENT", "RECTOR", "رئيس الجامعة"}
    declared = set(Role.values) | {str(label) for label in Role.labels}
    assert not (declared & forbidden), (
        "Q-14: the president is an EXTERNAL approver represented by a text reference "
        "plus an attachment (D-31). Giving him a role creates a workflow state waiting "
        "for a login that will never happen."
    )


def test_t277_no_pending_approval_state_waits_for_an_external_approver() -> None:
    """No model may carry a status that blocks on someone with no account."""
    from django.apps import apps as django_apps

    offenders = []
    for model in django_apps.get_models():
        if model._meta.app_label not in {"core", "people"}:
            continue
        for field in model._meta.get_fields():
            for value, _label in getattr(field, "choices", None) or []:
                if str(value).upper() in {"PENDING_APPROVAL", "AWAITING_PRESIDENT"}:
                    offenders.append(f"{model.__name__}.{field.name}")
    assert not offenders, "D-31 breached by: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# Deferred rules are registered, and honestly labelled
# ---------------------------------------------------------------------------
def test_every_documented_deny_rule_is_registered() -> None:
    """D-01 … D-31, none missing — including the ones enforced in later sprints."""
    expected = {f"D-{n:02d}" for n in range(1, 32)}
    assert set(deny_rules.BY_ID) == expected


def test_deferred_rules_name_the_sprint_that_owns_them() -> None:
    """'Later' is not an owner. Every deferred control names its sprint."""
    for rule in deny_rules.DEFERRED_RULES:
        assert rule.deferred_to, f"{rule.rule_id} is deferred to nobody"
        assert rule.deferred_to.startswith("Sprint")


def test_entity_rules_do_not_pretend_to_be_enforced() -> None:
    """
    An ENTITY rule must not silently decide a screen-level question.

    If it did, the gate report would show a pass for a control that is not
    actually running against its entity — the worst of both outcomes.
    """
    for rule in deny_rules.DEFERRED_RULES:
        assert rule not in deny_rules.SCREEN_RULES


def test_the_three_enforcement_levels_partition_the_rules() -> None:
    """Every rule is runtime-matched, deferred, or guaranteed by absence."""
    total = len(deny_rules.SCREEN_RULES) + len(deny_rules.DEFERRED_RULES)
    total += len(deny_rules.STATIC_RULES)
    assert total == len(deny_rules.RULES)
    assert {r.rule_id for r in deny_rules.STATIC_RULES} == {"D-20", "D-31"}
