"""
Where each column sits, and how to read a value that may not be one.

All twenty-five sheets share a layout — row 1 metadata, row 2 headers, data
from row 3 — but they do not share meanings. Column ``M`` is the partner
ratio (0.45, later 0.5) in ``Business Administration.xlsx``, a date serial in
most ``General English4.xlsx`` sheets, and free text («براءة ذمة», «مخالصة
نهائية») in the rest. So ``M`` is never mapped to a field: it stays in
``raw_row`` where a reader can see it for what it is.

The partner is worse. It sits in ``B1`` in one workbook, ``E1`` in another and
``D1`` in the third, and ``B1`` in the English file holds the LEVEL («4»), not
a partner. Rather than guess, ``partner_label`` reads the metadata row for a
value that is not a number and not the programme, which is how a human reads
it too.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.datamigration.readers.xlsx_reader import ERROR_MARKERS

# --- The stable columns ----------------------------------------------------
SERIAL = "A"
NAME = "B"
LEGACY_NUMBER = "C"
DATE_OR_NOTE = "D"
RECEIPT_REF = "E"
COURSE_VALUE = "F"
COLLECTED = "G"
TUITION = "H"
REGISTRATION_FEE = "I"
REMAINING = "J"
CATEGORY_CODE = "K"
CATEGORY_LABEL = "L"

#: Where the per-subject allocation may begin. Everything from here rightwards
#: counts as a subject column only if row 1 gives it a price and row 2 a name.
SUBJECTS_FROM = "N"

#: «مركز» / «جامعة» as the sheets spell them — 283 and 108 rows respectively,
#: with one contradictory pair that validation reports rather than resolves.
CATEGORY_CENTER = "2"
CATEGORY_UNIVERSITY = "1"

#: Excel's 1900 epoch, offset by the famous phantom leap day. Column D holds
#: 31 of these serials among its 58 Arabic notes.
EXCEL_EPOCH = date(1899, 12, 30)
SERIAL_RANGE = (30000, 60000)  # roughly 1982 … 2064; anything else is not a date


def is_error(text: str) -> bool:
    """``#REF!`` and its family — a deleted reference, never a zero."""
    return text.strip() in ERROR_MARKERS


def as_decimal(text: str) -> Decimal | None:
    """
    A money value, or None when the cell does not hold one.

    Never ``float`` (A-01b): openpyxl hands back a Python float and going
    through ``str`` is what keeps the fils. None is returned rather than zero
    for a blank or an error, so the caller has to decide what absence means.
    """
    cleaned = text.strip().replace(",", "")
    if not cleaned or is_error(cleaned):
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def as_date(text: str) -> date | None:
    """
    The three date shapes the sheets actually use, and nothing else.

    ``42267`` (a serial), ``17/3/2018``, and — returning None — the 26 cells
    holding ``13/09+26/11 /2015``, which record two payments in one cell. A
    compound is not half a date, so it is reported as unparseable rather than
    silently truncated to its first half.
    """
    raw = text.strip()
    if not raw or is_error(raw) or "+" in raw:
        return None
    if re.fullmatch(r"\d{4,6}", raw):
        serial = int(raw)
        if SERIAL_RANGE[0] <= serial <= SERIAL_RANGE[1]:
            return EXCEL_EPOCH + timedelta(days=serial)
        return None
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


# --- Identity --------------------------------------------------------------
#: 20175202 (8) · 202251004 (9) · 2022520152 (10) · 20225201353 (11). The same
#: year+type+sequence scheme as production, with a sequence three to six digits
#: wide — which is exactly why it may not go in ``participant_number``.
LEGACY_NUMBER_RE = re.compile(r"^\d{6,12}$")

#: ``20155173-200920437`` — an old centre number and a university number in one
#: cell. Eight rows, all in ``General English4.xlsx``.
COMPOSITE_RE = re.compile(r"^(\d{6,12})\s*-\s*(\d{1,12})$")


def split_legacy_number(text: str) -> tuple[str, str]:
    """
    (number, alternate). Both empty when the cell holds no number at all.

    The 43 cells reading «مركز», «جامعة», «خريج» or «ط جامعة» return ("", "")
    and become ``UNIDENTIFIED_NO_USABLE_NUMBER`` — kept as a row, never as a
    participant.
    """
    raw = text.strip()
    if not raw or is_error(raw):
        return "", ""
    composite = COMPOSITE_RE.match(raw)
    if composite:
        return composite.group(1), composite.group(2)
    if LEGACY_NUMBER_RE.match(raw):
        return raw, ""
    return "", ""


def category_of(code: str, label: str) -> str:
    """
    The sheet's own «م / ج», normalised only as far as the sheet supports.

    Returns the raw code when the pair disagrees — one row has ``K=1`` beside
    «طالب مركز» — because reconciling them is a judgement, and this function is
    not the place a judgement gets made silently.
    """
    code = code.strip()
    if code in (CATEGORY_CENTER, CATEGORY_UNIVERSITY):
        return code
    if "مركز" in label:
        return CATEGORY_CENTER
    if "جامعة" in label:
        return CATEGORY_UNIVERSITY
    return ""


# --- The sheet header ------------------------------------------------------
def subject_columns(header: dict[str, Any]) -> list[str]:
    """
    Columns carrying a per-subject allocation.

    A column qualifies only when row 2 names a subject AND row 1 gives it a
    price — the pairing is what distinguishes the twelve subject columns of a
    diploma sheet from the stray ``AB``/``AC`` cells that hold a note and a
    date.
    """
    meta = header.get("meta", {})
    columns = header.get("columns", {})
    found = []
    for letter, entry in columns.items():
        if len(letter) != 1 or letter < SUBJECTS_FROM:
            continue
        label = str(entry.get("v") or "").strip()
        price = as_decimal(str((meta.get(letter) or {}).get("v") or ""))
        if label and price is not None:
            found.append(letter)
    return sorted(found)


def _meta_values(header: dict[str, Any]) -> dict[str, str]:
    return {k: str(v.get("v") or "").strip() for k, v in header.get("meta", {}).items()}


def program_label(header: dict[str, Any]) -> str:
    """Row 1's ``C`` in every workbook delivered — «ادارة الاعمال», «General English»."""
    return _meta_values(header).get("C", "")


def partner_label(header: dict[str, Any], known: list[str] | None = None) -> str:
    """
    The partner, matched against the names the centre actually has agreements
    with — never guessed from position.

    A guess was tried first and it was wrong. The partner sits in ``B1`` in
    Business Administration, ``E1`` in General English and ``D1`` in
    Generatave; ``B1`` in General English is the course level and ``B1`` in
    Generatave is the workbook's own name. "First metadata cell that reads as
    words" duly labelled a «تناغم» cohort as partner «Generatave» — a wrong
    partner on an archived cohort, which is exactly the kind of quiet error
    this sprint exists to avoid.

    So the vocabulary is data: ``archive_partner_labels`` lists the names the
    centre recognises, and a sheet naming none of them is archived with a
    BLANK partner for a human to fill in. Blank is honest; a plausible wrong
    name is not, and a new partner is a settings edit rather than a code
    change.
    """
    recognised = [name.strip() for name in (known or []) if name.strip()]
    if not recognised:
        return ""
    for value in _meta_values(header).values():
        for name in recognised:
            if name in value:
                return name
    return ""


def term_label(header: dict[str, Any]) -> str:
    """«ف 2 2018», «ف 1 2022» — the term, in whichever cell carries it."""
    values = _meta_values(header)
    for letter in ("D", "E", "B", "F"):
        candidate = values.get(letter, "")
        if candidate.startswith("ف "):
            return candidate
    return ""


def delivery_label(header: dict[str, Any]) -> str:
    """«On Line» in the one sheet that says so; empty everywhere else."""
    for value in _meta_values(header).values():
        if value.lower().replace(" ", "") == "online":
            return value
    return ""


def date_label(header: dict[str, Any]) -> str:
    """
    Row 1's date range, kept as the text it was.

    ``17/3/2018-17/9/2018`` in one sheet, the serial ``43833`` in another, and
    absent in most. A range is not a date and is not stored as one.
    """
    values = _meta_values(header)
    for letter in ("G", "F"):
        candidate = values.get(letter, "")
        if candidate and candidate.lower().replace(" ", "") != "online":
            return candidate
    return ""


__all__ = [
    "CATEGORY_CENTER",
    "CATEGORY_LABEL",
    "CATEGORY_UNIVERSITY",
    "COLLECTED",
    "COURSE_VALUE",
    "DATE_OR_NOTE",
    "LEGACY_NUMBER",
    "NAME",
    "RECEIPT_REF",
    "REGISTRATION_FEE",
    "REMAINING",
    "SERIAL",
    "TUITION",
    "as_date",
    "as_decimal",
    "category_of",
    "date_label",
    "delivery_label",
    "is_error",
    "partner_label",
    "program_label",
    "split_legacy_number",
    "subject_columns",
    "term_label",
]
