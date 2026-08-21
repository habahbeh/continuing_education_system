"""
Recording what a partner owes the university (§5.6).

**Recovery already worked; recording did not.** ``apply_offsets`` has deducted
obligations from claims since Sprint 5, bounded by ``net_payable >= 0`` so a
claim never becomes a demand on the partner (BR-036), and every deduction names
its source. Two obligation types were raised automatically — the advance
clawback and the refund recovery. The other three in §5.6 had a type code, a
constraint and no way to create one.

**Not expenses.** An obligation is money the PARTNER owes the university,
recovered by deduction from their claims. An ``Expense`` is money the
university paid out. They are separate ledgers and meet only in report 2.
``expenses/tests/test_ledger_separation.py`` holds that line.

**Pinned to their agreement.** Q-08's default is partner-wide offsetting, but
an obligation arising under one contract belongs to it — which is what
``close_name_list`` and ``record_refund_recovery`` already do. These follow the
same rule: an obligation with a cohort takes that cohort's agreement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.services.account_service import ZERO
from apps.core.display import text_of
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.models import ObligationType, PartnerObligation

ENTITY = "settlements.PartnerObligation"

#: §5.6's obligations that a human records, as opposed to the two the system
#: raises on its own. Each names the clause it comes from.
MANUAL_TYPES: dict[str, str] = {
    ObligationType.TRAINER_SALARIES: "§5.6 · تناغم بند 8 — رواتب المدربين وضرائبهم وضمانهم",
    ObligationType.FIELD_TRAINING_EXPENSE: "§5.6 · تناغم بند 7 — مصاريف التدريب العملي",
    ObligationType.WITHDRAWAL_RETURN: "§5.6 · تناغم بند 5 — إعادة ما قُبض عن أجزاء لم تُنفَّذ",
}


def _agreement_for(cohort: Any, partner: Any) -> Any:
    """
    The agreement an obligation is pinned to.

    A cohort names its own; without one the obligation stays partner-wide and
    Q-08's setting decides how far an offset may reach.
    """
    return getattr(cohort, "agreement", None) if cohort is not None else None


def record_obligation(
    *,
    actor: Any,
    partner: Any,
    obligation_type: str,
    amount: Decimal,
    occurred_on: date,
    statement_reference: str,
    code: str,
    cohort: Any = None,
    request: Any = None,
) -> PartnerObligation:
    """
    Record one of §5.6's manual obligations.

    ``statement_reference`` is required for all three, not only for field
    training: «بموجب كشف من المركز» is what makes the debt provable, and an
    obligation the partner cannot trace back to a document is one they will
    dispute at settlement time.

    The trainer-absence penalty is NOT recordable here — it is a formula with
    inputs, and ``absence_service`` computes it from the absences actually
    recorded.
    """
    policy.require(actor, Screen.OBLIGATIONS, Action.CREATE, request=request)

    if obligation_type not in MANUAL_TYPES:
        if obligation_type == ObligationType.TRAINER_ABSENCE_PENALTY:
            raise ValidationError(
                "غرامة غياب المدرب تُحتسب من سجل الغيابات لا تُقيَّد يدوياً — "
                "الغرامة صيغة بمدخلات (BR-057)."
            )
        raise ValidationError(f"نوع التزام لا يُقيَّد يدوياً: {obligation_type}")

    if amount <= ZERO:
        raise ValidationError("مبلغ الالتزام يجب أن يكون موجباً.")
    if not statement_reference.strip():
        raise ValidationError(
            "مرجع الكشف إلزامي — «بموجب كشف من المركز»، وهو سند الذمة على الشريك."
        )
    if PartnerObligation.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الالتزام {code} مستعمل سلفاً.")

    return _record(
        actor=actor,
        partner=partner,
        obligation_type=obligation_type,
        amount=amount,
        occurred_on=occurred_on,
        statement_reference=statement_reference.strip(),
        code=code,
        cohort=cohort,
        request=request,
    )


@transaction.atomic
def _record(
    *,
    actor: Any,
    partner: Any,
    obligation_type: str,
    amount: Decimal,
    occurred_on: date,
    statement_reference: str,
    code: str,
    cohort: Any,
    request: Any,
) -> PartnerObligation:
    agreement = _agreement_for(cohort, partner)
    obligation = PartnerObligation.objects.create(
        code=code,
        partner=partner,
        restricted_to_agreement=agreement,
        obligation_type=obligation_type,
        cohort=cohort,
        amount=amount,
        statement_reference=statement_reference,
        occurred_on=occurred_on,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(obligation.pk),
        reference=code,
        summary_ar=f"قيد التزام على الشريك {amount} — {obligation.get_obligation_type_display()}",
        actor=actor,
        changes={
            "obligation_type": obligation_type,
            "amount": str(amount),
            "partner": partner.code,
            "cohort": text_of(cohort, "code"),
            "statement_reference": statement_reference,
            "restricted_to_agreement": text_of(agreement, "agreement_number"),
            "clause": MANUAL_TYPES[obligation_type],
        },
        request=request,
    )
    return obligation


def manual_type_choices() -> list[tuple[str, str]]:
    """(code, label) pairs of the obligations a human may record."""
    return [(value, str(ObligationType(value).label)) for value in MANUAL_TYPES]


def obligation_instance(*, actor: Any, code: str, request: Any = None) -> PartnerObligation:
    """The PartnerObligation object, for handing to another service (A-05)."""
    policy.require(actor, Screen.OBLIGATIONS, Action.VIEW, request=request)
    return PartnerObligation.objects.select_related("partner").get(code=code)


__all__ = [
    "MANUAL_TYPES",
    "manual_type_choices",
    "obligation_instance",
    "record_obligation",
]
