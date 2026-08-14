"""
A refusal must not erase its own evidence (BR-085).

Found while building Sprint 2A: `user_service.set_role` was decorated with
`@transaction.atomic`, wrote a DENIED_ATTEMPT row and then raised
PermissionDenied. The rollback took the audit row with it, so a blocked
privilege escalation left NO trace — while the gate report would still have
shown "every denial is audited" as satisfied, because the write really did
happen. It just did not survive.

The fix is layering, not cleverness: decide, then act. These tests hold that
layering in place for every service written from here on, including the
financial ones in Sprints 4 and 5 where the same mistake would hide a blocked
attempt to touch cash.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.core.models import AuditEvent
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions import policy

pytestmark = pytest.mark.django_db

APPS_DIR = Path(__file__).resolve().parents[3] / "apps"
PASSWORD = "probe-password-1234"


def test_denial_audit_survives_a_refused_service_call(seeded_settings: None) -> None:
    """The regression itself: self-promotion is refused AND recorded."""
    from apps.people.services import user_service

    admin = User.objects.create_user(
        username="sysadmin.durable", password=PASSWORD, role=Role.SYSTEM_ADMINISTRATOR
    )
    with pytest.raises(PermissionDenied):
        user_service.set_role(actor=admin, target=admin, role=Role.CENTER_MANAGER)

    assert AuditEvent.objects.filter(denial_rule="D-20").exists(), (
        "the refusal was rolled back with the transaction — BR-085 is not satisfied"
    )


def test_denial_inside_a_caller_transaction_is_visibly_lost(seeded_settings: None) -> None:
    """
    Demonstrates WHY the rule exists, so it is not mistaken for style.

    Wrapping the call in a transaction that swallows the exception destroys the
    audit row. This test documents the failure mode rather than pretending the
    engine is immune to it.
    """
    cashier = User.objects.create_user(
        username="cashier.durable", password=PASSWORD, role=Role.CASHIER
    )
    before = AuditEvent.objects.count()

    with contextlib_suppress_rollback(), transaction.atomic():
        with pytest.raises(PermissionDenied):
            policy.require(cashier, Screen.REPORTS, Action.VIEW)
        raise _RollbackError

    assert AuditEvent.objects.count() == before, (
        "expected the rollback to discard the denial — if this now passes, the "
        "engine gained durable denial logging and this test should be rewritten"
    )


class _RollbackError(Exception):
    pass


def contextlib_suppress_rollback():
    import contextlib

    return contextlib.suppress(_RollbackError)


def test_no_service_calls_require_inside_an_atomic_block() -> None:
    """
    Static guard — the shape, not one instance of it.

    Walks every services module and fails if `policy.require` (or a bare
    `require`) appears lexically inside a `with transaction.atomic()` body or
    under an `@transaction.atomic` decorator.
    """
    offenders: list[str] = []

    for path in APPS_DIR.rglob("services/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and _has_atomic_decorator(node)
                and _contains_require(node)
            ):
                offenders.append(f"{path.name}:{node.name} (@transaction.atomic)")
            if isinstance(node, ast.With) and _is_atomic_with(node):
                for child in node.body:
                    if _contains_require(child):
                        offenders.append(f"{path.name}:line {node.lineno} (with atomic)")

    assert not offenders, (
        "policy.require() inside a transaction: a refusal there is rolled back and "
        "leaves no audit row (BR-085). Check permissions BEFORE opening the "
        "transaction. Offenders: " + ", ".join(offenders)
    )


def test_no_service_audits_then_raises_inside_a_transaction() -> None:
    """
    The general shape of the same defect, caught statically.

    `require()` was only the first instance. The second was
    `auth_service.attempt_login`, which was `@transaction.atomic`, wrote a
    LOGIN_FAILED row for an attempt on a LOCKED account, and then raised —
    discarding the record of the attempt.

    The pattern is: inside a transaction, write_audit(...) followed by a raise
    on any path. Any function that both audits and raises must not be atomic,
    unless losing that audit row is genuinely acceptable — which, for a
    refusal, it is not.
    """
    offenders: list[str] = []

    for path in APPS_DIR.rglob("services/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            # Every transactional scope in this function: the whole body when
            # decorated, plus each `with transaction.atomic()` body.
            scopes: list[list[ast.stmt]] = []
            if _has_atomic_decorator(node):
                scopes.append(node.body)
            scopes.extend(
                inner.body
                for inner in ast.walk(node)
                if isinstance(inner, ast.With) and _is_atomic_with(inner)
            )

            for scope in scopes:
                if _audits_then_raises(scope):
                    offenders.append(f"{path.name}:{node.name}")
                    break

    assert not offenders, (
        "these functions audit and then raise inside a transaction, so the audit "
        "row is rolled back with the operation it was recording (BR-085). Move "
        "the audited-and-raising path outside the transaction. Offenders: " + ", ".join(offenders)
    )


def _audits_then_raises(scope: list[ast.stmt]) -> bool:
    """
    True when a raise can follow a write_audit within the same transaction.

    Ordering matters: a guard clause that raises BEFORE anything is written
    loses nothing, and flagging it would train people to ignore this test.
    Line numbers stand in for execution order — imprecise across branches, but
    it errs toward reporting, and a false report here costs one reading of the
    function while a miss costs an audit row.
    """
    audit_lines: list[int] = []
    raise_lines: list[int] = []

    for stmt in scope:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Raise):
                raise_lines.append(child.lineno)
            elif isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "write_audit":
                    audit_lines.append(child.lineno)

    if not audit_lines or not raise_lines:
        return False
    return max(raise_lines) > min(audit_lines)


def _has_atomic_decorator(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "atomic":
            return True
    return False


def _is_atomic_with(node: ast.With) -> bool:
    for item in node.items:
        call = item.context_expr
        func = call.func if isinstance(call, ast.Call) else call
        if isinstance(func, ast.Attribute) and func.attr == "atomic":
            return True
    return False


def _contains_require(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr in {"require", "require_report"}:
            return True
        if isinstance(func, ast.Name) and func.id in {"require", "require_report"}:
            return True
    return False
