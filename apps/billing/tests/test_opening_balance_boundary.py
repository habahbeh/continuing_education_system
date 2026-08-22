"""
What Sprint 8D-2 was not allowed to break.

Opening balances are the one thing in this system permitted to turn archived
history into money, so the sprint that built them is exactly the sprint most
likely to have loosened something on the way. Each test here names a
constraint that existed before 8D-2 and asserts it still bites.

The first is the one that matters most: the gateway is one-way. ``billing``
may read the archive; the archive still cannot reach the ledger.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db

BASE_DIR = Path(__file__).resolve().parents[3]
APPS_DIR = BASE_DIR / "apps"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# ---------------------------------------------------------------------------
# The gateway is one-way
# ---------------------------------------------------------------------------
def test_the_archive_still_cannot_reach_the_ledger() -> None:
    """
    A-04, restated here because 8D-2 is the sprint with a motive to break it.

    Building the bridge in ``datamigration`` would have been the obvious
    shortcut and would have handed the archive a way to create money. The
    model lives in ``billing`` instead, and this asserts the consequence
    rather than trusting the intention.
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
    assert not offenders, "the archive reached into the ledger: " + ", ".join(offenders)


def test_the_opening_balance_lives_on_the_ledger_side() -> None:
    """
    And the arrow points the other way, deliberately.

    ``OpeningBalance`` is a ``billing`` model holding a foreign key INTO the
    archive. That direction is the whole design: a door can only be opened
    from the money side, by a person, and the archive does not know the door
    exists.
    """
    from apps.billing.models import OpeningBalance

    assert OpeningBalance._meta.app_label == "billing"

    source = OpeningBalance._meta.get_field("source_enrollment")
    assert source.related_model._meta.app_label == "datamigration"
    assert source.null is True  # a paper-file balance has no archive row


def test_no_opening_balance_model_appeared_in_the_archive_app() -> None:
    """
    8D-1 listed ``OpeningBalance`` as deferred and said its appearance there
    would mean the sprint had crossed its own line. It did not appear there,
    and it must not appear there now.
    """
    from django.apps import apps as django_apps

    archive_models = {
        model.__name__ for model in django_apps.get_app_config("datamigration").get_models()
    }
    assert "OpeningBalance" not in archive_models
    assert archive_models == {
        "MigrationBatch",
        "MigrationRow",
        "HistoricalCohort",
        "HistoricalParticipant",
        "HistoricalEnrollment",
        "HistoricalPayment",
    }


# ---------------------------------------------------------------------------
# Constraints 8D-2 was not allowed to touch
# ---------------------------------------------------------------------------
def test_the_nine_digit_participant_number_constraint_is_untouched() -> None:
    """
    The archive exists so this never has to bend, and 8D-2 did not bend it.

    Asserted at the model rather than by writing a row, because the point is
    the DEFINITION: the width and the regex are both still there.
    """
    from apps.people.models import Participant

    field = Participant._meta.get_field("participant_number")
    assert field.max_length == 9
    assert field.unique is True

    names = {c.name for c in Participant._meta.constraints}
    assert "people_participant_number_format" in names


def test_legacy_number_is_still_format_free_and_not_unique() -> None:
    """
    8D-2 gave opening balances a ``source_legacy_number``, which is a second
    place a legacy number now lives — and neither copy may acquire a format.

    The historical numbers run 8 to 11 characters and some hold two numbers
    joined by a hyphen. A uniqueness rule would also be wrong: thirteen of
    them legitimately appear in more than one row.
    """
    from apps.billing.models import OpeningBalance
    from apps.datamigration.models import HistoricalParticipant

    legacy = HistoricalParticipant._meta.get_field("legacy_number")
    assert legacy.unique is False
    constraint_names = {c.name for c in HistoricalParticipant._meta.constraints}
    assert not any("format" in name or "regex" in name for name in constraint_names)

    copied = OpeningBalance._meta.get_field("source_legacy_number")
    assert copied.unique is False
    assert copied.max_length == 32
    ob_constraints = {c.name for c in OpeningBalance._meta.constraints}
    assert not any("legacy" in name for name in ob_constraints)


def test_a_legacy_number_repeats_freely_across_opening_balances(seeded_settings) -> None:
    """The non-uniqueness, exercised rather than merely declared."""
    from datetime import date
    from decimal import Decimal

    from apps.billing.models import OpeningBalance, OpeningBalanceDirection
    from apps.billing.services import opening_balance_service as obs
    from apps.people.models import Role, User

    officer = User.objects.create_user(
        username="fin.ob.legacy", password="probe-password-1234", role=Role.FINANCE_OFFICER
    )
    for index in (1, 2):
        obs.propose_manually(
            actor=officer,
            code=f"OB-LEG-{index}",
            direction=OpeningBalanceDirection.RECEIVABLE,
            amount=Decimal("100.000"),
            as_of=date(2026, 9, 20),
            description_ar="ذمة",
            legacy_number="2022520152",  # ten digits, and used twice
        )

    assert OpeningBalance.objects.filter(source_legacy_number="2022520152").count() == 2


def test_the_audit_event_model_is_still_append_only() -> None:
    """
    ADR-010 layer 3. The grants are checked by ``verify_audit_grants`` in the
    gate; this checks the half that lives in code — nothing in 8D-2 gave the
    audit table an editable field or a delete path.
    """
    from apps.core.models import AuditEvent

    assert AuditEvent._meta.get_field("entity_type").max_length == 64
    # The chain, and the fact that neither link can be rewritten after insert.
    assert AuditEvent._meta.get_field("prev_hash").editable is False
    assert AuditEvent._meta.get_field("row_hash").editable is False


def test_reporting_is_still_model_free() -> None:
    """
    The reports read what the other apps wrote. 8D-2 added a charge type and
    a new source of charge lines, and neither gave reporting a reason to start
    keeping figures of its own.
    """
    from django.apps import apps as django_apps

    assert list(django_apps.get_app_config("reporting").get_models()) == []


def test_the_new_charge_type_is_in_the_allocation_order() -> None:
    """
    BR-022 — a charge type missing from the order can never be paid.

    FIRST, by the client's decision in Sprint 8D-3. This test asserted LAST
    when 8D-2 wrote it, on the argument that money handed over for this term
    is for this term; the centre decided the other way, because an old debt is
    the one most at risk of never being collected. The reversal is recorded
    here rather than quietly edited, so the next reader knows the position was
    chosen twice and by whom.
    """
    from apps.billing.models import ALLOCATION_ORDER, ChargeType

    assert set(ALLOCATION_ORDER) == set(ChargeType.values)
    assert ALLOCATION_ORDER[0] == ChargeType.OPENING_BALANCE
