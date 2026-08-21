"""
The balance equation — the ONLY place it is computed (DATA_MODEL §8.2, P1).

Every screen, report and clearance check calls ``get_account_state``. Nothing
re-derives a balance of its own, because the moment two pieces of code compute
money independently they disagree, and the disagreement is discovered by a
participant holding a receipt.

Four different questions are answered from the same rows, and conflating any
two of them is a real accounting error rather than a rounding quibble:

* **What the participant owes** — gross, tax included.
* **What the partner shares** — net only. Tax is collected for the treasury,
  so splitting it would hand a partner money that was never the centre's
  (BR-093, pending Q-28).
* **What counts as revenue** — net where ``is_revenue``, which excludes
  deposits and tax alike (BR-092).
* **What the university owes back** — deposits received, less returned, less
  forfeited. A real liability, not a rounding line (Q-01).

Sign convention, fixed here and used everywhere (WORKFLOWS §6.5):
**positive = the participant owes us · negative = we owe the participant.**
The demo used the same negative number for both meanings.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Sum

ZERO = Decimal("0.000")


@dataclass(frozen=True)
class AccountState:
    """One enrolment's financial position, computed from the ledger."""

    total_due: Decimal
    total_discount: Decimal
    total_paid: Decimal
    balance: Decimal

    partner_base: Decimal
    revenue: Decimal
    deposit_liability: Decimal

    unallocated_credit: Decimal

    @property
    def is_settled(self) -> bool:
        """BR-073 — clearance needs exactly zero, in either direction."""
        return self.balance == ZERO

    @property
    def participant_owes(self) -> bool:
        return self.balance > ZERO

    @property
    def centre_owes(self) -> bool:
        """A credit balance must be returned before clearance closes."""
        return self.balance < ZERO


def _sum(queryset: Any, field: str) -> Decimal:
    return queryset.aggregate(total=Sum(field))["total"] or ZERO


def get_account_state(enrollment: Any) -> AccountState:
    """
    Compute the full position for one enrolment.

    Voided charge lines and voided receipts are excluded rather than deleted —
    a void is a reversing entry, and the original stays visible (BR-025).
    """
    from apps.billing.models import ChargeLine, DepositForfeiture, DepositReturn
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    live_lines = ChargeLine.objects.filter(enrollment=enrollment, voided=False)

    total_due = _sum(live_lines, "gross_amount")
    total_discount = _sum(enrollment.discounts.all(), "amount")

    # Allocations belonging to receipts that still stand. A voided receipt's
    # reversing allocations net its originals to zero, so both remain visible
    # and the arithmetic still comes out right.
    live_allocations = PaymentAllocation.objects.filter(
        enrollment=enrollment, receipt__status=ReceiptStatus.ISSUED
    )
    total_paid = _sum(live_allocations, "amount")

    balance = total_due - total_discount - total_paid

    # BR-093 — the partner's base is the NET portion of what was actually
    # collected against shareable lines. Cash basis (BR-044): what was
    # invoiced does not earn a partner anything until it is received.
    partner_base = ZERO
    shareable = live_allocations.filter(
        charge_line__is_partner_shareable=True, charge_line__isnull=False
    ).select_related("charge_line")
    for allocation in shareable:
        line = allocation.charge_line
        if line is None or line.gross_amount == ZERO:
            continue
        # Scale the payment down to its net share: a partial payment against a
        # taxed line carries its tax proportionally.
        partner_base += allocation.amount * (line.net_amount / line.gross_amount)

    revenue = _sum(live_lines.filter(is_revenue=True), "net_amount")

    # Q-01 — money held on the participant's behalf, still owed back.
    deposits_charged = _sum(live_lines.filter(charge_type="DEPOSIT"), "gross_amount")
    returned = _sum(DepositReturn.objects.filter(enrollment=enrollment), "amount")
    forfeited = _sum(DepositForfeiture.objects.filter(enrollment=enrollment), "amount")
    deposit_liability = deposits_charged - returned - forfeited

    unallocated = _sum(
        PaymentAllocation.objects.filter(
            enrollment=enrollment,
            charge_line__isnull=True,
            receipt__status=ReceiptStatus.ISSUED,
        ),
        "amount",
    )

    return AccountState(
        total_due=total_due,
        total_discount=total_discount,
        total_paid=total_paid,
        balance=balance,
        partner_base=partner_base.quantize(Decimal("0.001")),
        revenue=revenue,
        deposit_liability=deposit_liability,
        unallocated_credit=unallocated,
    )


def outstanding_for_line(charge_line: Any) -> Decimal:
    """
    What is still owed on one line — gross, less what has been allocated to it.

    Used by the allocation algorithm, and the reason a payment never
    over-allocates a line.
    """
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    allocated = _sum(
        PaymentAllocation.objects.filter(
            charge_line=charge_line, receipt__status=ReceiptStatus.ISSUED
        ),
        "amount",
    )
    return charge_line.gross_amount - allocated


def account_statement(*, actor: Any, enrollment: Any, request: Any = None) -> dict[str, Any]:
    """
    The participant's financial statement — §9 report 5, as a screen.

    Everything the statement must show is assembled HERE rather than in a
    template, because §5.1 requires the discount to appear as an explicit
    line and §9 requires transfers and refunds to be visible on it. A template
    that recomputed any of these would be a second opinion about money.

    Voided rows are carried with a flag rather than filtered out: a
    participant holding a receipt that was later voided is entitled to find it
    on the statement, marked, not to find it missing.
    """
    from apps.billing.models import ChargeLine, CreditReturn, Discount, Refund
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus
    from apps.core.display import text_of
    from apps.people.constants import Action, Screen
    from apps.people.permissions import policy

    policy.require(actor, Screen.ENROLLMENTS, Action.VIEW, request=request)

    state = get_account_state(enrollment)

    charges = [
        {
            "charged_on": line.charged_on,
            "charge_type": line.charge_type,
            "charge_type_display": line.get_charge_type_display(),
            "description_ar": line.description_ar,
            "net_amount": line.net_amount,
            "tax_amount": line.tax_amount,
            "gross_amount": line.gross_amount,
            "outstanding": outstanding_for_line(line),
            "is_partner_shareable": line.is_partner_shareable,
            "voided": line.voided,
            "void_reason_ar": line.void_reason_ar,
        }
        for line in ChargeLine.objects.filter(enrollment=enrollment).order_by("charged_on", "id")
    ]

    payments = [
        {
            "received_on": allocation.receipt.received_on,
            "receipt_number": allocation.receipt.internal_receipt_number,
            "external_ref": allocation.receipt.external_receipt_ref,
            "amount": allocation.amount,
            "against": text_of(allocation.charge_line, "description_ar", default="رصيد غير مخصَّص"),
            "allocation_type": allocation.allocation_type,
            "manual_reason_ar": allocation.manual_reason_ar,
            "is_reversal": allocation.amount < ZERO,
            "voided": allocation.receipt.status != ReceiptStatus.ISSUED,
        }
        for allocation in PaymentAllocation.objects.filter(enrollment=enrollment)
        .select_related("receipt", "charge_line")
        .order_by("id")
    ]

    discounts = [
        {
            "amount": discount.amount,
            "base_amount": discount.base_amount,
            "reason_ar": discount.reason_ar,
            "president_approval_ref": discount.president_approval_ref,
            "president_approval_date": discount.president_approval_date,
            "university_burden": discount.university_burden,
            "partner_burden": discount.partner_burden,
            "split_mode": discount.discount_split_mode_snapshot,
            "is_approved": discount.approved_by_id is not None,
        }
        for discount in Discount.objects.filter(enrollment=enrollment).order_by("created_at")
    ]

    refunds = [
        {
            "code": refund.code,
            "amount": refund.amount,
            "status": refund.status,
            "status_display": refund.get_status_display(),
            "reason_ar": refund.reason_ar,
            "official_letter_ref": refund.official_letter_ref,
            "president_approval_ref": refund.president_approval_ref,
            "partner_recovery_amount": refund.partner_recovery_amount,
        }
        for refund in Refund.objects.filter(enrollment=enrollment).order_by("created_at")
    ]

    credit_returns = [
        {
            "code": record.code,
            "amount": record.amount,
            "returned_on": record.returned_on,
            "reason_ar": record.reason_ar,
        }
        for record in CreditReturn.objects.filter(enrollment=enrollment).order_by("returned_on")
    ]

    return {
        "enrollment_code": enrollment.code,
        "participant_name": enrollment.participant.name_ar,
        "participant_number": enrollment.participant.participant_number,
        "cohort_code": enrollment.cohort.code,
        "program_name": enrollment.cohort.program.name_ar,
        "status": enrollment.status,
        "status_display": enrollment.get_status_display(),
        "state": state,
        "charges": charges,
        "payments": payments,
        "discounts": discounts,
        "refunds": refunds,
        "credit_returns": credit_returns,
    }


__all__ = [
    "ZERO",
    "AccountState",
    "account_statement",
    "get_account_state",
    "outstanding_for_line",
]
