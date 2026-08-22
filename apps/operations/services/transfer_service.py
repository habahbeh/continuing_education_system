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
from apps.catalog.services import pricing_service
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


#: Pricing refuses in plain ``Exception`` subclasses rather than in
#: ``ValidationError``, so every caller that prices something has to name them
#: or wear a 500. The transfer screen learned that the hard way (Sprint 8I).
PRICING_ERRORS = (pricing_service.NoEffectivePriceListError, pricing_service.ProgramNotPricedError)


class TransferRuleError(ValidationError):
    """A transfer that BR-060 … BR-065 forbid."""


class AttendanceNotDocumentedError(TransferRuleError):
    """
    BR-095 / Q-06 — the deadline cannot be judged on an undocumented count.

    Separate from the generic rule error because the answer is different: this
    is not "the transfer is refused", it is "the rule cannot be evaluated yet".
    """


def _quote_for(enrollment: Enrollment, cohort: Cohort, as_of: date) -> Any:
    return pricing_service.resolve_price(
        program=cohort.program,
        participant_category=enrollment.participant.category,
        as_of=as_of,
        level=cohort.level,
    )


def fee_difference_for(
    *, from_enrollment: Enrollment, to_cohort: Cohort, as_of: date
) -> dict[str, Decimal]:
    """
    BR-064 — what the move costs, in one implementation (Sprint 8H).

    Lifted verbatim out of ``_execute`` when the screens needed to SHOW the
    figure before anybody committed to it. A second copy in a preview would
    be a second opinion, and the two would disagree the first time either
    changed — which on this particular number means telling a participant one
    thing at the counter and charging them another.

    Positive means the participant owes the gap and it becomes an explicit
    ``TRANSFER_DIFFERENCE`` line; negative means the new course is cheaper and
    the surplus surfaces as a credit, because a charge cannot be negative.
    """
    from apps.billing.models import ChargeLine, ChargeType

    lines = list(ChargeLine.objects.filter(enrollment=from_enrollment, voided=False))
    old_tuition = sum(
        (line.net_amount for line in lines if line.charge_type == ChargeType.TUITION), ZERO
    )
    registration_carried = sum(
        (line.net_amount for line in lines if line.charge_type == ChargeType.REGISTRATION), ZERO
    )
    new_tuition = _quote_for(from_enrollment, to_cohort, as_of).course_fee

    return {
        "old_tuition": old_tuition,
        "new_tuition": new_tuition,
        "difference": new_tuition - old_tuition,
        "registration_carried": registration_carried,
    }


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
    money = fee_difference_for(from_enrollment=old, to_cohort=to_cohort, as_of=executed_on)
    old_tuition = money["old_tuition"]
    registration_carried = money["registration_carried"]
    new_tuition = money["new_tuition"]
    difference = money["difference"]

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


# ---------------------------------------------------------------------------
# The read layer the screens need (Sprint 8H)
# ---------------------------------------------------------------------------
def _row(transfer: Transfer) -> dict[str, Any]:
    old = transfer.from_enrollment
    return {
        "code": transfer.code,
        "participant_name": old.participant.name_ar,
        "participant_number": old.participant.participant_number,
        "from_enrollment_code": old.code,
        "from_cohort_code": old.cohort.code,
        "from_program_name": old.cohort.program.name_ar,
        "to_cohort_code": transfer.to_cohort.code,
        "to_program_name": transfer.to_cohort.program.name_ar,
        "to_enrollment_code": (
            transfer.to_enrollment.code if transfer.to_enrollment is not None else ""
        ),
        "requested_on": transfer.requested_on,
        "reason": transfer.reason,
        "reason_display": transfer.get_reason_display(),
        "status": transfer.status,
        "status_display": transfer.get_status_display(),
        "same_category": transfer.same_category,
        "category_waiver_granted": transfer.category_waiver_granted,
        "lectures_attended_at_request": transfer.lectures_attended_at_request,
        "fee_difference": transfer.fee_difference,
        "registration_fee_transferred": transfer.registration_fee_transferred,
        "rejection_reason_ar": transfer.rejection_reason_ar,
    }


def list_transfers(
    *, actor: Any, status: str = "", query: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Transfers as rows (§3.2/6), every status including the rejected ones."""
    policy.require(actor, Screen.TRANSFERS, Action.VIEW, request=request)

    queryset = Transfer.objects.select_related(
        "from_enrollment__participant",
        "from_enrollment__cohort__program",
        "to_cohort__program",
        "to_enrollment",
    )
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = (
            queryset.filter(code__icontains=query)
            | queryset.filter(from_enrollment__participant__name_ar__icontains=query)
            | queryset.filter(from_enrollment__code__icontains=query)
        )
    return [_row(t) for t in queryset.order_by("-requested_on", "-id")]


def get_transfer(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """
    One transfer with its frozen evidence and the money that actually moved.

    ``transferred_amount`` is read back from the ledger rather than from a
    remembered figure, and the two are shown side by side on purpose: the
    difference that was CHARGED and the money that MOVED answer different
    questions, and a screen showing only one invites the wrong conclusion.
    """
    from apps.core.display import person_name

    policy.require(actor, Screen.TRANSFERS, Action.VIEW, request=request)

    transfer = Transfer.objects.select_related(
        "from_enrollment__participant",
        "from_enrollment__cohort__program",
        "to_cohort__program",
        "to_enrollment",
        "requested_by",
        "manager_approved_by",
        "finance_settled_by",
        "category_waiver_by",
    ).get(code=code)

    detail = _row(transfer)
    detail.update(
        {
            "attendance_record_ref_at_request": transfer.attendance_record_ref_at_request,
            "category_waiver_reason_ar": transfer.category_waiver_reason_ar,
            "category_waiver_by": person_name(transfer.category_waiver_by),
            "requested_by": person_name(transfer.requested_by),
            "manager_approved_by": person_name(transfer.manager_approved_by),
            "manager_approved_at": transfer.manager_approved_at,
            "finance_settled_by": person_name(transfer.finance_settled_by),
            "finance_settled_at": transfer.finance_settled_at,
            "transferred_amount": transferred_amount(transfer),
            # WORKFLOWS §5.2 — which act is next, decided here so the template
            # never reads a status and infers a workflow.
            "awaits_manager": transfer.status == TransferStatus.PENDING_MANAGER,
            "awaits_finance": transfer.status == TransferStatus.PENDING_FINANCE,
            "is_closed": transfer.status in {TransferStatus.EXECUTED, TransferStatus.REJECTED},
        }
    )
    return detail


def reason_choices() -> list[tuple[str, str]]:
    """
    The two documented reasons, projected for a form (A-05).

    Trivial, and it earns its place: without it the view reaches into
    ``apps.operations.models`` for ``TransferReason`` — which the architecture
    test refuses, and rightly. A screen that imports an enum today imports a
    queryset tomorrow.
    """
    return [(value, str(label)) for value, label in TransferReason.choices]


def transfer_instance(*, actor: Any, code: str, request: Any = None) -> Transfer:
    """The model object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.TRANSFERS, Action.VIEW, request=request)
    return Transfer.objects.select_related("from_enrollment", "to_cohort").get(code=code)


def transferable_enrollment_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    (code, label) of enrolments a transfer could start from.

    Short courses only (BR-060), still live, and not already the source of a
    transfer that is on its way through. The lecture deadline is NOT applied
    here: BR-062 refuses the move with a number in the message — «حضر 5» —
    and silently dropping the enrolment from the list would replace that
    explanation with an absence the user cannot interpret.
    """
    from apps.catalog.models import ProgramType

    policy.require(actor, Screen.TRANSFER_NEW, Action.VIEW, request=request)

    in_flight = set(
        Transfer.objects.filter(
            status__in=(
                TransferStatus.DRAFT,
                TransferStatus.PENDING_MANAGER,
                TransferStatus.PENDING_FINANCE,
                TransferStatus.EXECUTED,
            )
        ).values_list("from_enrollment_id", flat=True)
    )
    return [
        (
            e.code,
            f"{e.code} — {e.participant.name_ar} · {e.cohort.code}",
        )
        for e in Enrollment.objects.select_related("participant", "cohort__program")
        .filter(
            cohort__program__program_type=ProgramType.SHORT_COURSE,
            status__in=(
                EnrollmentStatus.ACTIVE,
                EnrollmentStatus.PENDING_FINANCE,
                EnrollmentStatus.PENDING_APPROVAL,
                EnrollmentStatus.PAYMENT_OVERDUE,
            ),
        )
        .order_by("-enrolled_on")
        if e.pk not in in_flight
    ]


def destination_cohort_choices(
    *, actor: Any, from_code: str = "", request: Any = None
) -> list[tuple[str, str]]:
    """
    (code, label) of cohorts a transfer could land on.

    Short courses the ministry has approved — the two checks
    ``validate_transfer`` would otherwise refuse on — minus the source cohort
    itself. Category is deliberately not filtered: BR-061 allows a
    cross-category move on a centre cancellation with a recorded waiver, and a
    list that hid the option would hide the exception too.
    """
    from apps.catalog.models import ProgramType

    policy.require(actor, Screen.TRANSFER_NEW, Action.VIEW, request=request)

    source_cohort_id = None
    if from_code:
        source = Enrollment.objects.filter(code=from_code).first()
        source_cohort_id = source.cohort_id if source else None

    return [
        (c.code, f"{c.code} — {c.name_ar} ({c.program.name_ar})")
        for c in Cohort.objects.select_related("program")
        .filter(program__program_type=ProgramType.SHORT_COURSE)
        .order_by("-starts_on")
        if c.pk != source_cohort_id and mohe_service.cohort_is_approved(c)
    ]


def preview_transfer(
    *,
    actor: Any,
    from_enrollment: Enrollment,
    to_cohort: Cohort,
    reason: str,
    as_of: date,
    waiver_by: Any = None,
    waiver_reason_ar: str = "",
    request: Any = None,
) -> dict[str, Any]:
    """
    Run the rules and the arithmetic without writing anything (§4 dry-run).

    ``validate_transfer`` is called, not reimplemented, and the money comes
    from ``fee_difference_for`` — the same function the execution uses. A
    preview that computed its own answer would be a promise the execution had
    not made.

    A refusal is RETURNED rather than raised. The caller here is a screen
    asking "what would happen?", and the answer «BR-062: حضر 5» is the useful
    output of that question, not an error condition. ``request_transfer``
    still raises, so nothing depends on this having been called.
    """
    policy.require(actor, Screen.TRANSFER_NEW, Action.VIEW, request=request)

    result: dict[str, Any] = {
        "from_enrollment_code": from_enrollment.code,
        "to_cohort_code": to_cohort.code,
        "allowed": False,
        "refusal": "",
        "evidence": {},
        "money": {},
    }
    try:
        result["evidence"] = validate_transfer(
            from_enrollment=from_enrollment,
            to_cohort=to_cohort,
            reason=reason,
            as_of=as_of,
            waiver_by=waiver_by,
            waiver_reason_ar=waiver_reason_ar,
        )
    except ValidationError as refusal:
        result["refusal"] = " · ".join(str(m) for m in refusal.messages)
        return result

    try:
        result["money"] = fee_difference_for(
            from_enrollment=from_enrollment, to_cohort=to_cohort, as_of=as_of
        )
    except PRICING_ERRORS as unpriced:
        # The rules pass and the arithmetic cannot be done: no approved price
        # list covers the date, or the target programme has no item on it.
        # ``NoEffectivePriceListError`` is a plain Exception rather than a
        # ValidationError, so before Sprint 8I it escaped this function and
        # the transfer screen answered a preview with a 500. A preview exists
        # to report exactly this kind of "not yet" — it is an answer, not a
        # crash.
        result["refusal"] = str(unpriced)
        return result

    result["allowed"] = True
    return result


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
    "PRICING_ERRORS",
    "AttendanceNotDocumentedError",
    "TransferRuleError",
    "destination_cohort_choices",
    "execute_transfer",
    "fee_difference_for",
    "get_transfer",
    "list_transfers",
    "manager_recommend",
    "preview_transfer",
    "reason_choices",
    "reject_transfer",
    "request_transfer",
    "transfer_instance",
    "transferable_enrollment_choices",
    "transferred_amount",
    "validate_transfer",
]
