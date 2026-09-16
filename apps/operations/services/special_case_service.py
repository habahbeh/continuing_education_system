"""
Documented exceptions to the ordinary lifecycle (BR-067 … BR-071).

Each of the six kinds has a financial consequence, and each records that
consequence in words as well as in rows. ``financial_effect_ar`` is not
decoration: a deferral that moves 700 dinars and a substitution that moves a
seat look identical in a status column, and the person reading the file a year
later is usually asking about the money.

**Sprint 7 boundary.** A credit balance is CREATED here — by a deferral, a
substitution or a cheaper transfer — and the case row records it. RETURNING
it is BR-071, which happens at clearance, and clearance is Sprint 7. Nothing
in this module pays anything back.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.services.account_service import ZERO, get_account_state
from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.operations.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStatusHistory,
    SpecialCase,
    SpecialCaseStatus,
    SpecialCaseType,
)
from apps.operations.services.enrollment_service import (
    InvalidStatusTransitionError,
    record_status_change,
)
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.SpecialCase"

#: A special case is a filed document; its number is the system's to mint —
#: ``SC-YYYY-NNNN``, the year it occurred and a sequence within it — as the
#: enrolment and clearance codes are. Callers that carry a code from elsewhere
#: (a migration, a test) may still pass one.
SPECIAL_CASE_SCOPE = "special_case"
SPECIAL_CASE_PREFIX = "SC-"
SPECIAL_CASE_PADDING = 4
MAX_CODE_ATTEMPTS = 8


def special_case_partition(occurred_on: date) -> str:
    return str(occurred_on.year)


def next_special_case_code(occurred_on: date) -> str:
    """Mint the next ``SC-YYYY-NNNN``; call INSIDE the transaction that files the case."""
    partition = special_case_partition(occurred_on)
    for _attempt in range(MAX_CODE_ATTEMPTS):
        code = next_number(
            SPECIAL_CASE_SCOPE,
            partition,
            prefix=f"{SPECIAL_CASE_PREFIX}{partition}-",
            padding=SPECIAL_CASE_PADDING,
        )
        if not SpecialCase.objects.filter(code=code).exists():
            return code
    raise ValidationError(
        f"تعذّر توليد رمز حالة خاصة غير مكرَّر للسنة {partition} بعد {MAX_CODE_ATTEMPTS} محاولات."
    )


class DecisionReferenceRequiredError(ValidationError):
    """BR-067 — a dismissal without the decision that ordered it."""


def dismiss(
    *,
    actor: Any,
    enrollment: Enrollment,
    decision_reference: str,
    detail_ar: str,
    occurred_on: date,
    code: str = "",
    request: Any = None,
) -> SpecialCase:
    """
    BR-067 · BR-068 — dismissal, and what it costs.

    No refund follows a dismissal, the partner earns nothing on it (BR-045),
    and any outstanding balance stays a debt that blocks clearance. That is
    severe enough that the decision reference is mandatory in the service and
    in the database both.

    ``code`` is normally omitted and minted (:func:`next_special_case_code`).
    """
    policy.require(actor, Screen.SPECIAL_CASES, Action.CREATE, request=request)

    if not decision_reference.strip():
        raise DecisionReferenceRequiredError("الفصل يتطلب مرجع قرار مسجَّلاً (BR-067).")
    if not code:
        ensure_sequence(
            SPECIAL_CASE_SCOPE, special_case_partition(occurred_on), padding=SPECIAL_CASE_PADDING
        )
    return _dismiss(
        actor=actor,
        enrollment=enrollment,
        decision_reference=decision_reference.strip(),
        detail_ar=detail_ar,
        occurred_on=occurred_on,
        code=code,
        request=request,
    )


@transaction.atomic
def _dismiss(
    *,
    actor: Any,
    enrollment: Enrollment,
    decision_reference: str,
    detail_ar: str,
    occurred_on: date,
    code: str,
    request: Any,
) -> SpecialCase:
    state = get_account_state(enrollment)
    code = code or next_special_case_code(occurred_on)
    case = SpecialCase.objects.create(
        code=code,
        case_type=SpecialCaseType.DISMISSAL,
        enrollment=enrollment,
        occurred_on=occurred_on,
        detail_ar=detail_ar,
        financial_effect_ar=(
            f"لا استرداد (BR-068) · الرصيد القائم {state.balance} يبقى ديناً "
            "يمنع براءة الذمة · ولا استحقاق للشريك (BR-045)."
        ),
        decision_reference=decision_reference,
        created_by=actor,
    )
    record_status_change(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.DISMISSED,
        reason_ar=f"فصل — {decision_reference}",
        reference=code,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(case.pk),
        reference=code,
        summary_ar=f"فصل المشارك عن {enrollment.code} — {decision_reference}",
        actor=actor,
        changes={
            "enrollment": enrollment.code,
            "decision_reference": decision_reference,
            "balance_at_dismissal": str(state.balance),
        },
        request=request,
    )
    return case


def defer_to_cohort(
    *,
    actor: Any,
    enrollment: Enrollment,
    to_cohort: Cohort,
    occurred_on: date,
    case_code: str,
    new_enrollment_code: str,
    detail_ar: str = "",
    request: Any = None,
) -> tuple[SpecialCase, Enrollment]:
    """
    BR-069 — move a participant to a later cohort, money and all.

    The money moves the same way a transfer moves it: a reversing allocation
    on the old enrolment and a matching one on the new, against the same
    receipt. Nothing is edited and nothing is deleted.
    """
    policy.require(actor, Screen.SPECIAL_CASES, Action.CREATE, request=request)
    return _defer(
        actor=actor,
        enrollment=enrollment,
        to_cohort=to_cohort,
        occurred_on=occurred_on,
        case_code=case_code,
        new_enrollment_code=new_enrollment_code,
        detail_ar=detail_ar,
        request=request,
    )


@transaction.atomic
def _defer(
    *,
    actor: Any,
    enrollment: Enrollment,
    to_cohort: Cohort,
    occurred_on: date,
    case_code: str,
    new_enrollment_code: str,
    detail_ar: str,
    request: Any,
) -> tuple[SpecialCase, Enrollment]:
    from apps.cashbox.models import AllocationType, PaymentAllocation, ReceiptStatus

    new = Enrollment.objects.create(
        code=new_enrollment_code,
        participant=enrollment.participant,
        cohort=to_cohort,
        enrolled_on=occurred_on,
        price_list=enrollment.price_list,
        status=EnrollmentStatus.PENDING_APPROVAL,
        status_changed_at=timezone.now(),
        status_note_ar=f"ترحيل من {enrollment.code}",
        voucher_received=enrollment.voucher_received,
        voucher_received_at=enrollment.voucher_received_at,
        voucher_received_by=enrollment.voucher_received_by,
    )
    EnrollmentStatusHistory.objects.create(
        enrollment=new,
        from_status="",
        to_status=EnrollmentStatus.PENDING_APPROVAL,
        changed_by=actor,
        reason_ar=f"ترحيل من {enrollment.code}",
        reference=case_code,
    )

    carried: Decimal = ZERO
    for allocation in PaymentAllocation.objects.filter(
        enrollment=enrollment, receipt__status=ReceiptStatus.ISSUED
    ).select_related("receipt"):
        if allocation.amount == ZERO:
            continue
        PaymentAllocation.objects.create(
            receipt=allocation.receipt,
            charge_line=allocation.charge_line,
            enrollment=enrollment,
            amount=-allocation.amount,
            allocation_type=AllocationType.MANUAL,
            allocated_by=actor,
            manual_reason_ar=f"عكس تخصيص — ترحيل إلى {new.code} ({case_code})"[:255],
        )
        # Carried as an unallocated credit: the new cohort's charge lines do
        # not exist yet, and inventing a line to point at would be worse than
        # BR-023's own representation of money received against nothing.
        PaymentAllocation.objects.create(
            receipt=allocation.receipt,
            charge_line=None,
            enrollment=new,
            amount=allocation.amount,
            allocation_type=AllocationType.MANUAL,
            allocated_by=actor,
            manual_reason_ar=f"رصيد مُرحَّل من {enrollment.code} ({case_code})"[:255],
        )
        carried += allocation.amount

    for line in enrollment.charge_lines.filter(voided=False):
        line.voided = True
        line.voided_by = actor
        line.voided_at = timezone.now()
        line.void_reason_ar = f"ترحيل إلى {new.code} بموجب {case_code} (BR-069)"
        line.save(update_fields=["voided", "voided_by", "voided_at", "void_reason_ar"])

    enrollment.deferred_to = new
    enrollment.save(update_fields=["deferred_to"])
    record_status_change(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.DEFERRED,
        reason_ar=f"ترحيل إلى {new.code}",
        reference=case_code,
    )

    case = SpecialCase.objects.create(
        code=case_code,
        case_type=SpecialCaseType.DEFERRAL,
        enrollment=enrollment,
        related_enrollment=new,
        occurred_on=occurred_on,
        detail_ar=detail_ar or f"ترحيل إلى الدفعة {to_cohort.code}",
        financial_effect_ar=(
            f"نُقل المقبوض {carried} رصيداً للتسجيل {new.code} (BR-069) · "
            "وتُنشأ بنود الرسوم على الدفعة الجديدة بسعرها."
        ),
        created_by=actor,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(case.pk),
        reference=case_code,
        summary_ar=f"ترحيل {enrollment.code} ← {new.code} برصيد {carried}",
        actor=actor,
        changes={
            "from": enrollment.code,
            "to": new.code,
            "credit_carried": str(carried),
            "to_cohort": to_cohort.code,
        },
        request=request,
    )
    return case, new


def substitute(
    *,
    actor: Any,
    withdrawn_enrollment: Enrollment,
    incoming_participant: Any,
    occurred_on: date,
    case_code: str,
    new_enrollment_code: str,
    detail_ar: str = "",
    request: Any = None,
) -> tuple[SpecialCase, Enrollment]:
    """
    BR-070 — a replacement takes the seat, and NOT a second registration fee.

    The seat was already paid for administratively. Charging the incoming
    participant a fresh registration fee would bill the centre's admin work
    twice for one seat.
    """
    policy.require(actor, Screen.SPECIAL_CASES, Action.CREATE, request=request)

    if withdrawn_enrollment.status != EnrollmentStatus.WITHDRAWN:
        raise ValidationError("الإحلال لا يكون إلا على مقعد شاغر بانسحاب موثّق (BR-070).")
    return _substitute(
        actor=actor,
        withdrawn_enrollment=withdrawn_enrollment,
        incoming_participant=incoming_participant,
        occurred_on=occurred_on,
        case_code=case_code,
        new_enrollment_code=new_enrollment_code,
        detail_ar=detail_ar,
        request=request,
    )


@transaction.atomic
def _substitute(
    *,
    actor: Any,
    withdrawn_enrollment: Enrollment,
    incoming_participant: Any,
    occurred_on: date,
    case_code: str,
    new_enrollment_code: str,
    detail_ar: str,
    request: Any,
) -> tuple[SpecialCase, Enrollment]:
    new = Enrollment.objects.create(
        code=new_enrollment_code,
        participant=incoming_participant,
        cohort=withdrawn_enrollment.cohort,
        enrolled_on=occurred_on,
        price_list=withdrawn_enrollment.price_list,
        status=EnrollmentStatus.PENDING_FINANCE,
        status_changed_at=timezone.now(),
        status_note_ar=f"إحلال محل {withdrawn_enrollment.code}",
    )
    EnrollmentStatusHistory.objects.create(
        enrollment=new,
        from_status="",
        to_status=EnrollmentStatus.PENDING_FINANCE,
        changed_by=actor,
        reason_ar=f"إحلال محل {withdrawn_enrollment.code}",
        reference=case_code,
    )
    case = SpecialCase.objects.create(
        code=case_code,
        case_type=SpecialCaseType.SUBSTITUTION,
        enrollment=withdrawn_enrollment,
        related_enrollment=new,
        occurred_on=occurred_on,
        detail_ar=detail_ar or "إحلال مشارك بدل منسحب في نفس الدفعة",
        financial_effect_ar=(
            "المقعد ينتقل بلا رسم تسجيل جديد (BR-070) · وتُستوفى الرسوم الدراسية من المشارك الجديد."
        ),
        created_by=actor,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(case.pk),
        reference=case_code,
        summary_ar=f"إحلال {incoming_participant.name_ar} محل {withdrawn_enrollment.code}",
        actor=actor,
        changes={
            "seat_from": withdrawn_enrollment.code,
            "seat_to": new.code,
            "registration_fee_charged": False,
        },
        request=request,
    )
    return case, new


#: §6.5 «إلغاء التسجيل — قبل البدء»: the two statuses an application can be
#: cancelled from. Anything approved is running (ACTIVE) and leaves by
#: withdrawal or dismissal — those are §6.4's exits, with a clearance behind
#: them; this one has none, because nothing was ever delivered.
CANCELLABLE_STATUSES = {EnrollmentStatus.PENDING_FINANCE, EnrollmentStatus.PENDING_APPROVAL}


def cancel_registration(
    *,
    actor: Any,
    enrollment: Enrollment,
    reason_ar: str,
    occurred_on: date,
    code: str = "",
    request: Any = None,
) -> SpecialCase:
    """
    §6.5 · §5.3 — cancel an application before the centre approved it.

    Not a withdrawal: the trainee never became ACTIVE, so no partner earned
    anything and no clearance follows. The charges are voided so the account
    stops reading as a debt (and stops appearing overdue); any money already
    taken is NOT touched — it stays an unallocated credit on the statement,
    and leaves only through the refund workflow with its official letter and
    the president's approval (BR-034), or a credit transfer. «لا استرداد» is
    the default here exactly as it is everywhere else (§5.3).

    ``code`` is normally omitted and minted (:func:`next_special_case_code`).
    """
    policy.require(actor, Screen.SPECIAL_CASES, Action.CREATE, request=request)

    if not reason_ar.strip():
        raise ValidationError("إلغاء التسجيل يتطلب سبباً مكتوباً.")
    if enrollment.status not in CANCELLABLE_STATUSES:
        raise InvalidStatusTransitionError(
            f"لا يُلغى إلا تسجيل لم يُعتمد بعد؛ التسجيل {enrollment.code} في الحالة "
            f"«{enrollment.get_status_display()}» — المعتمَد يخرج بانسحاب أو فصل (§6.4)."
        )
    if not code:
        ensure_sequence(
            SPECIAL_CASE_SCOPE, special_case_partition(occurred_on), padding=SPECIAL_CASE_PADDING
        )
    return _cancel_registration(
        actor=actor,
        enrollment=enrollment,
        reason_ar=reason_ar.strip(),
        occurred_on=occurred_on,
        code=code,
        request=request,
    )


@transaction.atomic
def _cancel_registration(
    *,
    actor: Any,
    enrollment: Enrollment,
    reason_ar: str,
    occurred_on: date,
    code: str,
    request: Any,
) -> SpecialCase:
    from apps.billing.models import ChargeLine

    state = get_account_state(enrollment)
    code = code or next_special_case_code(occurred_on)

    # Voided, never deleted — the same device a transfer uses (BR-063): the
    # line stays on the statement with the reason it stopped counting.
    now = timezone.now()
    for line in ChargeLine.objects.filter(enrollment=enrollment, voided=False):
        line.voided = True
        line.voided_by = actor
        line.voided_at = now
        line.void_reason_ar = f"إلغاء التسجيل قبل الاعتماد ({code})"
        line.save(update_fields=["voided", "voided_by", "voided_at", "void_reason_ar"])

    paid = state.total_paid
    case = SpecialCase.objects.create(
        code=code,
        case_type=SpecialCaseType.CANCELLATION,
        enrollment=enrollment,
        occurred_on=occurred_on,
        detail_ar=reason_ar,
        financial_effect_ar=(
            f"لا استرداد افتراضياً (§5.3) · المقبوض {paid} يبقى رصيداً دائناً على التسجيل، "
            "ولا يُعاد إلا باسترداد رسمي بكتاب وموافقة الرئيس (BR-034) أو بتحويل رصيد · "
            "أُلغيت بنود الرسوم فلا ذمة · ولا استحقاق للشريك."
        ),
        created_by=actor,
    )
    record_status_change(
        actor=actor,
        enrollment=enrollment,
        to_status=EnrollmentStatus.CANCELLED,
        reason_ar=f"إلغاء قبل الاعتماد — {reason_ar}",
        reference=code,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(case.pk),
        reference=code,
        summary_ar=f"إلغاء التسجيل {enrollment.code} قبل الاعتماد — {reason_ar}",
        actor=actor,
        changes={
            "enrollment": enrollment.code,
            "balance_at_cancellation": str(state.balance),
            "paid_at_cancellation": str(paid),
        },
        request=request,
    )
    return case


def record_credit_balance(
    *, actor: Any, enrollment: Enrollment, occurred_on: date, code: str, request: Any = None
) -> SpecialCase | None:
    """
    Make a credit balance a case someone owns, rather than a negative number.

    ⏳ **DEFERRED to Sprint 7 — the RETURN itself (BR-071).** This records that
    the centre owes money and blocks nothing on its own; clearance is what
    refuses to close while a balance is non-zero (BR-073), and clearance does
    not exist yet.
    """
    policy.require(actor, Screen.SPECIAL_CASES, Action.CREATE, request=request)

    state = get_account_state(enrollment)
    if not state.centre_owes:
        return None
    return _record_credit(
        actor=actor,
        enrollment=enrollment,
        amount=-state.balance,
        occurred_on=occurred_on,
        code=code,
        request=request,
    )


@transaction.atomic
def _record_credit(
    *,
    actor: Any,
    enrollment: Enrollment,
    amount: Decimal,
    occurred_on: date,
    code: str,
    request: Any,
) -> SpecialCase:
    case = SpecialCase.objects.create(
        code=code,
        case_type=SpecialCaseType.CREDIT_BALANCE,
        enrollment=enrollment,
        occurred_on=occurred_on,
        detail_ar=f"رصيد دائن للمشارك بقيمة {amount}",
        financial_effect_ar=(
            f"على المركز إعادة {amount} — يُردّ عند براءة الذمة (BR-071) · "
            "⏳ آلية الإعادة في Sprint 7."
        ),
        status=SpecialCaseStatus.OPEN,
        created_by=actor,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(case.pk),
        reference=code,
        summary_ar=f"تسجيل رصيد دائن {amount} على {enrollment.code}",
        actor=actor,
        changes={"amount": str(amount), "returned": False},
        request=request,
    )
    return case


__all__ = [
    "CANCELLABLE_STATUSES",
    "DecisionReferenceRequiredError",
    "cancel_registration",
    "defer_to_cohort",
    "dismiss",
    "next_special_case_code",
    "record_credit_balance",
    "substitute",
]
