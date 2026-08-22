"""
Partners and agreements — reading, and now recording (Sprints 8B, 8F).

Sprint 8B built this read-only and said so: agreements are negotiated
documents signed on paper, and the system's job is to carry their terms
faithfully rather than to author them. That remains true. What changed is
that a fresh installation could not record a signed agreement at all — the
only Partner and Agreement rows in existence were the ones test fixtures
made, which blocked claims, settlements, obligations and every partner figure
in the reports.

So this module now records what was signed. It still does not AUTHOR:

* Nothing computes a term. Every number here comes off the paper contract.
* Nothing is editable once live. ``is_frozen`` covers ACTIVE, EXPIRED and
  TERMINATED, and no function below writes to an agreement in those states.
  A term that moved after claims had been drawn against it would quietly
  restate what a partner had already been paid — the reason 8B held back, and
  still the reason.
* A correction to a live agreement is a NEW agreement that supersedes it, the
  way an appendix works on paper. ``supersede_agreement`` is that act.

**Three separate permissions, because they are three separate acts** and the
matrix already separates them. Authoring a draft is ``C`` on
``AGREEMENT_NEW`` (§3.5/25, MGR only — the audit account holds ``V`` there and
may read the editor without filling it in). Making a draft live is ``A`` on
``AGREEMENTS`` (§3.5/24). Ending the agreement it replaces is ``E`` on the
same screen. Nobody was granted anything new to make this fit.

The listing exposes every configurable term rather than a summary, because
§3.4's whole point is that the three contract models are DATA: «النظام يدعم
ثلاثة نماذج ولا يثبّت أياً منها في الكود». A screen showing only "50%" would
hide the exclusions, the discount split and the settlement cycle — which are
exactly the fields that differ between the signed agreements in the file.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.display import text_of
from apps.core.exceptions import ImmutableRecordError
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

PARTNER_ENTITY = "partners.Partner"
AGREEMENT_ENTITY = "partners.Agreement"

#: Which value fields each calculation model actually uses. Everything absent
#: from a model's tuple is cleared on save — a leftover ``percent_rate`` on a
#: per-student agreement is not harmless, it reads as a term of the contract.
MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    "PERCENT": ("percent_rate",),
    "FIXED_PER_STUDENT": ("fixed_amount_per_student", "sell_price"),
    "SERVICE_COMMISSION": ("service_name_ar", "service_price", "commission_amount"),
}

VALUE_FIELDS: tuple[str, ...] = (
    "percent_rate",
    "fixed_amount_per_student",
    "sell_price",
    "service_name_ar",
    "service_price",
    "commission_amount",
)


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

    today = timezone.localdate()
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
            # Computed, not stored (Sprint 8F-1). The listing shows every
            # agreement including the lapsed ones — a contract that ended is
            # still the contract a past claim was raised under — and these two
            # flags let the screen say so without anybody having to compare
            # dates in their head.
            "is_expired": a.valid_to < today,
            "is_available": a.is_available_on(today),
        }
        for a in queryset.order_by("-valid_from", "agreement_number")
    ]


def get_agreement(*, actor: Any, agreement_number: str, request: Any = None) -> dict[str, Any]:
    """One agreement, or a KeyError-free empty dict if it is not there."""
    rows = list_agreements(actor=actor, request=request)
    return next((r for r in rows if r["agreement_number"] == agreement_number), {})


def agreement_choices(
    *, actor: Any, as_of: date | None = None, request: Any = None
) -> list[tuple[str, str]]:
    """
    (number, label) pairs of agreements in force on ``as_of``, for the cohort form.

    Sprint 8B's docstring here claimed expired agreements were omitted and
    Sprint 8F left the claim standing, but the filter was on STATUS alone and
    nothing ever writes EXPIRED — so a contract that ran out last year was
    offered to the cohort form exactly like one signed last week. That is the
    defect Sprint 8F-1 exists for.

    **Filtered on the day the list is drawn, not on the cohort's start date.**
    The honest question for a cohort is whether the contract covers the period
    it runs in, and ``cohort_service`` asks precisely that when the form is
    submitted, because by then ``starts_on`` is known. Here it is not — the
    dropdown is rendered before the user has typed a date — so the list uses
    today and the service uses the date that matters. A contract that lapses
    between the two is offered and then refused BY NAME, with both dates in
    the message, which is a better outcome than a list that cannot be built.

    Terminated and draft agreements stay out for the reason they always did:
    one was ended by somebody and the other was never made live.
    """
    from apps.partners.models import Agreement, AgreementStatus

    policy.require(actor, Screen.AGREEMENTS, Action.VIEW, request=request)
    on_date = as_of or timezone.localdate()

    return [
        (a.agreement_number, f"{a.agreement_number} — {a.partner.name_ar}")
        for a in Agreement.objects.select_related("partner")
        .filter(
            status=AgreementStatus.ACTIVE,
            valid_from__lte=on_date,
            valid_to__gte=on_date,
        )
        .order_by("agreement_number")
    ]


# ---------------------------------------------------------------------------
# Recording what was signed (Sprint 8F)
# ---------------------------------------------------------------------------
def create_partner(*, actor: Any, data: dict[str, Any], request: Any = None) -> Any:
    """
    A partner as they appear on the contract (§3.5/23 gives MGR ``C``).

    Thin on purpose. A partner is a name, a type and the registry details that
    identify it; every rule that matters lives on the AGREEMENT, and inventing
    validation here would only stand between the manager and a company that
    exists.
    """
    from apps.partners.models import Partner

    policy.require(actor, Screen.PARTNERS, Action.CREATE, request=request)

    with transaction.atomic():
        partner = Partner(**data)
        partner.full_clean()
        partner.save()
        write_audit(
            action="CREATE",
            entity_type=PARTNER_ENTITY,
            entity_id=str(partner.pk),
            reference=partner.code,
            summary_ar=f"إنشاء شريك — {partner.name_ar}",
            actor=actor,
            changes={"partner_type": partner.partner_type, "status": partner.status},
            request=request,
        )
    return partner


def check_agreement_terms(data: dict[str, Any]) -> None:
    """
    The refusals the database would give, in words that name the field.

    Every rule here has a CHECK constraint behind it, so this function is not
    the control — it is the MESSAGE. Django surfaces a violated constraint as
    «Constraint “partners_agreement_percent_complete” is violated», which is
    true, unhelpful, and not in Arabic. A person filling in a contract needs
    to be told which box is empty.

    Raised as a field-keyed ``ValidationError`` so the form can put each
    message beside its own input.
    """
    from apps.partners.models import CalculationModel, DiscountSplitMode

    model = data.get("calculation_model", "")
    errors: dict[str, str] = {}

    if model == CalculationModel.PERCENT:
        rate = data.get("percent_rate")
        if rate is None:
            errors["percent_rate"] = "الاتفاقية النسبية تتطلب نسبة القسمة (BR-047)."
        elif not (Decimal("0") <= Decimal(rate) <= Decimal("100")):
            errors["percent_rate"] = "النسبة يجب أن تقع بين 0 و 100."

    elif model == CalculationModel.FIXED_PER_STUDENT:
        amount = data.get("fixed_amount_per_student")
        sell = data.get("sell_price")
        if amount is None:
            errors["fixed_amount_per_student"] = (
                "اتفاقية المبلغ الثابت تتطلب حصة الشريك لكل طالب (BR-048)."
            )
        if sell is None:
            errors["sell_price"] = "اتفاقية المبلغ الثابت تتطلب سعر البيع المرجعي."
        if amount is not None and sell is not None and Decimal(sell) < Decimal(amount):
            errors["sell_price"] = "سعر البيع لا يقل عن حصة الشريك — وإلا كانت حصة الجامعة سالبة."

    elif model == CalculationModel.SERVICE_COMMISSION:
        price = data.get("service_price")
        commission = data.get("commission_amount")
        if price is None:
            errors["service_price"] = "اتفاقية العمولة تتطلب سعر الخدمة."
        if commission is None:
            errors["commission_amount"] = "اتفاقية العمولة تتطلب مبلغ العمولة (BR-049)."
        if price is not None and commission is not None and Decimal(commission) > Decimal(price):
            errors["commission_amount"] = "العمولة لا تتجاوز سعر الخدمة."

    else:
        errors["calculation_model"] = "نموذج احتساب غير معروف."

    # C-01 — the demo's live defect. BY_RATIO reads the split as a proportion,
    # so on a per-student agreement it treated 195 dinars as a percentage and
    # printed a negative university share.
    if (
        data.get("discount_split_mode") == DiscountSplitMode.BY_RATIO
        and model != CalculationModel.PERCENT
    ):
        errors["discount_split_mode"] = (
            "توزيع الخصم «بنسبة القسمة» لا يصلح إلا للاتفاقية النسبية — "
            "على غيرها يُقرأ المبلغ الثابت كأنه نسبة (C-01)."
        )

    valid_from, valid_to = data.get("valid_from"), data.get("valid_to")
    if valid_from and valid_to and valid_to <= valid_from:
        errors["valid_to"] = "نهاية السريان بعد بدايته."

    cap = data.get("consumables_cap_per_student")
    if cap is not None and Decimal(cap) < 0:
        errors["consumables_cap_per_student"] = "سقف المستهلكات لا يكون سالباً."

    if errors:
        raise ValidationError(errors)


def _only_the_terms_this_model_uses(data: dict[str, Any]) -> dict[str, Any]:
    """Clear the value fields the chosen model does not read."""
    keep = MODEL_FIELDS.get(data.get("calculation_model", ""), ())
    cleaned = dict(data)
    for field in VALUE_FIELDS:
        if field not in keep:
            cleaned[field] = "" if field == "service_name_ar" else None
    return cleaned


def create_agreement(*, actor: Any, partner: Any, data: dict[str, Any], request: Any = None) -> Any:
    """
    Record a signed agreement as a DRAFT (§3.5/25 gives MGR ``C``).

    **It is born a draft and that is the point.** Making it live is a separate
    act under a separate permission (``A`` on §3.5/24), because activation is
    what freezes the terms and ends whatever it supersedes. A single button
    that did both would give the manager no moment to read back what they had
    typed against the paper in front of them.

    ``supersedes`` may be set here — the appendix knows which contract it
    amends from the day it is written — but the predecessor is not ended
    until this one is activated. Ending it sooner would leave the partner
    under no agreement at all in between.
    """
    from apps.partners.models import Agreement, AgreementStatus

    policy.require(actor, Screen.AGREEMENT_NEW, Action.CREATE, request=request)
    check_agreement_terms(data)

    fields = _only_the_terms_this_model_uses(data)
    with transaction.atomic():
        agreement = Agreement(partner=partner, status=AgreementStatus.DRAFT, **fields)
        agreement.full_clean()
        agreement.save()
        write_audit(
            action="CREATE",
            entity_type=AGREEMENT_ENTITY,
            entity_id=str(agreement.pk),
            reference=agreement.agreement_number,
            summary_ar=f"تسجيل اتفاقية موقّعة — {agreement.title_ar} ({partner.name_ar})",
            actor=actor,
            changes={
                "calculation_model": agreement.calculation_model,
                "valid_from": agreement.valid_from.isoformat(),
                "valid_to": agreement.valid_to.isoformat(),
                "supersedes": text_of(agreement.supersedes, "agreement_number"),
            },
            request=request,
        )
    return agreement


def _refuse_unless_supersedable(previous: Any, successor: Any) -> None:
    from apps.partners.models import AgreementStatus

    if previous.pk == successor.pk:
        raise ValidationError("الاتفاقية لا تحلّ محلّ نفسها.")
    if previous.partner_id != successor.partner_id:
        raise ValidationError(
            f"الملحق يحلّ محلّ اتفاقية الشريك نفسه — {previous.agreement_number} تخصّ شريكاً آخر."
        )
    if previous.status != AgreementStatus.ACTIVE:
        raise ValidationError(
            f"الاتفاقية {previous.agreement_number} بحالة {previous.get_status_display()} — "
            "لا يحلّ محلّ اتفاقية غير سارية."
        )


def _write_supersession(actor: Any, previous: Any, successor: Any, request: Any) -> None:
    from apps.partners.models import AgreementStatus

    if successor.supersedes_id != previous.pk:
        successor.supersedes = previous
        successor.save(update_fields=["supersedes"])

    previous.status = AgreementStatus.TERMINATED
    previous.save(update_fields=["status"])

    write_audit(
        action="UPDATE",
        entity_type=AGREEMENT_ENTITY,
        entity_id=str(previous.pk),
        reference=previous.agreement_number,
        summary_ar=(
            f"إنهاء الاتفاقية {previous.agreement_number} — "
            f"حلّ محلّها الملحق {successor.agreement_number}"
        ),
        actor=actor,
        changes={
            "status": {"from": AgreementStatus.ACTIVE, "to": AgreementStatus.TERMINATED},
            "superseded_by": successor.agreement_number,
        },
        request=request,
    )


def supersede_agreement(*, actor: Any, previous: Any, successor: Any, request: Any = None) -> Any:
    """
    An appendix replaces the contract it amends (§3.5/24 ``E`` + ``A``).

    **Nothing is rewritten and nothing is deleted.** The predecessor keeps
    every term it was signed with and moves to TERMINATED; the successor
    carries a ``supersedes`` link back to it. Both stay readable, which is the
    whole reason the link exists — a claim raised last spring was raised under
    the old terms, and a reader has to be able to find them.

    **TERMINATED, not a new SUPERSEDED status.** ``AgreementStatus`` has four
    values and its CHECK constraint is frozen into a migration, so a fifth
    would cost a schema change for a distinction the ``supersedes`` link
    already records: an agreement that ended AND has a successor pointing at
    it was superseded, and one without such a pointer was simply ended.

    Both must be ACTIVE. Ending a live contract in favour of a draft would
    leave the partner under nothing.
    """
    from apps.partners.models import AgreementStatus

    policy.require(actor, Screen.AGREEMENTS, Action.EDIT, request=request)
    policy.require(actor, Screen.AGREEMENTS, Action.APPROVE, request=request)

    _refuse_unless_supersedable(previous, successor)
    if successor.status != AgreementStatus.ACTIVE:
        raise ValidationError(
            f"الملحق {successor.agreement_number} ليس سارياً بعد — "
            "لا تُنهى الاتفاقية السابقة قبل أن يسري ما يحلّ محلّها."
        )

    with transaction.atomic():
        _write_supersession(actor, previous, successor, request)
    return previous


def activate_agreement(
    *, actor: Any, agreement: Any, as_of: date | None = None, request: Any = None
) -> Any:
    """
    Make a draft live, and end what it replaces in the same breath (``A``).

    Activation is the freeze: ``is_frozen`` becomes true and nothing below
    writes to the agreement again. If the draft names a predecessor, that
    contract ends here — one act, one transaction, so there is no instant in
    which both are live and no instant in which neither is.

    **An already-lapsed window is refused; a future one is not** (Sprint
    8F-1). The asymmetry is the whole of the rule and it follows the paper:
    agreements are signed in August for a term that starts in September, so
    refusing activation until ``valid_from`` would mean the centre could not
    record its own signed contracts until the day they took effect. Such an
    agreement is activated, and simply not OFFERED by ``agreement_choices``
    until its window opens. One whose ``valid_to`` has already passed is a
    different thing: nothing it could be attached to lies inside its window,
    so activating it would produce a contract that is live and unusable in the
    same breath.

    Recording a lapsed agreement is still allowed — ``create_agreement`` takes
    any dates, because a claim on work delivered under an old contract needs
    that contract to exist in the system. What is refused is pretending it is
    in force.
    """
    from apps.partners.models import AgreementStatus

    policy.require(actor, Screen.AGREEMENTS, Action.APPROVE, request=request)
    on_date = as_of or timezone.localdate()

    previous = agreement.supersedes
    if previous is not None:
        policy.require(actor, Screen.AGREEMENTS, Action.EDIT, request=request)
        _refuse_unless_supersedable(previous, agreement)

    if agreement.valid_to < on_date:
        raise ValidationError(
            f"انتهت مدة سريان الاتفاقية {agreement.agreement_number} في "
            f"{agreement.valid_to} — لا تُعتمد اتفاقية منقضية. "
            "التعاقد المستمر يكون بملحق جديد بمدة سريان جديدة."
        )

    if agreement.status != AgreementStatus.DRAFT:
        raise ImmutableRecordError(
            f"الاتفاقية {agreement.agreement_number} بحالة "
            f"{agreement.get_status_display()} — السريان لا يُمنح إلا لمسودة (D-15)."
        )

    with transaction.atomic():
        agreement.status = AgreementStatus.ACTIVE
        agreement.save(update_fields=["status"])
        write_audit(
            action="APPROVE",
            entity_type=AGREEMENT_ENTITY,
            entity_id=str(agreement.pk),
            reference=agreement.agreement_number,
            summary_ar=f"سريان الاتفاقية {agreement.agreement_number} — {agreement.title_ar}",
            actor=actor,
            changes={"status": {"from": AgreementStatus.DRAFT, "to": AgreementStatus.ACTIVE}},
            request=request,
        )
        if previous is not None:
            _write_supersession(actor, previous, agreement, request)
    return agreement


def partner_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """(code, label) pairs of ACTIVE partners, for the agreement form."""
    from apps.partners.models import Partner, PartnerStatus

    policy.require(actor, Screen.PARTNERS, Action.VIEW, request=request)

    return [
        (p.code, f"{p.code} — {p.name_ar}")
        for p in Partner.objects.filter(status=PartnerStatus.ACTIVE).order_by("code")
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
    "AGREEMENT_ENTITY",
    "MODEL_FIELDS",
    "PARTNER_ENTITY",
    "VALUE_FIELDS",
    "activate_agreement",
    "agreement_choices",
    "agreement_instance",
    "check_agreement_terms",
    "create_agreement",
    "create_partner",
    "get_agreement",
    "get_partner",
    "list_agreements",
    "list_partners",
    "partner_choices",
    "partner_instance",
    "supersede_agreement",
]
