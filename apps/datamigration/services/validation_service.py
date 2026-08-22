"""
The dry run — what the sheets actually contain, said out loud.

Every finding here names something the SOURCE does. None of them corrects it.
That distinction is the whole design: 146 of 163 checked rows have subject
columns that do not sum to the tuition, and the right response is to report
that 146 rows disagree with themselves, not to make them agree.

Validation writes only to ``MigrationRow`` — its state, its findings, its
reason. It creates no archive row, which is what makes ``--dry-run`` mean
something: run it as often as you like and the archive is untouched.

**Two findings decide whether a row can be archived at all.**

``UNIDENTIFIED_NO_USABLE_NUMBER`` — the 335 ``#REF!`` cells and the 43 reading
«مركز», «جامعة», «خريج» where a university number belongs. The row is kept
verbatim and counted, so ``read = archived + unidentified + rejected``
reconciles against the sheet, but it never becomes a participant and can never
be linked to one.

``LEGACY_NUMBER_NAME_CONFLICT`` — eight numbers carry two different names
across the workbooks. 20195073 is both «براءه حسام محمود» and «تمارا عامر
الشيب». The row is still archived, because it is a real record of something;
it is flagged so the registrar reviewing the link sees the conflict before
deciding, rather than after.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.services.audit_service import write_audit
from apps.datamigration.models import (
    BatchStatus,
    Finding,
    MigrationBatch,
    MigrationRow,
    RowState,
)
from apps.datamigration.services import mapping
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "datamigration.MigrationBatch"
ZERO = Decimal("0.000")

#: Tolerance on the subject-sum check. Three fils, because the sheets carry
#: halves (22.5, 17.5) and one carries 36.450000000000003 — a float artefact
#: from Excel itself, not a discrepancy anyone should be asked about.
TOLERANCE = Decimal("0.01")


def _row_findings(row: MigrationRow, subjects: list[str]) -> list[str]:
    """Everything wrong, or merely notable, about one row."""
    found: list[str] = []
    cells = row.raw_row

    def text(column: str) -> str:
        return str((cells.get(column) or {}).get("v") or "")

    if any((entry or {}).get("e") for entry in cells.values()):
        markers = {text(col) for col, entry in cells.items() if (entry or {}).get("e")}
        if "#REF!" in markers:
            found.append(Finding.REF_ERROR)
        if "#VALUE!" in markers:
            found.append(Finding.VALUE_ERROR)

    raw_number = text(mapping.LEGACY_NUMBER)
    number, alternate = mapping.split_legacy_number(raw_number)
    if not number:
        found.append(Finding.UNIDENTIFIED_NO_USABLE_NUMBER)
        if raw_number.strip() and not mapping.is_error(raw_number):
            found.append(Finding.NON_NUMERIC_IDENTITY)
    elif alternate:
        found.append(Finding.LEGACY_NUMBER_COMPOSITE)

    if not text(mapping.NAME).strip():
        found.append(Finding.NAME_MISSING)

    course_value = mapping.as_decimal(text(mapping.COURSE_VALUE))
    collected = mapping.as_decimal(text(mapping.COLLECTED))
    tuition = mapping.as_decimal(text(mapping.TUITION))

    if course_value is not None and collected is not None and collected > course_value:
        found.append(Finding.OVERPAYMENT)
        found.append(Finding.NEGATIVE_BALANCE)

    if subjects and tuition is not None:
        total = sum((mapping.as_decimal(text(col)) or ZERO for col in subjects), ZERO)
        if abs(total - tuition) > TOLERANCE:
            found.append(Finding.SUBJECT_SUM_MISMATCH)

    receipt = text(mapping.RECEIPT_REF).strip()
    if "+" in receipt:
        found.append(Finding.RECEIPT_REF_COMPOUND)

    date_text = text(mapping.DATE_OR_NOTE).strip()
    if date_text and mapping.as_date(date_text) is None:
        found.append(Finding.DATE_UNPARSEABLE)

    return found


def validate(*, actor: Any, batch: MigrationBatch, request: Any = None) -> dict[str, Any]:
    """
    Examine every row and record what was found. Creates no archive row.

    Re-runnable: findings are recomputed from ``raw_row`` each time, so fixing
    the mapping and validating again gives a fresh report without re-reading
    the workbook.

    Permission is decided before the transaction opens: a refusal writes a
    DENIED_ATTEMPT row and raises, and a rollback would take that row with it
    (BR-085).
    """
    policy.require(actor, Screen.MIGRATION, Action.CREATE, request=request)

    if batch.status == BatchStatus.COMMITTED:
        from apps.datamigration.services.batch_service import ArchivedBatchError

        raise ArchivedBatchError(
            "الدفعة مؤرشفة — إعادة التدقيق تكون على دفعة جديدة (D-27 · BR-087)."
        )

    return _run(actor=actor, batch=batch, request=request)


@transaction.atomic
def _run(*, actor: Any, batch: MigrationBatch, request: Any) -> dict[str, Any]:
    """The write half — permission already decided by the caller."""
    rows = list(batch.rows.all())
    subjects_by_sheet = {
        name: mapping.subject_columns(header)
        for name, header in (batch.sheet_headers or {}).items()
    }

    by_number: dict[str, set[str]] = defaultdict(set)
    per_row: dict[int, list[str]] = {}
    for row in rows:
        findings = _row_findings(row, subjects_by_sheet.get(row.sheet_name, []))
        per_row[row.pk] = findings
        number, _alt = mapping.split_legacy_number(row.cell(mapping.LEGACY_NUMBER))
        name = row.cell(mapping.NAME).strip()
        if number and name:
            by_number[number].add(name)

    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        findings = per_row[row.pk]
        number, _alt = mapping.split_legacy_number(row.cell(mapping.LEGACY_NUMBER))
        if number and len(by_number.get(number, ())) > 1:
            findings.append(Finding.LEGACY_NUMBER_NAME_CONFLICT)

        if Finding.UNIDENTIFIED_NO_USABLE_NUMBER in findings:
            row.state = RowState.UNIDENTIFIED
            row.reason_ar = "لا رقم جامعي صالح في العمود C — يُحفظ الصف ولا يُنشأ منه مشارك."
        else:
            row.state = RowState.VALID
            row.reason_ar = ""
        row.findings = findings
        for code in findings:
            counts[code] += 1

    MigrationRow.objects.bulk_update(rows, ["state", "findings", "reason_ar"], batch_size=500)

    batch.status = BatchStatus.VALIDATED
    batch.validated_by = actor
    batch.validated_at = timezone.now()
    batch.save(update_fields=["status", "validated_by", "validated_at"])

    report = _report(batch, rows, counts)
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(batch.pk),
        reference=batch.code,
        summary_ar=(
            f"تدقيق تجريبي للدفعة {batch.code} — "
            f"{report['archivable']} صالح و{report['unidentified']} بلا رقم"
        ),
        actor=actor,
        changes={
            "rows_read": str(report["rows_read"]),
            "archivable": str(report["archivable"]),
            "unidentified": str(report["unidentified"]),
            "findings": {code: str(n) for code, n in sorted(counts.items())},
            "writes": "لا شيء خارج صفوف المصدر — التشغيل التجريبي لا يؤرشف",
        },
        request=request,
    )
    return report


def _report(
    batch: MigrationBatch, rows: list[MigrationRow], counts: dict[str, int]
) -> dict[str, Any]:
    labels = dict(Finding.choices)
    archivable = sum(1 for row in rows if row.state == RowState.VALID)
    unidentified = sum(1 for row in rows if row.state == RowState.UNIDENTIFIED)
    return {
        "batch_code": batch.code,
        "source_filename": batch.source_filename,
        "sheet_count": batch.sheet_count,
        "rows_read": len(rows),
        "archivable": archivable,
        "unidentified": unidentified,
        "rejected": len(rows) - archivable - unidentified,
        "reconciles": archivable + unidentified == len(rows),
        "findings": [
            {"code": code, "label": str(labels.get(code, code)), "count": count}
            for code, count in sorted(counts.items(), key=lambda pair: -pair[1])
        ],
    }


def report_for(*, actor: Any, batch: MigrationBatch, request: Any = None) -> dict[str, Any]:
    """The last validation's findings, recounted from the stored rows."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    rows = list(batch.rows.all())
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for code in row.findings or ():
            counts[code] += 1
    return _report(batch, rows, counts)


__all__ = ["TOLERANCE", "report_for", "validate"]
