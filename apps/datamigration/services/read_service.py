"""
The read layer the migration screens use (A-05).

Views may not import ``apps.*.models``, so every figure a template renders is
asked for here. Nothing in this module writes, and nothing in it computes
money — there is no money in this app to compute.
"""

from __future__ import annotations

from typing import Any

from apps.core.display import person_name, text_of
from apps.datamigration.models import (
    BatchStatus,
    Finding,
    HistoricalParticipant,
    MigrationBatch,
    MigrationRow,
    RowState,
)
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: How many raw rows a screen shows at once. The three workbooks delivered
#: carry 1321 rows between them; rendering all of them would make the page
#: useless rather than thorough.
PAGE = 100


def list_batches(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """Every batch, newest first, with what became of its rows."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    return [
        {
            "code": batch.code,
            "source_filename": batch.source_filename,
            "sha_short": batch.source_sha256[:12],
            "sheet_count": batch.sheet_count,
            "row_count": batch.row_count,
            "status": batch.status,
            "status_display": batch.get_status_display(),
            "is_live": batch.is_live,
            "superseded_by": text_of(batch.superseded_by, "code"),
            "created_by": person_name(batch.created_by),
            "created_at": batch.created_at,
            "validated_at": batch.validated_at,
            "committed_at": batch.committed_at,
            "committed_by": person_name(batch.committed_by),
            "archived_rows": batch.participants.count(),
            "linked_rows": batch.participants.exclude(linked_participant=None).count(),
            "note_ar": batch.note_ar,
        }
        for batch in MigrationBatch.objects.select_related(
            "created_by", "committed_by", "superseded_by"
        )
    ]


def batch_detail(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """One batch: its counts, its sheets, and how its rows fell out."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    batch = MigrationBatch.objects.select_related("created_by", "committed_by").get(code=code)
    rows = batch.rows.all()
    states = {state: rows.filter(state=state).count() for state, _label in RowState.choices}
    return {
        "code": batch.code,
        "source_filename": batch.source_filename,
        "status": batch.status,
        "status_display": batch.get_status_display(),
        "sheet_count": batch.sheet_count,
        "row_count": batch.row_count,
        "states": [
            {"state": state, "label": str(label), "count": states.get(state, 0)}
            for state, label in RowState.choices
        ],
        "reconciles": states.get(RowState.VALID, 0)
        + states.get(RowState.UNIDENTIFIED, 0)
        + states.get(RowState.REJECTED, 0)
        == batch.row_count,
        "can_validate": batch.status in {BatchStatus.DRAFT, BatchStatus.VALIDATED},
        "can_commit": batch.status == BatchStatus.VALIDATED,
        "cohorts": [
            {
                "sheet_name": cohort.sheet_name,
                "program_label_ar": cohort.program_label_ar,
                "term_label": cohort.term_label,
                "partner_label_ar": cohort.partner_label_ar,
                "delivery_label": cohort.delivery_label,
                "date_label": cohort.date_label,
                "subject_count": len(cohort.subject_prices or {}),
                "enrollment_count": cohort.enrollments.count(),
            }
            for cohort in batch.cohorts.all()
        ],
    }


def rows_of(
    *,
    actor: Any,
    code: str,
    state: str = "",
    finding: str = "",
    sheet: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """Raw rows, filtered the three ways a reviewer actually filters them."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    labels = dict(Finding.choices)
    queryset = MigrationRow.objects.filter(batch__code=code)
    if state:
        queryset = queryset.filter(state=state)
    if sheet:
        queryset = queryset.filter(sheet_name=sheet)
    if finding:
        queryset = queryset.filter(findings__contains=finding)
    return [
        {
            "id": row.pk,
            "sheet_name": row.sheet_name,
            "source_row": row.source_row,
            "state": row.state,
            "state_display": row.get_state_display(),
            "legacy_number_raw": row.cell("C"),
            "name_raw": row.cell("B"),
            "reason_ar": row.reason_ar,
            "findings": [
                {"code": code_, "label": str(labels.get(code_, code_))}
                for code_ in (row.findings or ())
            ],
            "cells": dict(sorted((row.raw_row or {}).items())),
        }
        for row in queryset[:PAGE]
    ]


def link_queue(
    *, actor: Any, code: str = "", only_unlinked: bool = True, request: Any = None
) -> list[dict[str, Any]]:
    """
    Archive rows awaiting an identity decision.

    ``name_conflict`` is surfaced on the row rather than left in the findings
    list, because it is the one fact a reviewer must see BEFORE choosing, not
    after.
    """
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    queryset = HistoricalParticipant.objects.select_related(
        "batch", "source_row", "linked_participant", "linked_by"
    )
    if code:
        queryset = queryset.filter(batch__code=code)
    if only_unlinked:
        queryset = queryset.filter(linked_participant__isnull=True)
    return [
        {
            "id": historical.pk,
            "batch_code": historical.batch.code,
            "legacy_number": historical.legacy_number,
            "legacy_alt_number": historical.legacy_alt_number,
            "name_ar": historical.name_ar,
            "legacy_category": historical.legacy_category,
            "sheet_name": historical.source_row.sheet_name,
            "source_row": historical.source_row.source_row,
            "name_conflict": Finding.LEGACY_NUMBER_NAME_CONFLICT
            in (historical.source_row.findings or ()),
            "composite": bool(historical.legacy_alt_number),
            "linked_number": text_of(historical.linked_participant, "participant_number"),
            "linked_name": text_of(historical.linked_participant, "name_ar"),
            "linked_by": person_name(historical.linked_by),
            "linked_at": historical.linked_at,
            "link_note_ar": historical.link_note_ar,
        }
        for historical in queryset[:PAGE]
    ]


def candidate_participants(*, actor: Any, query: str, request: Any = None) -> list[tuple[str, str]]:
    """
    Production participants matching a typed search — SUGGESTIONS ONLY.

    Deliberately driven by what the reviewer types rather than by the archive
    row, so the screen never presents a "best match" the reviewer might accept
    without reading. Given the eight numbers carrying two different names,
    a ranked suggestion would be a wrong answer wearing a confidence score.
    """
    policy.require(actor, Screen.MIGRATION, Action.EDIT, request=request)
    from apps.people.models import Participant

    typed = (query or "").strip()
    if len(typed) < 3:
        return []
    matches = Participant.objects.filter(name_ar__icontains=typed)[:20]
    return [(p.participant_number, f"{p.participant_number} — {p.name_ar}") for p in matches]


def sheet_choices(*, actor: Any, code: str, request: Any = None) -> list[tuple[str, str]]:
    """The sheets in one batch, for the filter."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    names = (
        MigrationRow.objects.filter(batch__code=code)
        .values_list("sheet_name", flat=True)
        .distinct()
        .order_by("sheet_name")
    )
    return [(name, name) for name in names]


def finding_choices() -> list[tuple[str, str]]:
    return [(value, str(label)) for value, label in Finding.choices]


def state_choices() -> list[tuple[str, str]]:
    return [(value, str(label)) for value, label in RowState.choices]


__all__ = [
    "PAGE",
    "batch_detail",
    "candidate_participants",
    "finding_choices",
    "historical_enrollment_for",
    "link_queue",
    "list_batches",
    "rows_of",
    "sheet_choices",
    "state_choices",
]


def historical_enrollment_for(*, actor: Any, source_row_id: int, request: Any = None) -> Any:
    """
    One archived enrolment, for the opening-balance screen to propose from.

    A read, and only a read. The archive still cannot import ``billing`` —
    A-04 makes sure of it — so the gateway between the two is opened from the
    ledger side by ``opening_balance_service`` calling in here, never by this
    app reaching out. That direction is the whole of Sprint 8D-1's boundary.
    """
    from apps.datamigration.models import HistoricalEnrollment

    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    return HistoricalEnrollment.objects.select_related(
        "batch", "participant", "source_row", "cohort"
    ).get(source_row_id=source_row_id)
