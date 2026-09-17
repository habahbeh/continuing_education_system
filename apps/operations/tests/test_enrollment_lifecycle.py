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


def test_a_cancelled_application_takes_neither_a_voucher_nor_an_approval(
    approved_cohort, make_enrollment, registrar, manager
) -> None:
    """
    §6.5 — cancellation ends the registration flow. The voucher and the
    approval are refused up front, in the exits' own shape, and nothing is
    written: no voucher stamp, no approver, and the status stays CANCELLED.
    """
    from apps.operations.services import special_case_service

    enrollment = make_enrollment(approved_cohort)
    special_case_service.cancel_registration(
        actor=manager,
        enrollment=enrollment,
        reason_ar="اعتذر قبل البدء",
        occurred_on=date(2026, 9, 20),
        code="SC-CXL-V",
    )

    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.CANCELLED
    assert enrollment.voucher_received is False
    assert enrollment.approved_by_id is None


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
# QA EN-QA-1 — the payment gate beside the voucher gate
#
# A voucher over a ledger with nothing received is a contradiction. The rule
# is "something paid", not "balance zero": instalments (the diploma minimum
# first payment) leave a balance by design and T4/T5 chase it.
# ---------------------------------------------------------------------------
def test_approval_is_refused_while_a_charge_has_no_payment_at_all(
    approved_cohort, make_enrollment, charge_and_pay, registrar, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    charge_and_pay(enrollment, amount=None)  # 270 charged, nothing paid
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    before = (enrollment.status, enrollment.approved_by_id, enrollment.approved_at)

    with pytest.raises(enrollment_service.PaymentRequiredError, match="تسديد الرصيد المتبقي"):
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert (enrollment.status, enrollment.approved_by_id, enrollment.approved_at) == before
    assert enrollment.status == EnrollmentStatus.PENDING_FINANCE
    assert not EnrollmentStatusHistory.objects.filter(
        enrollment=enrollment, to_status=EnrollmentStatus.ACTIVE
    ).exists()


def test_approval_still_succeeds_once_the_charge_is_settled(
    approved_cohort, make_enrollment, charge_and_pay, registrar, manager
) -> None:
    enrollment = make_enrollment(approved_cohort)
    charge_and_pay(enrollment)  # 270 charged, 270 paid
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert enrollment.approved_by_id == manager.pk


def test_a_first_instalment_is_enough_to_approve(
    approved_cohort, make_enrollment, charge_and_pay, registrar, manager
) -> None:
    """The lifecycle's PAYMENT_OVERDUE leg depends on this staying true."""
    enrollment = make_enrollment(approved_cohort)
    charge_and_pay(enrollment, amount="100.000")  # 270 charged, 170 still owed
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE


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


# ---------------------------------------------------------------------------
# The enrolment code — minted, not typed
# ---------------------------------------------------------------------------
def test_an_enrolment_opened_without_a_code_is_given_one(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    ``EN-YYYY-NNNN``, from the year the enrolment was opened in.

    The operator used to type this. A code typed from memory is a code that
    collides, or repeats last year's, or reads ``EN-AHMAD-001`` — which was a
    QA example and never an entry in the register.
    """
    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(71),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    assert enrollment.code == f"EN-{TERM_START.year}-0001"


def test_the_sequence_increments_and_restarts_each_year(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    Two enrolments in a year are 0001 and 0002; the next year opens at 0001.

    The year is read from ``enrolled_on``, not from today, so backdating an
    enrolment files it under the year it belongs to rather than the year it
    was keyed in.
    """
    codes = [
        enrollment_service.create_enrollment(
            actor=registrar,
            participant=make_participant(index),
            cohort=approved_cohort,
            enrolled_on=enrolled_on,
            price_list=priced_catalog,
        ).code
        for index, enrolled_on in (
            (72, TERM_START),
            (73, TERM_START),
            (74, date(2027, 1, 12)),
        )
    ]
    assert codes == ["EN-2026-0001", "EN-2026-0002", "EN-2027-0001"]


def test_a_minted_code_steps_over_one_that_was_entered_by_hand(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    An import or a legacy migration may already hold ``EN-2026-0001``.

    The counter knows nothing about codes it did not issue, so the mint checks
    and moves on. Skipping a number costs a gap in the register; refusing
    would cost the enrolment.
    """
    enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(75),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
        code="EN-2026-0001",
    )
    minted = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(76),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    assert minted.code == "EN-2026-0002"


def test_an_explicit_code_is_still_honoured(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """Imports, fixtures and migration paths name the enrolment they carry."""
    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(77),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
        code="EN-LEGACY-9",
    )
    assert enrollment.code == "EN-LEGACY-9"


def test_a_refused_enrolment_returns_its_number(
    make_cohort, approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    BR-013's refusal must not burn a code.

    The counter is locked inside the creating transaction, so a rollback takes
    the number back with it. A gap in the register is a question somebody has
    to answer later.
    """
    unapproved = make_cohort("SC-NET", code="CO-NO-MOHE")
    with pytest.raises(enrollment_service.CohortNotApprovedError):
        enrollment_service.create_enrollment(
            actor=registrar,
            participant=make_participant(78),
            cohort=unapproved,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
        )

    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(79),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    assert enrollment.code == "EN-2026-0001"


def test_enrolling_with_charges_mints_the_code_too(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """The screen's own path — one call, and no code passed through it."""
    enrollment = enrollment_service.enroll_with_charges(
        actor=registrar,
        participant=make_participant(80),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
    )
    assert enrollment.code == "EN-2026-0001"
    assert enrollment.charge_lines.exists()


# ---------------------------------------------------------------------------
# Q-19 — one enrolment per participant per cohort
# ---------------------------------------------------------------------------
def test_a_second_enrolment_on_the_same_cohort_is_refused(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    Q-19, as a sentence rather than as a 500.

    The database has said this all along; what it says is
    ``Duplicate entry '4-1' for key …unique_participant_cohort``, which tells
    the operator nothing about the person or the cohort in front of them. The
    constraint stays as the guard that actually holds.
    """
    participant = make_participant(81)
    enrollment_service.create_enrollment(
        actor=registrar,
        participant=participant,
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )

    with pytest.raises(enrollment_service.DuplicateEnrollmentError) as refusal:
        enrollment_service.create_enrollment(
            actor=registrar,
            participant=participant,
            cohort=approved_cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
        )
    assert "مسجّل مسبقاً" in str(refusal.value)
    assert Enrollment.objects.filter(participant=participant, cohort=approved_cohort).count() == 1


def test_the_refused_duplicate_does_not_burn_a_code(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """
    The refusal happens before the mint, so the register keeps its order.

    A number consumed by an attempt that created nothing is a gap somebody has
    to account for later, and «it was a double-click» is not an answer anyone
    can give a year afterwards.
    """
    participant = make_participant(82)
    first = enrollment_service.create_enrollment(
        actor=registrar,
        participant=participant,
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    assert first.code == "EN-2026-0001"

    with pytest.raises(enrollment_service.DuplicateEnrollmentError):
        enrollment_service.create_enrollment(
            actor=registrar,
            participant=participant,
            cohort=approved_cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
        )

    second = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(83),
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    assert second.code == "EN-2026-0002"


def test_a_withdrawn_enrolment_still_blocks_a_second_one(
    approved_cohort, priced_catalog, registrar, make_participant
) -> None:
    """Q-19 counts enrolments, not live ones — the constraint is unconditional."""
    participant = make_participant(84)
    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=participant,
        cohort=approved_cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
    )
    enrollment_service.change_status(
        actor=registrar,
        enrollment=enrollment,
        to_status=EnrollmentStatus.WITHDRAWN,
        reason_ar="انسحاب بطلب المشارك",
    )

    with pytest.raises(enrollment_service.DuplicateEnrollmentError):
        enrollment_service.create_enrollment(
            actor=registrar,
            participant=participant,
            cohort=approved_cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
        )


# ---------------------------------------------------------------------------
# §6.4 — the ordinary exit: ACTIVE → COMPLETED, so a clearance can be opened
# ---------------------------------------------------------------------------
@pytest.fixture
def active_enrollment(approved_cohort, make_enrollment, registrar, manager):
    """Registered, voucher in, approved — the state Ahmed is stuck in."""
    enrollment = make_enrollment(approved_cohort, index=61)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    return enrollment


def test_the_manager_completes_an_active_enrolment_and_it_is_recorded(
    active_enrollment, manager
) -> None:
    before = timezone.now()
    enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)

    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.COMPLETED
    assert active_enrollment.status_changed_at >= before

    last = EnrollmentStatusHistory.objects.filter(enrollment=active_enrollment).latest("changed_at")
    assert (last.from_status, last.to_status) == (
        EnrollmentStatus.ACTIVE,
        EnrollmentStatus.COMPLETED,
    )
    assert last.changed_by_id == manager.pk
    assert last.reason_ar == enrollment_service.COMPLETION_REASON_AR

    audit = AuditEvent.objects.filter(
        entity_type="operations.Enrollment", reference=active_enrollment.code
    ).latest("occurred_at")
    assert audit.changes == {
        "from": EnrollmentStatus.ACTIVE,
        "to": EnrollmentStatus.COMPLETED,
        "reason": enrollment_service.COMPLETION_REASON_AR,
    }


@pytest.mark.parametrize("who", ["registrar", "finance", "cashier"])
def test_only_the_approving_authority_may_record_completion(
    request, active_enrollment, who
) -> None:
    """The registrar may EDIT an enrolment; closing it is the manager's call."""
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        enrollment_service.complete_enrollment(
            actor=request.getfixturevalue(who), enrollment=active_enrollment
        )
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


def test_the_auditor_may_not_record_completion(active_enrollment, seeded_settings) -> None:
    from django.core.exceptions import PermissionDenied

    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="audit.complete", password="probe-password-1234", role=Role.AUDIT_ACCOUNT
    )
    with pytest.raises(PermissionDenied):
        enrollment_service.complete_enrollment(actor=auditor, enrollment=active_enrollment)


def test_only_an_active_enrolment_can_be_completed(
    approved_cohort, make_enrollment, active_enrollment, manager
) -> None:
    pending = make_enrollment(approved_cohort, index=62)  # still PENDING_FINANCE
    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.complete_enrollment(actor=manager, enrollment=pending)

    enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)
    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)
    assert (
        EnrollmentStatusHistory.objects.filter(
            enrollment=active_enrollment, to_status=EnrollmentStatus.COMPLETED
        ).count()
        == 1
    )


def test_completion_offers_the_enrolment_to_clearance_without_opening_one(
    active_enrollment, manager
) -> None:
    from apps.operations.models import Clearance
    from apps.operations.services import clearance_service

    def offered() -> set[str]:
        return {code for code, _ in clearance_service.clearable_enrollment_choices(actor=manager)}

    assert active_enrollment.code not in offered(), "an ACTIVE enrolment has nothing to clear"

    enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)

    assert active_enrollment.code in offered()
    assert not Clearance.objects.filter(enrollment=active_enrollment).exists()


# ---------------------------------------------------------------------------
# §6.4 — the other two exits: withdrawal and dismissal, from the same screen
# ---------------------------------------------------------------------------
def test_the_manager_withdraws_an_active_enrolment_with_its_reason(
    active_enrollment, manager
) -> None:
    enrollment_service.withdraw_enrollment(
        actor=manager, enrollment=active_enrollment, reason_ar="ظروف عمل"
    )

    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.WITHDRAWN
    last = EnrollmentStatusHistory.objects.filter(enrollment=active_enrollment).latest("changed_at")
    assert (last.from_status, last.to_status, last.changed_by_id) == (
        EnrollmentStatus.ACTIVE,
        EnrollmentStatus.WITHDRAWN,
        manager.pk,
    )
    assert "ظروف عمل" in last.reason_ar
    assert (
        AuditEvent.objects.filter(
            entity_type="operations.Enrollment", reference=active_enrollment.code
        )
        .latest("occurred_at")
        .changes["to"]
        == EnrollmentStatus.WITHDRAWN
    )


def test_a_withdrawal_without_a_reason_is_refused(active_enrollment, manager) -> None:
    with pytest.raises(ValidationError, match="سبباً"):
        enrollment_service.withdraw_enrollment(
            actor=manager, enrollment=active_enrollment, reason_ar="   "
        )
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


def test_the_manager_dismisses_through_the_special_case_rule(active_enrollment, manager) -> None:
    """BR-067 — the enrolments screen files the same special case the rule requires."""
    from apps.operations.models import SpecialCase, SpecialCaseType

    enrollment_service.dismiss_enrollment(
        actor=manager,
        enrollment=active_enrollment,
        decision_reference="قرار 12/2026",
        reason_ar="غياب متكرر موثّق",
    )

    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.DISMISSED
    case = SpecialCase.objects.get(enrollment=active_enrollment)
    assert case.case_type == SpecialCaseType.DISMISSAL
    assert case.decision_reference == "قرار 12/2026"
    assert case.detail_ar == "غياب متكرر موثّق"
    assert case.code.startswith(f"SC-{timezone.localdate().year}-")
    last = EnrollmentStatusHistory.objects.filter(enrollment=active_enrollment).latest("changed_at")
    assert (last.to_status, last.changed_by_id, last.reference) == (
        EnrollmentStatus.DISMISSED,
        manager.pk,
        case.code,
    )


@pytest.mark.parametrize(
    ("decision_reference", "reason_ar"),
    [("", "سبب"), ("   ", "سبب"), ("قرار 1", ""), ("قرار 1", "  ")],
)
def test_a_dismissal_needs_both_the_decision_and_the_reason(
    active_enrollment, manager, decision_reference, reason_ar
) -> None:
    from apps.operations.models import SpecialCase

    with pytest.raises(ValidationError):
        enrollment_service.dismiss_enrollment(
            actor=manager,
            enrollment=active_enrollment,
            decision_reference=decision_reference,
            reason_ar=reason_ar,
        )
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE
    assert not SpecialCase.objects.filter(enrollment=active_enrollment).exists()


@pytest.mark.parametrize("who", ["finance", "cashier"])
@pytest.mark.parametrize("exit_", ["withdraw", "dismiss"])
def test_the_financial_roles_may_neither_withdraw_nor_dismiss(
    request, active_enrollment, who, exit_
) -> None:
    from django.core.exceptions import PermissionDenied

    actor = request.getfixturevalue(who)
    with pytest.raises(PermissionDenied):
        if exit_ == "withdraw":
            enrollment_service.withdraw_enrollment(
                actor=actor, enrollment=active_enrollment, reason_ar="سبب"
            )
        else:
            enrollment_service.dismiss_enrollment(
                actor=actor, enrollment=active_enrollment, decision_reference="ق", reason_ar="سبب"
            )
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


def test_the_registrar_may_not_withdraw(active_enrollment, registrar) -> None:
    """Withdrawal is a final decision and sits with APPROVE, like graduation."""
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        enrollment_service.withdraw_enrollment(
            actor=registrar, enrollment=active_enrollment, reason_ar="سبب"
        )


def test_the_auditor_may_neither_withdraw_nor_dismiss(active_enrollment, seeded_settings) -> None:
    from django.core.exceptions import PermissionDenied

    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="audit.exit", password="probe-password-1234", role=Role.AUDIT_ACCOUNT
    )
    with pytest.raises(PermissionDenied):
        enrollment_service.withdraw_enrollment(
            actor=auditor, enrollment=active_enrollment, reason_ar="سبب"
        )
    with pytest.raises(PermissionDenied):
        enrollment_service.dismiss_enrollment(
            actor=auditor, enrollment=active_enrollment, decision_reference="ق", reason_ar="سبب"
        )


@pytest.mark.parametrize("exit_", ["withdraw", "dismiss"])
def test_only_an_active_enrolment_can_be_withdrawn_or_dismissed(
    approved_cohort, make_enrollment, active_enrollment, manager, exit_
) -> None:
    def attempt(enrollment):
        if exit_ == "withdraw":
            enrollment_service.withdraw_enrollment(
                actor=manager, enrollment=enrollment, reason_ar="سبب"
            )
        else:
            enrollment_service.dismiss_enrollment(
                actor=manager, enrollment=enrollment, decision_reference="ق", reason_ar="سبب"
            )

    pending = make_enrollment(approved_cohort, index=64)  # PENDING_FINANCE
    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        attempt(pending)

    enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)
    with pytest.raises(enrollment_service.InvalidStatusTransitionError):
        attempt(active_enrollment)


@pytest.mark.parametrize(
    ("exit_", "status", "case_label"),
    [
        ("withdraw", EnrollmentStatus.WITHDRAWN, "انسحاب"),
        ("dismiss", EnrollmentStatus.DISMISSED, "فصل"),
    ],
)
def test_an_exit_offers_the_enrolment_to_clearance_without_opening_one(
    active_enrollment, manager, exit_, status, case_label
) -> None:
    from apps.operations.models import Clearance
    from apps.operations.services import clearance_service

    if exit_ == "withdraw":
        enrollment_service.withdraw_enrollment(
            actor=manager, enrollment=active_enrollment, reason_ar="سبب"
        )
    else:
        enrollment_service.dismiss_enrollment(
            actor=manager, enrollment=active_enrollment, decision_reference="ق 3", reason_ar="سبب"
        )

    active_enrollment.refresh_from_db()
    assert active_enrollment.status == status
    assert not Clearance.objects.filter(enrollment=active_enrollment).exists()
    offered = dict(clearance_service.clearable_enrollment_choices(actor=manager))
    assert case_label in offered[active_enrollment.code]
