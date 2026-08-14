"""
The permission engine — two-stage evaluation, deny first (BR-080).

    1. explicit deny  →  decided, and nothing can overturn it
    2. allow matrix   →  granted only if the cell lists the action
    3. otherwise      →  DENIED

There is no fourth branch and no implicit permission. "Not mentioned" means
"forbidden", which is why a user with no role has no access to anything rather
than access to everything not yet restricted.

Why the stages are structural rather than ordered statements: `evaluate` looks
up the deny registry and RETURNS. An allow entry is never consulted for a
combination a deny rule matched, so adding permission to the matrix cannot
cancel a prohibition — the failure mode that made every control in the demo
accidental (PERMISSIONS.md §0.1, T-151).

Enforcement lives here and only here. Templates hide buttons for the look of
the thing; hiding is never the control (T-153, T-272 call the service directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied

from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import deny_rules, matrix

#: denial_rule value used when access falls through for want of a grant, as
#: opposed to being struck down by a named rule. BR-080 is the rule that says
#: silence means no.
IMPLICIT_DENY_RULE = "BR-080"

#: denial_rule value for a request from someone not authenticated at all.
UNAUTHENTICATED_RULE = "Q-12"

#: denial_rule value for an authenticated user carrying no role.
NO_ROLE_RULE = "Q-12/BR-080"


@dataclass(frozen=True)
class Decision:
    """The outcome of one permission question, with the reason attached."""

    allowed: bool
    screen: str
    action: str
    role: str
    #: Identifier of the rule that decided a denial ("D-29", "BR-080", …).
    #: A denial without one is a bug, and T-167 fails the build over it.
    denial_rule: str = ""
    reason_ar: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _role_of(user: Any) -> str:
    return (getattr(user, "role", "") or "") if user is not None else ""


def evaluate(user: Any, screen: str, action: str, *, obj: Any = None) -> Decision:
    """
    Decide whether ``user`` may perform ``action`` on ``screen``.

    ``obj`` is accepted so entity-level rules can be honoured as their sprints
    land. It is unused today: every rule that needs an object is registered in
    deny_rules with the sprint that owns it, and is reported DEFERRED rather
    than silently treated as satisfied.
    """
    role = _role_of(user)

    if user is None or not getattr(user, "is_authenticated", False):
        return Decision(False, screen, action, role, UNAUTHENTICATED_RULE, "لم يتم تسجيل الدخول")

    if not getattr(user, "is_active", False):
        return Decision(False, screen, action, role, UNAUTHENTICATED_RULE, "الحساب معطّل")

    if not role:
        # A user without a role is not an administrator by default. Q-12 puts
        # the role in the account; an account that carries none carries no
        # authority either.
        return Decision(False, screen, action, role, NO_ROLE_RULE, "الحساب بلا دور")

    # --- Stage 1: explicit deny, evaluated first and final -----------------
    rule = deny_rules.first_matching(role, screen, action)
    if rule is not None:
        return Decision(False, screen, action, role, rule.rule_id, rule.summary_ar)

    # --- Stage 2: allow matrix --------------------------------------------
    if action in matrix.allowed_actions(role, screen):
        return Decision(True, screen, action, role)

    # --- Stage 3: silence means no ----------------------------------------
    return Decision(
        False, screen, action, role, IMPLICIT_DENY_RULE, "لا يوجد إذن صريح بهذا الإجراء"
    )


def is_allowed(user: Any, screen: str, action: str, *, obj: Any = None) -> bool:
    """Boolean form, for templates deciding what to draw. NOT a control."""
    return evaluate(user, screen, action, obj=obj).allowed


def require(user: Any, screen: str, action: str, *, obj: Any = None, request: Any = None) -> None:
    """
    Enforce a permission, recording every refusal (BR-085).

    Raises PermissionDenied on failure, after writing a DENIED_ATTEMPT audit
    row carrying the rule that refused. Every path — view, future API,
    management command — goes through this function.

    **Call this BEFORE opening a transaction, never inside one.**

    The refusal is written to the audit trail and then PermissionDenied is
    raised. If an enclosing ``transaction.atomic()`` catches that exception —
    or simply unwinds through it — the rollback takes the audit row with it,
    and the refusal leaves no trace at all. A denial that erases its own
    evidence is worse than no denial logging, because the gate report shows
    BR-085 satisfied while the record is empty.

    So services check first and mutate second::

        policy.require(actor, Screen.USERS, Action.EDIT)   # may raise
        with transaction.atomic():                          # only then
            ...

    ``test_denial_audit_survives`` and ``test_no_service_requires_inside_atomic``
    hold this shape in place.
    """
    decision = evaluate(user, screen, action, obj=obj)
    if decision.allowed:
        return

    write_audit(
        action="DENIED_ATTEMPT",
        entity_type=screen,
        entity_id=str(getattr(obj, "pk", "") or ""),
        reference=f"{screen}:{action}",
        summary_ar=f"محاولة مرفوضة — {decision.reason_ar} ({decision.denial_rule})",
        actor=user if getattr(user, "is_authenticated", False) else None,
        denial_rule=decision.denial_rule,
        request=request,
    )
    raise PermissionDenied(decision.reason_ar)


def require_report(user: Any, report_number: int, *, request: Any = None) -> None:
    """
    Enforce per-report access (PERMISSIONS.md §3.7 footnotes 23, 24, 31).

    The reports screen is one row in the matrix but seven different documents
    behind it. Granting the screen is not granting all seven.
    """
    require(user, Screen.REPORTS, Action.VIEW, request=request)
    if report_number not in matrix.allowed_reports(_role_of(user)):
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=Screen.REPORTS,
            reference=f"report:{report_number}",
            summary_ar=f"محاولة فتح التقرير {report_number} خارج نطاق الدور",
            actor=user,
            denial_rule="BR-099" if _role_of(user) == "FINANCE_MANAGER" else "BR-080",
            request=request,
        )
        raise PermissionDenied(f"التقرير {report_number} خارج نطاق دورك")


__all__ = [
    "IMPLICIT_DENY_RULE",
    "Decision",
    "evaluate",
    "is_allowed",
    "require",
    "require_report",
]
