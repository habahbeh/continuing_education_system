"""
Handing back a credit balance (BR-071).

A participant can end up in credit without anything going wrong: Sprint 6's
transfer to a cheaper course leaves the difference sitting on their account,
and BR-064 says explicitly that it becomes a credit returned at clearance
rather than cash handed over on the spot.

**This is not a refund.** ``Refund`` reverses revenue and demands an official
letter plus the president's approval (BR-034); requiring a presidential decree
to give someone back their own fifty dinars would make BR-071 unusable. It is
not a charge line either — everything that is not a deposit must be revenue
(C-21), and money going out is not income. Hence ``CreditReturn``, which
BR-071 calls "an independent financial movement".

**How the money moves.** A REVERSING allocation against the credit rows, the
same pattern as a void (BR-025) and a transfer. Nothing is edited, nothing is
deleted, and the original credit row stays readable beside its reversal.

⚠️ **ASSUMPTION — the authorisation path.** The documents name no approver for
this. It is executed inside clearance step 2, which already requires the
finance officer's certification followed by the finance manager's (BR-074,
D-30), so the dual control that exists is the control used. A professional
reading, not a settled client decision.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import CreditReturn
from apps.billing.services.account_service import ZERO, get_account_state
from apps.core.display import person_name
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "billing.CreditReturn"


class NoCreditToReturnError(ValidationError):
    """Raised when a return is attempted on an account that is not in credit."""


def outstanding_credit(enrollment: Any) -> Decimal:
    """
    What the centre owes this participant, as a positive number.

    Zero when the account is settled or the participant owes money. The sign
    convention is fixed in ``get_account_state`` (WORKFLOWS §6.5): positive
    balance = they owe us, negative = we owe them.
    """
    state = get_account_state(enrollment)
    return -state.balance if state.centre_owes else ZERO


def return_credit(
    *,
    actor: Any,
    enrollment: Any,
    returned_on: date,
    reason_ar: str,
    code: str,
    request: Any = None,
) -> CreditReturn:
    """
    Return the whole credit balance so clearance can close (BR-071, BR-073).

    The full amount, deliberately: BR-073 requires the balance to be EXACTLY
    zero before the financial step closes, so a partial return would leave the
    clearance blocked and the participant back at the counter.
    """
    # The finance officer executes; §3.4 row 19 gives CREATE on refunds to FIN
    # and MGR, and this is the nearest existing authority for money going out.
    policy.require(actor, Screen.REFUNDS, Action.CREATE, request=request)

    if not reason_ar.strip():
        raise ValidationError("سبب الرصيد الدائن إلزامي — المال الخارج يقول لماذا خرج.")

    amount = outstanding_credit(enrollment)
    if amount <= ZERO:
        raise NoCreditToReturnError(
            f"لا رصيد دائن على التسجيل {enrollment.code} — لا شيء يُعاد (BR-071)."
        )

    return _return_credit(
        actor=actor,
        enrollment=enrollment,
        amount=amount,
        returned_on=returned_on,
        reason_ar=reason_ar.strip(),
        code=code,
        request=request,
    )


@transaction.atomic
def _return_credit(
    *,
    actor: Any,
    enrollment: Any,
    amount: Decimal,
    returned_on: date,
    reason_ar: str,
    code: str,
    request: Any,
) -> CreditReturn:
    from apps.cashbox.models import AllocationType, PaymentAllocation, ReceiptStatus

    record = CreditReturn.objects.create(
        code=code,
        enrollment=enrollment,
        amount=amount,
        reason_ar=reason_ar,
        returned_on=returned_on,
        returned_by=actor,
    )

    # BR-023 — the allocator never over-allocates a line, so an excess payment
    # always lands in rows with no charge line. Those are what is handed back.
    remaining = amount
    first_reversal = None
    credit_rows = (
        PaymentAllocation.objects.filter(
            enrollment=enrollment,
            charge_line__isnull=True,
            receipt__status=ReceiptStatus.ISSUED,
            amount__gt=0,
        )
        .select_related("receipt")
        .order_by("id")
    )

    for row in credit_rows:
        if remaining <= ZERO:
            break
        portion = min(remaining, row.amount)
        reversal = PaymentAllocation.objects.create(
            receipt=row.receipt,
            charge_line=None,
            enrollment=enrollment,
            amount=-portion,
            allocation_type=AllocationType.MANUAL,
            allocated_by=actor,
            manual_reason_ar=f"ردّ رصيد دائن {code} — {reason_ar}"[:255],
        )
        first_reversal = first_reversal or reversal
        remaining -= portion

    if remaining > ZERO:
        # The balance said one thing and the allocation rows another. Refusing
        # is the only safe answer: paying out money the ledger cannot account
        # for is exactly the failure this whole design exists to prevent.
        raise ValidationError(
            f"تعذّر ردّ {amount} — الرصيد الدائن غير مغطّى بتخصيصات "
            f"(المتبقّي {remaining}). راجع سجل التخصيصات قبل الصرف."
        )

    record.reversal_allocation = first_reversal
    record.save(update_fields=["reversal_allocation"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(record.pk),
        reference=code,
        summary_ar=f"ردّ رصيد دائن {amount} للتسجيل {enrollment.code}",
        actor=actor,
        changes={
            "amount": str(amount),
            "enrollment": enrollment.code,
            "reason": reason_ar,
            "balance_after": str(get_account_state(enrollment).balance),
        },
        request=request,
    )
    return record


def list_credit_returns(
    *, actor: Any, enrollment_code: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Credit returns as rows — BR-071, shown beside refunds and never as one."""
    policy.require(actor, Screen.REFUNDS, Action.VIEW, request=request)

    queryset = CreditReturn.objects.select_related("enrollment__participant", "returned_by")
    if enrollment_code:
        queryset = queryset.filter(enrollment__code=enrollment_code)

    return [
        {
            "code": c.code,
            "enrollment_code": c.enrollment.code,
            "participant_name": c.enrollment.participant.name_ar,
            "amount": c.amount,
            "returned_on": c.returned_on,
            "reason_ar": c.reason_ar,
            "returned_by": person_name(c.returned_by),
        }
        for c in queryset.order_by("-returned_on", "-id")
    ]


__all__ = [
    "NoCreditToReturnError",
    "list_credit_returns",
    "outstanding_credit",
    "return_credit",
]
