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


__all__ = ["ZERO", "AccountState", "get_account_state", "outstanding_for_line"]
