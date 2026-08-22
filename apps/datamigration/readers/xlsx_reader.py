"""
The only module in the system that reads a spreadsheet.

**Why a dependency here and nowhere else.** The project carries no Excel
library on purpose — Sprint 8C-1 printed its documents through the browser
rather than take one on. Reading is a different problem from writing: the
centre has promised further workbooks, and their shape is unknown. A
hand-rolled parser meeting an unfamiliar file fails quietly, in the one place
where quiet failure means a wrong number in the archive. So ``openpyxl`` is
pinned and confined to this file, and the architecture test A-09 fails the
build if any other module imports it.

**Nothing here interprets anything.** Every cell comes back as text plus the
type openpyxl reported, and the reader says whether a formula produced it.
``#REF!`` arrives as the string ``#REF!``. A blank arrives as ``""``. The
judgement about what any of that MEANS belongs to ``validation_service``,
which can be corrected without re-reading a file that may by then have moved.

``data_only=True`` asks for the cached values Excel last computed rather than
the formula text — the numbers a human saw on screen. That is also why the
error strings survive: the cached value of a broken formula IS ``#REF!``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

#: Confined here by A-09. Import it anywhere else and the build fails.
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

#: The cached value of a formula whose reference no longer exists. 852 of them
#: in the three workbooks delivered, plus 504 ``#VALUE!``.
ERROR_MARKERS = ("#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!")


def file_digest(path: str | Path) -> str:
    """SHA-256 of the workbook — the anchor a re-import collides against."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cell_entry(cell: Any) -> dict[str, Any]:
    """
    One cell, as text plus what it was.

    The value is stringified rather than kept as a number because the raw
    layer must survive JSON without a float creeping in (A-01). Numbers become
    ``Decimal`` later, from this text, in ``validation_service``.

    ``getattr`` rather than attribute access because a read-only worksheet
    yields ``EmptyCell`` for gaps, and an ``EmptyCell`` carries neither a type
    nor a position. It is still a cell in the row, and treating it as one is
    what keeps the column letters aligned.
    """
    value = getattr(cell, "value", None)
    kind = getattr(cell, "data_type", "n")
    if value is None:
        return {"v": "", "t": kind}
    text = str(value).strip()
    return {"v": text, "t": kind, "e": text in ERROR_MARKERS}


def read_sheet(sheet: Any) -> tuple[dict[str, dict[str, Any]], list[tuple[int, dict[str, Any]]]]:
    """
    One sheet as (header block, rows).

    The header block is rows 1 and 2 keyed by column letter — every one of the
    twenty-five sheets delivered puts its metadata in row 1 and its column
    names in row 2. Rows are ``(source_row, {column: entry})`` from row 3 down,
    and a row where every cell is empty is skipped rather than stored: those
    are the spreadsheet's own padding, not the centre's records.
    """
    header: dict[str, dict[str, Any]] = {}
    rows: list[tuple[int, dict[str, Any]]] = []

    # The row number comes from the enumeration rather than from the cells: a
    # read-only sheet yields ``EmptyCell`` for gaps, and those carry no
    # position at all. Enumerating also means the index still matches the
    # spreadsheet even where a whole row is blank.
    for index, row in enumerate(sheet.iter_rows(), start=1):
        cells = {}
        for offset, cell in enumerate(row, start=1):
            entry = _cell_entry(cell)
            if entry["v"] != "":
                cells[get_column_letter(offset)] = entry
        if index == 1:
            header["meta"] = cells
        elif index == 2:
            header["columns"] = cells
        elif cells:
            rows.append((index, cells))

    header.setdefault("meta", {})
    header.setdefault("columns", {})
    return header, rows


def read_workbook(path: str | Path) -> list[dict[str, Any]]:
    """
    Every visible sheet, in file order.

    Hidden sheets are skipped and the fact is not hidden in turn: the caller
    records ``sheet_count`` from what was read, so a workbook whose history
    lives on a hidden tab shows up as a count that does not match the file.
    """
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        sheets = []
        for name in workbook.sheetnames:
            sheet = workbook[name]
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            header, rows = read_sheet(sheet)
            sheets.append({"name": name, "header": header, "rows": rows})
        return sheets
    finally:
        workbook.close()


__all__ = ["ERROR_MARKERS", "file_digest", "read_sheet", "read_workbook"]
