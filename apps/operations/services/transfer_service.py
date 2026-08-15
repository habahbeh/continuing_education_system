"""
Transfers between short courses (WORKFLOWS §5, BR-060 … BR-066).

**Nothing here edits or deletes a financial record.** Charge lines are voided
and re-raised on the new enrolment; allocations move as a REVERSING pair —
a negative row on the old enrolment and a positive row on the new, against the
same receipt. ``Σ allocations == receipt.amount`` still holds to the fils, the
original rows stay readable, and the pair reads as a history rather than a
number that changed by itself.

After the move the old enrolment has collected nothing, so **the partner earns
nothing on it without any exception rule** — the structural answer to Q-11.
⚠️ Q-11 is NOT client-settled: if the target cohort sits under a different
partner's agreement, that partner now earns the money. WORKFLOWS §5.4 F4 calls
this correct by construction, and it is recorded as an assumption, not a
decision.

``get_account_state``, the BR-022 allocation algorithm and every claim
calculation are untouched. This module writes new rows through the existing
vocabulary; it does not restate any equation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.services.account_service import ZERO
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.operations.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStatusHistory,
    Transfer,
    TransferReason,
    TransferStatus,
)
from apps.operations.services import mohe_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Transfer"

#: BR-062 — the deadline, in lectures. Seeded in Sprint 1; the centre's to change.
LECTURE_LIMIT_KEY = "transfer_lecture_limit"


class TransferRuleError(ValidationError):
    """A transfer that BR-060 … BR-065 forbid."""


class AttendanceNotDocumentedError(TransferRuleError):
    """
    BR-095 / Q-06 — the deadline cannot be judged on an undocumented count.

    Separate from the generic rule error because the answer is different: this
    is not "the transfer is refused", it is "the rule cannot be evaluated yet".
    """


def _quote_for(enrollment: Enrollment, cohort: Cohort, as_of: date) -> Any:
    from apps.catalog.services import pricing_service

    return pricing_service.resolve_price(
        program=cohort.program,
        participant_category=enrollment.participant.category,
        as_of=as_of,
        level=cohort.level,
    )


def validate_transfer(
    *,
    from_enrollment: Enrollment,
    to_cohort: Cohort,
    reason: str,
    as_of: date,
    waiver_by: Any = None,
    waiver_reason_ar: str = "",
) -> dict[str, Any]:
    """
    WORKFLOWS §5.3 — the five checks, in order, returning the evidence.

    The result is stored on the Transfer so the justification is frozen at the
    moment of the decision: a course recategorised next term must not
    retroactively justify — or condemn — a transfer already decided.
    """
    from apps.catalog.models import ProgramType

    from_cohort = from_enrollment.cohort

    # 1. Scope — short courses only (BR-060).
    if (
        from_cohort.program.program_type != ProgramType.SHORT_COURSE
        or to_cohort.program.program_type != ProgramType.SHORT_COURSE
    ):
        raise TransferRuleError("النقل متاح للدورات القصيرة فقط (BR-060).")

    # 2. It must actually be a move.
    if from_cohort.program_id == to_cohort.program_id and from_cohort.pk == to_cohort.pk:
        raise TransferRuleError("الدفعة الهدف هي الدفعة نفسها.")

    # 3. Category (BR-061), waivable only on a centre cancellation (BR-065).
    same_category = from_cohort.program.course_category_id == to_cohort.program.course_category_id
    waiver_granted = False
    if not same_category:
        if reason != TransferReason.CENTER_CANCELLATION:
            raise TransferRuleError(
                "النقل خارج نفس مجال الدورة — الاستثناء لا يكون إلا "
                "بإلغاء المركز للدورة (BR-061 · BR-065)."
            )
        if waiver_by is None or not waiver_reason_ar.strip():
            raise TransferRuleError(
                "الاستثناء يتطلب موافقة مدير المركز وسبباً مسجَّلاً (BR-065 · C-12)."
            )
        waiver_granted = True

    # 4. The lecture deadline (BR-062) — no waiver exists for this one.
    #    Q-06: the counter must be documented before the rule can be judged.
    if not from_enrollment.attendance_is_documented:
        raise AttendanceNotDocumentedError(
            "لا يمكن تقييم القاعدة — عدد المحاضرات غير موثَّق "
            "(المصدر والمُتحقِّق والتاريخ إلزامية — BR-095 · Q-06)."
        )
    limit = int(get_setting(LECTURE_LIMIT_KEY, as_of=as_of, default=3))
    attended = from_enrollment.lectures_attended or 0
    if attended > limit:
        raise TransferRuleError(
            f"تجاوز مهلة {limit} محاضرات (حضر {attended}) — بلا استثناء (BR-062)."
        )

    # 5. The target cohort must itself be approved (BR-013).
    if not mohe_service.cohort_is_approved(to_cohort):
        raise TransferRuleError(f"الدفعة الهدف {to_cohort.code} لم تُعتمد من الوزارة (BR-013).")

    return {
        "same_category": same_category,
        "category_waiver_granted": waiver_granted,
        "lectures_attended_at_request": attended,
        "attendance_record_ref_at_request": from_enrollment.attendance_record_ref,
    }


def request_transfer(
    *,
    actor: Any,
    from_enrollment: Enrollment,
    to_cohort: Cohort,
    reason: str,
    requested_on: date,
    code: str,
    waiver_by: Any = None,
    waiver_reason_ar: str = "",
    request: Any = None,
) -> Transfer:
    """WORKFLOWS §5.2 X1–X2 — raise the request, having passed the engine."""
    policy.require(actor, Screen.TRANSFER_NEW, Action.CREATE, request=request)

    evidence = validate_transfer(
        from_enrollment=from_enrollment,
        to_cohort=to_cohort,
        reason=reason,
        as_of=requested_on,
        waiver_by=waiver_by,
        waiver_reason_ar=waiver_reason_ar,
    )
    return _request_transfer(
        actor=actor,
        from_enrollment=from_enrollment,
        to_cohort=to_cohort,
        reason=reason,
        requested_on=requested_on,
        code=code,
        evidence=evidence,
        waiver_by=waiver_by,
        waiver_reason_ar=waiver_reason_ar.strip(),
        request=request,
    )


@transaction.atomic
def _request_transfer(
    *,
    actor: Any,
    from_enrollment: Enrollment,
    to_cohort: Cohort,
    reason: str,
    requested_on: date,
    code: str,
    evidence: dict[str, Any],
    waiver_by: Any,
    waiver_reason_ar: str,
    request: Any,
) -> Transfer:
    transfer = Transfer.objects.create(
        code=code,
        from_enrollment=from_enrollment,
        to_cohort=to_cohort,
        requested_on=requested_on,
        requested_by=actor,
        reason=reason,
        status=TransferStatus.PENDING_MANAGER,
        category_waiver_by=waiver_by if evidence["category_waiver_granted"] else None,
        category_waiver_reason_ar=(waiver_reason_ar if evidence["category_waiver_granted"] else ""),
        **evidence,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(transfer.pk),
        reference=transfer.code,
        summary_ar=(
            f"طلب نقل {from_enrollment.code} ← {to_cohort.code} — "
            f"{'نفس المجال' if evidence['same_category'] else 'مجال مختلف باستثناء'}"
        ),
        actor=actor,
        changes={
            "from": from_enrollment.code,
            "to_cohort": to_cohort.code,
            "reason": reason,
            "same_category": evidence["same_category"],
            "lectures_attended": evidence["lectures_attended_at_request"],
            "attendance_ref": evidence["attendance_record_ref_at_request"],
        },
        request=request,
    )

    if evidence["category_waiver_granted"]:
        # WORKFLOWS §5.6 asks for a dedicated WAIVE code. Adding one means a
        # core migration, which this sprint was scoped not to touch, so the
        # event is carried in `changes` and stays queryable. Promoting it to a
        # first-class action is a follow-up, recorded in the sprint notes.
        write_audit(
            action="APPROVE",
            entity_type=ENTITY,
            entity_id=str(transfer.pk),
            reference=transfer.code,
            summary_ar="منح استثناء قيد المجال (BR-065)",
            actor=waiver_by,
            changes={
                "event": "WAIVE",
                "waiver_by": getattr(waiver_by, "username", None),
                "waiver_reason": waiver_reason_ar,
                "rule": "BR-065",
            },
            request=request,
        )
    return transfer


def manager_recommend(*, actor: Any, transfer: Transfer, request: Any = None) -> Transfer:
    """WORKFLOWS §5.2 X3 — the centre manager's recommendation (BR-066)."""
    policy.require(actor, Screen.TRANSFERS, Action.APPROVE, request=request)

    if transfer.status != TransferStatus.PENDING_MANAGER:
        raise ValidationError("التنسيب لا يكون إلا على طلب بانتظار المدير.")
    return _manager_recommend(actor=actor, transfer=transfer, request=request)


@transaction.atomic
def _manager_recommend(*, actor: Any, transfer: Transfer, request: Any) -> Transfer:
    transfer.status = TransferStatus.PENDING_FINANCE
    transfer.manager_approved_by = actor
    transfer.manager_approved_at = timezone.now()
    transfer.save(update_fields=["status", "manager_approved_by", "manager_approved_at"])

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(transfer.pk),
        reference=transfer.code,
        summary_ar="تنسيب مدير المركز على طلب النقل",
        actor=actor,
        request=request,
    )
    return transfer


def reject_transfer(
    *, actor: Any, transfer: Transfer, reason_ar: str, request: Any = None
) -> Transfer:
    """WORKFLOWS §5.2 X5 — a rejection always carries its reason."""
    policy.require(actor, Screen.TRANSFERS, Action.APPROVE, request=request)

    if transfer.status == TransferStatus.EXECUTED:
        raise ValidationError("لا يُرفض نقل منفَّذ.")
    if not reason_ar.strip():
        raise ValidationError("سبب الرفض إلزامي.")
    return _reject(actor=actor, transfer=transfer, reason_ar=reason_ar.strip(), request=request)


@transaction.atomic
def _reject(*, actor: Any, transfer: Transfer, reason_ar: str, request: Any) -> Transfer:
    transfer.status = TransferStatus.REJECTED
    transfer.rejection_reason_ar = reason_ar
    transfer.save(update_fields=["status", "rejection_reason_ar"])

    write_audit(
        action="REJECT",
        entity_type=ENTITY,
        entity_id=str(transfer.pk),
        reference=transfer.code,
        summary_ar=f"رفض طلب النقل — {reason_ar}",
        actor=actor,
        changes={"reason": reason_ar},
        request=request,
    )
    return transfer


def execute_transfer(
    *, actor: Any, transfer: Transfer, executed_on: date, new_code: str, request: Any = None
) -> Transfer:
    """
    WORKFLOWS §5.2 X4 / §5.4 — settle and execute, in one atomic transaction.

    A half-executed transfer is the worst outcome available: money on one
    enrolment and a seat on another. Everything below either happens or none
    of it does.
    """
    policy.require(actor, Screen.TRANSFERS, Action.EDIT, request=request)

    if transfer.status != TransferStatus.PENDING_FINANCE:
        raise ValidationError("التنفيذ لا يكون إلا بعد تنسيب المدير وقبل التسوية المالية (BR-066).")
    return _execute(
        actor=actor,
        transfer=transfer,
        executed_on=executed_on,
        new_code=new_code,
        request=request,
    )


@transaction.atomic
def _execute(
    *, actor: Any, transfer: Transfer, executed_on: date, new_code: str, request: Any
) -> Transfer:
    from apps.billing.models import ChargeLine, ChargeType
    from apps.cashbox.models import AllocationType, PaymentAllocation, ReceiptStatus

    old = transfer.from_enrollment
    to_cohort = transfer.to_cohort

    # --- 1. the new enrolment ------------------------------------------------
    # Created directly rather than through create_enrollment(): the
    # authorisation being exercised is the TRANSFER's, already checked above
    # and recommended by the manager, not a fresh registration by whoever
    # happens to be settling it. BR-013 was verified on the target cohort at
    # request time and is re-verified here.
    if not mohe_service.cohort_is_approved(to_cohort):
        raise ValidationError(f"الدفعة الهدف {to_cohort.code} لم تعد معتمدة وزارياً (BR-013).")

    new = Enrollment.objects.create(
        code=new_code,
        participant=old.participant,
        cohort=to_cohort,
        enrolled_on=executed_on,
        price_list=old.price_list,
        status=EnrollmentStatus.ACTIVE,
        status_changed_at=timezone.now(),
        status_note_ar=f"نقل من {old.code} ({transfer.code})",
        # The voucher and the money came with them; re-demanding either would
        # be charging twice for one payment.
        voucher_received=old.voucher_received,
        voucher_received_at=old.voucher_received_at,
        voucher_received_by=old.voucher_received_by,
        approved_by=actor if old.voucher_received else None,
        approved_at=timezone.now() if old.voucher_received else None,
    )
    EnrollmentStatusHistory.objects.create(
        enrollment=new,
        from_status="",
        to_status=EnrollmentStatus.ACTIVE,
        changed_by=actor,
        reason_ar=f"نقل من {old.code}",
        reference=transfer.code,
    )

    # --- 2. the charge lines: void on the old, re-raise on the new ----------
    old_lines = list(ChargeLine.objects.filter(enrollment=old, voided=False))
    old_tuition = sum(
        (line.net_amount for line in old_lines if line.charge_type == ChargeType.TUITION),
        ZERO,
    )
    registration_carried = sum(
        (line.net_amount for line in old_lines if line.charge_type == ChargeType.REGISTRATION),
        ZERO,
    )

    quote = _quote_for(old, to_cohort, executed_on)
    new_tuition = quote.course_fee
    difference = new_tuition - old_tuition

    mirror: dict[int, ChargeLine] = {}
    for line in old_lines:
        # BR-064 — when the new course is CHEAPER the tuition is re-raised at
        # the new price, because a charge cannot be negative
        # (billing_charge_amounts_not_negative) and the surplus must surface
        # as a credit rather than vanish. When it is dearer the old amount is
        # carried and the gap becomes an explicit TRANSFER_DIFFERENCE line,
        # exactly as WORKFLOWS §5.4 describes. Either way the new enrolment's
        # total is `carried registration + the new course's price`.
        amount = line.net_amount
        if line.charge_type == ChargeType.TUITION and difference < ZERO:
            amount = new_tuition

        mirror[line.pk] = ChargeLine.objects.create(
            enrollment=new,
            charge_type=line.charge_type,
            description_ar=f"{line.description_ar} — مُرحَّل من {old.code}",
            net_amount=amount,
            is_taxable=line.is_taxable,
            tax_rate_snapshot=line.tax_rate_snapshot,
            tax_amount=line.tax_amount if amount == line.net_amount else ZERO,
            gross_amount=(line.gross_amount if amount == line.net_amount else amount),
            charged_on=executed_on,
            subject=line.subject,
            is_partner_shareable=line.is_partner_shareable,
            is_revenue=line.is_revenue,
            deposit_policy_snapshot=line.deposit_policy_snapshot,
        )

        line.voided = True
        line.voided_by = actor
        line.voided_at = timezone.now()
        line.void_reason_ar = f"نقل إلى {new.code} بموجب {transfer.code} (BR-063)"
        line.save(update_fields=["voided", "voided_by", "voided_at", "void_reason_ar"])

    # --- 3. the money: a reversing pair per allocation -----------------------
    moved = ZERO
    live_allocations = PaymentAllocation.objects.filter(
        enrollment=old, receipt__status=ReceiptStatus.ISSUED
    ).select_related("receipt")

    for allocation in live_allocations:
        if allocation.amount == ZERO:
            continue
        target_line = mirror.get(allocation.charge_line_id) if allocation.charge_line_id else None

        PaymentAllocation.objects.create(
            receipt=allocation.receipt,
            charge_line=allocation.charge_line,
            enrollment=old,
            amount=-allocation.amount,
            allocation_type=AllocationType.MANUAL,
            allocated_by=actor,
            manual_reason_ar=f"عكس تخصيص — نقل إلى {new.code} ({transfer.code})"[:255],
            reversed_by=None,
        )

        # A payment larger than the re-raised line keeps its surplus as an
        # unallocated credit — BR-023's own representation, not a new concept.
        remaining = allocation.amount
        if target_line is not None:
            portion = min(remaining, target_line.gross_amount)
            if portion > ZERO:
                PaymentAllocation.objects.create(
                    receipt=allocation.receipt,
                    charge_line=target_line,
                    enrollment=new,
                    amount=portion,
                    allocation_type=AllocationType.MANUAL,
                    allocated_by=actor,
                    manual_reason_ar=f"تخصيص مُرحَّل من {old.code} ({transfer.code})"[:255],
                )
                remaining -= portion
        if remaining > ZERO:
            PaymentAllocation.objects.create(
                receipt=allocation.receipt,
                charge_line=None,
                enrollment=new,
                amount=remaining,
                allocation_type=AllocationType.MANUAL,
                allocated_by=actor,
                manual_reason_ar=f"رصيد دائن مُرحَّل من {old.code} ({transfer.code})"[:255],
            )
        moved += allocation.amount

    # --- 4. BR-064 — the difference the participant owes ---------------------
    if difference > ZERO:
        ChargeLine.objects.create(
            enrollment=new,
            charge_type=ChargeType.TRANSFER_DIFFERENCE,
            description_ar=(
                f"فرق نقل — {to_cohort.program.name_ar} {new_tuition} مقابل {old_tuition}"
            )[:255],
            net_amount=difference,
            is_taxable=False,
            tax_amount=ZERO,
            gross_amount=difference,
            charged_on=executed_on,
            is_partner_shareable=True,
            is_revenue=True,
        )

    # --- 5. close the old enrolment -----------------------------------------
    old.transferred_to = new
    old.save(update_fields=["transferred_to"])
    from apps.operations.services.enrollment_service import record_status_change

    record_status_change(
        actor=actor,
        enrollment=old,
        to_status=EnrollmentStatus.TRANSFERRED_OUT,
        reason_ar=f"نقل إلى {new.code}",
        reference=transfer.code,
    )

    obligation = _recover_partner_share_if_claimed(
        actor=actor, transfer=transfer, old_enrollment=old, occurred_on=executed_on
    )

    transfer.to_enrollment = new
    transfer.status = TransferStatus.EXECUTED
    transfer.finance_settled_by = actor
    transfer.finance_settled_at = timezone.now()
    transfer.registration_fee_transferred = registration_carried
    transfer.fee_difference = difference
    transfer.save(
        update_fields=[
            "to_enrollment",
            "status",
            "finance_settled_by",
            "finance_settled_at",
            "registration_fee_transferred",
            "fee_difference",
        ]
    )

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(transfer.pk),
        reference=transfer.code,
        summary_ar=(f"تنفيذ النقل {old.code} ← {new.code} — فرق {difference} · مُرحَّل {moved}"),
        actor=actor,
        changes={
            "event": "EXECUTE",
            "new_enrollment": new.code,
            "registration_transferred": str(registration_carried),
            "fee_difference": str(difference),
            "allocations_moved": str(moved),
            "lines_voided": len(old_lines),
            "partner_obligation": obligation.code if obligation else None,
        },
        request=request,
    )
    return transfer


def _recover_partner_share_if_claimed(
    *, actor: Any, transfer: Transfer, old_enrollment: Enrollment, occurred_on: date
) -> Any:
    """
    WORKFLOWS §5.4 F3 — the partner was already paid for money that just left.

    An approved claim is frozen (BR-051), so it is NOT recomputed. The
    correction is an obligation recovered from the NEXT claim, which is the
    same mechanism every other partner recovery uses (BR-036), pinned to the
    agreement that produced the claim.
    """
    from apps.settlements.models import (
        ClaimStatus,
        ObligationType,
        PartnerClaimLine,
        PartnerObligation,
    )

    claimed = (
        PartnerClaimLine.objects.filter(
            enrollment=old_enrollment,
            is_included=True,
            claim__status__in=[ClaimStatus.APPROVED, ClaimStatus.PAID],
        )
        .select_related("claim", "claim__agreement")
        .order_by("-claim__approved_at")
        .first()
    )
    if claimed is None or claimed.partner_share <= ZERO:
        return None

    claim = claimed.claim
    return PartnerObligation.objects.create(
        code=f"OBL-TR-{transfer.code}"[:32],
        partner=claim.partner,
        restricted_to_agreement=claim.agreement,
        obligation_type=ObligationType.WITHDRAWAL_RETURN,
        cohort=old_enrollment.cohort,
        amount=claimed.partner_share,
        statement_reference=(
            f"نقل {old_enrollment.code} بعد اعتماد المطالبة {claim.code} — "
            f"حصة {claimed.partner_share} عن {claimed.participant_name_snapshot}"
        )[:255],
        occurred_on=occurred_on,
        created_by=actor,
    )


def transferred_amount(transfer: Transfer) -> Decimal:
    """What actually moved, read back from the ledger rather than remembered."""
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    if transfer.to_enrollment_id is None:
        return ZERO
    total = PaymentAllocation.objects.filter(
        enrollment_id=transfer.to_enrollment_id, receipt__status=ReceiptStatus.ISSUED
    ).aggregate(total=Sum("amount"))["total"]
    return total or ZERO


__all__ = [
    "LECTURE_LIMIT_KEY",
    "AttendanceNotDocumentedError",
    "TransferRuleError",
    "execute_transfer",
    "manager_recommend",
    "reject_transfer",
    "request_transfer",
    "transferred_amount",
    "validate_transfer",
]
