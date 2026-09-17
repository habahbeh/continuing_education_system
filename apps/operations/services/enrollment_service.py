"""
The enrolment lifecycle (WORKFLOWS §1, BR-013 · BR-017 … BR-019, BR-095).

Two gates the demo showed and never enforced live here:

* **BR-013 / D-21** — no enrolment on a cohort the ministry has not approved.
  The demo listed every cohort in every dropdown regardless of status.
* **BR-018** — no approval without a recorded voucher. That one is also a
  database constraint, so the service check is the readable message rather
  than the last line of defence.

Every status change writes an ``EnrollmentStatusHistory`` row, because a
partner's entitlement turns on status (BR-045) and "when did this become
WITHDRAWN, and who said so" has to be answerable after a claim is signed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.operations.models import (
    AttendanceSource,
    Cohort,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStatusHistory,
    MoheStatus,
    MoheSubmission,
)
from apps.operations.services import mohe_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Enrollment"

#: The enrolment code is the system's to mint, not the operator's to remember.
#: ``EN-YYYY-NNNN`` — the year the enrolment was opened, then a four-digit
#: sequence within that year, which is how the paper register already reads.
ENROLLMENT_SCOPE = "enrollment"
ENROLLMENT_PREFIX = "EN-"
SEQUENCE_PADDING = 4

#: A minted code can still meet a manually-entered one from an import or a
#: legacy migration, which the counter knows nothing about. Skipping past it
#: costs one number; refusing would cost the enrolment.
MAX_CODE_ATTEMPTS = 8


def enrollment_partition(enrolled_on: date) -> str:
    """The sequence partition an enrolment falls in — its year (BR-076-style)."""
    return str(enrolled_on.year)


def next_enrollment_code(enrolled_on: date) -> str:
    """
    Mint the next ``EN-YYYY-NNNN`` for the year ``enrolled_on`` falls in.

    Numbers come from the shared counter (ADR-011), which locks its row inside
    the caller's transaction: two clerks enrolling at the same moment cannot
    receive the same code, and a rolled-back enrolment takes its number back
    with it rather than leaving a hole in the register.

    Must therefore be called INSIDE the transaction that creates the row.
    """
    partition = enrollment_partition(enrolled_on)
    for _attempt in range(MAX_CODE_ATTEMPTS):
        code = next_number(
            ENROLLMENT_SCOPE,
            partition,
            prefix=f"{ENROLLMENT_PREFIX}{partition}-",
            padding=SEQUENCE_PADDING,
        )
        if not Enrollment.objects.filter(code=code).exists():
            return code
    raise ValidationError(
        f"تعذّر توليد رمز تسجيل غير مكرَّر للسنة {partition} بعد {MAX_CODE_ATTEMPTS} محاولات."
    )


class CohortNotApprovedError(ValidationError):
    """BR-013 / D-21 — enrolment attempted on an unapproved cohort."""


class VoucherRequiredError(ValidationError):
    """BR-018 — approval attempted with no voucher recorded."""


class PaymentRequiredError(ValidationError):
    """Approval attempted on a charged enrolment against which nothing was paid."""


class DuplicateEnrollmentError(ValidationError):
    """Q-19 — the participant already holds an enrolment on this cohort."""


class AttendanceNotDocumentedError(ValidationError):
    """BR-095 / Q-06 — a rule was evaluated against an undocumented counter."""


class InvalidStatusTransitionError(ValidationError):
    """WORKFLOWS §1.2 — a move out of a final state, or one not on the map."""


def record_status_change(
    *,
    actor: Any,
    enrollment: Enrollment,
    to_status: str,
    reason_ar: str = "",
    reference: str = "",
    allow_from_final: bool = False,
) -> EnrollmentStatusHistory:
    """
    Move an enrolment and record the move. Callers must already be inside
    their own transaction — this is a step, not a workflow.

    ``allow_from_final`` exists for the documented reversal WORKFLOWS §1.2
    permits; it always demands a reason, because reopening a closed enrolment
    restates what a partner earned.
    """
    from_status = enrollment.status
    if from_status == to_status:
        raise InvalidStatusTransitionError(f"التسجيل {enrollment.code} في الحالة {to_status} أصلاً.")
    if enrollment.is_final and not allow_from_final:
        raise InvalidStatusTransitionError(
            f"لا انتقال من حالة نهائية ({from_status}) إلا بإلغاء موثّق بمُجيز وسبب (WORKFLOWS §1.2)."
        )
    if enrollment.is_final and allow_from_final and not reason_ar.strip():
        raise InvalidStatusTransitionError("الرجوع عن حالة نهائية يتطلب سبباً مسجَّلاً.")

    enrollment.status = to_status
    enrollment.status_changed_at = timezone.now()
    if reason_ar:
        enrollment.status_note_ar = reason_ar[:255]
    enrollment.save(update_fields=["status", "status_changed_at", "status_note_ar"])

    return EnrollmentStatusHistory.objects.create(
        enrollment=enrollment,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor,
        reason_ar=reason_ar[:255],
        reference=reference[:64],
    )


def create_enrollment(
    *,
    actor: Any,
    participant: Any,
    cohort: Cohort,
    enrolled_on: date,
    price_list: Any,
    code: str | None = None,
    request: Any = None,
) -> Enrollment:
    """
    WORKFLOWS §1.2 T1 — open an enrolment, if the ministry has approved.

    The gate runs BEFORE the transaction so a refusal's denied-attempt audit
    row survives the raise (BR-100).

    ``code`` is optional and normally omitted: the system mints it
    (:func:`next_enrollment_code`). It stays settable for the callers that
    carry a code from somewhere else — imports, legacy migration, and tests
    that name the enrolment they are asserting about.
    """
    policy.require(actor, Screen.ENROLLMENTS, Action.CREATE, request=request)

    if not mohe_service.cohort_is_approved(cohort):
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=ENTITY,
            reference=cohort.code,
            summary_ar=(
                f"محاولة تسجيل على دفعة غير معتمدة وزارياً — {cohort.code} "
                f"(الحالة: {cohort.get_status_display()})"
            ),
            actor=actor,
            denial_rule="D-21",
            request=request,
        )
        raise CohortNotApprovedError(
            f"لا يُسمح بالتسجيل — الدفعة {cohort.code} لم تُعتمد من الوزارة (BR-013)."
        )

    # Q-19 — one enrolment per participant per cohort, whatever its status.
    # The database says the same thing and is the guard that actually holds;
    # this one exists so the operator reads a sentence instead of a 500, and
    # so the refusal happens before a code is minted. A burnt number would
    # leave a gap in the register that nobody could later account for.
    if Enrollment.objects.filter(participant=participant, cohort=cohort).exists():
        raise DuplicateEnrollmentError(
            f"هذا المشارك مسجّل مسبقاً على الدفعة {cohort.code}. "
            "لا يمكن إنشاء تسجيل مكرر (Q-19)."
        )

    if code is None:
        # Documented in numbering_service: creating the row BEFORE anyone locks
        # it keeps concurrent callers on a brand-new year out of insert-intention
        # contention. Inside the transaction it is safe but not free.
        ensure_sequence(
            ENROLLMENT_SCOPE, enrollment_partition(enrolled_on), padding=SEQUENCE_PADDING
        )

    return _create_enrollment(
        actor=actor,
        participant=participant,
        cohort=cohort,
        enrolled_on=enrolled_on,
        price_list=price_list,
        code=code,
        request=request,
    )


@transaction.atomic
def _create_enrollment(
    *,
    actor: Any,
    participant: Any,
    cohort: Cohort,
    enrolled_on: date,
    price_list: Any,
    code: str | None,
    request: Any,
) -> Enrollment:
    # Minted inside the transaction so a failed creation returns its number.
    code = code or next_enrollment_code(enrolled_on)
    enrollment = Enrollment.objects.create(
        code=code,
        participant=participant,
        cohort=cohort,
        enrolled_on=enrolled_on,
        price_list=price_list,
        status=EnrollmentStatus.PENDING_FINANCE,
        status_changed_at=timezone.now(),
    )
    EnrollmentStatusHistory.objects.create(
        enrollment=enrollment,
        from_status="",
        to_status=EnrollmentStatus.PENDING_FINANCE,
        changed_by=actor,
        reason_ar="إنشاء التسجيل",
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar=f"تسجيل {participant.name_ar} على الدفعة {cohort.code}",
        actor=actor,
        changes={
            "cohort": cohort.code,
            "price_list": str(price_list.pk),
            "mohe_approved": True,
        },
        request=request,
    )
    return enrollment


def _require_not_final(enrollment: Enrollment, verb_ar: str) -> None:
    """
    The registration flow (voucher, approval) is over once the enrolment has
    ended — a cancelled application must not be approved into ACTIVE, and a
    voucher against it would be logged for nothing. Refused here, before any
    write, in the same shape as the exits' ``_require_active``.
    """
    if enrollment.is_final:
        raise InvalidStatusTransitionError(
            f"لا يُسجَّل {verb_ar} لتسجيل انتهى؛ التسجيل {enrollment.code} "
            f"في الحالة {enrollment.get_status_display()}."
        )


def record_voucher(*, actor: Any, enrollment: Enrollment, request: Any = None) -> Enrollment:
    """BR-018's precondition — the voucher is logged before anyone can approve."""
    policy.require(actor, Screen.ENROLLMENTS, Action.EDIT, request=request)
    _require_not_final(enrollment, "استلام الوصل")
    return _record_voucher(actor=actor, enrollment=enrollment, request=request)


@transaction.atomic
def _record_voucher(*, actor: Any, enrollment: Enrollment, request: Any) -> Enrollment:
    enrollment.voucher_received = True
    enrollment.voucher_received_at = timezone.now()
    enrollment.voucher_received_by = actor
    enrollment.save(
        update_fields=["voucher_received", "voucher_received_at", "voucher_received_by"]
    )
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar="تسجيل استلام الوصل",
        actor=actor,
        request=request,
    )
    return enrollment


def approve_enrollment(*, actor: Any, enrollment: Enrollment, request: Any = None) -> Enrollment:
    """WORKFLOWS §1.2 T3 — BR-018: no approval without a recorded voucher."""
    policy.require(actor, Screen.ENROLLMENTS, Action.APPROVE, request=request)

    _require_not_final(enrollment, "اعتماد التسجيل")
    if not enrollment.voucher_received:
        raise VoucherRequiredError("لا يمكن اعتماد التسجيل قبل تسجيل استلام الوصل (BR-018).")
    _require_first_payment(enrollment)
    return _approve_enrollment(actor=actor, enrollment=enrollment, request=request)


def _require_first_payment(enrollment: Enrollment) -> None:
    """
    A voucher is evidence money was paid; a voucher over a ledger that shows
    nothing received is a contradiction, and approving on it is the QA case
    EN-QA-1 — approved while the whole charge was still owed.

    Deliberately NOT "balance must be zero": an instalment plan (the diploma
    minimum first payment) leaves a balance by design, and T4/T5 exist to
    chase it (ACTIVE → PAYMENT_OVERDUE). What is refused is the case with no
    payment at all against a live charge. A fully discounted, exempt or
    credit-covered enrolment has nothing owed and passes untouched.
    """
    from apps.billing.services.account_service import ZERO, get_account_state

    state = get_account_state(enrollment)
    if state.participant_owes and state.total_paid == ZERO:
        raise PaymentRequiredError(
            f"لا يمكن اعتماد التسجيل قبل تسديد الرصيد المتبقي ({state.balance}) "
            "أو دفعة أولى منه من الصندوق."
        )


@transaction.atomic
def _approve_enrollment(*, actor: Any, enrollment: Enrollment, request: Any) -> Enrollment:
    enrollment.approved_by = actor
    enrollment.approved_at = timezone.now()
    enrollment.save(update_fields=["approved_by", "approved_at"])

    record_status_change(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.ACTIVE,
        reason_ar="اعتماد التسجيل",
    )
    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar="اعتماد التسجيل",
        actor=actor,
        request=request,
    )
    return enrollment


def mark_uploaded_to_mohe(
    *,
    actor: Any,
    enrollment: Enrollment,
    uploaded_on: date,
    manager_override_reason_ar: str = "",
    request: Any = None,
) -> Enrollment:
    """
    BR-019 — report the name to the ministry, within the window.

    Past the deadline a centre manager may still record it, but only with a
    reason: the ministry's window closing is a fact about the outside world,
    and pretending otherwise is what produces names the ministry never got.
    """
    policy.require(actor, Screen.ENROLLMENTS, Action.EDIT, request=request)

    if enrollment.approved_at is None:
        raise ValidationError("لا يُرفع اسم عن تسجيل غير معتمد (BR-019).")

    submission = mohe_service.approved_submission_for(enrollment.cohort)
    deadline = submission.registration_deadline if submission else None
    overridden = False

    if deadline is not None and uploaded_on > deadline:
        if not manager_override_reason_ar.strip():
            raise ValidationError(
                f"انتهت مدة التسجيل الوزارية في {deadline} — الرفع بعدها "
                "يتطلب تجاوزاً من مدير المركز بسبب مسجَّل (BR-019)."
            )
        policy.require(actor, Screen.ENROLLMENTS, Action.APPROVE, request=request)
        overridden = True

    return _mark_uploaded(
        actor=actor,
        enrollment=enrollment,
        uploaded_on=uploaded_on,
        overridden=overridden,
        reason_ar=manager_override_reason_ar.strip(),
        deadline=deadline,
        request=request,
    )


@transaction.atomic
def _mark_uploaded(
    *,
    actor: Any,
    enrollment: Enrollment,
    uploaded_on: date,
    overridden: bool,
    reason_ar: str,
    deadline: date | None,
    request: Any,
) -> Enrollment:
    enrollment.mohe_uploaded_on = uploaded_on
    enrollment.save(update_fields=["mohe_uploaded_on"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar=(
            f"رفع الاسم للوزارة {uploaded_on}"
            + (" — بتجاوز المدير بعد المهلة" if overridden else "")
        ),
        actor=actor,
        changes={
            "uploaded_on": uploaded_on.isoformat(),
            "deadline": deadline.isoformat() if deadline else None,
            "manager_override": overridden,
            "override_reason": reason_ar,
        },
        request=request,
    )
    return enrollment


def list_mohe_name_uploads(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """Approved cohorts and their ACTIVE enrolments, for the MOHE names register."""
    policy.require(actor, Screen.MOHE, Action.VIEW, request=request)
    can_record = policy.is_allowed(actor, Screen.ENROLLMENTS, Action.EDIT)
    today = timezone.localdate()

    sections: list[dict[str, Any]] = []
    submissions = (
        MoheSubmission.objects.filter(status=MoheStatus.APPROVED)
        .select_related("cohort__program")
        .order_by("-decided_on", "cohort__code")
    )
    for submission in submissions:
        enrollments = list(
            Enrollment.objects.filter(
                cohort=submission.cohort,
                status=EnrollmentStatus.ACTIVE,
                approved_at__isnull=False,
            )
            .select_related("participant")
            .order_by("code")
        )
        rows = []
        for enrollment in enrollments:
            upload_is_late = (
                submission.registration_deadline is not None
                and today > submission.registration_deadline
                and enrollment.mohe_uploaded_on is None
            )
            rows.append(
                {
                    "participant_name": enrollment.participant.name_ar,
                    "enrollment_code": enrollment.code,
                    "status": enrollment.status,
                    "status_display": enrollment.get_status_display(),
                    "approved_at": enrollment.approved_at,
                    "mohe_uploaded_on": enrollment.mohe_uploaded_on,
                    "can_record_upload": can_record
                    and enrollment.mohe_uploaded_on is None
                    and not upload_is_late,
                    "upload_requires_manager_reason": upload_is_late,
                }
            )

        uploaded_count = sum(1 for row in rows if row["mohe_uploaded_on"] is not None)
        sections.append(
            {
                "cohort_code": submission.cohort.code,
                "cohort_name_ar": submission.cohort.name_ar,
                "program_name_ar": submission.cohort.program.name_ar,
                "registration_deadline": submission.registration_deadline,
                "approved_count": len(rows),
                "uploaded_count": uploaded_count,
                "pending_count": len(rows) - uploaded_count,
                "rows": rows,
            }
        )
    return sections


def list_mohe_uploaded_names(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Flat rows for the "Export Uploaded Names" download on the MOHE screen.

    The same gate as :func:`list_mohe_name_uploads`, and a strict subset of
    what it shows: only ACTIVE, approved enrolments on an APPROVED cohort
    file whose name has already been recorded as uploaded to the ministry.
    """
    policy.require(actor, Screen.MOHE, Action.VIEW, request=request)

    submissions = (
        MoheSubmission.objects.filter(status=MoheStatus.APPROVED)
        .select_related("cohort__program")
        .order_by("cohort__code")
    )
    rows: list[dict[str, Any]] = []
    for submission in submissions:
        enrollments = (
            Enrollment.objects.filter(
                cohort=submission.cohort,
                status=EnrollmentStatus.ACTIVE,
                approved_at__isnull=False,
                mohe_uploaded_on__isnull=False,
            )
            .select_related("participant")
            .order_by("code")
        )
        for enrollment in enrollments:
            rows.append(
                {
                    "participant_name": enrollment.participant.name_ar,
                    "enrollment_code": enrollment.code,
                    "program_name_ar": submission.cohort.program.name_ar,
                    "cohort_code": submission.cohort.code,
                    "mohe_course_number": submission.mohe_course_number,
                    "approved_at": enrollment.approved_at,
                    "mohe_uploaded_on": enrollment.mohe_uploaded_on,
                    "registration_deadline": submission.registration_deadline,
                }
            )
    return rows


def record_attendance(
    *,
    actor: Any,
    enrollment: Enrollment,
    lectures_attended: int,
    record_ref: str,
    source: str = AttendanceSource.MANUAL,
    note: str = "",
    request: Any = None,
) -> Enrollment:
    """
    BR-095 / Q-06 — the lecture counter, as a documented assertion.

    The transfer deadline (BR-062) is decided on this number and a partner's
    entitlement can follow from it, so the source, the verifier and the
    timestamp are recorded together with the count. The database refuses a
    half-documented one; this is where the number acquires its evidence.
    """
    policy.require(actor, Screen.ENROLLMENTS, Action.EDIT, request=request)

    if lectures_attended < 0:
        raise ValidationError("عدد المحاضرات لا يكون سالباً.")
    if not record_ref.strip():
        raise ValidationError("مصدر العدّاد إلزامي — كشف المدرب أو كشف يدوي أو إفادة (BR-095).")
    return _record_attendance(
        actor=actor,
        enrollment=enrollment,
        lectures_attended=lectures_attended,
        record_ref=record_ref.strip(),
        source=source,
        note=note,
        request=request,
    )


@transaction.atomic
def _record_attendance(
    *,
    actor: Any,
    enrollment: Enrollment,
    lectures_attended: int,
    record_ref: str,
    source: str,
    note: str,
    request: Any,
) -> Enrollment:
    previous = enrollment.lectures_attended
    enrollment.lectures_attended = lectures_attended
    enrollment.attendance_source = source
    enrollment.attendance_record_ref = record_ref
    enrollment.attendance_verified_by = actor
    enrollment.attendance_verified_at = timezone.now()
    if note:
        enrollment.attendance_note = note
    enrollment.save(
        update_fields=[
            "lectures_attended",
            "attendance_source",
            "attendance_record_ref",
            "attendance_verified_by",
            "attendance_verified_at",
            "attendance_note",
        ]
    )
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar=f"تسجيل عدّاد المحاضرات: {lectures_attended} — {record_ref}",
        actor=actor,
        changes={
            "from": previous,
            "to": lectures_attended,
            "source": source,
            "record_ref": record_ref,
            "verified_by": getattr(actor, "username", None),
        },
        request=request,
    )
    return enrollment


def change_status(
    *,
    actor: Any,
    enrollment: Enrollment,
    to_status: str,
    reason_ar: str,
    reference: str = "",
    allow_from_final: bool = False,
    request: Any = None,
) -> Enrollment:
    """A manual status change by a human, with its reason and its audit row."""
    policy.require(actor, Screen.ENROLLMENTS, Action.EDIT, request=request)
    return _change_status(
        actor=actor,
        enrollment=enrollment,
        to_status=to_status,
        reason_ar=reason_ar,
        reference=reference,
        allow_from_final=allow_from_final,
        request=request,
    )


@transaction.atomic
def _change_status(
    *,
    actor: Any,
    enrollment: Enrollment,
    to_status: str,
    reason_ar: str,
    reference: str,
    allow_from_final: bool,
    request: Any,
) -> Enrollment:
    from_status = enrollment.status
    record_status_change(
        actor=actor,
        enrollment=enrollment,
        to_status=to_status,
        reason_ar=reason_ar,
        reference=reference,
        allow_from_final=allow_from_final,
    )
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar=f"تغيير حالة التسجيل: {from_status} ← {to_status}",
        actor=actor,
        changes={"from": from_status, "to": to_status, "reason": reason_ar},
        request=request,
    )
    return enrollment


#: What the history row says when a trainee finishes normally. Fixed text,
#: not a free field: graduation is the one exit that needs no explanation.
COMPLETION_REASON_AR = "إكمال الدورة — تخرج"


def complete_enrollment(*, actor: Any, enrollment: Enrollment, request: Any = None) -> Enrollment:
    """
    §6.4 — the trainee finished the course; move ACTIVE → COMPLETED.

    This is the ordinary exit, the one a clearance of type «تخرج» is opened
    for. It does NOT open that clearance: §6.4 makes opening it a separate
    decision, and the financial step there is what stops an unpaid balance
    from turning into a certificate (BR-075) — so nothing about money is
    checked here, and nothing about it is hidden either: the balance stays on
    the statement for the clearance to find.

    Gated on APPROVE, the same authority that put the enrolment into ACTIVE.
    """
    policy.require(actor, Screen.ENROLLMENTS, Action.APPROVE, request=request)

    _require_active(enrollment, "الإكمال")
    return _change_status(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.COMPLETED,
        reason_ar=COMPLETION_REASON_AR,
        reference="",
        allow_from_final=False,
        request=request,
    )


def _require_active(enrollment: Enrollment, verb_ar: str) -> None:
    """The three ways out of the course all start from ACTIVE and nowhere else."""
    if enrollment.status != EnrollmentStatus.ACTIVE:
        raise InvalidStatusTransitionError(
            f"لا يُسجَّل {verb_ar} إلا لتسجيل فعّال؛ التسجيل {enrollment.code} "
            f"في الحالة {enrollment.get_status_display()}."
        )


def withdraw_enrollment(
    *, actor: Any, enrollment: Enrollment, reason_ar: str, request: Any = None
) -> Enrollment:
    """
    §6.4 — the trainee left; move ACTIVE → WITHDRAWN.

    The reason is mandatory: a withdrawal decides what the partner earned
    (BR-045) and whether a refund is even askable, and a status row with no
    reason is what the file cannot answer a year later. As with graduation,
    nothing here opens the clearance or touches the balance — both wait for
    the clearance of type «انسحاب» that this makes possible.

    Gated on APPROVE like completion: the authority that let the trainee in
    is the one that records how they left.
    """
    policy.require(actor, Screen.ENROLLMENTS, Action.APPROVE, request=request)

    if not reason_ar.strip():
        raise ValidationError("تسجيل الانسحاب يتطلب سبباً مكتوباً.")
    _require_active(enrollment, "الانسحاب")
    return _change_status(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.WITHDRAWN,
        reason_ar=f"انسحاب — {reason_ar.strip()}",
        reference="",
        allow_from_final=False,
        request=request,
    )


def dismiss_enrollment(
    *,
    actor: Any,
    enrollment: Enrollment,
    decision_reference: str,
    reason_ar: str,
    request: Any = None,
) -> Enrollment:
    """
    §6.4 — the trainee was dismissed; move ACTIVE → DISMISSED.

    The enrolments screen's entry point to ``special_case_service.dismiss``,
    which owns the rule (BR-067: no dismissal without the decision that
    ordered it; BR-068: no refund) and files the special case, the status
    row and the audit. This adds only what a row on that screen needs: the
    enrolment must actually be running, and the reason must be written.
    """
    from apps.operations.services import special_case_service

    if not reason_ar.strip():
        raise ValidationError("تسجيل الفصل يتطلب سبباً مكتوباً.")
    _require_active(enrollment, "الفصل")
    special_case_service.dismiss(
        actor=actor,
        enrollment=enrollment,
        decision_reference=decision_reference,
        detail_ar=reason_ar.strip(),
        occurred_on=timezone.localdate(),
        request=request,
    )
    return enrollment


def list_enrollments(
    *,
    actor: Any,
    query: str = "",
    cohort_code: str = "",
    status: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Enrolments as rows, each carrying its balance.

    The balance comes from ``get_account_state`` rather than a stored column,
    because there is exactly one place the balance equation lives and a list
    screen is not entitled to a second opinion about it (DATA_MODEL §8.2).
    """
    from apps.billing.services.account_service import get_account_state

    policy.require(actor, Screen.ENROLLMENTS, Action.VIEW, request=request)

    queryset = Enrollment.objects.select_related(
        "participant", "cohort__program", "cohort__agreement__partner"
    )
    if query:
        queryset = queryset.filter(code__icontains=query) | queryset.filter(
            participant__name_ar__icontains=query
        )
    if cohort_code:
        queryset = queryset.filter(cohort__code=cohort_code)
    if status:
        queryset = queryset.filter(status=status)

    rows: list[dict[str, Any]] = []
    for enrollment in queryset.order_by("-enrolled_on", "code"):
        state = get_account_state(enrollment)
        rows.append(
            {
                "code": enrollment.code,
                "participant_name": enrollment.participant.name_ar,
                "participant_number": enrollment.participant.participant_number,
                "cohort_code": enrollment.cohort.code,
                "cohort_name": enrollment.cohort.name_ar,
                "program_name": enrollment.cohort.program.name_ar,
                "enrolled_on": enrollment.enrolled_on,
                "status": enrollment.status,
                "status_display": enrollment.get_status_display(),
                "status_note_ar": enrollment.status_note_ar,
                "voucher_received": enrollment.voucher_received,
                "is_approved": enrollment.approved_by_id is not None,
                "is_final": enrollment.is_final,
                "balance": state.balance,
                "total_due": state.total_due,
                "total_paid": state.total_paid,
                "total_discount": state.total_discount,
                "participant_owes": state.participant_owes,
                "centre_owes": state.centre_owes,
                "is_settled": state.is_settled,
            }
        )
    return rows


def get_enrollment(*, actor: Any, code: str, request: Any = None) -> Enrollment:
    """The Enrollment instance, for handing to another service."""
    policy.require(actor, Screen.ENROLLMENTS, Action.VIEW, request=request)
    return Enrollment.objects.select_related("participant", "cohort__program").get(code=code)


def enrollment_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """(code, label) pairs for forms that act on one enrolment."""
    policy.require(actor, Screen.ENROLLMENTS, Action.VIEW, request=request)
    return [
        (e.code, f"{e.code} — {e.participant.name_ar}")
        for e in Enrollment.objects.select_related("participant").order_by("-enrolled_on")[:200]
    ]


def enroll_with_charges(
    *,
    actor: Any,
    participant: Any,
    cohort: Cohort,
    enrolled_on: date,
    code: str | None = None,
    request: Any = None,
) -> Enrollment:
    """
    Enrol a participant AND raise the charges the price list says they owe.

    One call rather than two, because the two halves are not independently
    meaningful: an enrolment with no charge lines owes nothing, so the cashier
    would find nothing to collect against and the balance would read zero on
    someone who has paid nothing.

    Resolving the price is a DECISION — which list is in force, whether the
    programme is levelled, which category the participant falls in — and
    decisions belong here rather than in a view assembling two service calls
    (ADR-008).

    ``code`` is passed straight through to :func:`create_enrollment`: omitted
    by the screen, supplied only where a code already exists.
    """
    from apps.billing.services import charge_service
    from apps.catalog.models import PriceList
    from apps.catalog.services import pricing_service

    quote = pricing_service.resolve_price(
        program=cohort.program,
        participant_category=participant.category,
        as_of=enrolled_on,
        level=cohort.level,
    )

    enrollment = create_enrollment(
        actor=actor,
        participant=participant,
        cohort=cohort,
        enrolled_on=enrolled_on,
        # The quote names the list it came from, so the enrolment records the
        # list that actually priced it rather than whichever is current later.
        price_list=PriceList.objects.get(pk=quote.price_list_id),
        code=code,
        request=request,
    )
    charge_service.charge_lines_from_quote(
        actor=actor,
        enrollment=enrollment,
        quote=quote,
        charged_on=enrolled_on,
        request=request,
    )
    return enrollment


def payable_enrollment_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    Enrolments the cashier may collect against, with what is still owed.

    Guarded by ``PAYMENT_NEW`` rather than ``ENROLLMENTS`` on purpose. §8 gives
    the cashier the till and nothing else — they hold no permission on the
    enrolments screen at all — yet taking a payment requires naming the
    enrolment it pays. Choosing a target is part of the payment screen's own
    authority, not a borrowed view of someone else's screen.

    Settled enrolments are omitted: collecting against a zero balance produces
    an unallocated credit that then has to be handed back at clearance.
    """
    from apps.billing.services.account_service import ZERO, get_account_state

    policy.require(actor, Screen.PAYMENT_NEW, Action.CREATE, request=request)

    pairs: list[tuple[str, str]] = []
    for enrollment in (
        Enrollment.objects.select_related("participant")
        .exclude(status=EnrollmentStatus.CANCELLED)
        .order_by("-enrolled_on")[:200]
    ):
        balance = get_account_state(enrollment).balance
        if balance <= ZERO:
            continue
        pairs.append(
            (enrollment.code, f"{enrollment.code} — {enrollment.participant.name_ar} ({balance})")
        )
    return pairs


def payable_enrollment(*, actor: Any, code: str, request: Any = None) -> Enrollment:
    """
    One enrolment, resolved under the PAYMENT screen's authority.

    The cashier's counterpart to :func:`get_enrollment` — see
    :func:`payable_enrollment_choices` for why the till does not borrow the
    enrolments screen's permission.
    """
    policy.require(actor, Screen.PAYMENT_NEW, Action.CREATE, request=request)
    return Enrollment.objects.select_related("participant", "cohort__program").get(code=code)


__all__ = [
    "AttendanceNotDocumentedError",
    "CohortNotApprovedError",
    "DuplicateEnrollmentError",
    "InvalidStatusTransitionError",
    "PaymentRequiredError",
    "VoucherRequiredError",
    "approve_enrollment",
    "change_status",
    "complete_enrollment",
    "create_enrollment",
    "dismiss_enrollment",
    "enroll_with_charges",
    "enrollment_choices",
    "enrollment_partition",
    "get_enrollment",
    "list_enrollments",
    "list_mohe_name_uploads",
    "mark_uploaded_to_mohe",
    "next_enrollment_code",
    "payable_enrollment",
    "payable_enrollment_choices",
    "record_attendance",
    "record_status_change",
    "record_voucher",
    "withdraw_enrollment",
]
