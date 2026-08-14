"""
Verify apps/people/permissions/matrix.py against PERMISSIONS.md §3.

The matrix in code is a transcription. Transcriptions drift. This command
re-parses the document's §3 tables and fails on any divergence, so the drift
is caught by a person running one command rather than by an auditor finding a
role that could do something the document says it cannot.

The documentation tree lives outside the repository, so this is a command and
not a test: CI has the code but not the document. T-282 covers what CI *can*
check — that every declared screen and role has a cell.

    python manage.py check_permissions_matrix \
        --docs "/Users/…/التعليم المستمر/docs"
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.people.constants import Action
from apps.people.permissions.matrix import _COLUMNS, ALLOW

#: A §3 table row: | # | screen name `key` | cell | cell | cell | cell | cell | cell |
ROW = re.compile(r"^\|\s*\*{0,2}(\d+أ?)\*{0,2}\s*\|(.+)\|\s*$")
SCREEN_KEY = re.compile(r"`([a-z0-9-]+)`")
#: Markers that decorate a cell without changing it: footnote superscripts,
#: bold markers, deviation warnings.
NOISE = re.compile(r"[*⚠🔒✅❌]|[¹²³⁴⁵⁶⁷⁸⁹⁰ᵃᵇᵈᶠᵍᵖᵗᵛ]+")

#: Rows 37 and 38 of PERMISSIONS.md §3.7. They appear in the table denied to
#: everyone, but they are demo review tools that are never built in production
#: (SPEC.md §3.1), so they have no Screen constant and no matrix cell. Listed
#: explicitly rather than inferred: "the document has a row the code lacks" is
#: normally a real divergence, and these two are the only sanctioned exceptions.
NON_PRODUCTION = {"coverage", "future"}


def _clean(cell: str) -> str:
    text = NOISE.sub(" ", cell)
    text = text.replace("—", "").replace("(", " ").replace(")", " ")
    tokens = [t for t in text.split() if t in set(Action.values)]
    # Preserve document order but drop duplicates a footnote may have doubled.
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return " ".join(seen)


class Command(BaseCommand):
    help = "Check the code permission matrix against PERMISSIONS.md §3."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--docs", required=True, help="Path to the docs directory")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["docs"]) / "02-analysis" / "PERMISSIONS.md"
        if not path.exists():
            raise CommandError(f"PERMISSIONS.md not found at {path}")

        parsed = self._parse(path)
        problems = self._compare(parsed)

        if problems:
            for line in problems:
                self.stderr.write(self.style.ERROR(line))
            raise CommandError(
                f"{len(problems)} divergence(s) between the code matrix and PERMISSIONS.md §3."
            )

        self.stdout.write(
            self.style.SUCCESS(f"Matrix matches PERMISSIONS.md §3 — {len(parsed)} cells checked.")
        )

    def _parse(self, path: Path) -> dict[tuple[str, str], str]:
        cells: dict[tuple[str, str], str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ROW.match(line.strip())
            if not match:
                continue
            columns = [c.strip() for c in match.group(2).split("|")]
            if len(columns) < 7:
                continue
            key = SCREEN_KEY.search(columns[0])
            if not key:
                continue
            screen = key.group(1)
            if screen in NON_PRODUCTION:
                continue
            for role, cell in zip(_COLUMNS, columns[1:7], strict=False):
                cells[(screen, role)] = _clean(cell)
        return cells

    def _compare(self, parsed: dict[tuple[str, str], str]) -> list[str]:
        problems: list[str] = []
        for key, doc_cell in parsed.items():
            if key not in ALLOW:
                problems.append(f"In document but not in code: {key[0]} / {key[1]}")
                continue
            code_cell = " ".join(sorted(ALLOW[key]))
            if sorted(doc_cell.split()) != sorted(code_cell.split()):
                problems.append(
                    f"{key[0]} / {key[1]}: document says '{doc_cell or '—'}', "
                    f"code says '{code_cell or '—'}'"
                )
        for key in ALLOW:
            if key not in parsed:
                problems.append(f"In code but not found in document: {key[0]} / {key[1]}")
        return problems
