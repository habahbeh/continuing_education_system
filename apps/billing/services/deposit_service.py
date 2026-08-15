"""
Settling a deposit — return or forfeiture (BR-096, BR-097, Q-30).

Both paths read the policy SNAPSHOT captured on the charge line, never the
live ``DepositPolicy`` row. The policy is client-owned data that may be edited
or retired; the question years later is not "what does the policy say now?"
but "what did this participant agree to?" (ADR-012).

Q-30 — exactly when a deposit is returned and when it is forfeited — is still
the client's to answer, so ``refund_trigger`` and ``forfeit_on`` are read as
DATA and carry no CHECK constraint. A trigger this code has never seen is
storable, which is the point.

Returning a deposit is not a refund. A refund reverses revenue and needs an
official letter plus the president's approval (BR-034); returning a deposit
hands back money that was never income, so C-07 does not apply.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.billing.models import ChargeLine, ChargeType, DepositForfeiture, DepositReturn
from apps.billing.services.account_service import ZERO
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy as perm

RETURN_ENTITY = "billing.DepositReturn"
FORFEIT_ENTITY = "billing.DepositForfeiture"


def deposit_line_for(enrollment: Any) -> ChargeLine | None:
    """The deposit line, or None when the programme has no policy (BR-096)."""
    return ChargeLine.objects.filter(
        enrollment=enrollment, charge_type=ChargeType.DEPOSIT, voided=False
    ).first()


def collected_on(deposit_line: ChargeLine) -> Decimal:
    """How much of the deposit was actually received."""
    from django.db.models import Sum

    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    return (
        PaymentAllocation.objects.filter(
            charge_line=deposit_line, receipt__status=ReceiptStatus.ISSUED
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )


def settled_amount(deposit_line: ChargeLine) -> Decimal:
    """Already returned or forfeited — a deposit is settled once."""
    from django.db.models import Sum

    returned = (
        DepositReturn.objects.filter(deposit_charge_line=deposit_line).aggregate(
            total=Sum("amount")
        )["total"]
        or ZERO
    )
    deducted = (
        DepositReturn.objects.filter(deposit_charge_line=deposit_line).aggregate(
            total=Sum("deduction_amount")
        )["total"]
        or ZERO
    )
    forfeited = (
        DepositForfeiture.objects.filter(deposit_charge_line=deposit_line).aggregate(
            total=Sum("amount")
        )["total"]
        or ZERO
    )
    return returned + deducted + forfeited


def return_deposit(
    *,
    actor: Any,
    enrollment: Any,
    returned_on: date,
    deduction_amount: Decimal = ZERO,
    deduction_reason_ar: str = "",
    request: Any = None,
) -> DepositReturn:
    """
    Return a deposit, optionally withholding part of it with a stated reason.

    ``amount + deduction == what was collected`` — the deposit is accounted
    for in full, so money cannot quietly go missing between the two figures.
    """
    perm.require(actor, Screen.REFUNDS, Action.CREATE, request=request)

    line = deposit_line_for(enrollment)
    if line is None:
        raise ValidationError(
            "لا يوجد بند تأمين على هذا التسجيل — البرنامج بلا سياسة تأمين (BR-096)."
        )

    collected = collected_on(line)
    if collected <= ZERO:
        raise ValidationError("لم يُقبض أي مبلغ على بند التأمين بعد.")
    if settled_amount(line) > ZERO:
        raise ValidationError("التأمين مُسوّى سلفاً — لا يُعاد مرتين.")

    snapshot = line.deposit_policy_snapshot or {}
    if deduction_amount > ZERO and not snapshot.get("allows_partial_deduction", True):
        raise ValidationError(
            f"سياسة التأمين «{snapshot.get('name_ar', '')}» لا تسمح بالحسم الجزئي."
        )
    if deduction_amount > ZERO and not deduction_reason_ar.strip():
        raise ValidationError("الحسم الجزئي يتطلب مبرراً (BR-097).")

    amount = collected - deduction_amount
    if amount <= ZERO:
        raise ValidationError("المبلغ المُعاد بعد الحسم يجب أن يكون أكبر من صفر.")

    with transaction.atomic():
        record = DepositReturn(
            enrollment=enrollment,
            deposit_charge_line=line,
            amount=amount,
            # Copied from the snapshot — client data, unconstrained (Q-30).
            trigger=snapshot.get("refund_trigger", ""),
            returned_on=returned_on,
            returned_by=actor,
            deduction_amount=deduction_amount,
            deduction_reason_ar=deduction_reason_ar.strip(),
        )
        record.full_clean(exclude=["enrollment", "deposit_charge_line", "returned_by"])
        record.save()

        write_audit(
            action="UPDATE",
            entity_type=RETURN_ENTITY,
            entity_id=str(record.pk),
            reference=enrollment.code,
            summary_ar=f"إعادة تأمين {amount} — {snapshot.get('name_ar', '')}",
            actor=actor,
            changes={
                "collected": str(collected),
                "returned": str(amount),
                "deduction": str(deduction_amount),
                "deduction_reason": deduction_reason_ar.strip() or None,
                "trigger": record.trigger,
            },
            request=request,
        )
    return record


def forfeit_deposit(
    *,
    actor: Any,
    enrollment: Any,
    reason: str,
    justification_ar: str,
    forfeited_on: date,
    approved_by: Any = None,
    request: Any = None,
) -> DepositForfeiture:
    """
    Forfeit a deposit, turning it into revenue via a NEW charge line.

    The deposit line is never edited. ``ChargeLine(DEPOSIT).is_revenue`` is
    False by hard constraint (C-21), so flipping it would breach the database
    and erase the fact that the money arrived as a deposit. Instead a second
    line records the moment of conversion, and both rows stay readable.

    Whether that new revenue line is taxable depends on Q-27, which is
    unanswered — so it is created non-taxable, and if the answer is yes it
    will need the rate from Q-25 anyway.
    """
    from apps.billing.services import charge_service

    # CREATE, not APPROVE. Row 20 gives the finance officer ``V C E P`` and
    # withholds ``A`` (footnote 17: he executes, he does not approve his own).
    # The second signature is the ``approved_by`` field, enforced by D-18 as a
    # database constraint — a control that cannot be clicked past.
    #
    # These paths belong on the CLEARANCE screen, where WORKFLOWS §6.4 V4 puts
    # them (step 2 cannot close until the deposit is settled). That screen is
    # Sprint 7, so until then the refunds permission is the closest documented
    # home rather than a new matrix row invented to fit.
    perm.require(actor, Screen.REFUNDS, Action.CREATE, request=request)

    if approved_by is not None and getattr(approved_by, "pk", None) == getattr(actor, "pk", None):
        raise PermissionDenied("لا يجوز اعتماد مصادرة نفّذتها بنفسك (D-18).")
    if not justification_ar.strip():
        raise ValidationError("المصادرة تتطلب مبرراً موثّقاً (BR-097).")

    line = deposit_line_for(enrollment)
    if line is None:
        raise ValidationError("لا يوجد بند تأمين على هذا التسجيل (BR-096).")

    collected = collected_on(line)
    if collected <= ZERO:
        raise ValidationError("لم يُقبض أي مبلغ على بند التأمين — لا شيء يُصادَر.")
    if settled_amount(line) > ZERO:
        raise ValidationError("التأمين مُسوّى سلفاً.")

    snapshot = line.deposit_policy_snapshot or {}
    allowed = list(snapshot.get("forfeit_on") or [])
    if allowed and reason not in allowed:
        # Data-driven, not a hardcoded state list: the policy row decides.
        raise ValidationError(
            f"سبب المصادرة «{reason}» غير مذكور في سياسة التأمين المطبَّقة "
            f"({', '.join(allowed)}) — BR-097 · Q-30."
        )

    with transaction.atomic():
        revenue_line = charge_service.create_charge_line(
            actor=actor,
            enrollment=enrollment,
            charge_type=ChargeType.EXTRA_FEE,
            description_ar=f"إيراد مصادرة تأمين — {snapshot.get('name_ar', '')}",
            net_amount=collected,
            charged_on=forfeited_on,
            # Q-27 unanswered; a taxable line would need Q-25's rate anyway.
            is_taxable=False,
            is_partner_shareable=False,
            request=request,
        )

        record = DepositForfeiture(
            enrollment=enrollment,
            deposit_charge_line=line,
            amount=collected,
            reason=reason,
            justification_ar=justification_ar.strip(),
            revenue_charge_line=revenue_line,
            forfeited_on=forfeited_on,
            forfeited_by=actor,
            approved_by=approved_by,
        )
        record.full_clean(
            exclude=[
                "enrollment",
                "deposit_charge_line",
                "revenue_charge_line",
                "forfeited_by",
                "approved_by",
            ]
        )
        record.save()

        write_audit(
            action="UPDATE",
            entity_type=FORFEIT_ENTITY,
            entity_id=str(record.pk),
            reference=enrollment.code,
            summary_ar=f"مصادرة تأمين {collected} — {reason}",
            actor=actor,
            changes={
                "amount": str(collected),
                "reason": reason,
                "justification": justification_ar.strip(),
                "revenue_charge_line": revenue_line.pk,
            },
            request=request,
        )
    return record


__all__ = [
    "collected_on",
    "deposit_line_for",
    "forfeit_deposit",
    "return_deposit",
    "settled_amount",
]
