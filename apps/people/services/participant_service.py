"""
Participant service — the only write path to people.Participant.

Two things in here are load-bearing beyond the obvious CRUD:

**BR-101 — the matrix cell is not the whole permission.** PERMISSIONS.md §3.2
grants the cashier ``V`` on participants, and footnote 3 then narrows it to
name, number and outstanding balance "inside the cash-collection context". The
narrowing is enforced HERE, by projecting the row down to an allowed field set
before it ever reaches a template. Doing it in the template would mean the
phone number and identity document are one direct call away.

**BR-100 — check permission before opening the transaction.** A refusal writes
a DENIED_ATTEMPT row and then raises; inside an atomic block the rollback would
erase the very record of the refusal. The static guards from Sprint 2A fail the
build if this is forgotten.
"""

from __future__ import annotations

import random
import time
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.exceptions import ConcurrencyRetryExhausted
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.people.constants import Action, Screen
from apps.people.models import Participant, ParticipantCategory, Role
from apps.people.permissions import policy
from apps.people.services import participant_numbering

ENTITY = "people.Participant"

#: MySQL deadlock and lock-wait timeout — the two worth retrying.
RETRYABLE_LOCK_ERRORS = {1213, 1205}
MAX_CREATE_ATTEMPTS = 6
_BACKOFF_SECONDS = 0.005

#: BR-005 — "WARN" surfaces the matching row and lets the user proceed with a
#: recorded reason; "BLOCK" refuses outright. WARN in v1 because the Sprint 8
#: archive will legitimately contain duplicates that must still be importable.
UNIQUENESS_MODE_KEY = "identity_document_uniqueness_mode"

#: BR-101 — the full field set, for the roles the matrix grants it to.
FULL_FIELDS: tuple[str, ...] = (
    "participant_number",
    "category",
    "name_ar",
    "name_en",
    "id_document_type",
    "id_document_number",
    "nationality",
    "gender",
    "date_of_birth",
    "qualification",
    "city",
    "phone",
    "po_box",
    "email",
    "employer",
    "registered_on",
    "no_refund_pledge_accepted",
    "no_refund_pledge_at",
    "is_exempt",
    "exemption_approval_ref",
    "exemption_approval_date",
)

#: BR-101 — cashier (footnote 3) and finance manager (footnote g).
#: The documented set also includes the outstanding balance, which does not
#: exist until Sprint 4; it is added there, not faked here.
RESTRICTED_FIELDS: tuple[str, ...] = ("participant_number", "name_ar")

#: Roles whose participant view is narrowed to RESTRICTED_FIELDS.
RESTRICTED_ROLES = frozenset({Role.CASHIER, Role.FINANCE_MANAGER})


def visible_fields_for(user: Any) -> tuple[str, ...]:
    """The field set this role may see on a participant (BR-101)."""
    role = getattr(user, "role", "") or ""
    return RESTRICTED_FIELDS if role in RESTRICTED_ROLES else FULL_FIELDS


def project(participant: Participant, user: Any) -> dict[str, Any]:
    """
    Reduce a participant to what ``user`` is allowed to see.

    Views render THIS, never the model instance, so a field the role may not
    see is absent from the response body — not merely hidden in the page
    (T-283).
    """
    return {name: getattr(participant, name) for name in visible_fields_for(user)}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_participants(
    *, actor: Any, query: str = "", category: str = "", request: Any = None
) -> list[dict[str, Any]]:
    policy.require(actor, Screen.STUDENTS, Action.VIEW, request=request)

    visible = visible_fields_for(actor)

    rows = Participant.objects.all()
    # A filter narrows the list by a field, which reports that field as surely
    # as printing it: three requests for three categories read the category of
    # every row back. So a field the projection withholds cannot be filtered on
    # either, and the parameter is ignored rather than obeyed — the reader gets
    # the same set with it and without it, and learns nothing from the
    # difference. This is the search guard below, applied to the other way in.
    if category and "category" in visible:
        rows = rows.filter(category=category)
    if query:
        # Restricted roles may not search by the fields they cannot see —
        # otherwise the search box becomes an oracle for the hidden data.
        if getattr(actor, "role", "") in RESTRICTED_ROLES:
            rows = rows.filter(participant_number__startswith=query)
        else:
            rows = (
                rows.filter(name_ar__icontains=query)
                | rows.filter(participant_number__startswith=query)
                | rows.filter(phone__startswith=query)
                | rows.filter(id_document_number__startswith=query)
            )
    return [project(row, actor) for row in rows.distinct()[:200]]


def get_participant(*, actor: Any, participant_number: str, request: Any = None) -> dict[str, Any]:
    policy.require(actor, Screen.STUDENTS, Action.VIEW, request=request)
    participant = Participant.objects.get(participant_number=participant_number)
    return project(participant, actor)


def field_labels() -> dict[str, str]:
    """Arabic labels for the projected keys, taken from the model itself."""
    return {
        f.name: str(f.verbose_name)
        for f in Participant._meta.get_fields()
        if hasattr(f, "verbose_name")
    }


def get_participant_display(
    *, actor: Any, participant_number: str, request: Any = None
) -> list[tuple[str, str, Any]]:
    """
    (key, Arabic label, value) triples for the detail screen.

    Built here rather than in the template because the template cannot look a
    label up by a variable key — and because the projection must stay in one
    place, where it can be tested (T-283).
    """
    policy.require(actor, Screen.STUDENTS, Action.VIEW, request=request)
    participant = Participant.objects.get(participant_number=participant_number)
    labels = field_labels()
    return [
        (name, labels.get(name, name), _readable(participant, name))
        for name in visible_fields_for(actor)
    ]


def _readable(participant: Participant, name: str) -> Any:
    """
    The value as a person reads it, not as the column stores it.

    The detail screen was printing ``UNIVERSITY``, ``NATIONAL_ID`` and
    ``True`` at an Arabic-speaking registrar — the codes are the storage, and
    the model already carries the Arabic for every one of them in its
    ``choices``. Django generates ``get_<field>_display`` for exactly this;
    booleans have no equivalent, so they get one here.

    Found in the Sprint 8I browser pass.
    """
    display = getattr(participant, f"get_{name}_display", None)
    if callable(display):
        return display()

    value = getattr(participant, name)
    if isinstance(value, bool):
        return _("نعم") if value else _("لا")
    return value


def get_editable(*, actor: Any, participant_number: str, request: Any = None) -> Participant:
    """
    The model instance, for an edit form.

    Separate from ``get_participant`` on purpose: that one returns a projected
    dict and is what every read path uses. This returns the full row and is
    reachable only after an EDIT check, which no restricted role passes.
    """
    policy.require(actor, Screen.STUDENTS, Action.EDIT, request=request)
    return Participant.objects.get(participant_number=participant_number)


def find_identity_duplicates(
    *, id_document_type: str, id_document_number: str, exclude_pk: Any = None
) -> list[Participant]:
    """BR-005 — the matching rows a warning must show."""
    rows = Participant.objects.filter(
        id_document_type=id_document_type, id_document_number=id_document_number
    )
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return list(rows)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def create_participant(
    *,
    actor: Any,
    data: dict[str, Any],
    duplicate_override_reason: str = "",
    request: Any = None,
) -> Participant:
    """
    Create a participant and allocate its permanent number.

    ``duplicate_override_reason`` is required only when an identity document
    already exists and the mode is WARN — BR-005 wants the override recorded,
    not merely permitted.
    """
    # BR-100 — outside the transaction, so a refusal survives.
    policy.require(actor, Screen.STUDENT_NEW, Action.CREATE, request=request)

    _validate(data)

    duplicates = find_identity_duplicates(
        id_document_type=data["id_document_type"],
        id_document_number=data["id_document_number"],
    )
    if duplicates:
        _handle_duplicates(actor, duplicates, duplicate_override_reason, data, request)

    # Both resolved before the transaction opens: the semester lookup so its
    # failure path never holds a lock, and the counter row so a brand-new
    # partition is committed before anyone locks it. A new partition appears at
    # the start of every term — the moment several clerks register at once.
    semester = participant_numbering.active_semester()
    participant_numbering.prepare_sequence(category=data["category"], semester=semester)

    return _create_with_retry(actor=actor, data=data, semester=semester, request=request)


def _create_with_retry(*, actor: Any, data: dict[str, Any], semester: Any, request: Any):
    """
    Run the creating transaction, retrying it whole on lock contention.

    The retry lives here rather than in the numbering service because a MySQL
    deadlock aborts the entire transaction: only the code that OPENED it can
    start a fresh one. Each attempt is self-contained — a lost attempt commits
    nothing and consumes no number, so retrying cannot skip a participant
    number or double-write an audit row.
    """
    last_error: OperationalError | None = None

    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with transaction.atomic():
                return _create_once(actor=actor, data=data, semester=semester, request=request)
        except OperationalError as exc:
            code = exc.args[0] if exc.args else None
            if code not in RETRYABLE_LOCK_ERRORS:
                raise
            last_error = exc
            time.sleep(_BACKOFF_SECONDS * (2**attempt) * (0.5 + random.random()))

    raise ConcurrencyRetryExhausted(
        f"تعذّر إنشاء المشارك بعد {MAX_CREATE_ATTEMPTS} محاولات بسبب ازدحام الأقفال."
    ) from last_error


def _create_once(*, actor: Any, data: dict[str, Any], semester: Any, request: Any):
    number = participant_numbering.next_participant_number(
        category=data["category"], semester=semester
    )
    participant = Participant(participant_number=number, **data)
    if participant.no_refund_pledge_accepted and participant.no_refund_pledge_at is None:
        participant.no_refund_pledge_at = timezone.now()
    participant.full_clean(exclude=["participant_number"])
    participant.save()

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(participant.pk),
        reference=number,
        summary_ar=f"إنشاء مشارك — {participant.name_ar} ({number})",
        actor=actor,
        changes={
            "category": participant.category,
            "sequence_scope": participant_numbering.SCOPE,
            "sequence_partition": number[:5],
            "semester": semester.code,
            "no_refund_pledge_at": (
                participant.no_refund_pledge_at.isoformat()
                if participant.no_refund_pledge_at
                else None
            ),
            "is_exempt": participant.is_exempt,
            "exemption_approval_ref": participant.exemption_approval_ref or None,
        },
        request=request,
    )
    return participant


def update_participant(
    *, actor: Any, participant: Participant, data: dict[str, Any], request: Any = None
) -> Participant:
    policy.require(actor, Screen.STUDENTS, Action.EDIT, request=request)

    _validate(data, updating=True)

    before = {field: getattr(participant, field) for field in data}

    with transaction.atomic():
        for field, value in data.items():
            setattr(participant, field, value)
        if participant.no_refund_pledge_accepted and participant.no_refund_pledge_at is None:
            participant.no_refund_pledge_at = timezone.now()
        participant.full_clean(exclude=["participant_number"])
        participant.save()

        changed = {
            field: {"from": _plain(before[field]), "to": _plain(getattr(participant, field))}
            for field in data
            if before[field] != getattr(participant, field)
        }
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(participant.pk),
            reference=participant.participant_number,
            summary_ar=f"تعديل بيانات المشارك {participant.participant_number}",
            actor=actor,
            changes=changed,
            request=request,
        )
    return participant


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """JSON-safe rendering for the audit `changes` payload."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _validate(data: dict[str, Any], *, updating: bool = False) -> None:
    """
    Business validation the database cannot express.

    The database still owns BR-003, BR-004 and C-19 as CHECK constraints; this
    is the layer that produces a readable Arabic message before MySQL produces
    an IntegrityError.
    """
    from apps.people.services import reference_data

    if not updating and not data.get("no_refund_pledge_accepted"):
        raise ValidationError({"no_refund_pledge_accepted": "يجب الإقرار بالتعهّد قبل الحفظ"})

    if data.get("is_exempt") and not (data.get("exemption_approval_ref") or "").strip():
        raise ValidationError({"exemption_approval_ref": "الإعفاء يتطلب رقم موافقة رئيس الجامعة"})

    if data.get("category") and data["category"] not in ParticipantCategory.values:
        raise ValidationError({"category": "فئة مشارك غير معروفة"})

    # Q-31 — validated against the effective-dated list, not a code constant.
    qualification = data.get("qualification") or ""
    if qualification and not reference_data.is_valid_qualification(qualification):
        raise ValidationError({"qualification": "مؤهل علمي غير معروف"})

    city = data.get("city") or ""
    if city and not reference_data.is_valid_city(city):
        raise ValidationError({"city": "مدينة غير معروفة"})


def _handle_duplicates(
    actor: Any,
    duplicates: list[Participant],
    reason: str,
    data: dict[str, Any],
    request: Any,
) -> None:
    """BR-005 — warn and record, or block, depending on the configured mode."""
    mode = str(get_setting(UNIQUENESS_MODE_KEY, as_of=timezone.localdate(), default="WARN")).upper()
    existing = ", ".join(p.participant_number for p in duplicates)

    if mode == "BLOCK":
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=ENTITY,
            reference=data["id_document_number"],
            summary_ar=f"محاولة إنشاء مشارك بوثيقة هوية مكررة — القائم: {existing}",
            actor=actor,
            denial_rule="BR-005",
            request=request,
        )
        raise PermissionDenied(f"وثيقة الهوية مسجَّلة سلفاً للمشارك: {existing}")

    if not reason.strip():
        # Not an error state — the caller must come back with a reason. Raised
        # as a validation error so the form can show the matching record.
        raise ValidationError(
            {
                "id_document_number": (
                    f"وثيقة الهوية مسجَّلة سلفاً للمشارك: {existing}. للمتابعة يلزم سبب موثّق."
                )
            }
        )

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        reference=data["id_document_number"],
        summary_ar=f"تجاوز تحذير تكرار وثيقة الهوية — {reason.strip()}",
        actor=actor,
        changes={"existing": existing, "reason": reason.strip()},
        request=request,
    )


def participant_instance(*, actor: Any, participant_number: str, request: Any = None) -> Any:
    """
    The Participant model object, for handing to another service.

    Distinct from :func:`get_participant`, which projects the record down to
    the fields the caller may see and is what a SCREEN should render. This one
    exists so a view can pass a participant into ``enroll_with_charges``
    without ever importing the model itself (A-05).
    """
    policy.require(actor, Screen.STUDENTS, Action.VIEW, request=request)
    return Participant.objects.get(participant_number=participant_number)


__all__ = [
    "FULL_FIELDS",
    "RESTRICTED_FIELDS",
    "RESTRICTED_ROLES",
    "UNIQUENESS_MODE_KEY",
    "create_participant",
    "field_labels",
    "find_identity_duplicates",
    "get_editable",
    "get_participant",
    "get_participant_display",
    "list_participants",
    "participant_instance",
    "project",
    "update_participant",
    "visible_fields_for",
]
