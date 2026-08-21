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
from apps.operations.models import (
    AttendanceSource,
    Cohort,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStatusHistory,
)
from apps.operations.services import mohe_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Enrollment"


class CohortNotApprovedError(ValidationError):
    """BR-013 / D-21 — enrolment attempted on an unapproved cohort."""


class VoucherRequiredError(ValidationError):
    """BR-018 — approval attempted with no voucher recorded."""


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
    code: str,
    request: Any = None,
) -> Enrollment:
    """
    WORKFLOWS §1.2 T1 — open an enrolment, if the ministry has approved.

    The gate runs BEFORE the transaction so a refusal's denied-attempt audit
    row survives the raise (BR-100).
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
    code: str,
    request: Any,
) -> Enrollment:
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


def record_voucher(*, actor: Any, enrollment: Enrollment, request: Any = None) -> Enrollment:
    """BR-018's precondition — the voucher is logged before anyone can approve."""
    policy.require(actor, Screen.ENROLLMENTS, Action.EDIT, request=request)
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

    if not enrollment.voucher_received:
        raise VoucherRequiredError("لا يمكن اعتماد التسجيل قبل تسجيل استلام الوصل (BR-018).")
    return _approve_enrollment(actor=actor, enrollment=enrollment, request=request)


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
    code: str,
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
    "InvalidStatusTransitionError",
    "VoucherRequiredError",
    "approve_enrollment",
    "change_status",
    "create_enrollment",
    "enroll_with_charges",
    "enrollment_choices",
    "get_enrollment",
    "list_enrollments",
    "mark_uploaded_to_mohe",
    "payable_enrollment",
    "payable_enrollment_choices",
    "record_attendance",
    "record_status_change",
    "record_voucher",
]
