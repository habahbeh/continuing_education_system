"""
The enrolment lifecycle — T-114 … T-118, T-239 … T-245.

BR-018 (no approval without a voucher) is held twice: as a readable service
message and as a database constraint. The constraint is the one that matters,
because it survives a caller that forgets to ask.

The attendance counter (Q-06 / BR-095) gets its own section. It decides who
pays a transfer difference and can decide what a partner earns, so a number
typed without a source, a verifier and a timestamp is not storable at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.operations.models import (
    AttendanceSource,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStatusHistory,
)
from apps.operations.services import enrollment_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
DEADLINE = date(2026, 10, 5)


@pytest.fixture
def approved_cohort(make_cohort, approve_cohort):
    cohort = make_cohort()
    approve_cohort(cohort)
    return cohort


# ---------------------------------------------------------------------------
# T-114 · T-115 — BR-018, the voucher gate
# ---------------------------------------------------------------------------
def test_approval_without_a_voucher_is_refused_by_the_database(
    approved_cohort, make_enrollment, manager
) -> None:
    """
    T-114 / C-13 — an enrolment approved with no evidence anything was paid.

    Asserted against the DATABASE, not the service: the service check is the
    readable message, and this is what holds when a caller skips it.
    """
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.filter(pk=enrollment.pk).update(
            approved_by=manager, approved_at=timezone.now()
        )


def test_the_service_refuses_first_with_a_readable_message(
    approved_cohort, make_enrollment, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(enrollment_service.VoucherRequiredError):
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)


def test_recording_the_voucher_stamps_who_and_when(
    approved_cohort, make_enrollment, registrar
) -> None:
    """T-115 — the timestamp is a constraint, not a convention."""
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert enrollment.voucher_received is True
    assert enrollment.voucher_received_at is not None
    assert enrollment.voucher_received_by_id == registrar.pk


def test_a_voucher_flag_without_a_timestamp_is_refused(approved_cohort, make_enrollment) -> None:
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.filter(pk=enrollment.pk).update(voucher_received=True)


def test_approval_succeeds_once_the_voucher_is_recorded(
    approved_cohort, make_enrollment, registrar, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert enrollment.approved_by_id == manager.pk


# ---------------------------------------------------------------------------
# T-116 — Q-19
# ---------------------------------------------------------------------------
def test_the_same_participant_cannot_enrol_twice_on_one_cohort(
    approved_cohort, make_enrollment, make_participant, priced_catalog, registrar
) -> None:
    """T-116 / Q-19 — one enrolment per participant per cohort."""
    participant = make_participant(1)
    make_enrollment(approved_cohort, index=1, participant=participant)

    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.create(
            code="EN-DUP",
            participant=participant,
            cohort=approved_cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
        )


def test_the_same_participant_may_enrol_on_a_different_cohort(
    make_cohort, approve_cohort, make_enrollment, make_participant
) -> None:
    """
    Q-19 — multiple enrolments are the ordinary case, not the exception.

    A participant taking a diploma and a short course is normal; the
    uniqueness is per cohort, not per person.
    """
    first = make_cohort("SC-NET", code="CO-A")
    second = make_cohort("SC-CMA", code="CO-B")
    approve_cohort(first, course_number="MOHE-A")
    approve_cohort(second, course_number="MOHE-B")

    participant = make_participant(1)
    make_enrollment(first, index=1, participant=participant)
    make_enrollment(second, index=2, participant=participant)

    assert Enrollment.objects.filter(participant=participant).count() == 2


# ---------------------------------------------------------------------------
# T-117 · T-118 — the status history
# ---------------------------------------------------------------------------
def test_every_transition_writes_a_history_row(
    approved_cohort, make_enrollment, registrar, manager
) -> None:
    """
    T-117 — a partner's entitlement turns on status (BR-045).

    "When did this become WITHDRAWN, and who said so" has to be answerable
    months after a claim was signed.
    """
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    enrollment_service.change_status(
        actor=manager,
        enrollment=enrollment,
        to_status=EnrollmentStatus.WITHDRAWN,
        reason_ar="طلب انسحاب موثّق",
    )

    history = list(
        EnrollmentStatusHistory.objects.filter(enrollment=enrollment).order_by("changed_at")
    )
    assert [row.to_status for row in history] == [
        EnrollmentStatus.PENDING_FINANCE,
        EnrollmentStatus.ACTIVE,
        EnrollmentStatus.WITHDRAWN,
    ]
    assert history[-1].changed_by_id == manager.pk
    assert history[-1].reason_ar == "طلب انسحاب موثّق"


def test_the_history_is_append_only(approved_cohort, make_enrollment) -> None:
    """The same guarantee the audit trail carries, for the same reason."""
    from apps.core.exceptions import ImmutableRecordError

    enrollment = make_enrollment(approved_cohort)
    row = EnrollmentStatusHistory.objects.filter(enrollment=enrollment).first()
    assert row is not None

    row.reason_ar = "سبب آخر"
    with pytest.raises(ImmutableRecordError):
        row.save()


def test_a_change_that_changes_nothing_is_refused(
    approved_cohort, make_enrollment, manager
) -> None:
    """A no-op row would pad the record BR-045 is audited against."""
    enrollment = make_enrollment(approved_cohort)

    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.change_status(
            actor=manager,
            enrollment=enrollment,
            to_status=EnrollmentStatus.PENDING_FINANCE,
            reason_ar="لا شيء",
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        EnrollmentStatusHistory.objects.create(
            enrollment=enrollment,
            from_status=EnrollmentStatus.ACTIVE,
            to_status=EnrollmentStatus.ACTIVE,
            changed_by=manager,
        )


def test_a_final_status_does_not_move_without_a_documented_reversal(
    approved_cohort, make_enrollment, manager
) -> None:
    """
    T-118 — reopening a closed enrolment restates what a partner earned.

    Not forbidden outright: WORKFLOWS §1.2 allows a documented reversal, and
    the documentation is what makes it different from an accident.
    """
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.change_status(
        actor=manager,
        enrollment=enrollment,
        to_status=EnrollmentStatus.CANCELLED,
        reason_ar="إلغاء قبل البدء",
    )

    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.change_status(
            actor=manager,
            enrollment=enrollment,
            to_status=EnrollmentStatus.ACTIVE,
            reason_ar="",
        )

    enrollment_service.change_status(
        actor=manager,
        enrollment=enrollment,
        to_status=EnrollmentStatus.ACTIVE,
        reason_ar="إلغاء الإلغاء بقرار المدير رقم 12",
        allow_from_final=True,
    )
    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE


# ---------------------------------------------------------------------------
# T-239 … T-245 — Q-06 / BR-095, the documented counter
# ---------------------------------------------------------------------------
def test_a_counter_without_its_documentation_is_refused_by_the_database(
    approved_cohort, make_enrollment
) -> None:
    """
    T-239 / C-23 — this is what turns a typed number into an assertion.

    The transfer deadline is decided on it, so an undocumented count is not a
    small gap in tidiness.
    """
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.filter(pk=enrollment.pk).update(lectures_attended=2)


def test_a_null_counter_is_accepted_and_differs_from_zero(approved_cohort, make_enrollment) -> None:
    """
    T-240 — NULL is "not recorded yet"; zero is "recorded, and they attended none".

    Collapsing the two would let an unrecorded enrolment pass a rule that a
    genuinely absent participant fails.
    """
    enrollment = make_enrollment(approved_cohort)
    assert enrollment.lectures_attended is None
    assert enrollment.attendance_is_documented is False


def test_recording_a_counter_captures_source_verifier_and_time(
    approved_cohort, make_enrollment, documented_attendance, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    documented_attendance(enrollment, 2, ref="كشف المدرب 2026/09/25")

    enrollment.refresh_from_db()
    assert enrollment.lectures_attended == 2
    assert enrollment.attendance_source == AttendanceSource.MANUAL
    assert enrollment.attendance_record_ref == "كشف المدرب 2026/09/25"
    assert enrollment.attendance_verified_by_id == manager.pk
    assert enrollment.attendance_verified_at is not None
    assert enrollment.attendance_is_documented is True


def test_a_counter_without_a_source_reference_is_refused(
    approved_cohort, make_enrollment, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(ValidationError):
        enrollment_service.record_attendance(
            actor=manager, enrollment=enrollment, lectures_attended=2, record_ref="  "
        )


def test_every_counter_entry_is_audited_with_its_value_and_source(
    approved_cohort, make_enrollment, documented_attendance
) -> None:
    """T-244 — including the change from one value to another."""
    enrollment = make_enrollment(approved_cohort)
    documented_attendance(enrollment, 2)
    documented_attendance(enrollment, 4, ref="كشف مصحَّح")

    events = AuditEvent.objects.filter(
        entity_type="operations.Enrollment", summary_ar__contains="عدّاد المحاضرات"
    ).order_by("id")
    assert events.count() == 2
    correction = events.last()
    assert correction is not None and correction.changes is not None
    assert correction.changes["from"] == 2
    assert correction.changes["to"] == 4
    assert correction.changes["record_ref"] == "كشف مصحَّح"


def test_the_attendance_source_vocabulary_is_manual_and_system_only(
    approved_cohort, make_enrollment
) -> None:
    """T-245 — SYSTEM exists for an attendance module that does not exist yet."""
    assert list(AttendanceSource.values) == ["MANUAL", "SYSTEM"]


# ---------------------------------------------------------------------------
# T-113 — BR-019, the ministry upload window
# ---------------------------------------------------------------------------
def test_a_name_cannot_be_uploaded_for_an_unapproved_enrolment(
    approved_cohort, make_enrollment, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(ValidationError):
        enrollment_service.mark_uploaded_to_mohe(
            actor=manager, enrollment=enrollment, uploaded_on=date(2026, 10, 1)
        )


def test_the_database_also_refuses_an_upload_without_approval(
    approved_cohort, make_enrollment
) -> None:
    enrollment = make_enrollment(approved_cohort)
    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.filter(pk=enrollment.pk).update(mohe_uploaded_on=date(2026, 10, 1))


def test_uploading_within_the_window_is_recorded(
    approved_cohort, make_enrollment, registrar, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment_service.mark_uploaded_to_mohe(
        actor=manager, enrollment=enrollment, uploaded_on=date(2026, 10, 1)
    )
    enrollment.refresh_from_db()
    assert enrollment.mohe_uploaded_on == date(2026, 10, 1)


def test_uploading_after_the_deadline_needs_a_managers_reason(
    approved_cohort, make_enrollment, registrar, manager
) -> None:
    """
    T-113 — the ministry's window closing is a fact about the outside world.

    Recording a name past it is sometimes right and must always be explained;
    pretending the deadline did not pass is what produces names the ministry
    never received.
    """
    enrollment = make_enrollment(approved_cohort)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    late = date(2026, 10, 20)

    with pytest.raises(ValidationError):
        enrollment_service.mark_uploaded_to_mohe(
            actor=manager, enrollment=enrollment, uploaded_on=late
        )

    enrollment_service.mark_uploaded_to_mohe(
        actor=manager,
        enrollment=enrollment,
        uploaded_on=late,
        manager_override_reason_ar="كتاب رسمي بتمديد المهلة رقم 44",
    )
    enrollment.refresh_from_db()
    assert enrollment.mohe_uploaded_on == late

    event = AuditEvent.objects.filter(
        entity_type="operations.Enrollment", summary_ar__contains="بتجاوز المدير"
    ).first()
    assert event is not None and event.changes is not None
    assert event.changes["manager_override"] is True
    assert event.changes["deadline"] == DEADLINE.isoformat()
