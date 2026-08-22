"""
The ministry file and the gate it controls (BR-013 … BR-016).

The demo printed "registration requires ministry approval" on the screen and
enforced it in no code path at all. That is not a UI defect: an enrolment
taken on an unapproved cohort is an illegal registration, and the participant
who paid for it has a receipt for a course the ministry never sanctioned.

``cohort_is_approved()`` is the single predicate the enrolment service calls,
so the gate has one implementation rather than one per caller.

Q-13 (attachment storage policy) is untouched here. BR-016 asks only whether
the two required documents EXIST, which ``core.Attachment`` already answers —
deciding retention, size limits or virus scanning is a separate question and
not one this sprint needs answered.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models.attachment import AttachmentPurpose
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.operations.models import Cohort, MoheStatus, MoheSubmission
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.MoheSubmission"

#: BR-015 — how many days before the deadline the warning starts. Seeded in
#: Sprint 1; the number is the centre's to change, not this module's.
DEADLINE_ALERT_KEY = "mohe_deadline_alert_days"

#: BR-016 — the two documents the ministry will not accept a file without.
REQUIRED_ATTACHMENTS = (AttachmentPurpose.TRAINER_CV, AttachmentPurpose.ENTITY_LICENSE)


class MoheAttachmentsMissingError(ValidationError):
    """Raised when a submission is sent without the two mandatory PDFs (BR-016)."""


class CohortNotApprovedError(ValidationError):
    """Raised when something is attempted on a cohort the ministry has not approved."""


def approved_submission_for(cohort: Any) -> MoheSubmission | None:
    """
    The cohort's live approval, or None.

    At most one can exist — ``operations_mohe_one_approval_per_cohort``
    guarantees it at the database level, so this is not a "first match wins"
    that could quietly pick between two contradictory approvals.
    """
    return MoheSubmission.objects.filter(cohort=cohort, status=MoheStatus.APPROVED).first()


def cohort_is_approved(cohort: Any) -> bool:
    """BR-013 — the gate, in one place."""
    return approved_submission_for(cohort) is not None


def missing_attachments(submission: MoheSubmission) -> list[str]:
    """Which of the two required documents are not attached yet (BR-016)."""
    from apps.core.models import Attachment

    content_type = ContentType.objects.get_for_model(MoheSubmission)
    present = set(
        Attachment.objects.filter(
            content_type=content_type, object_id=str(submission.pk)
        ).values_list("purpose", flat=True)
    )
    return [purpose for purpose in REQUIRED_ATTACHMENTS if purpose not in present]


def create_submission(
    *, actor: Any, cohort: Cohort, data: dict[str, Any], request: Any = None
) -> MoheSubmission:
    """Open a DRAFT file. Saving a draft is deliberately unconditional."""
    policy.require(actor, Screen.MOHE_SUBMIT, Action.CREATE, request=request)
    return _create_submission(actor=actor, cohort=cohort, data=data, request=request)


@transaction.atomic
def _create_submission(
    *, actor: Any, cohort: Cohort, data: dict[str, Any], request: Any
) -> MoheSubmission:
    submission = MoheSubmission.objects.create(
        cohort=cohort, status=MoheStatus.DRAFT, created_by=actor, **data
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(submission.pk),
        reference=cohort.code,
        summary_ar=f"فتح ملف وزاري للدفعة {cohort.code}",
        actor=actor,
        request=request,
    )
    return submission


def submit_to_mohe(
    *, actor: Any, submission: MoheSubmission, submitted_on: date, request: Any = None
) -> MoheSubmission:
    """
    BR-016 — send the file, but only with both documents attached.

    A draft may be saved incomplete; it is SENDING that is gated. The
    distinction matters because the file is assembled over days and the
    ministry rejects an incomplete one outright.

    Sending requires APPROVE, not EDIT: PERMISSIONS.md §3.3 footnote ⁹ gives
    the registration officer the draft and reserves the conversion to
    SUBMITTED for the centre manager. The file leaves the building with the
    centre's name on it.
    """
    policy.require(actor, Screen.MOHE_SUBMIT, Action.APPROVE, request=request)

    if submission.status != MoheStatus.DRAFT:
        raise ValidationError("لا يُرسَل إلا ملف بحالة مسودة.")

    missing = missing_attachments(submission)
    if missing:
        labels = "، ".join(str(AttachmentPurpose(p).label) for p in missing)
        raise MoheAttachmentsMissingError(
            f"لا يُرسَل الطلب بلا المرفقين الإلزاميين — الناقص: {labels} (BR-016)."
        )

    return _submit(actor=actor, submission=submission, submitted_on=submitted_on, request=request)


@transaction.atomic
def _submit(
    *, actor: Any, submission: MoheSubmission, submitted_on: date, request: Any
) -> MoheSubmission:
    submission.status = MoheStatus.SUBMITTED
    submission.submitted_on = submitted_on
    submission.save()

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(submission.pk),
        reference=submission.cohort.code,
        summary_ar=f"إرسال الطلب الوزاري للدفعة {submission.cohort.code}",
        actor=actor,
        changes={"submitted_on": submitted_on.isoformat()},
        request=request,
    )
    return submission


def record_decision(
    *,
    actor: Any,
    submission: MoheSubmission,
    approved: bool,
    decided_on: date,
    mohe_course_number: str = "",
    registration_deadline: date | None = None,
    rejection_reason_ar: str = "",
    request: Any = None,
) -> MoheSubmission:
    """
    BR-014 — record what the ministry actually said.

    The rejection reason is stored VERBATIM. A summary written from memory is
    not what a resubmission has to answer, and the reason is the only guide to
    what must change.
    """
    policy.require(actor, Screen.MOHE, Action.APPROVE, request=request)

    if submission.status != MoheStatus.SUBMITTED:
        raise ValidationError("لا يُسجَّل قرار إلا على طلب مُرسَل.")
    if approved and not mohe_course_number.strip():
        raise ValidationError("الاعتماد يتطلب الرقم الوزاري (C-16).")
    if not approved and not rejection_reason_ar.strip():
        raise ValidationError("الرفض يتطلب تسجيل سبب الوزارة نصاً (BR-014).")

    return _record_decision(
        actor=actor,
        submission=submission,
        approved=approved,
        decided_on=decided_on,
        mohe_course_number=mohe_course_number.strip(),
        registration_deadline=registration_deadline,
        rejection_reason_ar=rejection_reason_ar.strip(),
        request=request,
    )


@transaction.atomic
def _record_decision(
    *,
    actor: Any,
    submission: MoheSubmission,
    approved: bool,
    decided_on: date,
    mohe_course_number: str,
    registration_deadline: date | None,
    rejection_reason_ar: str,
    request: Any,
) -> MoheSubmission:
    from apps.operations.models import CohortStatus

    submission.status = MoheStatus.APPROVED if approved else MoheStatus.REJECTED
    submission.decided_on = decided_on
    submission.mohe_course_number = mohe_course_number
    submission.registration_deadline = registration_deadline
    submission.rejection_reason_ar = rejection_reason_ar
    submission.save()

    cohort = submission.cohort
    cohort.status = CohortStatus.PLANNED if approved else CohortStatus.MOHE_REJECTED
    cohort.save(update_fields=["status"])

    write_audit(
        action="APPROVE" if approved else "REJECT",
        entity_type=ENTITY,
        entity_id=str(submission.pk),
        reference=submission.cohort.code,
        summary_ar=(f"اعتماد وزاري — رقم {mohe_course_number}" if approved else "رفض وزاري"),
        actor=actor,
        changes={
            "decided_on": decided_on.isoformat(),
            "mohe_course_number": mohe_course_number,
            "registration_deadline": (
                registration_deadline.isoformat() if registration_deadline else None
            ),
            "rejection_reason": rejection_reason_ar,
        },
        request=request,
    )
    return submission


def resubmit(
    *, actor: Any, rejected: MoheSubmission, data: dict[str, Any], request: Any = None
) -> MoheSubmission:
    """
    Open a fresh file answering a rejection, linked to the one it replaces.

    A new row rather than an edit: the rejection and its reason stay readable,
    which is the record of what the ministry objected to and what changed.
    """
    policy.require(actor, Screen.MOHE_SUBMIT, Action.CREATE, request=request)

    if rejected.status != MoheStatus.REJECTED:
        raise ValidationError("إعادة الإرسال لا تكون إلا لطلب مرفوض.")

    submission = _create_submission(actor=actor, cohort=rejected.cohort, data=data, request=request)
    submission.resubmission_of = rejected
    submission.save(update_fields=["resubmission_of"])
    return submission


# ---------------------------------------------------------------------------
# The read layer the screens need (Sprint 8G)
# ---------------------------------------------------------------------------
#: The seven fields the ministry's own form asks for (BR-014). Named once so
#: the editor, the detail page and the resubmission all copy the same set.
CONTENT_FIELDS: tuple[str, ...] = (
    "training_axes_ar",
    "practical_aspects_ar",
    "target_audience_ar",
    "trainer_name",
    "trainer_qualifications",
    "training_location",
    "responsible_entity",
)


def _row(submission: MoheSubmission) -> dict[str, Any]:
    cohort = submission.cohort
    return {
        "id": submission.pk,
        "cohort_code": cohort.code,
        "cohort_name_ar": cohort.name_ar,
        "program_code": cohort.program.code,
        "program_name_ar": cohort.program.name_ar,
        "starts_on": cohort.starts_on,
        "status": submission.status,
        "status_display": submission.get_status_display(),
        "mohe_course_number": submission.mohe_course_number,
        "submitted_on": submission.submitted_on,
        "decided_on": submission.decided_on,
        "registration_deadline": submission.registration_deadline,
        "rejection_reason_ar": submission.rejection_reason_ar,
        "resubmission_of": submission.resubmission_of_id,
        "created_at": submission.created_at,
    }


def list_submissions(
    *, actor: Any, status: str = "", query: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """
    The ministry files as rows (§3.3/14).

    Every status, including the rejected ones. A rejection is the record of
    what the ministry objected to and the only guide to what a resubmission
    must change (BR-014) — filtering it out of the default view would hide the
    thing the screen exists to act on.
    """
    policy.require(actor, Screen.MOHE, Action.VIEW, request=request)

    queryset = MoheSubmission.objects.select_related("cohort__program", "resubmission_of")
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(cohort__code__icontains=query) | queryset.filter(
            cohort__name_ar__icontains=query
        )

    return [_row(s) for s in queryset.order_by("-created_at")]


def get_submission(*, actor: Any, submission_id: int, request: Any = None) -> dict[str, Any]:
    """
    One file, with its documents and what is still missing.

    ``missing`` is the BR-016 answer the send button turns on, computed here
    rather than in the template: a screen that decided for itself which
    documents were required would be a second copy of the rule.
    """
    from apps.core.services import attachment_service

    policy.require(actor, Screen.MOHE, Action.VIEW, request=request)

    submission = MoheSubmission.objects.select_related(
        "cohort__program", "resubmission_of", "created_by"
    ).get(pk=submission_id)

    missing = missing_attachments(submission)
    detail = _row(submission)
    detail.update(
        {
            "content": {field: getattr(submission, field) for field in CONTENT_FIELDS},
            "attachments": attachment_service.attachments_for(submission),
            "missing_attachments": [
                {"purpose": p, "label": str(AttachmentPurpose(p).label)} for p in missing
            ],
            "is_sendable": submission.status == MoheStatus.DRAFT and not missing,
            "is_decidable": submission.status == MoheStatus.SUBMITTED,
            "is_resubmittable": submission.status == MoheStatus.REJECTED,
            "required_purposes": [
                {"purpose": p, "label": str(AttachmentPurpose(p).label)}
                for p in REQUIRED_ATTACHMENTS
            ],
            "created_by": getattr(submission.created_by, "full_name_ar", "")
            or submission.created_by.get_username(),
            "resubmissions": [r.pk for r in submission.resubmissions.order_by("pk")],
        }
    )
    return detail


def submission_instance(*, actor: Any, submission_id: int, request: Any = None) -> MoheSubmission:
    """The model object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.MOHE, Action.VIEW, request=request)
    return MoheSubmission.objects.select_related("cohort").get(pk=submission_id)


def submittable_cohort_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    (code, label) pairs of cohorts that still need a ministry file.

    Excluded: a cohort already approved — the unique key allows one approval
    and a second file could only ever be refused — and one whose file is
    already sitting with the ministry, because two open files for the same
    cohort is not a state anybody wants to explain.

    A cohort whose file was REJECTED comes back onto this list. ``resubmit``
    is the better route for it, because the new file then carries a link back
    to the rejection it answers — but a rejection is not a dead end, and
    refusing to let anyone start again from here would make it one.
    """
    policy.require(actor, Screen.MOHE_SUBMIT, Action.VIEW, request=request)

    busy = set(
        MoheSubmission.objects.filter(
            status__in=(MoheStatus.DRAFT, MoheStatus.SUBMITTED, MoheStatus.APPROVED)
        ).values_list("cohort_id", flat=True)
    )
    return [
        (c.code, f"{c.code} — {c.name_ar}")
        for c in Cohort.objects.select_related("program").order_by("-starts_on")
        if c.pk not in busy
    ]


def attach_document(
    *,
    actor: Any,
    submission: MoheSubmission,
    purpose: str,
    upload: Any,
    request: Any = None,
) -> Any:
    """
    Attach one of the ministry's required documents (BR-016).

    ``EDIT`` on §3.3/15, which is the cell the registration officer holds:
    footnote ⁹ gives them the draft and its contents and reserves SENDING for
    the centre manager. Assembling the file is the drafting work.

    Refused once the file has left the building. The ministry decided on the
    documents it received, and a system that let the set change afterwards
    could not answer what was actually sent.
    """
    from apps.core.services import attachment_service

    policy.require(actor, Screen.MOHE_SUBMIT, Action.EDIT, request=request)

    if submission.status != MoheStatus.DRAFT:
        raise ValidationError(
            f"الطلب بحالة {submission.get_status_display()} — لا تُعدَّل مرفقات ملف غادر المركز."
        )

    return attachment_service.attach(
        actor=actor, target=submission, purpose=purpose, upload=upload, request=request
    )


def deadline_alerts(*, as_of: date) -> list[dict[str, Any]]:
    """
    BR-015 — approved cohorts whose registration window is closing or closed.

    Returns data rather than sending anything: the daily command prints it,
    a dashboard will render it, and neither needs its own copy of the rule.
    """
    alert_days = int(get_setting(DEADLINE_ALERT_KEY, as_of=as_of, default=15))
    horizon = as_of + timedelta(days=alert_days)

    submissions = (
        MoheSubmission.objects.filter(
            status=MoheStatus.APPROVED,
            registration_deadline__isnull=False,
            registration_deadline__lte=horizon,
        )
        .select_related("cohort")
        .order_by("registration_deadline")
    )

    alerts: list[dict[str, Any]] = []
    for submission in submissions:
        deadline = submission.registration_deadline
        if deadline is None:  # pragma: no cover - excluded by the filter
            continue
        days_left = (deadline - as_of).days
        alerts.append(
            {
                "cohort_code": submission.cohort.code,
                "cohort_name_ar": submission.cohort.name_ar,
                "deadline": deadline,
                "days_left": days_left,
                # Past the deadline is not a louder warning of the same thing:
                # BR-019 stops accepting new names entirely.
                "severity": "EXPIRED" if days_left < 0 else "WARNING",
            }
        )
    return alerts


__all__ = [
    "CONTENT_FIELDS",
    "DEADLINE_ALERT_KEY",
    "REQUIRED_ATTACHMENTS",
    "CohortNotApprovedError",
    "MoheAttachmentsMissingError",
    "approved_submission_for",
    "attach_document",
    "cohort_is_approved",
    "create_submission",
    "deadline_alerts",
    "get_submission",
    "list_submissions",
    "missing_attachments",
    "record_decision",
    "resubmit",
    "submission_instance",
    "submit_to_mohe",
    "submittable_cohort_choices",
]
