"""
T-150 — the full permission matrix, and T-282 — its link to the document.

T-150 generates 37 screens x 6 roles x 6 actions = 1332 cases from the matrix
data table and asserts the engine agrees with every one. Not one case is
hand-written: 1332 hand-written assertions is a test nobody maintains, and an
unmaintained matrix test is worse than none because it looks like coverage.

T-282 is the reason the generated form is safe. It fails the build when a
Screen or business Role exists in code without a cell, so a new screen cannot
be added and quietly left unguarded — which is precisely how the "deny lists
that were never read" defect in the demo would come back.
"""

from __future__ import annotations

import pytest

from apps.people.constants import BUSINESS_ROLES, Action, Screen
from apps.people.models import Role, User
from apps.people.permissions import policy
from apps.people.permissions.matrix import ALLOW, DOC_REF, REPORT_ACCESS, allowed_actions

pytestmark = pytest.mark.django_db

EXPECTED_SCREENS = 37
EXPECTED_ROLES = 6
EXPECTED_ACTIONS = 6
EXPECTED_CASES = EXPECTED_SCREENS * EXPECTED_ROLES * EXPECTED_ACTIONS  # 1332


def _user(role: str) -> User:
    return User(username=f"probe.{role.lower()}", role=role, is_active=True)


def _cases() -> list[tuple[str, str, str]]:
    return [
        (screen, role, action)
        for screen in Screen.values
        for role in BUSINESS_ROLES
        for action in Action.values
    ]


# ---------------------------------------------------------------------------
# Shape of the matrix
# ---------------------------------------------------------------------------
def test_matrix_dimensions_match_the_document() -> None:
    assert len(Screen.values) == EXPECTED_SCREENS
    assert len(BUSINESS_ROLES) == EXPECTED_ROLES
    assert len(Action.values) == EXPECTED_ACTIONS
    assert len(_cases()) == EXPECTED_CASES


def test_t282_every_screen_and_role_has_a_documented_cell() -> None:
    """
    T-282 — code and PERMISSIONS.md §3 cannot drift apart silently.

    Adding a Screen member or a business Role without a matrix row fails here.
    """
    missing = [
        f"{screen} / {role}"
        for screen in Screen.values
        for role in BUSINESS_ROLES
        if (screen, role) not in ALLOW
    ]
    assert not missing, (
        "Screen/role combinations declared in code with no cell in the matrix. "
        "Add the row to PERMISSIONS.md §3 first, then transcribe it: " + ", ".join(missing)
    )


def test_t282_matrix_has_no_cells_for_undeclared_screens_or_roles() -> None:
    """The reverse drift: a stale row left behind after a rename."""
    stray = [
        f"{screen} / {role}"
        for (screen, role) in ALLOW
        if screen not in set(Screen.values) or role not in set(BUSINESS_ROLES)
    ]
    assert not stray, "Matrix cells for things that no longer exist: " + ", ".join(stray)


def test_every_cell_carries_a_documentation_reference() -> None:
    """A failing cell must be able to name the paragraph to go and read."""
    assert set(DOC_REF) == set(ALLOW)
    assert all(ref.startswith("§3") for ref in DOC_REF.values())


# ---------------------------------------------------------------------------
# T-150 — the generated matrix itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("screen", "role", "action"), _cases())
def test_t150_matrix_cell(screen: str, role: str, action: str) -> None:
    """
    Every one of the 1332 cases, compared against PERMISSIONS.md §3.

    An unintended ALLOW fails the build. So does an unintended DENY: a matrix
    that is merely stricter than the document is still a matrix that does not
    match it, and someone will "fix" the wrong side of the discrepancy later.
    """
    from apps.people.permissions import deny_rules

    decision = policy.evaluate(_user(role), screen, action)
    granted_by_matrix = action in allowed_actions(role, screen)
    denied_by_rule = deny_rules.first_matching(role, screen, action) is not None
    expected = granted_by_matrix and not denied_by_rule

    assert decision.allowed is expected, (
        f"{screen} / {role} / {action}: engine says "
        f"{'ALLOW' if decision.allowed else 'DENY'}, "
        f"PERMISSIONS.md {DOC_REF[(screen, role)]} says "
        f"{'ALLOW' if expected else 'DENY'} (rule: {decision.denial_rule or '—'})"
    )


def test_t150_case_count_is_actually_1332() -> None:
    """Guards against a silently shrinking parametrisation."""
    assert len(_cases()) == 1332


# ---------------------------------------------------------------------------
# Denials must be explainable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("screen", "role", "action"), _cases())
def test_t167_every_denial_names_a_rule(screen: str, role: str, action: str) -> None:
    """BR-085 — a refusal without a rule id cannot be audited or defended."""
    decision = policy.evaluate(_user(role), screen, action)
    if not decision.allowed:
        assert decision.denial_rule, f"{screen}/{role}/{action} denied with no rule id"
        assert decision.reason_ar, f"{screen}/{role}/{action} denied with no Arabic reason"


# ---------------------------------------------------------------------------
# Per-report scope (footnotes 23, 24, 31)
# ---------------------------------------------------------------------------
def test_report_scope_matches_the_footnotes() -> None:
    assert REPORT_ACCESS[Role.REGISTRATION_OFFICER] == frozenset({4, 5})
    assert REPORT_ACCESS[Role.FINANCE_MANAGER] == frozenset({5})
    assert REPORT_ACCESS[Role.CASHIER] == frozenset()
    assert REPORT_ACCESS[Role.CENTER_MANAGER] == frozenset({1, 2, 3, 4, 5, 6, 7})


def test_screen_grant_is_not_a_grant_of_all_seven_reports(seeded_settings: None) -> None:
    """The reports screen is one row but seven documents behind it."""
    from django.core.exceptions import PermissionDenied

    manager = User.objects.create_user(
        username="fin.manager.reports",
        password="probe-password-1234",
        role=Role.FINANCE_MANAGER,
    )
    policy.require_report(manager, 5)  # the participant statement — allowed
    with pytest.raises(PermissionDenied):
        policy.require_report(manager, 1)  # revenue — outside his v1 scope
