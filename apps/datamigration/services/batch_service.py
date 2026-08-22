"""
Batches — reading a workbook in, and superseding a reading that was wrong.

A batch is one workbook read once. Its ``source_sha256`` is what makes the
import command safe to run twice by accident: the same bytes produce the same
hash, and a live batch already holding that hash refuses the second run rather
than doubling the archive.

Correction is by SUPERSESSION, never by deletion. A better reading of the same
file becomes a new batch and the old one is marked; both stay. That is D-27
(«لا تعديل على سجل مؤرشف بعد الأرشفة», BR-087) expressed as a shape rather
than as a promise — there is no code path here that edits an archived row.

**This module imports no financial app, and cannot** (A-04). The archive is
structurally isolated from the ledger; the only gateway is a reviewed
``OpeningBalance``, which belongs to Sprint 8D-2 and does not exist yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.services.audit_service import write_audit
from apps.datamigration.models import BatchStatus, MigrationBatch, MigrationRow, RowState
from apps.datamigration.readers import xlsx_reader
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "datamigration.MigrationBatch"


class BatchStateError(Exception):
    """The batch is not in a state where this step makes sense."""


class DuplicateSourceError(Exception):
    """A live batch already holds this workbook's fingerprint."""


class ArchivedBatchError(Exception):
    """D-27 · BR-087 — an archived batch is not edited, it is superseded."""


def _guard_not_archived(batch: MigrationBatch) -> None:
    if batch.status == BatchStatus.COMMITTED:
        raise ArchivedBatchError(
            "الدفعة مؤرشفة ولا تُعدَّل — التصحيح يكون بدفعة جديدة تحلّ محلّها (D-27 · BR-087)."
        )


def import_workbook(
    *,
    actor: Any,
    path: str | Path,
    code: str,
    note_ar: str = "",
    request: Any = None,
) -> MigrationBatch:
    """
    Read a workbook into RAW rows. **Writes nothing outside this app.**

    Every cell arrives as text. No number is parsed, no blank becomes a zero
    and no ``#REF!`` is swallowed, because the whole value of a raw layer is
    that a parsing decision can be revisited without the file.

    **Decide, then act.** The permission check and the guards run OUTSIDE the
    transaction. A refusal writes a DENIED_ATTEMPT row and raises; inside an
    atomic block the rollback would take that row with it and a blocked
    attempt would leave no trace (BR-085).
    """
    policy.require(actor, Screen.MIGRATION, Action.CREATE, request=request)

    source = Path(path)
    if not source.exists():
        raise ValidationError(f"الملف غير موجود: {source}")
    if MigrationBatch.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الدفعة {code} مستعمل سلفاً.")

    digest = xlsx_reader.file_digest(source)
    clash = (
        MigrationBatch.objects.filter(source_sha256=digest)
        .exclude(status=BatchStatus.SUPERSEDED)
        .first()
    )
    if clash is not None:
        raise DuplicateSourceError(
            f"هذا الملف مستورَد سلفاً في الدفعة {clash.code} — الاستيراد مرتين لا يضاعف الأرشيف."
        )

    return _store(
        actor=actor,
        source=source,
        digest=digest,
        code=code,
        note_ar=note_ar,
        request=request,
    )


@transaction.atomic
def _store(
    *,
    actor: Any,
    source: Path,
    digest: str,
    code: str,
    note_ar: str,
    request: Any,
) -> MigrationBatch:
    """The write half — permission already decided by the caller."""
    sheets = xlsx_reader.read_workbook(source)
    batch = MigrationBatch.objects.create(
        code=code,
        source_filename=source.name,
        source_sha256=digest,
        sheet_count=len(sheets),
        sheet_headers={sheet["name"]: sheet["header"] for sheet in sheets},
        note_ar=note_ar,
        created_by=actor,
    )

    rows = [
        MigrationRow(
            batch=batch,
            sheet_name=sheet["name"],
            source_row=index,
            raw_row=cells,
            state=RowState.RAW,
        )
        for sheet in sheets
        for index, cells in sheet["rows"]
    ]
    MigrationRow.objects.bulk_create(rows, batch_size=500)

    batch.row_count = len(rows)
    batch.save(update_fields=["row_count"])

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(batch.pk),
        reference=code,
        summary_ar=f"استيراد أوّلي لملف {source.name} — {len(sheets)} ورقة و{len(rows)} صف خام",
        actor=actor,
        changes={
            "source_filename": source.name,
            "source_sha256": digest,
            "sheet_count": str(len(sheets)),
            "row_count": str(len(rows)),
            "isolation": "A-04 — الأرشيف لا يمسّ الدفتر المالي",
        },
        request=request,
    )
    return batch


def supersede(
    *, actor: Any, batch: MigrationBatch, successor: MigrationBatch, request: Any = None
) -> MigrationBatch:
    """
    Retire a batch in favour of a better reading of the same file.

    Both survive. The superseded batch keeps its rows, its findings and its
    audit trail, so a question about what the archive once said still has an
    answer.
    """
    policy.require(actor, Screen.MIGRATION, Action.APPROVE, request=request)

    if batch.pk == successor.pk:
        raise ValidationError("لا تحلّ الدفعة محلّ نفسها.")
    if batch.status == BatchStatus.SUPERSEDED:
        raise BatchStateError(f"الدفعة {batch.code} مستبدَلة سلفاً.")

    return _mark_superseded(actor=actor, batch=batch, successor=successor, request=request)


@transaction.atomic
def _mark_superseded(
    *, actor: Any, batch: MigrationBatch, successor: MigrationBatch, request: Any
) -> MigrationBatch:
    batch.superseded_by = successor
    batch.status = BatchStatus.SUPERSEDED
    batch.save(update_fields=["superseded_by", "status"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(batch.pk),
        reference=batch.code,
        summary_ar=f"استُبدلت الدفعة {batch.code} بالدفعة {successor.code}",
        actor=actor,
        changes={"superseded_by": successor.code, "status": BatchStatus.SUPERSEDED},
        request=request,
    )
    return batch


def batch_instance(*, actor: Any, code: str, request: Any = None) -> MigrationBatch:
    """The object, for handing to another service (A-05)."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    return MigrationBatch.objects.get(code=code)


__all__ = [
    "ArchivedBatchError",
    "BatchStateError",
    "DuplicateSourceError",
    "batch_instance",
    "import_workbook",
    "supersede",
]
