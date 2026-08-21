"""
Advance payouts and their recovery (Q-09, BR-048, BR-054).

⚠️ **ASSUMPTION (Q-09).** An advance-payout agreement pays the partner before
the course runs — the CFM agreement in the demo paid 20 × 195 = 3,900 dinars
up front. If three participants then withdraw and two fall behind on payment,
BR-045 says the partner earned nothing for those five, but the money is
already gone. The demo had no mechanism at all, so the centre absorbed it:
with 30 seats at 195, the exposure is 5,850 per cohort.

The assumption implemented here: at the name-list deadline (BR-054), each
ineligible participant becomes a recorded ``ADVANCE_CLAWBACK`` obligation, and
obligations are recovered by DEDUCTION from later claims (BR-036) — never by
invoicing the partner.

Recorded as obligations rather than silent arithmetic on purpose. A partner
whose next payment is smaller is entitled to see the row that made it smaller,
with the participant count and the rate behind it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.services.account_service import ZERO
from apps.core.services.audit_service import write_audit
from apps.settlements.models import ObligationType, PartnerObligation
from apps.settlements.services import entitlement_service

ENTITY = "settlements.PartnerObligation"


def name_list_deadline(cohort: Any, agreement: Any) -> date:
    """BR-054 — when the list closes and eligibility is fixed for the advance."""
    days = agreement.name_list_due_days or 7
    return cohort.starts_on + timedelta(days=days)


def exposure_for(*, cohort: Any, agreement: Any, as_of: date) -> tuple[Decimal, int]:
    """
    What the advance overpaid — amount and headcount.

    Only meaningful for FIXED_PER_STUDENT: a per-student advance overpays by
    exactly the per-student amount for each ineligible participant. Percentage
    agreements settle on what was collected, so an advance under one is a
    timing question rather than an overpayment.
    """
    from apps.partners.models import CalculationModel

    if agreement.calculation_model != CalculationModel.FIXED_PER_STUDENT:
        return ZERO, 0

    ineligible = [
        enrollment
        for enrollment in cohort.enrollments.select_related("participant", "cohort")
        if not entitlement_service.evaluate_eligibility(enrollment, as_of=as_of).is_eligible
    ]
    return agreement.fixed_amount_per_student * len(ineligible), len(ineligible)


@transaction.atomic
def close_name_list(
    *, actor: Any, cohort: Any, agreement: Any, as_of: date, request: Any = None
) -> PartnerObligation | None:
    """
    Close the list and raise a clawback if the advance overpaid.

    Returns None when nothing is owed back — the ordinary case, and not an
    error. The obligation is pinned to the agreement that produced it, which
    is tighter than the Q-08 default and correct here: an advance under one
    contract should not be recovered from an unrelated one unless the client
    says otherwise.
    """
    from apps.partners.models import PayoutTiming

    if agreement.payout_timing != PayoutTiming.ADVANCE:
        raise ValidationError("استرجاع الصرف المقدّم لا ينطبق إلا على اتفاقية بصرف مقدّم (BR-048).")

    amount, count = exposure_for(cohort=cohort, agreement=agreement, as_of=as_of)
    if amount <= ZERO:
        return None

    obligation = PartnerObligation.objects.create(
        code=f"OBL-CB-{cohort.code}"[:32],
        partner=agreement.partner,
        # Pinned deliberately — see the docstring.
        restricted_to_agreement=agreement,
        obligation_type=ObligationType.ADVANCE_CLAWBACK,
        cohort=cohort,
        amount=amount,
        statement_reference=(
            f"إقفال كشف الأسماء {as_of} — {count} غير مؤهل × {agreement.fixed_amount_per_student}"
        ),
        occurred_on=as_of,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(obligation.pk),
        reference=obligation.code,
        summary_ar=f"استرجاع صرف مقدّم {amount} — {count} غير مؤهل",
        actor=actor,
        changes={
            "amount": str(amount),
            "ineligible_count": count,
            "per_student": str(agreement.fixed_amount_per_student),
            "cohort": cohort.code,
            "restricted_to_agreement": agreement.agreement_number,
        },
        request=request,
    )
    return obligation


def record_refund_recovery(
    *,
    actor: Any,
    refund: Any,
    amount: Decimal,
    occurred_on: date,
    request: Any = None,
) -> PartnerObligation:
    """
    Record what a partner must return after a refund (§5.3, تناغم بند 4).

    Called by ``refund_service`` only when the partner has ALREADY been paid
    for the enrolment. A refund on an enrolment not yet claimed needs no
    obligation at all: the reversal lowers what was collected, and the partner
    simply never receives a share of it. Raising one anyway would recover the
    same dinar twice.

    Pinned to the agreement that produced it. The Q-08 default is partner-wide
    recovery, but a refund arises under one specific contract and the
    participant, the cohort and the rate all belong to it — so the tighter
    scope is the accurate one, exactly as for an advance clawback.
    """
    enrollment = refund.enrollment
    cohort = enrollment.cohort
    agreement = getattr(cohort, "agreement", None)
    if agreement is None:  # pragma: no cover - refund_service returns 0 first
        raise ValidationError("لا اتفاقية على الدفعة — لا استرجاع من الشريك.")

    obligation = PartnerObligation.objects.create(
        code=f"OBL-RF-{refund.code}"[:32],
        partner=agreement.partner,
        restricted_to_agreement=agreement,
        obligation_type=ObligationType.REFUND_RECOVERY,
        cohort=cohort,
        amount=amount,
        statement_reference=(
            f"استرجاع حصة الشريك من الاسترداد {refund.code} — "
            f"{enrollment.code} · {refund.amount} × {agreement.percent_rate}%"
        ),
        occurred_on=occurred_on,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(obligation.pk),
        reference=obligation.code,
        summary_ar=f"استرجاع حصة شريك {amount} عن الاسترداد {refund.code}",
        actor=actor,
        changes={
            "amount": str(amount),
            "refund": refund.code,
            "refund_amount": str(refund.amount),
            "enrollment": enrollment.code,
            "restricted_to_agreement": agreement.agreement_number,
        },
        request=request,
    )
    return obligation


__all__ = [
    "close_name_list",
    "exposure_for",
    "name_list_deadline",
    "record_refund_recovery",
]
