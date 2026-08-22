"""
Linking an archived name to a living participant — the registrar's act.

**Why this cannot be automatic.** Thirteen legacy numbers appear in more than
one row, which is ordinary: a student sits several cohorts. But eight numbers
carry two different names. 20195073 is «براءه حسام محمود» in one sheet and
«تمارا عامر الشيب» in another; 20205042 is «محمد زكريا الشيخ» and «نور نعيم
الحمد». One of those pairs is a typo and one is two people, and nothing in the
data says which. A matcher would pick one and be silently wrong about the
other — and the wrong answer here merges two human beings' records.

So the link is made by a person, on a screen, with a reason, and the audit
entry carries their name. ``Action.EDIT`` on ``Screen.MIGRATION`` belongs to
the REGISTRATION_OFFICER in the matrix precisely because this is an identity
judgement rather than a data-entry step or a financial one — the registrar
knows the participants, and the finance officer is deliberately left with
VIEW only.

**Linking is not migrating.** It records that an archive row and a production
participant refer to the same person. It copies nothing: no number, no money,
no enrolment. ``participant_number`` keeps its nine-digit CHECK and never sees
a legacy value, which is the entire reason ``legacy_number`` lives on this
side of the boundary.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.display import text_of
from apps.core.services.audit_service import write_audit
from apps.datamigration.models import Finding, HistoricalParticipant, MigrationRow, RowState
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "datamigration.HistoricalParticipant"


class UnidentifiedRowError(Exception):
    """A row with no usable number has nobody to link."""


class AlreadyLinkedError(Exception):
    """The archive row already names a participant."""


def link(
    *,
    actor: Any,
    historical: HistoricalParticipant,
    participant: Any,
    note_ar: str,
    request: Any = None,
) -> HistoricalParticipant:
    """
    Confirm that this archived name and this participant are one person.

    ``note_ar`` is required. A link with no stated reason is an identity
    decision nobody can be asked about later, and where the number carries a
    name conflict it is the only record of why the reviewer chose as they did.
    """
    policy.require(actor, Screen.MIGRATION, Action.EDIT, request=request)

    if historical.linked_participant_id is not None:
        existing = text_of(historical.linked_participant, "participant_number")
        raise AlreadyLinkedError(f"الصف مرتبط سلفاً بالمشارك {existing}.")
    if not note_ar.strip():
        raise ValidationError("مسوّغ الربط إلزامي — الربط قرار هوية، ومن يراجعه لاحقاً يحتاج سببه.")

    return _apply_link(
        actor=actor,
        historical=historical,
        participant=participant,
        note_ar=note_ar,
        request=request,
    )


@transaction.atomic
def _apply_link(
    *,
    actor: Any,
    historical: HistoricalParticipant,
    participant: Any,
    note_ar: str,
    request: Any,
) -> HistoricalParticipant:
    """
    The write half.

    Permission is decided by the caller, OUTSIDE this transaction: a refused
    link writes a DENIED_ATTEMPT row and raises, and a rollback here would
    erase the only record that somebody tried (BR-085).
    """
    conflicted = Finding.LEGACY_NUMBER_NAME_CONFLICT in (historical.source_row.findings or ())

    historical.linked_participant = participant
    historical.linked_by = actor
    historical.linked_at = timezone.now()
    historical.link_note_ar = note_ar.strip()[:255]
    historical.save(update_fields=["linked_participant", "linked_by", "linked_at", "link_note_ar"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(historical.pk),
        reference=historical.legacy_number,
        summary_ar=(
            f"ربط سجل تاريخي {historical.legacy_number} بالمشارك {participant.participant_number}"
        ),
        actor=actor,
        changes={
            "legacy_number": historical.legacy_number,
            "legacy_name": historical.name_ar,
            "participant_number": participant.participant_number,
            "participant_name": participant.name_ar,
            "note_ar": historical.link_note_ar,
            "name_conflict_present": "نعم" if conflicted else "لا",
            "effect": "ربط هوية فقط — لا نقل أرقام ولا مبالغ",
        },
        request=request,
    )
    return historical


def unlink(
    *, actor: Any, historical: HistoricalParticipant, note_ar: str, request: Any = None
) -> HistoricalParticipant:
    """
    Undo a link that was wrong.

    Available because the alternative is worse: a reviewer who cannot correct
    a mistaken link will avoid linking at all. The correction is itself
    audited, so the record shows both the link and its withdrawal rather than
    a gap where a decision used to be.
    """
    policy.require(actor, Screen.MIGRATION, Action.EDIT, request=request)

    if historical.linked_participant_id is None:
        raise ValidationError("الصف غير مرتبط أصلاً.")
    if not note_ar.strip():
        raise ValidationError("سبب فكّ الربط إلزامي.")

    return _apply_unlink(actor=actor, historical=historical, note_ar=note_ar, request=request)


@transaction.atomic
def _apply_unlink(
    *, actor: Any, historical: HistoricalParticipant, note_ar: str, request: Any
) -> HistoricalParticipant:
    """The write half — permission already decided by the caller (BR-085)."""
    previous = text_of(historical.linked_participant, "participant_number")
    historical.linked_participant = None
    historical.linked_by = None
    historical.linked_at = None
    historical.link_note_ar = note_ar.strip()[:255]
    historical.save(update_fields=["linked_participant", "linked_by", "linked_at", "link_note_ar"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(historical.pk),
        reference=historical.legacy_number,
        summary_ar=f"فُكّ ربط السجل التاريخي {historical.legacy_number} عن {previous}",
        actor=actor,
        changes={"previous_participant": previous, "note_ar": historical.link_note_ar},
        request=request,
    )
    return historical


def linkable_row(*, actor: Any, row_id: int, request: Any = None) -> MigrationRow:
    """
    Guard for the UNIDENTIFIED case, stated where a caller will meet it.

    A row with no usable number has no ``HistoricalParticipant`` at all, so
    there is nothing to link — the object simply does not exist. Raising here
    with an explanation beats a bare ``DoesNotExist`` from the view.
    """
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    row = MigrationRow.objects.get(pk=row_id)
    if row.state == RowState.UNIDENTIFIED:
        raise UnidentifiedRowError("هذا الصف بلا رقم جامعي صالح — محفوظ كما ورد ولا يُربط بمشارك.")
    return row


def historical_instance(
    *, actor: Any, historical_id: int, request: Any = None
) -> HistoricalParticipant:
    """The object, for handing back into ``link`` (A-05)."""
    policy.require(actor, Screen.MIGRATION, Action.VIEW, request=request)
    return HistoricalParticipant.objects.select_related(
        "source_row", "linked_participant", "batch"
    ).get(pk=historical_id)


__all__ = [
    "AlreadyLinkedError",
    "UnidentifiedRowError",
    "historical_instance",
    "link",
    "linkable_row",
    "unlink",
]
