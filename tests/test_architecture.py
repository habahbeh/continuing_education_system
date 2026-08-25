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


# ---------------------------------------------------------------------------
# A-10 — a filter may not narrow by a field the reader cannot see
# ---------------------------------------------------------------------------
# `402f848` closed one of these. A restricted role could not see a participant's
# category, and three requests for three categories handed back three different
# one-row lists — reading the field off the result set without it ever being
# rendered. A filter reports a field as surely as printing it.
#
# Nothing else in the codebase can do that TODAY, and for a structural reason:
# ``project()``/``visible_fields_for`` exist in one service. Every other list
# hands back the field it filters on, so narrowing tells the reader nothing the
# row did not already say.
#
# That is the fact this guard protects. The risk is forward, not backward: the
# first service to gain a per-role projection turns its own filters into
# oracles in the same commit, and there would be nothing to notice.
#
# Two things are asserted rather than one, because a parser over function
# bodies would be brittle exactly where it needs to be certain:
#
#   * every ``list_*`` is discovered by IMPORT and must be declared below, with
#     its filters matching the real signature. A new list service, or a new
#     parameter on an old one, fails until somebody classifies it.
#   * a service declared to project nothing must really have no projection.
#     Adding one flips the classification, which forces a probe to be written.
#
# What is NOT machine-checked: whether a parameter is reachable from a URL.
# ``list_receipts(cashier_id=…)`` is not, but that is a weaker argument than
# the two above — where nothing is projected away, reachability does not matter
# — so it lives in a note instead of pretending to be verified.

#: The service hands every reader the same keys, so no filter can be an oracle.
NO_PROJECTION = "no-projection"
#: The service narrows rows per role, so every filter needs its own gate AND a
#: behavioural probe below.
FIELD_GATED = "field-gated"

#: dotted path -> (filter parameters, projection class, why it is safe)
LIST_SERVICE_CONTRACT: dict[str, tuple[tuple[str, ...], str, str]] = {
    "apps.billing.services.credit_service.list_credit_returns": (
        ("enrollment_code",),
        NO_PROJECTION,
        "enrollment_code is on the row it filters.",
    ),
    "apps.billing.services.discount_service.list_discounts": (
        ("enrollment_code",),
        NO_PROJECTION,
        "enrollment_code is on the row it filters.",
    ),
    "apps.billing.services.extra_fee_service.list_extra_fees": (
        ("enrollment_code",),
        NO_PROJECTION,
        "enrollment_code is on the row it filters.",
    ),
    "apps.billing.services.opening_balance_service.list_balances": (
        ("status", "direction"),
        NO_PROJECTION,
        "status and direction are both on the row.",
    ),
    "apps.billing.services.refund_service.list_refunds": (
        ("enrollment_code", "status"),
        NO_PROJECTION,
        "both are on the row.",
    ),
    "apps.cashbox.services.closing_service.list_closings": (
        ("on_date",),
        NO_PROJECTION,
        "closing_date is on the row. Whether the cashier should see another "
        "cashier's day is a scope question (footnote 14), not an oracle.",
    ),
    "apps.cashbox.services.payment_service.list_receipts": (
        ("query", "on_date", "cashier_id"),
        NO_PROJECTION,
        "received_on and cashier are on the row. cashier_id is not passed by "
        "any view, but that is not what makes it safe.",
    ),
    "apps.catalog.services.catalog_service.list_programs": (
        ("program_type",),
        NO_PROJECTION,
        "program_type is on the row.",
    ),
    "apps.catalog.services.catalog_service.list_price_lists": (
        (),
        NO_PROJECTION,
        "takes no filter at all, so there is nothing to narrow by.",
    ),
    "apps.datamigration.services.read_service.list_batches": (
        (),
        NO_PROJECTION,
        "takes no filter at all, so there is nothing to narrow by.",
    ),
    "apps.expenses.services.expense_service.list_expenses": (
        ("category", "status", "cohort_code", "date_from", "date_to"),
        NO_PROJECTION,
        "category, status and cohort_code are all on the row.",
    ),
    "apps.operations.services.certificate_service.list_certificates": (
        ("query",),
        NO_PROJECTION,
        "search runs over fields the row carries.",
    ),
    "apps.operations.services.clearance_service.list_clearances": (
        ("status", "query"),
        NO_PROJECTION,
        "status is on the row.",
    ),
    "apps.operations.services.cohort_service.list_cohorts": (
        ("query", "status", "program_code"),
        NO_PROJECTION,
        "status and program_code are on the row.",
    ),
    "apps.operations.services.enrollment_service.list_enrollments": (
        ("query", "cohort_code", "status"),
        NO_PROJECTION,
        "cohort_code and status are on the row. If this ever narrows per role "
        "— the finance manager's cell is a bare V — it becomes FIELD_GATED.",
    ),
    "apps.operations.services.mohe_service.list_submissions": (
        ("status", "query"),
        NO_PROJECTION,
        "status is on the row.",
    ),
    "apps.operations.services.transfer_service.list_transfers": (
        ("status", "query"),
        NO_PROJECTION,
        "status is on the row.",
    ),
    "apps.partners.services.partner_service.list_partners": (
        ("query",),
        NO_PROJECTION,
        "search runs over fields the row carries.",
    ),
    "apps.partners.services.partner_service.list_agreements": (
        ("partner_code",),
        NO_PROJECTION,
        "partner is on the row, and BR-083 shuts the cashier out of the screen "
        "entirely rather than out of a column.",
    ),
    "apps.people.services.participant_service.list_participants": (
        ("query", "category"),
        FIELD_GATED,
        "BR-101 narrows the cashier and the finance manager to number and "
        "name. Both filters are gated; the probe below holds them.",
    ),
    "apps.people.services.user_service.list_users": (
        (),
        NO_PROJECTION,
        "takes no filter at all, so there is nothing to narrow by.",
    ),
    "apps.settlements.services.absence_service.list_absences": (
        ("cohort_code",),
        NO_PROJECTION,
        "cohort_code is on the row.",
    ),
    "apps.settlements.services.claim_service.list_claims": (
        ("partner_code", "status"),
        NO_PROJECTION,
        "partner_name and status are on the row.",
    ),
    "apps.settlements.services.clawback_service.list_obligations": (
        ("partner_code",),
        NO_PROJECTION,
        "the row names its partner.",
    ),
    "apps.settlements.services.settlement_service.list_settlements": (
        ("partner_code",),
        NO_PROJECTION,
        "partner_name is on the row.",
    ),
}

#: Names that mean a module narrows rows per role. Presence of either turns a
#: service into one whose filters need gating.
PROJECTION_SYMBOLS = ("project", "visible_fields_for")


def _discover_list_services() -> dict[str, object]:
    """Every ``list_*`` defined in a service module, found by import.

    By import rather than by reading source: a rename, a decorator or a
    reformat must not be able to hide a service from this guard.
    """
    import importlib
    import inspect

    found: dict[str, object] = {}
    for path in sorted(APPS_DIR.glob("*/services/*.py")):
        if path.name == "__init__.py":
            continue
        dotted = ".".join(path.relative_to(BASE_DIR).with_suffix("").parts)
        module = importlib.import_module(dotted)
        for name, obj in vars(module).items():
            if not name.startswith("list_") or not inspect.isfunction(obj):
                continue
            if obj.__module__ != dotted:  # re-exported from elsewhere
                continue
            found[f"{dotted}.{name}"] = obj
    return found


def test_a10_every_list_service_is_declared_in_the_filter_contract() -> None:
    """
    A new list service, or a new filter on an old one, has to be classified.

    This is the half of A-10 that reaches into the future: the leak that was
    fixed could only exist because a filter and a projection met without anyone
    asking whether they should. Adding either now fails here first.
    """
    discovered = _discover_list_services()

    undeclared = sorted(set(discovered) - set(LIST_SERVICE_CONTRACT))
    stale = sorted(set(LIST_SERVICE_CONTRACT) - set(discovered))

    assert not undeclared, (
        "A list service is not classified in LIST_SERVICE_CONTRACT. Decide "
        "whether its filters can report a field its reader cannot see, then "
        "declare it: " + ", ".join(undeclared)
    )
    assert not stale, "LIST_SERVICE_CONTRACT names services that no longer exist: " + ", ".join(
        stale
    )


def test_a10_the_declared_filters_match_the_real_signatures() -> None:
    """
    A contract that drifts from the code protects nothing.

    ``actor`` and ``request`` are not filters; everything else narrows the
    result set and therefore has to be accounted for.
    """
    import inspect

    offenders: list[str] = []
    for dotted, function in sorted(_discover_list_services().items()):
        declared, _projection, _why = LIST_SERVICE_CONTRACT[dotted]
        actual = tuple(
            name
            for name in inspect.signature(function).parameters
            if name not in ("actor", "request")
        )
        if actual != declared:
            offenders.append(f"{dotted}: declared {declared}, signature has {actual}")

    assert not offenders, (
        "A filter parameter is undeclared or renamed. Every one of them can "
        "narrow a result set: " + " · ".join(offenders)
    )


def test_a10_a_service_declared_to_project_nothing_really_projects_nothing() -> None:
    """
    The classification has to be a fact, not a habit.

    ``project``/``visible_fields_for`` is what makes a row mean different
    things to different readers, and it is what turns a filter into an oracle.
    A service that grows one must be re-declared FIELD_GATED — which forces a
    probe like ``test_a10_the_canonical_field_gated_service_holds`` to be
    written for it, rather than the change passing quietly.
    """
    import importlib

    offenders: list[str] = []
    for dotted, (_filters, projection, _why) in sorted(LIST_SERVICE_CONTRACT.items()):
        module = importlib.import_module(dotted.rsplit(".", 1)[0])
        present = [name for name in PROJECTION_SYMBOLS if hasattr(module, name)]
        if present and projection == NO_PROJECTION:
            offenders.append(f"{dotted} declares {NO_PROJECTION} but defines {present}")
        if not present and projection == FIELD_GATED:
            offenders.append(f"{dotted} declares {FIELD_GATED} and projects nothing")

    assert not offenders, (
        "A service's projection classification no longer matches its module. "
        "If a projection was just added, every filter on it needs a gate and a "
        "probe: " + " · ".join(offenders)
    )


def test_a10_every_contract_entry_says_why_it_is_safe() -> None:
    """A classification without a reason is a guess someone will inherit."""
    for dotted, (_filters, projection, why) in sorted(LIST_SERVICE_CONTRACT.items()):
        assert projection in (NO_PROJECTION, FIELD_GATED), dotted
        assert len(why) > 15, f"{dotted} carries no reason"


@pytest.mark.django_db
def test_a10_the_canonical_field_gated_service_holds() -> None:
    """
    The behaviour A-10 exists for, exercised rather than asserted about.

    BR-101 keeps ``category`` off the cashier's and the finance manager's row.
    Before `402f848`, ``?category=`` handed each of them a different one-row
    list per value — the field, read back from the shape of the answer. The
    filter is ignored for those readers now, so the answer does not move.

    Deleting that gate fails this test, which is the whole point of it.
    """
    from datetime import date

    from apps.people.models import (
        IdDocumentType,
        Participant,
        ParticipantCategory,
        Role,
        User,
    )
    from apps.people.services import participant_service as svc

    categories = (
        ParticipantCategory.UNIVERSITY,
        ParticipantCategory.CENTER,
        ParticipantCategory.EMPLOYEE,
    )
    for index, category in enumerate(categories):
        Participant.objects.create(
            participant_number=f"20261100{index}",
            category=category,
            name_ar=f"مشارك أ-١٠ {index}",
            id_document_type=IdDocumentType.NATIONAL_ID,
            id_document_number=f"7770000{index}",
            registered_on=date(2026, 1, 1),
        )

    def numbers(actor: object, **filters: str) -> list[str]:
        rows = svc.list_participants(actor=actor, **filters)
        return sorted(row["participant_number"] for row in rows)

    for role in (Role.CASHIER, Role.FINANCE_MANAGER):
        actor = User.objects.create_user(
            username=f"a10.{role.lower()}", password="a10-probe-1234", role=role
        )
        assert "category" not in svc.visible_fields_for(actor)
        everything = numbers(actor)
        assert len(everything) == 3
        for category in categories:
            assert numbers(actor, category=category) == everything, (
                f"?category={category} moved the result set for {role} — the "
                "filter is reporting a field BR-101 withholds"
            )

    # …and the gate is on the field, not on the feature: a reader who sees the
    # column still filters by it.
    registrar = User.objects.create_user(
        username="a10.registrar", password="a10-probe-1234", role=Role.REGISTRATION_OFFICER
    )
    assert "category" in svc.visible_fields_for(registrar)
    for category in categories:
        assert len(numbers(registrar, category=category)) == 1
