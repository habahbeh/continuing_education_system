"""
Exceptional refunds (BR-033 … BR-036, §5.3).

§5.3 starts from «الأصل: لا استرداد» — the participant pledges it on the
admission form, and ``Participant.no_refund_pledge_accepted`` records the
pledge. A refund is therefore an exception carrying two EXTERNAL documents:
an official letter and the president's approval. Constraint
``billing_refund_has_external_approvals`` refuses a refund without both, so a
caller who skipped this module still could not write one.

**Three steps, three people's worth of control.** Requested, then approved by
someone else (``billing_refund_approver_differs``), then executed. Only
execution moves money.

**How the money moves.** Reversing ``PaymentAllocation`` rows, the same
pattern as a void (BR-025) and a credit return. Nothing is edited and nothing
is deleted; the original allocation stays readable beside its reversal.

**Recovering the partner's share — and the double-recovery trap.**

Entitlement is cash basis, so a reversal lowers ``shareable_collected``
immediately. If the enrolment has NOT yet appeared on an approved claim, that
is the whole correction: the partner simply never receives a share of money
the participant got back, and raising an obligation on top would recover the
same dinar twice.

An obligation is raised only for what the partner has ALREADY been paid —
that is, when the enrolment appears on a claim that is approved or paid. §5.3
and clause 4 of the تناغم agreement both say the same thing about how it comes
back: «بالحسم من المطالبات اللاحقة» — by deduction from later claims, never as
an invoice or a cash demand.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import Refund, RefundStatus
from apps.billing.services.account_service import ZERO, get_account_state
from apps.core.display import person_name
from apps.core.money import round_money
from apps.core.services import period_service
from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "billing.Refund"

#: Refund codes are the system's own, yearly and gapless like receipt numbers
#: (Q-03): ``RF-2026-00001``. Nobody types one.
REFUND_SCOPE = "refund"
REFUND_FILTERS = ("REQUESTED", "APPROVED", "EXECUTED", "REJECTED")

HUNDRED = Decimal("100")


class RefundExceedsPaidError(ValidationError):
    """More was asked back than was ever received."""


class RefundStateError(ValidationError):
    """A step attempted out of order."""


def refundable_amount(enrollment: Any) -> Decimal:
    """
    The most that can be handed back — what was actually collected.

    Read from the account rather than the charge lines: a participant who paid
    half is owed at most that half, whatever the invoice said.
    """
    state = get_account_state(enrollment)
    return state.total_paid if state.total_paid > ZERO else ZERO


def already_claimed(enrollment: Any) -> bool:
    """
    Whether a partner has already been paid for this enrolment.

    APPROVED is the threshold rather than PAID: approval seals the claim and
    makes it the basis of a settlement, so the money is committed even if the
    transfer has not cleared.
    """
    from apps.settlements.models import ClaimStatus, PartnerClaimLine

    return PartnerClaimLine.objects.filter(
        enrollment=enrollment,
        is_included=True,
        claim__status__in=(ClaimStatus.APPROVED, ClaimStatus.PAID),
    ).exists()


def partner_recovery_for(*, enrollment: Any, amount: Decimal) -> Decimal:
    """
    The partner's share of a refund that must be recovered from later claims.

    Zero unless the partner has already been paid for this enrolment — see
    the double-recovery note in this module's docstring. Zero as well for
    per-student and commission agreements: their share never read the base
    this refund reduces, so nothing about it changed. A per-student advance
    paid for someone who then withdrew is ``clawback_service``'s subject, not
    this one.
    """
    from apps.partners.models import CalculationModel

    agreement = getattr(enrollment.cohort, "agreement", None)
    if agreement is None:
        return ZERO
    if agreement.calculation_model != CalculationModel.PERCENT:
        return ZERO
    if not already_claimed(enrollment):
        return ZERO
    return round_money(amount * (agreement.percent_rate or ZERO) / HUNDRED)


def _reversal_order(queryset: Any) -> list[Any]:
    """
    Which allocations a partial refund unwinds first, and why it is decided.

    A refund smaller than the total has to come out of SOME line, and the
    choice is not cosmetic: taking it from tuition lowers the partner's base,
    taking it from the registration fee does not. Left to the natural row
    order it would fall out of ``ALLOCATION_ORDER`` by accident — registration
    first, quietly protecting the partner at the university's expense.

    Decided instead as the mirror of allocation (BR-022): a refund unwinds in
    the REVERSE of the order the payment was applied, so the registration fee
    — satisfied first when the money came in — is the last thing given back.

    That is also the reading the documents support. §6.3 has registration fees
    TRANSFER with a participant who moves course rather than being refunded,
    which is only coherent if the centre treats them as earned on enrolment.
    No source states the order outright, so this is a conservative default
    recorded here rather than an inference left implicit.
    """
    from apps.billing.models import ALLOCATION_ORDER

    def key(allocation: Any) -> tuple[int, int]:
        line = allocation.charge_line
        # Unallocated credit carries no line; it is refunded before anything
        # attached to a charge, being money that was never owed.
        rank = (
            ALLOCATION_ORDER.index(line.charge_type) if line is not None else len(ALLOCATION_ORDER)
        )
        return (-rank, -allocation.pk)

    return sorted(queryset, key=key)


def request_refund(
    *,
    actor: Any,
    enrollment: Any,
    refund_type: str,
    amount: Decimal,
    reason_ar: str,
    official_letter_ref: str,
    official_letter_date: date,
    president_approval_ref: str,
    president_approval_date: date,
    code: str = "",
    request: Any = None,
) -> Refund:
    """
    WORKFLOWS §4 — raise the request, with both external documents (BR-034).

    ``code`` is normally left empty and drawn from the yearly sequence; a
    caller may still pass one (migrated rows, fixtures).
    """
    policy.require(actor, Screen.REFUNDS, Action.CREATE, request=request)

    if not reason_ar.strip():
        raise ValidationError("سبب الاسترداد إلزامي.")
    if not (official_letter_ref or "").strip():
        raise ValidationError("مرجع الكتاب الرسمي إلزامي للاسترداد (BR-034 · §5.3).")
    if not (president_approval_ref or "").strip():
        raise ValidationError("رقم موافقة رئيس الجامعة إلزامي للاسترداد (BR-034 · §5.3).")
    if amount <= ZERO:
        raise ValidationError("مبلغ الاسترداد يجب أن يكون موجباً.")

    paid = refundable_amount(enrollment)
    if amount > paid:
        raise RefundExceedsPaidError(
            f"الاسترداد {amount} يتجاوز المقبوض فعلياً {paid} على التسجيل {enrollment.code}."
        )

    if not code.strip():
        ensure_sequence(REFUND_SCOPE, str(timezone.localdate().year), padding=5)

    return _request(
        actor=actor,
        enrollment=enrollment,
        refund_type=refund_type,
        amount=amount,
        reason_ar=reason_ar.strip(),
        official_letter_ref=official_letter_ref.strip(),
        official_letter_date=official_letter_date,
        president_approval_ref=president_approval_ref.strip(),
        president_approval_date=president_approval_date,
        code=code.strip(),
        request=request,
    )


@transaction.atomic
def _request(
    *,
    actor: Any,
    enrollment: Any,
    refund_type: str,
    amount: Decimal,
    reason_ar: str,
    official_letter_ref: str,
    official_letter_date: date,
    president_approval_ref: str,
    president_approval_date: date,
    code: str,
    request: Any,
) -> Refund:
    if not code:
        year = str(timezone.localdate().year)
        code = next_number(REFUND_SCOPE, year, prefix=f"RF-{year}-", padding=5)
    refund = Refund.objects.create(
        code=code,
        enrollment=enrollment,
        refund_type=refund_type,
        amount=amount,
        reason_ar=reason_ar,
        official_letter_ref=official_letter_ref,
        official_letter_date=official_letter_date,
        president_approval_ref=president_approval_ref,
        president_approval_date=president_approval_date,
        requested_by=actor,
        status=RefundStatus.REQUESTED,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(refund.pk),
        reference=code,
        summary_ar=f"طلب استرداد {amount} — {reason_ar}",
        actor=actor,
        changes={
            "amount": str(amount),
            "enrollment": enrollment.code,
            "official_letter_ref": official_letter_ref,
            "president_approval_ref": president_approval_ref,
        },
        request=request,
    )
    return refund


def approve_refund(*, actor: Any, refund: Refund, request: Any = None) -> Refund:
    """D-18 — the approver is never the requester."""
    policy.require(actor, Screen.REFUNDS, Action.APPROVE, request=request)

    if refund.status != RefundStatus.REQUESTED:
        raise RefundStateError(f"لا يُعتمد استرداد حالته {refund.status}.")
    if refund.requested_by_id == getattr(actor, "pk", None):
        raise ValidationError("لا يعتمد الاستردادَ من طلبه (D-18).")

    return _approve(actor=actor, refund=refund, request=request)


@transaction.atomic
def _approve(*, actor: Any, refund: Refund, request: Any) -> Refund:
    refund.status = RefundStatus.APPROVED
    refund.approved_by = actor
    refund.approved_at = timezone.now()
    refund.save(update_fields=["status", "approved_by", "approved_at"])

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(refund.pk),
        reference=refund.code,
        summary_ar=f"اعتماد استرداد {refund.amount}",
        actor=actor,
        changes={"amount": str(refund.amount), "requester": refund.requested_by_id},
        request=request,
    )
    return refund


def reject_refund(*, actor: Any, refund: Refund, reason_ar: str, request: Any = None) -> Refund:
    """A refusal is a decision and gets recorded like one."""
    policy.require(actor, Screen.REFUNDS, Action.APPROVE, request=request)

    if refund.status != RefundStatus.REQUESTED:
        raise RefundStateError(f"لا يُرفض استرداد حالته {refund.status}.")
    if not reason_ar.strip():
        raise ValidationError("سبب الرفض إلزامي.")

    return _reject(actor=actor, refund=refund, reason_ar=reason_ar.strip(), request=request)


@transaction.atomic
def _reject(*, actor: Any, refund: Refund, reason_ar: str, request: Any) -> Refund:
    refund.status = RefundStatus.REJECTED
    refund.rejected_by = actor
    refund.rejected_at = timezone.now()
    refund.rejection_reason_ar = reason_ar
    refund.save(update_fields=["status", "rejected_by", "rejected_at", "rejection_reason_ar"])

    write_audit(
        action="REJECT",
        entity_type=ENTITY,
        entity_id=str(refund.pk),
        reference=refund.code,
        summary_ar=f"رفض استرداد {refund.amount} — {reason_ar}",
        actor=actor,
        changes={"reason": reason_ar},
        request=request,
    )
    return refund


def execute_refund(*, actor: Any, refund: Refund, executed_on: date, request: Any = None) -> Refund:
    """
    Hand the money back and record what the partner must return.

    The reversal is written first and the obligation second, in one
    transaction: a partner obligation for money that never actually left would
    be a demand nobody owes.

    EDIT rather than APPROVE, deliberately: §8 gives the centre manager the
    approval and the finance officer the money movement, so the person who
    authorised the refund is not the person who pays it out.
    """
    policy.require(actor, Screen.REFUNDS, Action.EDIT, request=request)

    # D-23 (Sprint 8D-6) — cash leaving on a date in a closed month would
    # restate a period already signed off. Among the guards, before any
    # transaction, so the refusal's audit row survives the raise (BR-085).
    period_service.require_open(
        executed_on,
        actor=actor,
        what_ar="تنفيذ استرداد",
        entity_type="billing.Refund",
        reference=refund.code,
        request=request,
    )

    if refund.status != RefundStatus.APPROVED:
        raise RefundStateError(
            f"لا يُنفَّذ استرداد حالته {refund.status} — الاعتماد يسبق التنفيذ (BR-034)."
        )

    paid = refundable_amount(refund.enrollment)
    if refund.amount > paid:
        raise RefundExceedsPaidError(
            f"تغيّر المقبوض منذ الاعتماد — الاسترداد {refund.amount} يتجاوز {paid}."
        )

    return _execute(actor=actor, refund=refund, executed_on=executed_on, request=request)


@transaction.atomic
def _execute(*, actor: Any, refund: Refund, executed_on: date, request: Any) -> Refund:
    from apps.cashbox.models import AllocationType, PaymentAllocation, ReceiptStatus
    from apps.settlements.services import clawback_service

    enrollment = refund.enrollment
    recovery = partner_recovery_for(enrollment=enrollment, amount=refund.amount)

    remaining = refund.amount
    rows = _reversal_order(
        PaymentAllocation.objects.filter(
            enrollment=enrollment, receipt__status=ReceiptStatus.ISSUED, amount__gt=0
        ).select_related("receipt", "charge_line")
    )

    for row in rows:
        if remaining <= ZERO:
            break
        portion = min(remaining, row.amount)
        PaymentAllocation.objects.create(
            receipt=row.receipt,
            charge_line=row.charge_line,
            enrollment=enrollment,
            amount=-portion,
            allocation_type=AllocationType.MANUAL,
            allocated_by=actor,
            manual_reason_ar=f"استرداد {refund.code} — {refund.reason_ar}"[:255],
        )
        remaining -= portion

    if remaining > ZERO:
        # The account said one thing and the allocation rows another. Paying
        # out money the ledger cannot account for is the failure this whole
        # design exists to prevent.
        raise ValidationError(
            f"تعذّر تنفيذ الاسترداد {refund.amount} — المتبقّي {remaining} بلا تخصيصات تقابله."
        )

    refund.status = RefundStatus.EXECUTED
    refund.executed_by = actor
    refund.executed_at = timezone.now()
    refund.partner_recovery_amount = recovery
    refund.save(update_fields=["status", "executed_by", "executed_at", "partner_recovery_amount"])

    if recovery > ZERO:
        clawback_service.record_refund_recovery(
            actor=actor, refund=refund, amount=recovery, occurred_on=executed_on, request=request
        )

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(refund.pk),
        reference=refund.code,
        summary_ar=f"تنفيذ استرداد {refund.amount} — {enrollment.code}",
        actor=actor,
        changes={
            "amount": str(refund.amount),
            "partner_recovery_amount": str(recovery),
            "already_claimed": already_claimed(enrollment),
            "balance_after": str(get_account_state(enrollment).balance),
        },
        request=request,
    )
    return refund


def refundable_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    The enrolments a refund can be requested on, with what the form previews:
    the amount actually collected (the ceiling, BR-034), what is already in
    flight, and the partner's rate when a paid claim would have to give some
    back (``partner_recovery_for``).
    """
    from apps.operations.models import Enrollment
    from apps.partners.models import CalculationModel

    policy.require(actor, Screen.REFUNDS, Action.CREATE, request=request)

    rows: list[dict[str, Any]] = []
    # No status filter on purpose: money collected is the only test the
    # service applies — a participant who paid and withdrew before approval
    # is exactly who §5.3 is written for.
    queryset = Enrollment.objects.select_related(
        "participant", "cohort__program", "cohort__agreement"
    ).order_by("-enrolled_on", "-id")[:300]
    for enrollment in queryset:
        paid = refundable_amount(enrollment)
        if paid <= ZERO:
            continue
        in_flight = ZERO
        for r in Refund.objects.filter(
            enrollment=enrollment, status__in=(RefundStatus.REQUESTED, RefundStatus.APPROVED)
        ):
            in_flight += r.amount
        agreement = getattr(enrollment.cohort, "agreement", None)
        recovers = (
            agreement is not None
            and agreement.calculation_model == CalculationModel.PERCENT
            and already_claimed(enrollment)
        )
        rows.append(
            {
                "code": enrollment.code,
                "participant_name": enrollment.participant.name_ar,
                "participant_number": enrollment.participant.participant_number,
                "program_name": enrollment.cohort.program.name_ar,
                "status_display": enrollment.get_status_display(),
                "paid": paid,
                "in_flight": in_flight,
                "partner_pct": (agreement.percent_rate or ZERO) if recovers else ZERO,
            }
        )
    return rows


def refunds_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and sums over already-projected rows."""
    out = {
        "requested": 0,
        "approved": 0,
        "executed": 0,
        "executed_total": ZERO,
        "recovery_total": ZERO,
    }
    for r in rows:
        if r["status"] == "REQUESTED":
            out["requested"] += 1
        elif r["status"] == "APPROVED":
            out["approved"] += 1
        elif r["status"] == "EXECUTED":
            out["executed"] += 1
            out["executed_total"] += r["amount"]
            out["recovery_total"] += r["partner_recovery_amount"]
    return out


def list_refunds(
    *,
    actor: Any,
    enrollment_code: str = "",
    status: str = "",
    query: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Refunds as rows for the refunds screen (§5.3), with the whole decision
    trail: who asked, who approved or refused and why, who paid it out.
    """
    from django.db.models import Q

    queryset = Refund.objects.select_related(
        "enrollment__participant",
        "enrollment__cohort__program",
        "requested_by",
        "approved_by",
        "rejected_by",
        "executed_by",
    )
    policy.require(actor, Screen.REFUNDS, Action.VIEW, request=request)

    if enrollment_code:
        queryset = queryset.filter(enrollment__code=enrollment_code)
    if status:
        queryset = queryset.filter(status=status)
    if query.strip():
        q = query.strip()
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(enrollment__code__icontains=q)
            | Q(enrollment__participant__name_ar__icontains=q)
            | Q(enrollment__participant__participant_number__icontains=q)
            | Q(official_letter_ref__icontains=q)
            | Q(president_approval_ref__icontains=q)
        )

    type_labels = {"FULL": "كامل", "PARTIAL": "جزئي"}
    return [
        {
            "code": r.code,
            "enrollment_code": r.enrollment.code,
            "participant_name": r.enrollment.participant.name_ar,
            "participant_number": r.enrollment.participant.participant_number,
            "program_name": r.enrollment.cohort.program.name_ar,
            "refund_type": r.refund_type,
            "refund_type_display": type_labels.get(r.refund_type, r.refund_type),
            "amount": r.amount,
            "reason_ar": r.reason_ar,
            "official_letter_ref": r.official_letter_ref,
            "official_letter_date": r.official_letter_date,
            "president_approval_ref": r.president_approval_ref,
            "president_approval_date": r.president_approval_date,
            "status": r.status,
            "status_display": r.get_status_display(),
            "requested_by": person_name(r.requested_by),
            "requested_by_id": r.requested_by_id,
            "requested_at": r.created_at,
            "approved_by": person_name(r.approved_by) if r.approved_by_id else "",
            "approved_at": r.approved_at,
            "rejected_by": person_name(r.rejected_by) if r.rejected_by_id else "",
            "rejected_at": r.rejected_at,
            "rejection_reason_ar": r.rejection_reason_ar,
            "executed_by": person_name(r.executed_by) if r.executed_by_id else "",
            "partner_recovery_amount": r.partner_recovery_amount,
            "executed_at": r.executed_at,
        }
        for r in queryset.order_by("-created_at")
    ]


def get_refund(*, actor: Any, code: str, request: Any = None) -> Refund:
    policy.require(actor, Screen.REFUNDS, Action.VIEW, request=request)
    return Refund.objects.get(code=code)


__all__ = [
    "REFUND_FILTERS",
    "RefundExceedsPaidError",
    "RefundStateError",
    "already_claimed",
    "approve_refund",
    "execute_refund",
    "get_refund",
    "list_refunds",
    "partner_recovery_for",
    "refundable_amount",
    "refundable_rows",
    "refunds_summary",
    "reject_refund",
    "request_refund",
]
