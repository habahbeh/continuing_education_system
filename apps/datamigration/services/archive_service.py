"""
Committing a validated batch into the archive.

Idempotent by construction rather than by checking: every archive row hangs
off exactly one ``MigrationRow`` through a ``OneToOneField``, so a second
commit of the same batch collides at the database rather than doubling
anything. The service checks first and says so in words, but the guarantee is
the constraint.

**Nothing here reaches the ledger.** A-04 forbids this app from importing
``billing``, ``cashbox`` or ``settlements``, and the test
``test_committing_a_workbook_writes_nothing_to_the_ledger`` asserts the
consequence rather than trusting the import rule: zero rows created, zero
partner shares moved, zero fils anywhere.

**Rows without a usable number do not become participants.** They were marked
``UNIDENTIFIED`` by validation and are skipped here, still present, still
counted. ``rows read = archived + unidentified`` is checked before the batch
is stamped, so a silent loss of rows fails the commit instead of passing
quietly.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.datamigration.models import (
    BatchStatus,
    HistoricalCohort,
    HistoricalEnrollment,
    HistoricalParticipant,
    HistoricalPayment,
    MigrationBatch,
    MigrationRow,
    RowState,
)
from apps.datamigration.services import mapping
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "datamigration.MigrationBatch"
ZERO = Decimal("0.000")


class NotValidatedError(Exception):
    """A batch is archived only after its findings have been produced."""


class AlreadyCommittedError(Exception):
    """The batch is archived. A second commit would double it."""


class ReconciliationError(Exception):
    """Rows went missing between reading and archiving."""


def _amount(row: MigrationRow, column: str) -> Decimal:
    """
    A money cell, with absence read as zero **at this layer only**.

    The raw row still holds what the cell said, so «blank» and «zero» remain
    distinguishable to anyone who looks. The archive needs a number to store,
    and 0 is the honest reading of an empty fee column in a sheet whose totals
    are computed from it.
    """
    return mapping.as_decimal(row.cell(column)) or ZERO


def _known_partners() -> list[str]:
    """
    The partner names the centre recognises — a setting, not a literal.

    ``archive_partner_labels`` is effective-dated like every other business
    vocabulary in this system, so a workbook naming a partner nobody has
    listed yet archives with a blank label and a settings edit fixes it.

    Stored as a JSON string, the way ``certificate_grades`` and
    ``clearance_custody_items`` already are, so it is decoded the same way. A
    malformed value yields an empty list: an unlabelled cohort is a gap a
    human fills, while an exception would stop the archive mid-workbook.
    """
    raw = get_setting("archive_partner_labels", as_of=date.today(), default=None)
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [str(name) for name in parsed] if isinstance(parsed, list) else []


def _cohort_for(batch: MigrationBatch, sheet_name: str) -> HistoricalCohort:
    header = (batch.sheet_headers or {}).get(sheet_name, {})
    subjects = mapping.subject_columns(header)
    meta = header.get("meta", {})
    columns = header.get("columns", {})
    cohort, _created = HistoricalCohort.objects.get_or_create(
        batch=batch,
        sheet_name=sheet_name,
        defaults={
            "program_label_ar": mapping.program_label(header)[:150],
            "term_label": mapping.term_label(header)[:120],
            "partner_label_ar": mapping.partner_label(header, _known_partners())[:150],
            "delivery_label": mapping.delivery_label(header)[:60],
            "date_label": mapping.date_label(header)[:120],
            "subject_prices": {
                letter: {
                    "label": str((columns.get(letter) or {}).get("v") or ""),
                    "price": str((meta.get(letter) or {}).get("v") or ""),
                }
                for letter in subjects
            },
        },
    )
    return cohort


def commit(*, actor: Any, batch: MigrationBatch, request: Any = None) -> dict[str, Any]:
    """
    Turn VALID rows into archive records. Run twice, and the second is refused.

    ``Action.APPROVE`` rather than ``CREATE``: importing a workbook is reading,
    and archiving it is the decision that this reading is the centre's history.
    The matrix gives that to the manager.

    Permission and state are decided before the transaction opens — a refusal
    rolled back with its own audit row would leave a blocked attempt on the
    archive with no trace of it (BR-085).
    """
    policy.require(actor, Screen.MIGRATION, Action.APPROVE, request=request)

    if batch.status == BatchStatus.COMMITTED:
        raise AlreadyCommittedError(
            f"الدفعة {batch.code} مؤرشفة سلفاً — الأرشفة مرة واحدة (D-27 · BR-087)."
        )
    if batch.status != BatchStatus.VALIDATED:
        raise NotValidatedError(
            f"الدفعة {batch.code} لم تُدقَّق بعد — التشغيل التجريبي يسبق الأرشفة دائماً."
        )

    return _write_archive(actor=actor, batch=batch, request=request)


@transaction.atomic
def _write_archive(*, actor: Any, batch: MigrationBatch, request: Any) -> dict[str, Any]:
    """The write half — permission already decided by the caller."""
    rows = list(batch.rows.all())
    archived = 0
    for row in rows:
        if row.state != RowState.VALID:
            continue
        _archive_row(batch, row)
        archived += 1

    unidentified = sum(1 for row in rows if row.state == RowState.UNIDENTIFIED)
    rejected = sum(1 for row in rows if row.state == RowState.REJECTED)
    if archived + unidentified + rejected != len(rows):
        raise ReconciliationError(
            f"عدم مطابقة: قُرئ {len(rows)} صفاً وحُوسب "
            f"{archived + unidentified + rejected} — الأرشفة موقوفة."
        )

    batch.status = BatchStatus.COMMITTED
    batch.committed_by = actor
    batch.committed_at = timezone.now()
    batch.save(update_fields=["status", "committed_by", "committed_at"])

    summary = {
        "batch_code": batch.code,
        "rows_read": len(rows),
        "archived": archived,
        "unidentified": unidentified,
        "rejected": rejected,
        "participants": batch.participants.count(),
        "enrollments": batch.enrollments.count(),
        "payments": batch.payments.count(),
        "cohorts": batch.cohorts.count(),
    }
    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(batch.pk),
        reference=batch.code,
        summary_ar=(f"أُرشفت الدفعة {batch.code} — {archived} صفاً و{unidentified} بلا رقم صالح"),
        actor=actor,
        changes={key: str(value) for key, value in summary.items()}
        | {"ledger_effect": "لا شيء — الأرشيف معزول عن الدفتر المالي (A-04 · D-26)"},
        request=request,
    )
    return summary


def _archive_row(batch: MigrationBatch, row: MigrationRow) -> None:
    """One VALID row becomes a participant, an enrolment and — sometimes — a payment."""
    number, alternate = mapping.split_legacy_number(row.cell(mapping.LEGACY_NUMBER))
    cohort = _cohort_for(batch, row.sheet_name)
    category = mapping.category_of(
        row.cell(mapping.CATEGORY_CODE), row.cell(mapping.CATEGORY_LABEL)
    )

    participant = HistoricalParticipant.objects.create(
        batch=batch,
        source_row=row,
        legacy_number=number,
        legacy_alt_number=alternate,
        name_ar=row.cell(mapping.NAME).strip()[:150],
        legacy_category=category,
    )

    collected = _amount(row, mapping.COLLECTED)
    enrollment = HistoricalEnrollment.objects.create(
        batch=batch,
        source_row=row,
        participant=participant,
        cohort=cohort,
        course_value=_amount(row, mapping.COURSE_VALUE),
        tuition=_amount(row, mapping.TUITION),
        registration_fee=_amount(row, mapping.REGISTRATION_FEE),
        collected=collected,
        legacy_category=category,
        source_note=row.cell(mapping.DATE_OR_NOTE).strip()[:255],
        subject_breakdown={
            letter: row.cell(letter)
            for letter in mapping.subject_columns(
                (batch.sheet_headers or {}).get(row.sheet_name, {})
            )
            if row.cell(letter)
        },
    )

    # A payment row only where money was actually recorded. A zero collected
    # column is an enrolment nobody paid for, not a payment of nothing.
    if collected > ZERO:
        date_text = row.cell(mapping.DATE_OR_NOTE).strip()
        HistoricalPayment.objects.create(
            batch=batch,
            source_row=row,
            enrollment=enrollment,
            amount=collected,
            legacy_receipt_ref=row.cell(mapping.RECEIPT_REF).strip()[:64],
            legacy_date_text=date_text[:120],
            parsed_date=mapping.as_date(date_text),
        )


__all__ = [
    "AlreadyCommittedError",
    "NotValidatedError",
    "ReconciliationError",
    "commit",
]
