"""
Building, freezing and paying a partner claim (BR-051, BR-052, ADR-007).

An approved claim is the legal basis of a settlement, so approval SEALS it:
the terms are snapshotted, the participant names and statuses are copied as
text, and a SHA-256 over the canonical content is stamped. ``verify_claim_hashes``
recomputes them later and reports any drift.

Deductions always name their source (Q-08 condition). "Deduction: 150" invites
a dispute; "recovery of refund RF-001 under agreement 2026/18" is a record the
partner can check.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.billing.services.account_service import ZERO
from apps.core.exceptions import ImmutableRecordError
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.models import (
    ClaimDeduction,
    ClaimStatus,
    DeductionType,
    Entitlement,
    ObligationStatus,
    PartnerClaim,
    PartnerClaimLine,
    PartnerObligation,
)
from apps.settlements.services import assumptions, entitlement_service

ENTITY = "settlements.PartnerClaim"

#: Fields that may legitimately change AFTER approval. Everything else is
#: sealed (BR-051 layer 1).
MUTABLE_AFTER_APPROVAL = {
    "status",
    "settlement",
    "settlement_id",
    "paid_by",
    "paid_by_id",
    "paid_at",
    "content_hash",
    "approved_by",
    "approved_by_id",
    "approved_at",
}


def canonical_content(claim: PartnerClaim) -> str:
    """
    The exact text the hash covers (BR-051 layer 2).

    Sorted keys and fixed formatting so the same claim always hashes the same
    way — a hash that depends on dictionary ordering proves nothing.
    """
    payload = {
        "code": claim.code,
        "partner": claim.partner_id,
        "agreement": claim.agreement_id,
        "period": [claim.period_from.isoformat(), claim.period_to.isoformat()],
        "model": claim.model_snapshot,
        "rate": str(claim.rate_snapshot) if claim.rate_snapshot is not None else None,
        "fixed": str(claim.fixed_amount_snapshot)
        if claim.fixed_amount_snapshot is not None
        else None,
        "base_mode": claim.base_mode_snapshot,
        "gross_collected": str(claim.gross_collected),
        "excluded_registration": str(claim.excluded_registration),
        "excluded_consumables": str(claim.excluded_consumables),
        "excluded_deposits": str(claim.excluded_deposits),
        "discount_partner_burden": str(claim.discount_partner_burden),
        "distribution_base": str(claim.distribution_base),
        "student_count": claim.student_count,
        "partner_share": str(claim.partner_share),
        "total_deductions": str(claim.total_deductions),
        "net_payable": str(claim.net_payable),
        "lines": [
            {
                "enrollment": line.enrollment_id,
                "number": line.participant_number_snapshot,
                "name": line.participant_name_snapshot,
                "status": line.enrollment_status_snapshot,
                "paid": str(line.paid_amount),
                "base": str(line.distribution_base),
                "share": str(line.partner_share),
                "included": line.is_included,
                "reason": line.exclusion_reason,
            }
            for line in claim.lines.order_by("participant_number_snapshot")
        ],
        "deductions": [
            {
                "type": d.deduction_type,
                "label": d.label_ar,
                "amount": str(d.amount),
                "obligation": d.source_obligation_id,
                "refund": d.source_refund_id,
            }
            for d in claim.deductions.order_by("id")
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash_for(claim: PartnerClaim) -> str:
    return hashlib.sha256(canonical_content(claim).encode("utf-8")).hexdigest()


def open_obligations_for(*, partner: Any, agreement: Any, as_of: date) -> Any:
    """
    Which obligations may be offset against this claim.

    ⚠️ ASSUMPTION (Q-08): partner-level by default, so a refund arising under
    one agreement is recoverable from a claim under another for the same
    partner. An obligation pinned to an agreement is only ever recovered from
    that agreement's claims, whatever the setting says — the tighter rule
    always wins, because it came from a contract.
    """
    base = PartnerObligation.objects.filter(
        partner=partner,
        status__in=[ObligationStatus.OPEN, ObligationStatus.PARTIALLY_RECOVERED],
    )
    if assumptions.offset_scope(as_of=as_of) == assumptions.OFFSET_SCOPE_AGREEMENT:
        return base.filter(restricted_to_agreement=agreement)
    return base.filter(
        Q(restricted_to_agreement__isnull=True) | Q(restricted_to_agreement=agreement)
    )


def build_claim(
    *,
    actor: Any,
    agreement: Any,
    cohort: Any,
    period_from: date,
    period_to: date,
    trigger_type: str,
    trigger_reference_ar: str = "",
    request: Any = None,
) -> PartnerClaim:
    """
    Draft a claim from what was actually collected in the period.

    Ineligible enrolments appear as EXCLUDED LINES rather than being dropped:
    a partner is entitled to see that a participant was considered and why
    they earned nothing (BR-045).

    The permission check runs BEFORE the transaction opens: a refusal writes a
    denied-attempt audit row, and inside an atomic block that row would be
    rolled back along with the raise (BR-100).
    """
    policy.require(actor, Screen.CLAIMS, Action.CREATE, request=request)
    return _build_claim(
        actor=actor,
        agreement=agreement,
        cohort=cohort,
        period_from=period_from,
        period_to=period_to,
        trigger_type=trigger_type,
        trigger_reference_ar=trigger_reference_ar,
        request=request,
    )


@transaction.atomic
def _build_claim(
    *,
    actor: Any,
    agreement: Any,
    cohort: Any,
    period_from: date,
    period_to: date,
    trigger_type: str,
    trigger_reference_ar: str,
    request: Any,
) -> PartnerClaim:
    claim = PartnerClaim(
        code=f"CLM-{agreement.agreement_number}-{period_to:%Y%m}".replace("/", "-")[:32],
        partner=agreement.partner,
        agreement=agreement,
        cohort=cohort,
        period_from=period_from,
        period_to=period_to,
        trigger_type=trigger_type,
        trigger_reference_ar=trigger_reference_ar,
        created_by=actor,
    )

    gross = ZERO
    excluded = {"registration": ZERO, "consumables": ZERO, "deposits": ZERO}
    eligible_lines: list[tuple[Any, Decimal]] = []
    excluded_lines: list[tuple[Any, str]] = []

    for enrollment in cohort.enrollments.select_related("participant", "cohort"):
        verdict = entitlement_service.evaluate_eligibility(enrollment, as_of=period_to)
        if not verdict.is_eligible:
            excluded_lines.append((enrollment, verdict.reason))
            continue
        base = entitlement_service.shareable_collected(
            enrollment, agreement=agreement, as_of=period_to
        )
        gross += base
        for key, value in entitlement_service.excluded_collected(
            enrollment, agreement=agreement
        ).items():
            excluded[key] += value
        eligible_lines.append((enrollment, base))

    # C-02 — the base equation is a database constraint, so these numbers are
    # computed to satisfy it rather than displayed alongside it.
    claim.gross_collected = gross + sum(excluded.values(), ZERO)
    claim.excluded_registration = excluded["registration"]
    claim.excluded_consumables = excluded["consumables"]
    claim.excluded_deposits = excluded["deposits"]
    claim.discount_partner_burden = ZERO
    claim.distribution_base = (
        claim.gross_collected
        - claim.excluded_registration
        - claim.excluded_consumables
        - claim.excluded_deposits
        - claim.discount_partner_burden
    )
    claim.student_count = len(eligible_lines)
    claim.partner_share = entitlement_service.partner_share_for(
        agreement=agreement,
        base=claim.distribution_base,
        student_count=len(eligible_lines),
    )
    claim.total_deductions = ZERO
    claim.net_payable = claim.partner_share

    claim.model_snapshot = agreement.calculation_model
    claim.rate_snapshot = agreement.percent_rate
    claim.fixed_amount_snapshot = agreement.fixed_amount_per_student
    claim.base_mode_snapshot = assumptions.partner_base_mode(as_of=period_to)
    claim.exclusions_snapshot = {
        "registration": agreement.exclude_registration_fee,
        "consumables": agreement.exclude_consumables,
        "deposits": agreement.exclude_deposits,
    }
    claim.consumables_cap_snapshot = agreement.consumables_cap_per_student
    claim.save()

    for enrollment, base in eligible_lines:
        share = claim.partner_share / len(eligible_lines) if eligible_lines else ZERO
        line = PartnerClaimLine.objects.create(
            claim=claim,
            enrollment=enrollment,
            participant_number_snapshot=enrollment.participant.participant_number,
            participant_name_snapshot=enrollment.participant.name_ar,
            enrollment_status_snapshot=enrollment.status,
            paid_amount=base,
            distribution_base=base,
            partner_share=share.quantize(Decimal("0.001")),
            is_included=True,
        )
        Entitlement.objects.create(
            enrollment=enrollment,
            agreement=agreement,
            is_eligible=True,
            paid_amount=base,
            distribution_base=base,
            applied_rate=agreement.percent_rate,
            partner_share=share.quantize(Decimal("0.001")),
            claim_line=line,
        )

    for enrollment, reason in excluded_lines:
        PartnerClaimLine.objects.create(
            claim=claim,
            enrollment=enrollment,
            participant_number_snapshot=enrollment.participant.participant_number,
            participant_name_snapshot=enrollment.participant.name_ar,
            enrollment_status_snapshot=enrollment.status,
            is_included=False,
            exclusion_reason=reason,
        )
        Entitlement.objects.create(
            enrollment=enrollment,
            agreement=agreement,
            is_eligible=False,
            ineligibility_reason=reason,
        )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(claim.pk),
        reference=claim.code,
        summary_ar=f"إنشاء مطالبة — الوعاء {claim.distribution_base} · الحصة {claim.partner_share}",
        actor=actor,
        changes={
            "distribution_base": str(claim.distribution_base),
            "partner_share": str(claim.partner_share),
            "base_mode": claim.base_mode_snapshot,
            "eligible": len(eligible_lines),
            "excluded": len(excluded_lines),
        },
        request=request,
    )
    return claim


@transaction.atomic
def apply_offsets(*, actor: Any, claim: PartnerClaim, request: Any = None) -> list[ClaimDeduction]:
    """
    Recover open obligations from this claim (BR-036, BR-052, Q-08).

    Never more than the claim can bear: ``net_payable >= 0`` is a database
    constraint, and BR-036 forbids demanding cash from a partner. Whatever is
    not recovered here stays OPEN for the next claim.

    Every deduction names its origin, so the partner sees which event reduced
    the payment.
    """
    if claim.is_frozen:
        raise ImmutableRecordError("لا تُعدَّل مطالبة معتمدة (BR-051 · D-12).")

    remaining = claim.partner_share
    created: list[ClaimDeduction] = []

    for obligation in open_obligations_for(
        partner=claim.partner, agreement=claim.agreement, as_of=claim.period_to
    ).order_by("occurred_on", "id"):
        if remaining <= ZERO:
            break
        outstanding = obligation.amount - obligation.recovered_amount
        portion = min(remaining, outstanding)
        if portion <= ZERO:
            continue

        scope = (
            f" — مقيَّد باتفاقية {obligation.restricted_to_agreement.agreement_number}"
            if obligation.restricted_to_agreement_id
            else ""
        )
        created.append(
            ClaimDeduction.objects.create(
                claim=claim,
                deduction_type=_deduction_type_for(obligation.obligation_type),
                label_ar=(
                    f"حسم {obligation.get_obligation_type_display()} — {obligation.code}{scope}"
                )[:255],
                amount=portion,
                source_obligation=obligation,
            )
        )
        obligation.recovered_amount += portion
        obligation.status = (
            ObligationStatus.RECOVERED
            if obligation.recovered_amount >= obligation.amount
            else ObligationStatus.PARTIALLY_RECOVERED
        )
        obligation.save(update_fields=["recovered_amount", "status"])
        remaining -= portion

    claim.total_deductions = sum((d.amount for d in created), ZERO)
    claim.net_payable = claim.partner_share - claim.total_deductions
    claim.save(update_fields=["total_deductions", "net_payable"])

    if created:
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(claim.pk),
            reference=claim.code,
            summary_ar=f"حسم {claim.total_deductions} من المطالبة",
            actor=actor,
            changes={
                "deductions": [
                    {
                        "label": d.label_ar,
                        "amount": str(d.amount),
                        "obligation": d.source_obligation.code if d.source_obligation else None,
                    }
                    for d in created
                ],
                "net_payable": str(claim.net_payable),
            },
            request=request,
        )
    return created


def _deduction_type_for(obligation_type: str) -> str:
    mapping = {
        "REFUND_RECOVERY": DeductionType.REFUND_RECOVERY,
        "FIELD_TRAINING_EXPENSE": DeductionType.FIELD_TRAINING,
        "TRAINER_ABSENCE_PENALTY": DeductionType.TRAINER_ABSENCE,
        "WITHDRAWAL_RETURN": DeductionType.WITHDRAWAL_RETURN,
        "ADVANCE_CLAWBACK": DeductionType.ADVANCE_CLAWBACK,
    }
    return mapping.get(obligation_type, DeductionType.OTHER)


def approve_claim(*, actor: Any, claim: PartnerClaim, request: Any = None) -> PartnerClaim:
    """
    Approve and SEAL (BR-051).

    After this the claim is evidence: the hash covers the legal fields and
    every line, and nobody — including the centre manager — may edit it.

    As with ``build_claim``, the permission check precedes the transaction so
    a refusal leaves its audit row behind (BR-100).
    """
    policy.require(actor, Screen.CLAIMS, Action.APPROVE, request=request)
    return _approve_claim(actor=actor, claim=claim, request=request)


@transaction.atomic
def _approve_claim(*, actor: Any, claim: PartnerClaim, request: Any) -> PartnerClaim:
    if claim.is_frozen:
        raise ImmutableRecordError("المطالبة معتمدة سلفاً.")
    if claim.created_by_id == getattr(actor, "pk", None):
        raise PermissionDenied("لا يجوز اعتماد مطالبة أنشأتها بنفسك (D-18).")
    if claim.net_payable < ZERO:
        raise ValidationError(
            "صافي المطالبة سالب — الاسترجاع يكون بالحسم من مطالبة لاحقة "
            "لا بمطالبة الشريك نقداً (BR-036)."
        )

    claim.status = ClaimStatus.APPROVED
    claim.approved_by = actor
    claim.approved_at = timezone.now()
    claim.content_hash = content_hash_for(claim)
    claim.save()

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(claim.pk),
        reference=claim.code,
        summary_ar=f"اعتماد مطالبة — صافي {claim.net_payable}",
        actor=actor,
        changes={"net_payable": str(claim.net_payable), "content_hash": claim.content_hash},
        request=request,
    )
    return claim


def verify_hash(claim: PartnerClaim) -> bool:
    """BR-051 layer 3 — recompute and compare."""
    return bool(claim.content_hash) and claim.content_hash == content_hash_for(claim)


__all__ = [
    "MUTABLE_AFTER_APPROVAL",
    "apply_offsets",
    "approve_claim",
    "build_claim",
    "canonical_content",
    "content_hash_for",
    "open_obligations_for",
    "verify_hash",
]
