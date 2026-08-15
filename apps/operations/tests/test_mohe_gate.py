"""
The ministry gate — T-106 … T-113, BR-013 … BR-016.

🐞 The demo printed "registration requires ministry approval" on the screen
and enforced it in no code path at all. Every cohort appeared in every
dropdown regardless of status. That is not a UI defect: an enrolment taken on
an unapproved cohort is an illegal registration, and the participant holds a
receipt for a course the ministry never sanctioned.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import AuditEvent
from apps.operations.models import CohortStatus, MoheStatus, MoheSubmission
from apps.operations.services import enrollment_service, mohe_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
DEADLINE = date(2026, 10, 5)


# ---------------------------------------------------------------------------
# T-106 … T-108 — the gate itself
# ---------------------------------------------------------------------------
def test_enrolment_on_an_unapproved_cohort_is_refused(
    make_cohort, make_enrollment, registrar
) -> None:
    """T-106 — the demo's defect, now a refusal rather than a caption."""
    cohort = make_cohort()
    with pytest.raises(enrollment_service.CohortNotApprovedError):
        make_enrollment(cohort)


def test_the_refusal_leaves_a_denied_attempt_naming_the_rule(
    make_cohort, make_enrollment, registrar
) -> None:
    """
    T-106 / D-21 — a blocked illegal registration must be visible afterwards.

    The check runs before any transaction opens, so the audit row survives the
    raise (BR-100). Without that, the single most important refusal in the
    system would leave no trace.
    """
    cohort = make_cohort()
    with pytest.raises(enrollment_service.CohortNotApprovedError):
        make_enrollment(cohort)

    event = AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-21").first()
    assert event is not None
    assert cohort.code in event.summary_ar


def test_enrolment_on_a_rejected_cohort_is_refused(
    make_cohort, make_enrollment, registrar, manager, attach_required_documents
) -> None:
    """T-107 — a rejection is not a pending state to be worked around."""
    cohort = make_cohort()
    submission = mohe_service.create_submission(
        actor=registrar, cohort=cohort, data={"training_axes_ar": "محاور"}
    )
    attach_required_documents(submission, registrar)
    mohe_service.submit_to_mohe(actor=manager, submission=submission, submitted_on=date(2026, 9, 1))
    mohe_service.record_decision(
        actor=manager,
        submission=submission,
        approved=False,
        decided_on=date(2026, 9, 10),
        rejection_reason_ar="نقص في مؤهلات المدرب",
    )

    cohort.refresh_from_db()
    assert cohort.status == CohortStatus.MOHE_REJECTED
    with pytest.raises(enrollment_service.CohortNotApprovedError):
        make_enrollment(cohort)


def test_enrolment_succeeds_once_the_ministry_approves(
    make_cohort, approve_cohort, make_enrollment
) -> None:
    cohort = make_cohort()
    approve_cohort(cohort)
    enrollment = make_enrollment(cohort)

    assert enrollment.pk is not None
    assert enrollment.status == "PENDING_FINANCE"


def test_the_gate_has_one_implementation(make_cohort, approve_cohort) -> None:
    """
    Every caller asks the same predicate.

    The demo's failure was not a missing check in one place — it was the check
    living in no place while being described in several.
    """
    cohort = make_cohort()
    assert mohe_service.cohort_is_approved(cohort) is False
    approve_cohort(cohort)
    assert mohe_service.cohort_is_approved(cohort) is True


# ---------------------------------------------------------------------------
# T-109 · T-110 — what the database refuses
# ---------------------------------------------------------------------------
def test_an_approval_without_a_ministry_number_is_refused_by_the_database(
    make_cohort, registrar
) -> None:
    """
    T-109 / C-16 — an approval nobody can produce evidence for.

    The ministry number is how the approval is proved to an auditor; an
    APPROVED row without one asserts something unverifiable.
    """
    cohort = make_cohort()
    submission = MoheSubmission.objects.create(
        cohort=cohort, status=MoheStatus.DRAFT, created_by=registrar
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MoheSubmission.objects.filter(pk=submission.pk).update(
            status=MoheStatus.APPROVED, approved_key=1, decided_on=date(2026, 9, 10)
        )


def test_a_rejection_without_a_reason_is_refused_by_the_database(make_cohort, registrar) -> None:
    """T-110 — the reason is the only guide to what a resubmission must fix."""
    cohort = make_cohort()
    submission = MoheSubmission.objects.create(
        cohort=cohort, status=MoheStatus.DRAFT, created_by=registrar
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MoheSubmission.objects.filter(pk=submission.pk).update(
            status=MoheStatus.REJECTED, decided_on=date(2026, 9, 10)
        )


def test_a_cohort_cannot_hold_two_approvals(make_cohort, approve_cohort, registrar) -> None:
    """
    Two contradictory approvals would make "is this approved" unanswerable.

    MySQL does not collide NULLs, which is exactly what is wanted: the
    companion column holds 1 only while APPROVED, so drafts and rejections
    never conflict and a second approval cannot be stored.
    """
    cohort = make_cohort()
    approve_cohort(cohort)

    with pytest.raises(IntegrityError), transaction.atomic():
        MoheSubmission.objects.create(
            cohort=cohort,
            status=MoheStatus.APPROVED,
            approved_key=1,
            mohe_course_number="MOHE-2",
            decided_on=date(2026, 10, 1),
            created_by=registrar,
        )


def test_drafts_and_rejections_do_not_collide(make_cohort, registrar) -> None:
    """The uniqueness must not block the ordinary case of several attempts."""
    cohort = make_cohort()
    for index in range(3):
        MoheSubmission.objects.create(
            cohort=cohort,
            status=MoheStatus.DRAFT,
            created_by=registrar,
            training_axes_ar=f"محاولة {index}",
        )
    assert MoheSubmission.objects.filter(cohort=cohort).count() == 3


# ---------------------------------------------------------------------------
# T-111 — BR-016, the two mandatory PDFs
# ---------------------------------------------------------------------------
def test_a_submission_without_both_documents_cannot_be_sent(
    make_cohort, registrar, manager, attach_required_documents
) -> None:
    """
    T-111 / BR-016 — sending is gated, saving a draft is not.

    The file is assembled over days and the ministry rejects an incomplete one
    outright, so the distinction is the whole point.
    """
    from apps.core.models.attachment import AttachmentPurpose

    cohort = make_cohort()
    submission = mohe_service.create_submission(
        actor=registrar, cohort=cohort, data={"training_axes_ar": "محاور"}
    )
    assert submission.status == MoheStatus.DRAFT  # a draft saved regardless

    with pytest.raises(mohe_service.MoheAttachmentsMissingError):
        mohe_service.submit_to_mohe(
            actor=manager, submission=submission, submitted_on=date(2026, 9, 1)
        )

    # One of the two is still not enough.
    attach_required_documents(submission, registrar, [AttachmentPurpose.TRAINER_CV])
    with pytest.raises(mohe_service.MoheAttachmentsMissingError):
        mohe_service.submit_to_mohe(
            actor=manager, submission=submission, submitted_on=date(2026, 9, 1)
        )

    attach_required_documents(submission, registrar, [AttachmentPurpose.ENTITY_LICENSE])
    sent = mohe_service.submit_to_mohe(
        actor=manager, submission=submission, submitted_on=date(2026, 9, 1)
    )
    assert sent.status == MoheStatus.SUBMITTED


def test_the_missing_document_is_named(make_cohort, registrar) -> None:
    """ "Attachments missing" sends someone hunting; naming which one does not."""
    cohort = make_cohort()
    submission = mohe_service.create_submission(actor=registrar, cohort=cohort, data={})
    assert set(mohe_service.missing_attachments(submission)) == {
        "TRAINER_CV",
        "ENTITY_LICENSE",
    }


# ---------------------------------------------------------------------------
# BR-014 — the response is kept verbatim
# ---------------------------------------------------------------------------
def test_the_rejection_reason_is_stored_as_received(
    make_cohort, registrar, manager, attach_required_documents
) -> None:
    """BR-014 — a summary from memory is not what a resubmission must answer."""
    cohort = make_cohort()
    submission = mohe_service.create_submission(actor=registrar, cohort=cohort, data={})
    attach_required_documents(submission, registrar)
    mohe_service.submit_to_mohe(actor=manager, submission=submission, submitted_on=date(2026, 9, 1))
    verbatim = "محاور التدريب غير كافية للساعات المطلوبة — يُعاد تقديم الطلب بعد التعديل"
    mohe_service.record_decision(
        actor=manager,
        submission=submission,
        approved=False,
        decided_on=date(2026, 9, 10),
        rejection_reason_ar=verbatim,
    )
    submission.refresh_from_db()
    assert submission.rejection_reason_ar == verbatim


def test_a_resubmission_links_to_what_it_replaces(
    make_cohort, registrar, manager, attach_required_documents
) -> None:
    """
    A new row, not an edit.

    The rejection and its reason stay readable, so what the ministry objected
    to and what changed are both part of the record.
    """
    cohort = make_cohort()
    first = mohe_service.create_submission(actor=registrar, cohort=cohort, data={})
    attach_required_documents(first, registrar)
    mohe_service.submit_to_mohe(actor=manager, submission=first, submitted_on=date(2026, 9, 1))
    mohe_service.record_decision(
        actor=manager,
        submission=first,
        approved=False,
        decided_on=date(2026, 9, 10),
        rejection_reason_ar="نقص",
    )

    second = mohe_service.resubmit(
        actor=registrar, rejected=first, data={"training_axes_ar": "محاور موسّعة"}
    )
    assert second.resubmission_of_id == first.pk
    first.refresh_from_db()
    assert first.status == MoheStatus.REJECTED
    assert first.rejection_reason_ar == "نقص"


def test_a_decision_requires_a_sent_submission(make_cohort, registrar, manager) -> None:
    cohort = make_cohort()
    draft = mohe_service.create_submission(actor=registrar, cohort=cohort, data={})
    with pytest.raises(ValidationError):
        mohe_service.record_decision(
            actor=manager,
            submission=draft,
            approved=True,
            decided_on=date(2026, 9, 10),
            mohe_course_number="X",
        )


# ---------------------------------------------------------------------------
# T-112 — BR-015, the deadline alert
# ---------------------------------------------------------------------------
def test_the_alert_window_follows_the_setting(make_cohort, approve_cohort) -> None:
    """
    T-112 — the alert horizon is the centre's number, not the code's.

    Fourteen days out: visible when the setting says 20, invisible when it
    says 10. No value is hardcoded anywhere in the rule.
    """
    from apps.core.models import EffectiveSetting, SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    cohort = make_cohort()
    approve_cohort(cohort, deadline=date(2026, 10, 5))
    as_of = date(2026, 9, 21)  # fourteen days before the deadline

    # Each change closes the open period and opens the next one a day later,
    # the way a dated setting actually moves — periods must not overlap.
    clock = {"day": 1}

    def _set(days: int) -> None:
        if EffectiveSetting.objects.filter(key=mohe_service.DEADLINE_ALERT_KEY).exists():
            close_setting(
                mohe_service.DEADLINE_ALERT_KEY,
                effective_to=date(2026, 1, clock["day"]),
            )
        clock["day"] += 1
        set_setting(
            mohe_service.DEADLINE_ALERT_KEY,
            days,
            value_type=SettingValueType.INTEGER,
            effective_from=date(2026, 1, clock["day"]),
            note="اختبار",
        )

    _set(20)
    assert [a["cohort_code"] for a in mohe_service.deadline_alerts(as_of=as_of)] == [cohort.code]

    _set(10)
    assert mohe_service.deadline_alerts(as_of=as_of) == []


def test_a_passed_deadline_is_reported_differently(make_cohort, approve_cohort) -> None:
    """
    Past the deadline is not a louder warning of the same thing.

    BR-019 stops accepting new names entirely, so the two states are given
    different severities rather than a shared "urgent".
    """
    cohort = make_cohort()
    approve_cohort(cohort, deadline=date(2026, 10, 5))

    alerts = mohe_service.deadline_alerts(as_of=date(2026, 10, 20))
    assert alerts[0]["severity"] == "EXPIRED"
    assert alerts[0]["days_left"] == -15


def test_an_unapproved_cohort_raises_no_deadline_alert(make_cohort) -> None:
    """There is no registration window to be running out of."""
    make_cohort()
    assert mohe_service.deadline_alerts(as_of=date(2026, 10, 1)) == []
