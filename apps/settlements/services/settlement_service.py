"""
Closing a financial cycle with a partner (BR-053, §3.1).

A claim says what is owed for a period. A SETTLEMENT (مخالصة) says the period
is finished and both sides agree there is nothing further in it. The تناغم
agreement makes it an obligation, not a courtesy: clause 11 — «يتم إجراء
مخالصة مالية ومطابقة بين الفريقين في نهاية كل دورة قصيرة».

**The cycle is read from the agreement, never assumed.** ``settlement_cycle``
has existed on ``Agreement`` since Sprint 5 and nothing read it; this module
is what makes it mean something:

* ``EVERY_4_MONTHS`` — the diploma rhythm from §3.1. Four calendar months from
  the opening date, ending the day before the next period starts, so
  consecutive settlements tile the year without overlapping by a day.
* ``END_OF_COURSE`` — the short-course rhythm. The period ends when the cohort
  does, so the settlement cannot be opened without naming the cohort it
  closes.

**Why the balance is stored and not derived.** ``total_due`` is the sum of the
claims as they stood when the settlement was signed. A claim re-approved later
must not silently move a settled figure, so the number is copied at attach
time — the same reason a claim snapshots its own terms (ADR-012). The
constraint ``settlements_settlement_balance_equation`` keeps the three numbers
consistent whatever writes them.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.services.account_service import ZERO
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.models import ClaimStatus, PartnerSettlement, SettlementStatus

ENTITY = "settlements.PartnerSettlement"

MONTHS_IN_CYCLE = 4


class SettlementCycleError(ValidationError):
    """The period does not match the cycle the agreement specifies."""


class SettlementStateError(ValidationError):
    """A step attempted on a settlement that is already signed."""


def _add_months(start: date, months: int) -> date:
    """Calendar-month arithmetic without pulling in a date library."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # The 31st has no counterpart in a 30-day month; step back to the last day
    # that exists rather than rolling into the next month.
    day = start.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def period_for(*, agreement: Any, opens_on: date, cohort: Any = None) -> tuple[date, date]:
    """
    The period this settlement covers, decided by the agreement's cycle.

    Returns ``(period_from, period_to)`` inclusive.
    """
    from apps.partners.models import SettlementCycle

    if agreement.settlement_cycle == SettlementCycle.EVERY_4_MONTHS:
        return opens_on, _add_months(opens_on, MONTHS_IN_CYCLE) - timedelta(days=1)

    if cohort is None:
        raise SettlementCycleError(
            "دورة المخالصة «نهاية الدورة» تحتاج الدفعة التي تُقفَل بها (§3.1 · تناغم بند 11)."
        )
    if cohort.ends_on < opens_on:
        raise SettlementCycleError(
            f"تنتهي الدفعة {cohort.code} في {cohort.ends_on} قبل بداية الفترة {opens_on}."
        )
    return opens_on, cohort.ends_on


def open_settlement(
    *,
    actor: Any,
    agreement: Any,
    opens_on: date,
    code: str,
    cohort: Any = None,
    request: Any = None,
) -> PartnerSettlement:
    """Open a cycle for a partner, with its period taken from the agreement."""
    policy.require(actor, Screen.SETTLEMENTS, Action.CREATE, request=request)

    period_from, period_to = period_for(agreement=agreement, opens_on=opens_on, cohort=cohort)

    live = PartnerSettlement.objects.filter(
        agreement=agreement, status=SettlementStatus.OPEN
    ).first()
    if live is not None:
        raise SettlementStateError(
            f"للاتفاقية {agreement.agreement_number} مخالصة مفتوحة سلفاً ({live.code}) — "
            "تُقفل قبل فتح دورة جديدة."
        )

    return _open(
        actor=actor,
        agreement=agreement,
        period_from=period_from,
        period_to=period_to,
        code=code,
        request=request,
    )


@transaction.atomic
def _open(
    *,
    actor: Any,
    agreement: Any,
    period_from: date,
    period_to: date,
    code: str,
    request: Any,
) -> PartnerSettlement:
    settlement = PartnerSettlement.objects.create(
        code=code,
        partner=agreement.partner,
        agreement=agreement,
        cycle_type=agreement.settlement_cycle,
        period_from=period_from,
        period_to=period_to,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(settlement.pk),
        reference=code,
        summary_ar=f"فتح مخالصة {code} — {period_from} إلى {period_to}",
        actor=actor,
        changes={
            "agreement": agreement.agreement_number,
            "cycle": agreement.settlement_cycle,
            "period_from": period_from.isoformat(),
            "period_to": period_to.isoformat(),
        },
        request=request,
    )
    return settlement


def attachable_claims(settlement: PartnerSettlement) -> Any:
    """
    Approved or paid claims for this agreement inside the settlement period.

    A DRAFT claim is deliberately excluded: an unapproved figure has no seal
    on it, and a settlement built from one would close a period over numbers
    that can still change (BR-051).
    """
    from apps.settlements.models import PartnerClaim

    return PartnerClaim.objects.filter(
        agreement=settlement.agreement,
        status__in=(ClaimStatus.APPROVED, ClaimStatus.PAID),
        settlement__isnull=True,
        period_to__gte=settlement.period_from,
        period_to__lte=settlement.period_to,
    ).order_by("period_to", "id")


def attach_claims(*, actor: Any, settlement: PartnerSettlement, request: Any = None) -> list[Any]:
    """Pull every eligible claim into the settlement and total it."""
    policy.require(actor, Screen.SETTLEMENTS, Action.EDIT, request=request)
    _require_open(settlement)
    return _attach(actor=actor, settlement=settlement, request=request)


@transaction.atomic
def _attach(*, actor: Any, settlement: PartnerSettlement, request: Any) -> list[Any]:
    claims = list(attachable_claims(settlement))

    total = ZERO
    for claim in claims:
        claim.settlement = settlement
        claim.save(update_fields=["settlement"])
        total += claim.net_payable

    settlement.total_due += total
    settlement.balance = settlement.total_due - settlement.total_paid
    settlement.save(update_fields=["total_due", "balance"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(settlement.pk),
        reference=settlement.code,
        summary_ar=f"ضمّ {len(claims)} مطالبة للمخالصة — الإجمالي {settlement.total_due}",
        actor=actor,
        changes={
            "claims": [c.code for c in claims],
            "added": str(total),
            "total_due": str(settlement.total_due),
        },
        request=request,
    )
    return claims


def record_payment(
    *,
    actor: Any,
    settlement: PartnerSettlement,
    amount: Decimal,
    request: Any = None,
) -> PartnerSettlement:
    """Record money paid to the partner against this cycle."""
    policy.require(actor, Screen.SETTLEMENTS, Action.EDIT, request=request)
    _require_open(settlement)

    if amount <= ZERO:
        raise ValidationError("مبلغ الدفع يجب أن يكون موجباً.")
    if settlement.total_paid + amount > settlement.total_due:
        raise ValidationError(
            f"الدفع {amount} يتجاوز المستحق — المستحق {settlement.total_due} "
            f"والمدفوع {settlement.total_paid}."
        )

    return _record_payment(actor=actor, settlement=settlement, amount=amount, request=request)


@transaction.atomic
def _record_payment(
    *, actor: Any, settlement: PartnerSettlement, amount: Decimal, request: Any
) -> PartnerSettlement:
    settlement.total_paid += amount
    settlement.balance = settlement.total_due - settlement.total_paid
    settlement.save(update_fields=["total_paid", "balance"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(settlement.pk),
        reference=settlement.code,
        summary_ar=f"دفعة {amount} على المخالصة — المتبقّي {settlement.balance}",
        actor=actor,
        changes={
            "amount": str(amount),
            "total_paid": str(settlement.total_paid),
            "balance": str(settlement.balance),
        },
        request=request,
    )
    return settlement


def sign_settlement(
    *,
    actor: Any,
    settlement: PartnerSettlement,
    signed_on: date,
    request: Any = None,
) -> PartnerSettlement:
    """
    Close the cycle — only once the balance is nil.

    A settlement is a statement that nothing remains outstanding for the
    period. Signing one with a balance still on it would say something untrue
    on the document both parties keep.
    """
    policy.require(actor, Screen.SETTLEMENTS, Action.APPROVE, request=request)
    _require_open(settlement)

    if settlement.balance != ZERO:
        raise SettlementStateError(
            f"لا تُوقَّع المخالصة ورصيدها {settlement.balance} — "
            "المخالصة إقرار بعدم بقاء مستحقات في الفترة (BR-053)."
        )

    return _sign(actor=actor, settlement=settlement, signed_on=signed_on, request=request)


@transaction.atomic
def _sign(
    *, actor: Any, settlement: PartnerSettlement, signed_on: date, request: Any
) -> PartnerSettlement:
    settlement.status = SettlementStatus.SIGNED
    settlement.signed_on = signed_on
    settlement.save(update_fields=["status", "signed_on"])

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(settlement.pk),
        reference=settlement.code,
        summary_ar=f"توقيع المخالصة {settlement.code} — {settlement.total_due}",
        actor=actor,
        changes={
            "signed_on": signed_on.isoformat(),
            "total_due": str(settlement.total_due),
            "total_paid": str(settlement.total_paid),
            "claims": [c.code for c in settlement.claims.order_by("id")],
        },
        request=request,
    )
    return settlement


def _require_open(settlement: PartnerSettlement) -> None:
    if settlement.status != SettlementStatus.OPEN:
        raise SettlementStateError(f"المخالصة {settlement.code} موقّعة — لا يُعدَّل ما وقّعه الطرفان.")


__all__ = [
    "MONTHS_IN_CYCLE",
    "SettlementCycleError",
    "SettlementStateError",
    "attach_claims",
    "attachable_claims",
    "open_settlement",
    "period_for",
    "record_payment",
    "sign_settlement",
]
