"""
Startup check tests.

The prompt is explicit: test that the check actually FAILS boot when
STRICT_ALL_TABLES is missing — do not assume it.
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import patch

import pytest
from django.core.checks import Error, Warning
from django.test import override_settings

from apps.core import checks

pytestmark = pytest.mark.django_db


def _ids(messages: Sequence[object]) -> set[str]:
    return {m.id for m in messages}  # type: ignore[attr-defined]


# --- Settings invariants ---------------------------------------------------
def test_settings_pass_in_the_real_configuration() -> None:
    assert checks.check_settings_invariants(None) == []


@override_settings(USE_TZ=False)
def test_use_tz_false_is_an_error() -> None:
    messages = checks.check_settings_invariants(None)
    assert checks.E_TIMEZONE in _ids(messages)
    assert all(isinstance(m, Error) for m in messages)


@override_settings(TIME_ZONE="UTC")
def test_wrong_timezone_is_an_error() -> None:
    assert checks.E_TIMEZONE in _ids(checks.check_settings_invariants(None))


@override_settings(DEFAULT_AUTO_FIELD="django.db.models.AutoField")
def test_wrong_auto_field_is_an_error() -> None:
    assert checks.E_AUTO_FIELD in _ids(checks.check_settings_invariants(None))


@override_settings(AUTH_USER_MODEL="auth.User")
def test_wrong_user_model_is_an_error() -> None:
    assert checks.E_USER_MODEL in _ids(checks.check_settings_invariants(None))


# --- MySQL configuration ---------------------------------------------------
def test_real_database_satisfies_the_hard_requirements() -> None:
    """The live dev server must genuinely pass — no mocks here."""
    messages = checks.check_mysql_configuration(None)
    errors = [m for m in messages if isinstance(m, Error)]
    assert not errors, f"real database failed a blocking check: {[m.msg for m in errors]}"


def test_missing_strict_all_tables_blocks_boot() -> None:
    """
    THE critical one. Without STRICT_ALL_TABLES MySQL truncates silently.
    Simulate its absence and assert the result is a blocking Error.
    """
    fake = {
        "sql_mode": "ONLY_FULL_GROUP_BY,NO_ZERO_DATE",  # STRICT_ALL_TABLES removed
        "character_set_server": "utf8mb4",
        "collation_server": "utf8mb4_0900_ai_ci",
        "transaction_isolation": "READ-COMMITTED",
        "lower_case_table_names": 2,
    }
    with patch.object(checks, "_mysql_variables", return_value=fake):
        messages = checks.check_mysql_configuration(None)

    blocking = [m for m in messages if isinstance(m, Error)]
    assert blocking, "a missing STRICT_ALL_TABLES did not block boot"
    assert checks.E_SQL_MODE in _ids(blocking)


def test_non_utf8mb4_charset_blocks_boot() -> None:
    fake = {
        "sql_mode": "STRICT_ALL_TABLES",
        "character_set_server": "utf8mb3",
        "collation_server": "utf8mb3_general_ci",
        "transaction_isolation": "READ-COMMITTED",
        "lower_case_table_names": 2,
    }
    with patch.object(checks, "_mysql_variables", return_value=fake):
        messages = checks.check_mysql_configuration(None)
    assert checks.E_CHARSET in _ids([m for m in messages if isinstance(m, Error)])


# --- lower_case_table_names: approved policy ------------------------------
def _lctn_messages(value: int) -> Sequence[object]:
    fake = {
        "sql_mode": "STRICT_ALL_TABLES",
        "character_set_server": "utf8mb4",
        "collation_server": "utf8mb4_0900_ai_ci",
        "transaction_isolation": "READ-COMMITTED",
        "lower_case_table_names": value,
    }
    with patch.object(checks, "_mysql_variables", return_value=fake):
        return checks.check_mysql_configuration(None)


def test_lctn_zero_is_accepted_silently() -> None:
    """0 is the production Linux target — accepted with no complaint."""
    messages = _lctn_messages(0)
    assert checks.E_LCTN not in _ids(messages)
    assert checks.W_LCTN_DEV not in _ids(messages)


def test_lctn_two_is_accepted_with_a_development_warning() -> None:
    """2 is the approved macOS local deviation — accepted, but flagged."""
    messages = _lctn_messages(2)
    assert checks.E_LCTN not in _ids(messages)
    assert checks.W_LCTN_DEV in _ids(messages)

    warning = next(m for m in messages if getattr(m, "id", None) == checks.W_LCTN_DEV)
    assert isinstance(warning, Warning)
    assert "DEVELOPMENT" in warning.msg
    assert "production Linux server must use 0" in warning.msg


def test_lctn_one_is_rejected() -> None:
    """1 lowercases stored identifiers — rejected outright."""
    assert checks.E_LCTN in _ids([m for m in _lctn_messages(1) if isinstance(m, Error)])


# --- Non-blocking advisories ----------------------------------------------
def test_repeatable_read_is_a_warning_not_an_error() -> None:
    fake = {
        "sql_mode": "STRICT_ALL_TABLES",
        "character_set_server": "utf8mb4",
        "collation_server": "utf8mb4_0900_ai_ci",
        "transaction_isolation": "REPEATABLE-READ",
        "lower_case_table_names": 0,
    }
    with patch.object(checks, "_mysql_variables", return_value=fake):
        messages = checks.check_mysql_configuration(None)
    assert checks.W_ISOLATION in _ids(messages)
    assert not [m for m in messages if isinstance(m, Error)]


def test_checks_are_silent_when_database_is_unreachable() -> None:
    with patch.object(checks, "_mysql_variables", return_value=None):
        assert checks.check_mysql_configuration(None) == []
