"""
Granting a discount, and deciding who bears it (BR-029 … BR-032, §5.1).

**The discount is an account-level reduction, not an edit to a charge line.**
``get_account_state`` already subtracts ``total_discount`` from the balance,
and ``ChargeLine.net_amount >= 0`` makes a negative line unstorable. So the
``Discount`` row IS the mechanism, and §5.1's "يظهر كبند صريح في كشف حساب
الطالب" is satisfied literally rather than by inference.

**Why the partner's share needs no second subtraction.**

Entitlement is cash basis (BR-044): the partner shares what was COLLECTED
against shareable lines. A participant granted a 100-dinar discount pays 100
less, so 100 less is allocated, so a 50% partner receives 50 less. The partner
has already borne their ratio of the discount by the time any claim is built.

Subtracting ``discount_partner_burden`` from the claim base on top of that
would deduct the same discount twice — which is why ``claim_service`` derives
that field from :func:`claim_base_adjustment` instead of assuming a value.
``Discount.partner_burden`` records what the partner bore; it is not an
instruction to subtract it again.

**Which split modes this supports, and why the rest are refused.**

Because the absorption happens through the base, a PERCENT agreement
distributes every discount BY RATIO whether or not that is what the contract
says. A mode that disagrees with the ratio needs a compensating movement in
the opposite direction, and the models offer none: ``ClaimDeduction.amount``
must be positive and ``PartnerObligation.amount`` may not be negative.

Supported — and between them these cover every agreement in the client file:

===========================  ==================  ============================
Calculation model            Split mode          Real agreement
===========================  ==================  ============================
PERCENT                      BY_RATIO            تناغم — «حسب النسبة المتفق
                                                 عليها لكلا الفريقين»
PERCENT at exactly 50%       HALF                تناغم · الأونلاين 50/50 —
                                                 مناصفة و«بالنسبة» تتطابقان
FIXED_PER_STUDENT            UNIVERSITY_ONLY     صرح — الشريك يقبض 195 لكل
                                                 طالب مهما بلغ الخصم
SERVICE_COMMISSION           UNIVERSITY_ONLY     عمولة ثابتة على خدمة
no agreement at all          —                   الجامعة تتحمّل الخصم كاملاً
===========================  ==================  ============================

Anything else is refused by name rather than approximated, because the wrong
answer here is a partner paid too much on a document they then sign.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import ChargeType, Discount, DiscountType
from apps.billing.services.account_service import ZERO
from apps.core.display import person_name
from apps.core.money import round_money
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "billing.Discount"

#: The snapshot written when no agreement governs the cohort — the university
#: bears the whole discount because there is no one to share it with.
NO_AGREEMENT_MODE = "UNIVERSITY_ONLY"

HUNDRED = Decimal("100")


class DiscountBaseError(ValidationError):
    """BR-029 / §5.1 — the discount is on tuition alone, and cannot exceed it."""


class DiscountAfterPaymentError(ValidationError):
    """
    A discount granted after money was allocated to shareable lines.

    Refused in Sprint 8A. The partner's absorption happens through what was
    collected, so a discount arriving after collection would leave them having
    shared a base that no longer exists, recoverable only by a mechanism this
    sprint does not build. §6.2 puts the manager's recommendation before the
    participant pays, so the supported order is also the documented one.
    """


class UnsupportedSplitError(ValidationError):
    """The agreed split cannot be reconciled with the ratio already absorbed."""


def tuition_base(enrollment: Any) -> Decimal:
    """
    What a discount may be measured against — live tuition, net of tax.

    §5.1 is explicit that registration fees are never discounted, so they are
    not merely excluded from the result: they are not in the question.
    """
    from apps.billing.models import ChargeLine

    total = ZERO
    for line in ChargeLine.objects.filter(
        enrollment=enrollment, charge_type=ChargeType.TUITION, voided=False
    ):
        total += line.net_amount
    return total


def has_shareable_allocations(enrollment: Any) -> bool:
    """Whether any money has already landed on a partner-shareable line."""
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    return PaymentAllocation.objects.filter(
        enrollment=enrollment,
        receipt__status=ReceiptStatus.ISSUED,
        charge_line__isnull=False,
        charge_line__is_partner_shareable=True,
        amount__gt=0,
    ).exists()


def agreement_for(enrollment: Any) -> Any:
    """The agreement governing this enrolment's cohort, or None."""
    return getattr(enrollment.cohort, "agreement", None)


def absorbed_by_ratio(*, amount: Decimal, agreement: Any) -> Decimal:
    """
    How much of the discount the partner bears automatically.

    For a percentage agreement this is the partner's rate applied to the
    discount, because the discount removes exactly that much from the base
    their share is drawn from. For a per-student or commission agreement it is
    zero: their share does not read the base at all, so a discount to the
    participant costs them nothing.
    """
    from apps.partners.models import CalculationModel

    if agreement is None:
        return ZERO
    if agreement.calculation_model != CalculationModel.PERCENT:
        return ZERO
    return round_money(amount * (agreement.percent_rate or ZERO) / HUNDRED)


def agreed_burden(*, amount: Decimal, agreement: Any) -> Decimal:
    """What the CONTRACT says the partner bears, before reconciliation."""
    from apps.partners.models import DiscountSplitMode

    if agreement is None:
        return ZERO

    mode = agreement.discount_split_mode
    if mode == DiscountSplitMode.UNIVERSITY_ONLY:
        return ZERO
    if mode == DiscountSplitMode.HALF:
        return round_money(amount / Decimal("2"))
    return absorbed_by_ratio(amount=amount, agreement=agreement)


def split_for(*, amount: Decimal, agreement: Any) -> tuple[Decimal, Decimal, str]:
    """
    Return ``(university_burden, partner_burden, mode_snapshot)``.

    Raises :class:`UnsupportedSplitError` when the agreed split and the ratio
    already absorbed disagree — see this module's docstring for why that is a
    refusal and not an approximation.
    """
    if agreement is None:
        return amount, ZERO, NO_AGREEMENT_MODE

    absorbed = absorbed_by_ratio(amount=amount, agreement=agreement)
    agreed = agreed_burden(amount=amount, agreement=agreement)

    if absorbed != agreed:
        raise UnsupportedSplitError(
            f"صيغة تقاسم الخصم «{agreement.discount_split_mode}» على اتفاقية "
            f"{agreement.agreement_number} تقتضي أن يتحمّل الشريك {agreed}، "
            f"بينما يتحمّل فعلياً {absorbed} لانخفاض وعاء القسمة بالخصم. "
            "التسوية بينهما تحتاج حركة تعويض غير مبنية في هذه المرحلة — "
            "يُراجَع نمط التقاسم في الاتفاقية أو يُصعَّد القرار."
        )

    return amount - agreed, agreed, agreement.discount_split_mode


def claim_base_adjustment(*, agreement: Any) -> Decimal:
    """
    The residual the CLAIM must subtract from its base — always zero here.

    Kept as a function rather than a literal so ``claim_service`` states the
    reasoning instead of asserting a number: the partner's burden is already
    inside ``gross_collected``, so the base needs no second reduction. If a
    compensating mechanism is ever built, this is the one place that changes.
    """
    return ZERO


def grant_discount(
    *,
    actor: Any,
    enrollment: Any,
    discount_type: str,
    reason_ar: str,
    president_approval_ref: str,
    president_approval_date: date,
    amount: Decimal | None = None,
    rate: Decimal | None = None,
    request: Any = None,
) -> Discount:
    """
    Record a discount on tuition (BR-029 … BR-032).

    The president approves from outside the system (D-31), so the reference
    and its date ARE the evidence — and constraint
    ``billing_discount_has_approval_ref`` refuses a discount without them even
    if a caller skipped this function.
    """
    policy.require(actor, Screen.DISCOUNTS, Action.CREATE, request=request)

    if not reason_ar.strip():
        raise ValidationError("سبب الخصم إلزامي — إعفاء من إيراد يقول لماذا أُعفي.")
    if not (president_approval_ref or "").strip():
        raise ValidationError("رقم موافقة رئيس الجامعة إلزامي للخصم (BR-030 · D-31).")

    base = tuition_base(enrollment)
    if base <= ZERO:
        raise DiscountBaseError(
            f"لا رسوم دراسية على التسجيل {enrollment.code} — "
            "الخصم على الرسوم الدراسية وحدها (§5.1)."
        )

    amount = _resolve_amount(discount_type=discount_type, amount=amount, rate=rate, base=base)
    if amount > base:
        raise DiscountBaseError(
            f"الخصم {amount} يتجاوز الرسوم الدراسية {base} — "
            "لا يُخصم من رسم التسجيل (§5.1 · BR-029)."
        )

    # The order is the control: BR-062-style refusals belong before any write.
    if has_shareable_allocations(enrollment):
        raise DiscountAfterPaymentError(
            f"سبق أن خُصِّصت دفعات على بنود قابلة للقسمة في التسجيل {enrollment.code} — "
            "الخصم يسبق القبض (§6.2: تنسيب المدير ثم الدفع)."
        )

    agreement = agreement_for(enrollment)
    university_burden, partner_burden, mode = split_for(amount=amount, agreement=agreement)

    return _grant(
        actor=actor,
        enrollment=enrollment,
        discount_type=discount_type,
        amount=amount,
        rate=rate,
        base=base,
        reason_ar=reason_ar.strip(),
        president_approval_ref=president_approval_ref.strip(),
        president_approval_date=president_approval_date,
        university_burden=university_burden,
        partner_burden=partner_burden,
        mode=mode,
        request=request,
    )


def _resolve_amount(
    *, discount_type: str, amount: Decimal | None, rate: Decimal | None, base: Decimal
) -> Decimal:
    """A percentage discount is stored as the money it came to (BR-031)."""
    if discount_type == DiscountType.PERCENT:
        if rate is None:
            raise ValidationError("الخصم النسبي يحتاج نسبة.")
        if rate <= ZERO or rate > HUNDRED:
            raise ValidationError(f"نسبة خصم غير مقبولة: {rate}.")
        return round_money(base * rate / HUNDRED)

    if amount is None:
        raise ValidationError("الخصم بمبلغ يحتاج مبلغاً.")
    if amount <= ZERO:
        raise ValidationError("مبلغ الخصم يجب أن يكون موجباً.")
    return amount


@transaction.atomic
def _grant(
    *,
    actor: Any,
    enrollment: Any,
    discount_type: str,
    amount: Decimal,
    rate: Decimal | None,
    base: Decimal,
    reason_ar: str,
    president_approval_ref: str,
    president_approval_date: date,
    university_burden: Decimal,
    partner_burden: Decimal,
    mode: str,
    request: Any,
) -> Discount:
    discount = Discount.objects.create(
        enrollment=enrollment,
        discount_type=discount_type,
        rate=rate,
        amount=amount,
        base_amount=base,
        reason_ar=reason_ar,
        president_approval_ref=president_approval_ref,
        president_approval_date=president_approval_date,
        created_by=actor,
        university_burden=university_burden,
        partner_burden=partner_burden,
        discount_split_mode_snapshot=mode,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(discount.pk),
        reference=enrollment.code,
        summary_ar=f"خصم {amount} على الرسوم الدراسية — {reason_ar}",
        actor=actor,
        changes={
            "amount": str(amount),
            "base_amount": str(base),
            "split_mode": mode,
            "university_burden": str(university_burden),
            "partner_burden": str(partner_burden),
            "president_approval_ref": president_approval_ref,
        },
        request=request,
    )
    return discount


def approve_discount(*, actor: Any, discount: Discount, request: Any = None) -> Discount:
    """
    Countersign a discount internally (D-18 — never your own).

    The president's approval is the external authority; this is the internal
    record that someone other than the raiser checked it. Constraint
    ``billing_discount_approver_differs`` refuses the same person twice.
    """
    policy.require(actor, Screen.DISCOUNTS, Action.APPROVE, request=request)

    if discount.approved_by_id is not None:
        raise ValidationError("الخصم معتمَد سلفاً.")
    if discount.created_by_id == getattr(actor, "pk", None):
        raise ValidationError("لا يعتمد الخصمَ من أنشأه (D-18).")

    return _approve(actor=actor, discount=discount, request=request)


@transaction.atomic
def _approve(*, actor: Any, discount: Discount, request: Any) -> Discount:
    from django.utils import timezone

    discount.approved_by = actor
    discount.approved_at = timezone.now()
    discount.save(update_fields=["approved_by", "approved_at"])

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(discount.pk),
        reference=discount.enrollment.code,
        summary_ar=f"اعتماد خصم {discount.amount}",
        actor=actor,
        changes={"amount": str(discount.amount), "creator": discount.created_by_id},
        request=request,
    )
    return discount


def list_discounts(
    *, actor: Any, enrollment_code: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Discounts as rows, with the split each one recorded (§5.1)."""
    policy.require(actor, Screen.DISCOUNTS, Action.VIEW, request=request)

    queryset = Discount.objects.select_related(
        "enrollment__participant", "created_by", "approved_by"
    )
    if enrollment_code:
        queryset = queryset.filter(enrollment__code=enrollment_code)

    return [
        {
            "id": d.pk,
            "enrollment_code": d.enrollment.code,
            "participant_name": d.enrollment.participant.name_ar,
            "discount_type": d.discount_type,
            "rate": d.rate,
            "amount": d.amount,
            "base_amount": d.base_amount,
            "reason_ar": d.reason_ar,
            "president_approval_ref": d.president_approval_ref,
            "president_approval_date": d.president_approval_date,
            "university_burden": d.university_burden,
            "partner_burden": d.partner_burden,
            "split_mode": d.discount_split_mode_snapshot,
            "created_by": person_name(d.created_by),
            "created_by_id": d.created_by_id,
            "is_approved": d.approved_by_id is not None,
            "approved_by": person_name(d.approved_by),
        }
        for d in queryset.order_by("-created_at")
    ]


def get_discount(*, actor: Any, discount_id: int, request: Any = None) -> Discount:
    """The Discount row itself, for a service call that needs the instance."""
    policy.require(actor, Screen.DISCOUNTS, Action.VIEW, request=request)
    return Discount.objects.get(pk=discount_id)


__all__ = [
    "NO_AGREEMENT_MODE",
    "DiscountAfterPaymentError",
    "DiscountBaseError",
    "UnsupportedSplitError",
    "absorbed_by_ratio",
    "agreed_burden",
    "agreement_for",
    "approve_discount",
    "claim_base_adjustment",
    "get_discount",
    "grant_discount",
    "has_shareable_allocations",
    "list_discounts",
    "split_for",
    "tuition_base",
]
