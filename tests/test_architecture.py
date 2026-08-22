"""
Static architecture tests (A-01 … A-08).

These enforce decisions that no amount of code review reliably catches. Each
one corresponds to a documented rule and fails the build on violation.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django.apps import apps
from django.db import models

BASE_DIR = Path(__file__).resolve().parent.parent
APPS_DIR = BASE_DIR / "apps"
TEMPLATE_DIRS = [BASE_DIR / "templates", APPS_DIR]

LOCAL_APP_LABELS = {
    "core",
    "people",
    "catalog",
    "partners",
    "operations",
    "billing",
    "cashbox",
    "settlements",
    "expenses",
    "reporting",
    "datamigration",
}


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "migrations" not in p.parts or p.name != "__init__.py"]


def _imported_modules(path: Path) -> set[str]:
    """Collect every module referenced by import statements in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# ---------------------------------------------------------------------------
# A-01 — no float anywhere near a model (ADR-005, BR-090, T-001)
# ---------------------------------------------------------------------------
def test_a01_no_float_fields_in_any_model() -> None:
    offenders: list[str] = []
    for model in apps.get_models():
        if model._meta.app_label not in LOCAL_APP_LABELS:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, (models.FloatField,)):
                offenders.append(f"{model._meta.label}.{field.name}")
    assert not offenders, (
        "FloatField is forbidden — money must be Decimal (ADR-005). "
        "Offenders: " + ", ".join(offenders)
    )


def test_a01b_no_float_calls_in_application_code() -> None:
    """No float() conversion in app code — it is how Decimal precision leaks away."""
    offenders: list[str] = []
    for path in APPS_DIR.rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            ):
                offenders.append(f"{path.relative_to(BASE_DIR)}:{node.lineno}")
    assert not offenders, "float() is forbidden in application code. Offenders: " + ", ".join(
        offenders
    )


# ---------------------------------------------------------------------------
# A-02 — no ENUM columns (ADR-004, T-197)
# ---------------------------------------------------------------------------
def test_a02_no_enum_in_migrations() -> None:
    offenders: list[str] = []
    for path in APPS_DIR.rglob("migrations/*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bENUM\s*\(", text, flags=re.IGNORECASE):
            offenders.append(str(path.relative_to(BASE_DIR)))
    assert not offenders, (
        "ENUM columns are forbidden — use VARCHAR + choices + CheckConstraint (ADR-004). "
        "Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-03 — core depends on nothing (ADR-008)
# ---------------------------------------------------------------------------
def test_a03_core_imports_no_other_local_app() -> None:
    forbidden = LOCAL_APP_LABELS - {"core"}
    offenders: list[str] = []
    for path in (APPS_DIR / "core").rglob("*.py"):
        for module in _imported_modules(path):
            for app in forbidden:
                if module == f"apps.{app}" or module.startswith(f"apps.{app}."):
                    offenders.append(f"{path.relative_to(BASE_DIR)} → {module}")
    assert not offenders, (
        "core must not know about any business app — it is infrastructure (ADR-008). "
        "Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-04 — datamigration is isolated from the ledger (Q-02, ADR-013, D-26, T-179)
# ---------------------------------------------------------------------------
def test_a04_datamigration_never_imports_financial_apps() -> None:
    """
    The archive's production code may not reach the ledger.

    Its TESTS must, and the exclusion is the point rather than a concession:
    ``test_committing_a_workbook_writes_nothing_to_the_ledger`` counts every
    row in ``Receipt``, ``PartnerClaim`` and eleven other tables before and
    after an import. A test forbidden from naming ``Receipt`` could not assert
    that no receipt was created — it could only assert that this module does
    not import cashbox, which is what the rule already says. The same
    exclusion A-01b makes, for the same reason.
    """
    forbidden = {"billing", "cashbox", "settlements"}
    offenders: list[str] = []
    for path in (APPS_DIR / "datamigration").rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        for module in _imported_modules(path):
            for app in forbidden:
                if module == f"apps.{app}" or module.startswith(f"apps.{app}."):
                    offenders.append(f"{path.relative_to(BASE_DIR)} → {module}")
    assert not offenders, (
        "The historical archive must stay structurally isolated from the production "
        "ledger (Q-02 / ADR-013 / D-26). Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-09 — the spreadsheet library is confined to one module (Sprint 8D-1)
# ---------------------------------------------------------------------------
def test_a09_openpyxl_is_confined_to_the_archive_reader() -> None:
    """
    ``openpyxl`` is the project's only Excel dependency and it earns its place
    in exactly one file: ``datamigration/readers/xlsx_reader.py``.

    Two reasons for the fence. A spreadsheet library reaching a request path
    would mean a user upload parsed inside a web worker, and these workbooks
    run to thousands of cells. And a second import site is how "read a
    spreadsheet" quietly becomes a capability of the whole system rather than
    of the one boundary that has been thought about.
    """
    allowed = APPS_DIR / "datamigration" / "readers" / "xlsx_reader.py"
    offenders: list[str] = []
    for path in APPS_DIR.rglob("*.py"):
        if path == allowed or "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        for module in _imported_modules(path):
            if module == "openpyxl" or module.startswith("openpyxl."):
                offenders.append(str(path.relative_to(BASE_DIR)))
    assert not offenders, (
        "openpyxl belongs to datamigration/readers/xlsx_reader.py alone. "
        "Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-05 — views call services, not models (ADR-008)
# ---------------------------------------------------------------------------
def test_a05_views_do_not_import_models_directly() -> None:
    offenders: list[str] = []
    for path in APPS_DIR.rglob("views.py"):
        for module in _imported_modules(path):
            if re.match(r"^apps\.\w+\.models(\.|$)", module):
                offenders.append(f"{path.relative_to(BASE_DIR)} → {module}")
    assert not offenders, (
        "Views must go through the services layer, not touch models directly (ADR-008). "
        "Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-06 — no directional CSS utilities (ADR-003)
# ---------------------------------------------------------------------------
DIRECTIONAL = re.compile(
    r'class="[^"]*(?<![\w-])(ml-|mr-|pl-|pr-|left-|right-|text-left|text-right)',
)


def test_a06_no_directional_css_in_templates() -> None:
    offenders: list[str] = []
    for root in TEMPLATE_DIRS:
        for path in root.rglob("*.html"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if DIRECTIONAL.search(line):
                    offenders.append(f"{path.relative_to(BASE_DIR)}:{lineno}")
    assert not offenders, (
        "Use logical properties (ms-/me-/ps-/pe-/start-/end-) so RTL does not silently "
        "break (ADR-003). Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-07 — no external resources (ADR-003)
# ---------------------------------------------------------------------------
EXTERNAL = re.compile(r'(src|href)\s*=\s*["\'](https?:)?//', re.IGNORECASE)


def test_a07_no_cdn_or_external_resource_in_templates() -> None:
    offenders: list[str] = []
    for root in TEMPLATE_DIRS:
        for path in root.rglob("*.html"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if EXTERNAL.search(line):
                    offenders.append(f"{path.relative_to(BASE_DIR)}:{lineno}")
    assert not offenders, (
        "No CDN. The system runs on an internal network and must not depend on the "
        "public internet (ADR-003). Offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# A-08 — AuditEvent admin is read-only (ADR-010, D-11)
# ---------------------------------------------------------------------------
def test_a08_audit_event_admin_is_read_only() -> None:
    from django.contrib import admin as django_admin

    from apps.core.models import AuditEvent

    registered = django_admin.site._registry
    assert AuditEvent in registered, "AuditEvent should be registered so auditors can read it."

    model_admin = registered[AuditEvent]
    assert model_admin.has_add_permission(None) is False, "AuditEvent admin must not allow add."
    assert model_admin.has_change_permission(None) is False, "admin must not allow change."
    assert model_admin.has_delete_permission(None) is False, "admin must not allow delete."


# ---------------------------------------------------------------------------
# Retired settings keys must not reappear (Q-01 / Q-05 revised)
# ---------------------------------------------------------------------------
RETIRED_KEYS = ["deposits_enabled", "tax_enabled"]

#: Files whose PURPOSE is to reject the retired keys, and which therefore have
#: to name them. Everything else in the codebase must be free of them.
#:
#: This allowlist is a deliberate, narrow deviation from "the strings must not
#: appear anywhere". Removing the names from these three files would delete the
#: guard that detects a stale `tax_enabled` row left in a database — trading a
#: real safety net for a literal reading of the rule. The distinction that
#: matters is USE versus DETECTION: no code may read, seed or branch on these
#: keys; these files only assert their absence.
RETIRED_KEY_ENFORCEMENT_FILES = {
    "tests/test_architecture.py",  # this file
    "apps/core/management/commands/seed_settings.py",  # RETIRED_KEYS reporter
    "apps/core/tests/test_seed_settings.py",  # asserts they are never seeded
}


def test_retired_setting_keys_absent_from_codebase() -> None:
    """
    `deposits_enabled` and `tax_enabled` encoded an assumption the client
    disproved: deposits ARE used and tax IS applied. Building on either would
    build on a known-false premise, so their presence fails the build.
    """
    offenders: list[str] = []
    scan_roots = [APPS_DIR, BASE_DIR / "config", BASE_DIR / "templates", BASE_DIR / "tests"]

    for root in scan_roots:
        for path in list(root.rglob("*.py")) + list(root.rglob("*.html")):
            relative = str(path.relative_to(BASE_DIR))
            if relative in RETIRED_KEY_ENFORCEMENT_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for key in RETIRED_KEYS:
                if key in text:
                    offenders.append(f"{relative} contains '{key}'")

    assert not offenders, (
        "Retired capability keys must not appear anywhere. Use deposits_supported / "
        "tax_supported instead. Offenders: " + ", ".join(offenders)
    )


def test_retired_keys_are_never_read_or_seeded() -> None:
    """
    The stronger guarantee behind the allowlist above: even the enforcement
    files must never READ these keys. `get_setting("tax_enabled", ...)` or a
    seed entry for either key is a hard failure.
    """
    offenders: list[str] = []
    for path in APPS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for key in RETIRED_KEYS:
            for pattern in (
                f'get_setting("{key}"',
                f"get_setting('{key}'",
                f'set_setting("{key}"',
                f"set_setting('{key}'",
                f'("{key}", ',  # a SEED table row
            ):
                if pattern in text:
                    offenders.append(f"{path.relative_to(BASE_DIR)}: {pattern}")

    assert not offenders, (
        "A retired key is being read or seeded, not merely named. Offenders: "
        + ", ".join(offenders)
    )


@pytest.mark.django_db
def test_retired_setting_keys_absent_from_database() -> None:
    from apps.core.models import EffectiveSetting

    present = list(
        EffectiveSetting.objects.filter(key__in=RETIRED_KEYS).values_list("key", flat=True)
    )
    assert not present, f"Retired setting keys found in the database: {present}"
