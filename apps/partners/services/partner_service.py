"""
Reading partners and agreements (Sprint 8B).

**Read only, deliberately.** Agreements are negotiated documents that get
signed on paper; the system's job is to carry their terms faithfully, not to
author them. An editor is a later sprint and a bigger conversation — a screen
that let someone change ``percent_rate`` after claims had been drawn against
it would quietly restate what a partner had already been paid.

The listing exposes every configurable term rather than a summary, because
§3.4's whole point is that the three contract models are DATA: «النظام يدعم
ثلاثة نماذج ولا يثبّت أياً منها في الكود». A screen showing only "50%" would
hide the exclusions, the discount split and the settlement cycle — which are
exactly the fields that differ between the signed agreements in the file.
"""

from __future__ import annotations

from typing import Any

from apps.core.display import text_of
from apps.people.constants import Action, Screen
from apps.people.permissions import policy


def list_partners(*, actor: Any, query: str = "", request: Any = None) -> list[dict[str, Any]]:
    """Partners as rows, with how many agreements each holds."""
    from apps.partners.models import Partner

    policy.require(actor, Screen.PARTNERS, Action.VIEW, request=request)

    queryset = Partner.objects.all()
    if query:
        queryset = queryset.filter(name_ar__icontains=query) | queryset.filter(
            code__icontains=query
        )

    return [
        {
            "code": p.code,
            "name_ar": p.name_ar,
            "partner_type": p.partner_type,
            "partner_type_display": p.get_partner_type_display(),
            "status": p.status,
            "status_display": p.get_status_display(),
            "registry_number": p.registry_number,
            "registry_date": p.registry_date,
            "contact_name": p.contact_name,
            "phone": p.phone,
            "email": p.email,
            "agreement_count": p.agreements.count(),
        }
        for p in queryset.order_by("code")
    ]


def list_agreements(
    *, actor: Any, partner_code: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """
    Agreements as rows, carrying every term that varies between them.

    ``calculation_model`` decides which of the value fields means anything:
    a percentage agreement has no ``fixed_amount_per_student`` and a
    per-student one has no rate. Both are handed over and the template shows
    whichever the model names — the alternative is a service deciding what a
    screen may display.
    """
    from apps.partners.models import Agreement

    policy.require(actor, Screen.AGREEMENTS, Action.VIEW, request=request)

    queryset = Agreement.objects.select_related("partner")
    if partner_code:
        queryset = queryset.filter(partner__code=partner_code)

    return [
        {
            "agreement_number": a.agreement_number,
            "partner_name": a.partner.name_ar,
            "partner_code": a.partner.code,
            "title_ar": a.title_ar,
            "signed_on": a.signed_on,
            "valid_from": a.valid_from,
            "valid_to": a.valid_to,
            "status": a.status,
            "status_display": a.get_status_display(),
            "calculation_model": a.calculation_model,
            "calculation_model_display": a.get_calculation_model_display(),
            "percent_rate": a.percent_rate,
            "fixed_amount_per_student": a.fixed_amount_per_student,
            "sell_price": a.sell_price,
            "service_name_ar": a.service_name_ar,
            "service_price": a.service_price,
            "commission_amount": a.commission_amount,
            "exclude_registration_fee": a.exclude_registration_fee,
            "exclude_deposits": a.exclude_deposits,
            "exclude_consumables": a.exclude_consumables,
            "consumables_cap_per_student": a.consumables_cap_per_student,
            "discount_split_mode": a.discount_split_mode,
            "discount_split_mode_display": a.get_discount_split_mode_display(),
            "payout_timing": a.payout_timing,
            "payout_timing_display": a.get_payout_timing_display(),
            "settlement_cycle": a.settlement_cycle,
            "settlement_cycle_display": a.get_settlement_cycle_display(),
            "name_list_due_days": a.name_list_due_days,
            "entitlement_rule_ar": a.entitlement_rule_ar,
            "supersedes": text_of(a.supersedes, "agreement_number"),
        }
        for a in queryset.order_by("-valid_from", "agreement_number")
    ]


def get_agreement(*, actor: Any, agreement_number: str, request: Any = None) -> dict[str, Any]:
    """One agreement, or a KeyError-free empty dict if it is not there."""
    rows = list_agreements(actor=actor, request=request)
    return next((r for r in rows if r["agreement_number"] == agreement_number), {})


def agreement_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    (number, label) pairs of ACTIVE agreements, for the cohort form.

    Expired and terminated agreements are omitted: a cohort opened today
    cannot sensibly be placed under a contract that has ended, and offering
    one would invite exactly that.
    """
    from apps.partners.models import Agreement, AgreementStatus

    policy.require(actor, Screen.AGREEMENTS, Action.VIEW, request=request)

    return [
        (a.agreement_number, f"{a.agreement_number} — {a.partner.name_ar}")
        for a in Agreement.objects.select_related("partner")
        .filter(status=AgreementStatus.ACTIVE)
        .order_by("agreement_number")
    ]


def get_partner(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """One partner with the agreements held under them."""
    rows = list_partners(actor=actor, request=request)
    partner = next((r for r in rows if r["code"] == code), {})
    if partner:
        partner["agreements"] = list_agreements(actor=actor, partner_code=code, request=request)
    return partner


def agreement_instance(*, actor: Any, agreement_number: str, request: Any = None) -> Any:
    """The Agreement model object, for handing to another service (A-05)."""
    from apps.partners.models import Agreement

    policy.require(actor, Screen.AGREEMENTS, Action.VIEW, request=request)
    return Agreement.objects.select_related("partner").get(agreement_number=agreement_number)


def partner_instance(*, actor: Any, code: str, request: Any = None) -> Any:
    """The Partner model object, for handing to another service (A-05)."""
    from apps.partners.models import Partner

    policy.require(actor, Screen.PARTNERS, Action.VIEW, request=request)
    return Partner.objects.get(code=code)


__all__ = [
    "agreement_choices",
    "agreement_instance",
    "get_agreement",
    "get_partner",
    "list_agreements",
    "list_partners",
    "partner_instance",
]
