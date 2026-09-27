"""
Who earns the partner what — on a CASH basis (BR-044, BR-045).

An invoice earns a partner nothing. Entitlement follows the money actually
received, read from ``PaymentAllocation``, which is why Sprint 4's allocation
table had to exist first: without a stored split there is no way to say which
part of a 400-dinar payment covered the excluded registration fee and which
covered shareable tuition.

Q-16's overdue predicate lives here because BR-045 needs it: an enrolment
behind on payment earns the partner nothing until it is settled. Only the
PREDICATE is built — the daily job that flips statuses in bulk is Sprint 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Max

from apps.billing.services.account_service import ZERO
from apps.settlements.models import IneligibilityReason
from apps.settlements.services import assumptions

#: BR-045 — statuses that earn a partner nothing, whatever was collected.
INELIGIBLE_STATUSES = {
    "WITHDRAWN": IneligibilityReason.WITHDRAWN,
    "DISMISSED": IneligibilityReason.DISMISSED,
    "NOT_ATTENDED": IneligibilityReason.NOT_ATTENDED,
    "INCOMPLETE": IneligibilityReason.INCOMPLETE,
    "PAYMENT_OVERDUE": IneligibilityReason.PAYMENT_OVERDUE,
    # Q-11 — a transferred-out enrolment keeps no allocations once they move
    # with the participant, so it earns nothing structurally rather than by
    # an exception rule. Listed for the explicit reason on the claim line.
    "TRANSFERRED_OUT": IneligibilityReason.WITHDRAWN,
}


@dataclass(frozen=True)
class EligibilityVerdict:
    is_eligible: bool
    reason: str = ""
    note: str = ""


def payment_overdue_map(enrollments: list[Any], *, as_of: date) -> dict[int, bool]:
    """
    ``is_payment_overdue`` لمجموعةٍ دفعةً واحدة — **القاعدة نفسها، لا نسخةٌ منها**.

    Sprint 8L. التقرير الرابع كان يسأل هذا السؤال داخل حلقة الصفوف، فيكلّف كل
    متأخّرٍ ثلاثة عشر استعلاماً إضافياً (قِيس: ٣٠ استعلاماً لصفّ واحد و٩٦
    لستّة). والمفارقة أن التقرير يبطؤ كلّما نجح المركز في التحصيل عكسياً.

    الشروط الثلاثة كما هي في التوأم المفرد سطراً بسطر، والتجاوز اليدوي كما هو.
    ما تغيّر أن الرصيد يُجمَع مرّة للمجموعة كلها (``get_account_states``)،
    وآخر دفعة تُقرأ باستعلامٍ مجمَّع واحد بدل استعلام لكل تسجيل.

    ``is_payment_overdue`` تنادي هذه الدالة بعنصر واحد، فلا وجود لتطبيقين
    للقاعدة يمكن أن يفترقا.
    """
    from apps.billing.services.account_service import get_account_states
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    if not enrollments:
        return {}

    states = get_account_states(enrollments)
    last_payments = dict(
        PaymentAllocation.objects.filter(
            enrollment__in=enrollments, receipt__status=ReceiptStatus.ISSUED
        )
        .values_list("enrollment_id")
        .annotate(latest=Max("receipt__received_on"))
        .values_list("enrollment_id", "latest")
    )
    limit = timedelta(days=assumptions.overdue_days(as_of=as_of))

    verdicts: dict[int, bool] = {}
    for enrollment in enrollments:
        if enrollment.status == "PAYMENT_OVERDUE" and enrollment.status_note_ar:
            verdicts[enrollment.pk] = True  # Manual override with a stated reason.
            continue
        if states[enrollment.pk].balance <= ZERO:
            verdicts[enrollment.pk] = False
            continue
        if enrollment.cohort.starts_on > as_of:
            verdicts[enrollment.pk] = False  # The course has not started.
            continue
        reference = last_payments.get(enrollment.pk) or enrollment.enrolled_on
        verdicts[enrollment.pk] = (as_of - reference) > limit
    return verdicts


def is_payment_overdue(enrollment: Any, *, as_of: date) -> bool:
    """
    ⚠️ ASSUMPTION (Q-16) — is this enrolment behind on payment?

    Three conditions, all required: a positive balance, more than
    ``payment_overdue_days`` since the last payment, and a course that has
    actually started. A participant who has not yet been taught anything is
    not "overdue" merely because term has not begun.

    A MANUAL override is honoured: an enrolment already flagged
    PAYMENT_OVERDUE by a human, with a reason recorded in ``status_note_ar``,
    stays overdue regardless of the arithmetic. The finance officer sees
    things the rule cannot.

    This is the predicate only. The daily job that sweeps statuses across all
    enrolments is Sprint 6 and deliberately not built here.
    """
    return payment_overdue_map([enrollment], as_of=as_of)[enrollment.pk]


def evaluate_eligibility(enrollment: Any, *, as_of: date) -> EligibilityVerdict:
    """BR-045 — whether this enrolment earns the partner anything."""
    if enrollment.cohort.agreement_id is None:
        return EligibilityVerdict(False, IneligibilityReason.NO_AGREEMENT)

    reason = INELIGIBLE_STATUSES.get(enrollment.status)
    if reason is not None:
        return EligibilityVerdict(False, reason)

    if is_payment_overdue(enrollment, as_of=as_of):
        return EligibilityVerdict(
            False,
            IneligibilityReason.PAYMENT_OVERDUE,
            note=f"تجاوز مهلة {assumptions.overdue_days(as_of=as_of)} يوماً (Q-16)",
        )

    return EligibilityVerdict(True)


def shareable_collected(enrollment: Any, *, agreement: Any, as_of: date) -> Decimal:
    """
    What was COLLECTED against lines this agreement shares.

    Exclusions come from the agreement, not from code: registration, deposits
    and consumables are each excluded or not according to the contract signed
    (BR-046). The deposit default is exclusion, overridable only by explicit
    contract text (BR-092, Q-01).
    """
    from apps.billing.models import ChargeType
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    excluded_types = set()
    if agreement.exclude_registration_fee:
        excluded_types.add(ChargeType.REGISTRATION)
    if agreement.exclude_deposits:
        excluded_types.add(ChargeType.DEPOSIT)
    if agreement.exclude_consumables:
        excluded_types.add(ChargeType.CONSUMABLES)

    allocations = (
        PaymentAllocation.objects.filter(
            enrollment=enrollment,
            receipt__status=ReceiptStatus.ISSUED,
            charge_line__isnull=False,
            charge_line__is_partner_shareable=True,
        )
        .exclude(charge_line__charge_type__in=excluded_types)
        .select_related("charge_line")
    )

    mode = assumptions.partner_base_mode(as_of=as_of)
    total = ZERO
    for allocation in allocations:
        line = allocation.charge_line
        if line is None:
            continue
        if mode == assumptions.BASE_MODE_GROSS:
            # ⚠️ Q-28 — only if the client chooses gross. Nothing here
            # assumes it, and the claim records which basis it used.
            total += allocation.amount
        else:
            total += allocation.amount * assumptions.net_ratio(line)
    return total.quantize(Decimal("0.001"))


def excluded_collected(enrollment: Any, *, agreement: Any) -> dict[str, Decimal]:
    """What was collected against each excluded type — shown on the claim."""
    from django.db.models import Sum

    from apps.billing.models import ChargeType
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    def total_for(charge_type: str) -> Decimal:
        return (
            PaymentAllocation.objects.filter(
                enrollment=enrollment,
                receipt__status=ReceiptStatus.ISSUED,
                charge_line__charge_type=charge_type,
            ).aggregate(total=Sum("amount"))["total"]
            or ZERO
        )

    return {
        "registration": total_for(ChargeType.REGISTRATION)
        if agreement.exclude_registration_fee
        else ZERO,
        "consumables": total_for(ChargeType.CONSUMABLES) if agreement.exclude_consumables else ZERO,
        "deposits": total_for(ChargeType.DEPOSIT) if agreement.exclude_deposits else ZERO,
    }


def partner_share_for(*, agreement: Any, base: Decimal, student_count: int) -> Decimal:
    """
    Apply the agreement's calculation model (BR-046 … BR-049).

    Three models, and each reads only the fields its own model requires — the
    database already refuses an agreement that lacks them.
    """
    from apps.core.money import round_money
    from apps.partners.models import CalculationModel

    if agreement.calculation_model == CalculationModel.PERCENT:
        return round_money(base * agreement.percent_rate / Decimal("100"))

    if agreement.calculation_model == CalculationModel.FIXED_PER_STUDENT:
        # BR-048 — a flat amount per eligible student, independent of the base.
        return round_money(agreement.fixed_amount_per_student * student_count)

    # SERVICE_COMMISSION — a flat commission on a delivered service.
    return round_money(agreement.commission_amount or ZERO)


def ineligibility_reasons() -> list[tuple[str, str]]:
    """(enrolment status, reason label) pairs of BR-045, for a read-only screen.

    Read from ``INELIGIBLE_STATUSES`` rather than retyped beside it, so a status
    added to the rule reaches the screen without anyone remembering to.
    """
    reasons = dict(IneligibilityReason.choices)
    return [
        (status, str(reasons.get(reason, reason)))
        for status, reason in sorted(INELIGIBLE_STATUSES.items())
    ]


__all__ = [
    "INELIGIBLE_STATUSES",
    "EligibilityVerdict",
    "evaluate_eligibility",
    "excluded_collected",
    "ineligibility_reasons",
    "is_payment_overdue",
    "partner_share_for",
    "shareable_collected",
]
