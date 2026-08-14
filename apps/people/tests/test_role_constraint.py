"""
T-270 — FINANCE_MANAGER exists in the choices AND in the database constraint.

Tested against a MIGRATED DATABASE, not against the model. An AlterField on
`choices` produces no SQL at all (`sqlmigrate` prints "-- (no-op)"), so a
migration that updates the choices and forgets the CheckConstraint leaves a
role Django considers valid and MySQL rejects at runtime. Asserting on
Role.values alone would pass in exactly that broken state.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction

from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

CONSTRAINT = "people_user_role_valid"


def _check_clause() -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cc.CHECK_CLAUSE
              FROM information_schema.CHECK_CONSTRAINTS cc
              JOIN information_schema.TABLE_CONSTRAINTS tc
                ON tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
               AND tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
             WHERE tc.TABLE_NAME = 'people_user'
               AND cc.CONSTRAINT_NAME = %s
               -- Scope to the CURRENT database. Without this the query also
               -- returns the same-named constraint from the development
               -- schema sitting alongside the test schema on this server.
               AND cc.CONSTRAINT_SCHEMA = DATABASE()
            """,
            [CONSTRAINT],
        )
        rows = cursor.fetchall()
    assert len(rows) == 1, f"expected exactly one {CONSTRAINT}, found {len(rows)}"
    return rows[0][0]


def test_finance_manager_is_declared() -> None:
    assert Role.FINANCE_MANAGER == "FINANCE_MANAGER"
    assert Role.FINANCE_MANAGER.label == "المدير المالي"


def test_constraint_lists_every_declared_role() -> None:
    """The constraint and the choices cannot drift apart (T-270)."""
    clause = _check_clause()
    for role in Role.values:
        assert role in clause, (
            f"{role} is in Role.choices but not in the {CONSTRAINT} CHECK. "
            f"MySQL would reject it at runtime while Django considered it valid."
        )


def test_old_six_role_constraint_is_gone() -> None:
    """The pre-Q-14 constraint must not survive alongside the new one."""
    clause = _check_clause()
    assert "FINANCE_MANAGER" in clause
    # Exactly one constraint of this name exists (asserted in _check_clause),
    # and it is the seven-role version.
    assert clause.count("FINANCE_OFFICER") == 1


def test_mysql_accepts_finance_manager() -> None:
    user = User.objects.create_user(
        username="finance.manager",
        password="probe-password-1234",
        full_name_ar="أ. سمير المدير المالي",
        role=Role.FINANCE_MANAGER,
    )
    assert User.objects.get(pk=user.pk).role == Role.FINANCE_MANAGER


def test_mysql_rejects_an_undeclared_role() -> None:
    """Raw SQL, bypassing Django validation — the database is the last line."""
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO people_user
                (password, is_superuser, username, first_name, last_name, email,
                 is_staff, is_active, date_joined, full_name_ar, role, department,
                 failed_login_count, lock_reason)
            VALUES ('x', 0, 'bogus.role', '', '', '', 0, 1, NOW(), '',
                    'SUPREME_OVERLORD', '', 0, '')
            """
        )


def test_blank_role_is_still_permitted_by_the_constraint() -> None:
    """A roleless account is legal at the database level and powerless in policy."""
    user = User.objects.create_user(username="no.role", password="probe-password-1234")
    assert User.objects.get(pk=user.pk).role == ""
