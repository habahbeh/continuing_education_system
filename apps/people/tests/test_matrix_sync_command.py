"""
check_permissions_matrix — the link between the code matrix and the document.

The command is the only thing standing between "PERMISSIONS.md says X" and
"the code does Y", so its own parser needs testing: a checker that silently
matches nothing would report success on an empty comparison.

The real document lives outside the repository and is not available in CI, so
these tests render a COMPLETE synthetic document from the code matrix and then
mutate it. Rendering it from the matrix makes the passing case somewhat
circular, and that is fine — what is under test here is the PARSER (markdown
cell -> set of actions), which is the part that can break silently. That the
transcription itself is faithful is established by running the command against
the real document, which reports 222 cells checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.people.constants import Screen
from apps.people.permissions.matrix import _COLUMNS, ALLOW

HEADER = (
    "| # | الشاشة | MGR | REG | FIN | **FIM** | CSH | AUD |\n|---|---|---|---|---|---|---|---|\n"
)
ACTION_ORDER = "VCEAXP"


def _cell(screen: str, role: str) -> str:
    actions = ALLOW.get((screen, role), frozenset())
    if not actions:
        return "**—**"
    return " ".join(sorted(actions, key=ACTION_ORDER.index))


def _render(overrides: dict[tuple[str, str], str] | None = None, extra: str = "") -> str:
    overrides = overrides or {}
    lines = [HEADER]
    for index, screen in enumerate(Screen.values, start=1):
        cells = [overrides.get((screen, role), _cell(screen, role)) for role in _COLUMNS]
        lines.append(f"| {index} | شاشة `{screen}` | " + " | ".join(cells) + " |\n")
    return "".join(lines) + extra


def _write(tmp_path: Path, body: str) -> Path:
    docs = tmp_path / "docs" / "02-analysis"
    docs.mkdir(parents=True)
    (docs / "PERMISSIONS.md").write_text(body, encoding="utf-8")
    return tmp_path / "docs"


def test_missing_document_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="not found"):
        call_command("check_permissions_matrix", docs=str(tmp_path))


def test_a_faithful_document_passes(tmp_path: Path) -> None:
    call_command("check_permissions_matrix", docs=str(_write(tmp_path, _render())))


def test_a_widened_cell_is_detected(tmp_path: Path) -> None:
    """The cashier granted the reports screen — the kind of change D-03 forbids."""
    body = _render(overrides={(Screen.REPORTS, _COLUMNS[4]): "V P"})
    with pytest.raises(CommandError, match="divergence"):
        call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))


def test_a_narrowed_cell_is_detected(tmp_path: Path) -> None:
    """Drift in either direction is drift."""
    body = _render(overrides={(Screen.PAYMENT_NEW, _COLUMNS[2]): "**—**"})
    with pytest.raises(CommandError, match="divergence"):
        call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))


def test_a_screen_in_the_document_but_absent_from_code_is_detected(tmp_path: Path) -> None:
    body = _render(extra="| 99 | شاشة مجهولة `smuggled-screen` | V | — | — | — | — | V |\n")
    with pytest.raises(CommandError, match="divergence"):
        call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))


def test_non_production_rows_are_skipped(tmp_path: Path) -> None:
    """
    Rows 37 and 38 are review tools that are never built (SPEC.md §3.1).

    They are denied to everyone in the document and have no Screen constant.
    Skipping them is a sanctioned exception, not a hole — any OTHER
    undocumented screen still fails, as the previous test shows.
    """
    body = _render(
        extra=(
            "| 37 | مصفوفة التغطية `coverage` | — | — | — | — | — | — |\n"
            "| 38 | النطاق المستقبلي `future` | — | — | — | — | — | — |\n"
        )
    )
    call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))


def test_footnote_superscripts_do_not_leak_into_actions(tmp_path: Path) -> None:
    """`V A¹¹ X¹¹ P` must parse as V A X P, not as tokens plus noise."""
    body = _render(
        overrides={
            (Screen.PAYMENTS, _COLUMNS[0]): "V P",
            (Screen.PAYMENTS, _COLUMNS[1]): "V E¹⁰ P",
            (Screen.PAYMENTS, _COLUMNS[2]): "V A¹¹ X¹¹ P",
            (Screen.PAYMENTS, _COLUMNS[3]): "**V P** ᵖ",
            (Screen.PAYMENTS, _COLUMNS[4]): "V C P",
            (Screen.PAYMENTS, _COLUMNS[5]): "V P",
        }
    )
    call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))


def test_deny_markers_and_warning_glyphs_parse_as_empty(tmp_path: Path) -> None:
    """`**—** ¹² `, `— ⚠`, `**—** ᵈ` all mean the same thing: nothing granted."""
    body = _render(
        overrides={
            (Screen.PAYMENT_NEW, _COLUMNS[0]): "**—** ¹²",
            (Screen.PAYMENT_NEW, _COLUMNS[1]): "— ⚠",
            (Screen.PAYMENT_NEW, _COLUMNS[3]): "**—** ᵈ",
            (Screen.PAYMENT_NEW, _COLUMNS[5]): "🔒 —",
        }
    )
    call_command("check_permissions_matrix", docs=str(_write(tmp_path, body)))
