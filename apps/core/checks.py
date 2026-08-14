"""
Startup checks (FINAL_TECHNICAL_DIRECTION hard rule 10).

These raise ``Error``, not ``Warning``. A misconfigured database in a financial
system is a stop condition, not a note: without STRICT_ALL_TABLES MySQL
truncates values silently instead of raising.

``lower_case_table_names`` is a deliberate local-development deviation:
the production Linux target uses 0, but macOS runs on a case-insensitive
filesystem where 0 is unsupported by MySQL. The check therefore accepts 0 or 2,
rejects 1, and warns clearly when it sees 2.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register
from django.db import OperationalError, connection

# Registered check IDs.
E_SQL_MODE = "core.E001"
E_CHARSET = "core.E002"
E_TIMEZONE = "core.E003"
E_AUTO_FIELD = "core.E004"
E_USER_MODEL = "core.E005"
E_LCTN = "core.E006"
W_COLLATION = "core.W001"
W_ISOLATION = "core.W002"
W_LCTN_DEV = "core.W003"

REQUIRED_SQL_MODE = "STRICT_ALL_TABLES"
REQUIRED_CHARSET = "utf8mb4"
REQUIRED_TIME_ZONE = "Asia/Amman"
ACCEPTED_LCTN = {0, 2}


def _mysql_variables(names: list[str]) -> dict[str, Any] | None:
    """Read server variables; return None when the DB is unreachable."""
    if connection.vendor != "mysql":
        return None
    try:
        with connection.cursor() as cursor:
            selects = ", ".join(f"@@{n}" for n in names)
            cursor.execute(f"SELECT {selects}")
            row = cursor.fetchone()
        return dict(zip(names, row, strict=True))
    except (OperationalError, Exception):
        return None


@register()
def check_settings_invariants(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Settings that must hold regardless of database availability."""
    messages: list[CheckMessage] = []

    if not getattr(settings, "USE_TZ", False):
        messages.append(Error("USE_TZ must be True — timestamps are stored in UTC.", id=E_TIMEZONE))
    if getattr(settings, "TIME_ZONE", None) != REQUIRED_TIME_ZONE:
        messages.append(
            Error(
                f"TIME_ZONE must be '{REQUIRED_TIME_ZONE}', "
                f"got '{getattr(settings, 'TIME_ZONE', None)}'.",
                id=E_TIMEZONE,
            )
        )
    if getattr(settings, "DEFAULT_AUTO_FIELD", None) != "django.db.models.BigAutoField":
        messages.append(
            Error("DEFAULT_AUTO_FIELD must be django.db.models.BigAutoField.", id=E_AUTO_FIELD)
        )
    if getattr(settings, "AUTH_USER_MODEL", None) != "people.User":
        messages.append(Error("AUTH_USER_MODEL must be 'people.User'.", id=E_USER_MODEL))
    return messages


@register(deploy=False)
def check_mysql_configuration(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Database-level invariants. Silent when the database is unreachable."""
    variables = _mysql_variables(
        [
            "sql_mode",
            "character_set_server",
            "collation_server",
            "transaction_isolation",
            "lower_case_table_names",
        ]
    )
    if variables is None:
        return []

    messages: list[CheckMessage] = []

    sql_mode = str(variables.get("sql_mode") or "")
    if REQUIRED_SQL_MODE not in sql_mode:
        messages.append(
            Error(
                f"MySQL sql_mode must contain {REQUIRED_SQL_MODE}. Got: {sql_mode or '(empty)'}. "
                "Without it MySQL truncates values silently instead of raising, "
                "which is unacceptable in a financial system (ADR-004).",
                hint="Set sql_mode in my.cnf, or via the connection init_command.",
                id=E_SQL_MODE,
            )
        )

    charset = str(variables.get("character_set_server") or "")
    if charset != REQUIRED_CHARSET:
        messages.append(
            Error(
                f"character_set_server must be {REQUIRED_CHARSET}, got '{charset}'. "
                "utf8mb3 cannot store the full Arabic range.",
                id=E_CHARSET,
            )
        )

    collation = str(variables.get("collation_server") or "")
    if not collation.startswith("utf8mb4_"):
        messages.append(
            Warning(
                f"collation_server is '{collation}'; expected a utf8mb4_* collation "
                "(utf8mb4_0900_ai_ci recommended for Arabic).",
                id=W_COLLATION,
            )
        )

    isolation = str(variables.get("transaction_isolation") or "").replace("_", "-")
    if isolation.upper() != "READ-COMMITTED":
        messages.append(
            Warning(
                f"transaction_isolation is '{isolation}'; READ-COMMITTED is recommended "
                "for MySQL with Django (ADR-004).",
                id=W_ISOLATION,
            )
        )

    lctn = variables.get("lower_case_table_names")
    if lctn is not None:
        lctn = int(lctn)
        if lctn not in ACCEPTED_LCTN:
            messages.append(
                Error(
                    f"lower_case_table_names is {lctn}; only 0 (Linux production) or "
                    "2 (macOS local development) are accepted. Value 1 lowercases stored "
                    "identifiers and is rejected.",
                    id=E_LCTN,
                )
            )
        elif lctn == 2:
            messages.append(
                Warning(
                    "lower_case_table_names = 2 — DEVELOPMENT ENVIRONMENT. "
                    "This is an intentional local deviation: macOS uses a case-insensitive "
                    "filesystem where 0 is unsupported by MySQL. "
                    "The production Linux server must use 0.",
                    id=W_LCTN_DEV,
                )
            )

    return messages
