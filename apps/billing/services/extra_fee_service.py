"""
Additional fees (BR-037 … BR-040, §5.5).

``ExtraFee`` and ``ChargeLine`` are not two representations of one thing —
``ExtraFee.charge_line`` has always been a foreign key waiting to be filled.
The fee is the BUSINESS record (which subject was repeated, whether the
participant agreed to the exam fee in advance); the charge line is the LEDGER
entry the participant pays and the allocator sees. This module is the only
place that creates the pair, so neither can exist without the other.

Who shares each fee comes from §5.5 and is not a caller's choice:

=========================  ========  =======================================
Fee                        Shared?   Source
=========================  ========  =======================================
إعادة مادة (75)            نعم       «50% لكل طرف» — القسمة عبر وعاء الشريك
بدل فاقد شهادة (15)        لا        «للمركز»
امتحان دولي               لا        خارج الرسوم، وبعلم المشارك (BR-040)
أخرى                      بالمعامل   يقرّرها المستدعي صراحةً
=========================  ========  =======================================

The repeat-subject fee is marked shareable rather than split in half here: a
50% agreement already halves it through the distribution base, and halving it
twice is the same double-count this sprint exists to remove.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import ChargeType, ExtraFee, ExtraFeeType
from apps.billing.services import charge_service
from apps.billing.services.account_service import ZERO
from apps.core.display import person_name, text_of
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "billing.ExtraFee"

#: BR-037 / BR-038 — the amounts are settings, seeded in Sprint 1.
FEE_SETTING_KEYS: dict[str, str] = {
    ExtraFeeType.SUBJECT_REPEAT: "subject_repeat_fee",
    ExtraFeeType.CERTIFICATE_REPLACEMENT: "certificate_replacement_fee",
}

#: §5.5 — whether the partner shares the fee. OTHER is decided by the caller.
SHAREABLE_BY_TYPE: dict[str, bool] = {
    ExtraFeeType.SUBJECT_REPEAT: True,
    ExtraFeeType.CERTIFICATE_REPLACEMENT: False,
    ExtraFeeType.INTERNATIONAL_EXAM: False,
}

DESCRIPTION_BY_TYPE: dict[str, str] = {
    ExtraFeeType.SUBJECT_REPEAT: "رسم إعادة مادة",
    ExtraFeeType.CERTIFICATE_REPLACEMENT: "رسم بدل فاقد شهادة",
    ExtraFeeType.INTERNATIONAL_EXAM: "رسم امتحان دولي",
    ExtraFeeType.OTHER: "رسم إضافي",
}


class PriorAgreementRequiredError(ValidationError):
    """BR-040 — an international exam fee the participant never agreed to."""


class FeeAmountUnknownError(ValidationError):
    """The fee has a configured amount and none was supplied or seeded."""


def default_amount_for(fee_type: str, *, as_of: date) -> Decimal | None:
    """The seeded amount for a fee type, or None where there is no default."""
    key = FEE_SETTING_KEYS.get(fee_type)
    if key is None:
        return None
    raw = get_setting(key, as_of=as_of, default=None)
    return Decimal(str(raw)) if raw is not None else None


def charge_extra_fee(
    *,
    actor: Any,
    enrollment: Any,
    fee_type: str,
    charged_on: date,
    amount: Decimal | None = None,
    subject_name: str = "",
    prior_agreement_with_participant: bool = False,
    is_partner_shareable: bool | None = None,
    request: Any = None,
) -> ExtraFee:
    """
    Charge one additional fee, creating its ledger line in the same breath.

    The permission check and every refusal run BEFORE the transaction opens,
    so a denied-attempt row survives the raise (BR-100).
    """
    policy.require(actor, Screen.EXTRA_FEES, Action.CREATE, request=request)

    if fee_type not in ExtraFeeType.values:
        raise ValidationError(f"نوع رسم غير معروف: {fee_type}")

    # BR-040 — «الامتحانات الدولية خارج الرسوم إلا باتفاق مسبق مع الطالب».
    if fee_type == ExtraFeeType.INTERNATIONAL_EXAM and not prior_agreement_with_participant:
        raise PriorAgreementRequiredError(
            "رسم الامتحان الدولي لا يُحمَّل بلا اتفاق مسبق مع المشارك (BR-040 · §5.5)."
        )

    if amount is None:
        amount = default_amount_for(fee_type, as_of=charged_on)
    if amount is None:
        raise FeeAmountUnknownError(
            f"مبلغ الرسم غير محدَّد للنوع {fee_type} — يُمرَّر صراحةً أو يُعرَّف في الإعدادات."
        )
    if amount <= ZERO:
        raise ValidationError("مبلغ الرسم الإضافي يجب أن يكون موجباً.")

    if is_partner_shareable is None:
        if fee_type == ExtraFeeType.OTHER:
            raise ValidationError(
                "الرسم من نوع «أخرى» يحتاج قراراً صريحاً بقابليته للقسمة مع الشريك."
            )
        is_partner_shareable = SHAREABLE_BY_TYPE[fee_type]

    return _charge(
        actor=actor,
        enrollment=enrollment,
        fee_type=fee_type,
        amount=amount,
        charged_on=charged_on,
        subject_name=subject_name.strip(),
        prior_agreement_with_participant=prior_agreement_with_participant,
        is_partner_shareable=is_partner_shareable,
        request=request,
    )


@transaction.atomic
def _charge(
    *,
    actor: Any,
    enrollment: Any,
    fee_type: str,
    amount: Decimal,
    charged_on: date,
    subject_name: str,
    prior_agreement_with_participant: bool,
    is_partner_shareable: bool,
    request: Any,
) -> ExtraFee:
    description = DESCRIPTION_BY_TYPE.get(fee_type, "رسم إضافي")
    if subject_name:
        description = f"{description} — {subject_name}"

    line = charge_service.create_charge_line(
        actor=actor,
        enrollment=enrollment,
        charge_type=ChargeType.EXTRA_FEE,
        description_ar=description,
        net_amount=amount,
        charged_on=charged_on,
        is_partner_shareable=is_partner_shareable,
        request=request,
    )

    fee = ExtraFee.objects.create(
        enrollment=enrollment,
        fee_type=fee_type,
        subject_name=subject_name,
        amount=amount,
        is_partner_shareable=is_partner_shareable,
        prior_agreement_with_participant=prior_agreement_with_participant,
        charge_line=line,
        charged_on=charged_on,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(fee.pk),
        reference=enrollment.code,
        summary_ar=f"{description} ({amount})",
        actor=actor,
        changes={
            "fee_type": fee_type,
            "amount": str(amount),
            "charge_line": str(line.pk),
            "is_partner_shareable": is_partner_shareable,
            "prior_agreement": prior_agreement_with_participant,
        },
        request=request,
    )
    return fee


def replacement_fee_for(enrollment: Any) -> ExtraFee | None:
    """
    The unconsumed certificate-replacement fee on this enrolment, if any.

    ``certificate_service`` finds the fee through its charge line so that a
    fee raised before this module existed still counts; this is the forward
    path, linking the business record rather than inferring it.
    """
    return (
        ExtraFee.objects.filter(
            enrollment=enrollment,
            fee_type=ExtraFeeType.CERTIFICATE_REPLACEMENT,
            charge_line__isnull=False,
            charge_line__voided=False,
            charge_line__replacement_certificates__isnull=True,
        )
        .select_related("charge_line")
        .order_by("charged_on", "id")
        .first()
    )


def list_extra_fees(
    *, actor: Any, enrollment_code: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Extra fees as rows, each with the ledger line it created (§5.5)."""
    policy.require(actor, Screen.EXTRA_FEES, Action.VIEW, request=request)

    queryset = ExtraFee.objects.select_related(
        "enrollment__participant", "charge_line", "created_by"
    )
    if enrollment_code:
        queryset = queryset.filter(enrollment__code=enrollment_code)

    return [
        {
            "id": f.pk,
            "enrollment_code": f.enrollment.code,
            "participant_name": f.enrollment.participant.name_ar,
            "fee_type": f.fee_type,
            "fee_type_display": f.get_fee_type_display(),
            "subject_name": f.subject_name,
            "amount": f.amount,
            "is_partner_shareable": f.is_partner_shareable,
            "prior_agreement_with_participant": f.prior_agreement_with_participant,
            "charged_on": f.charged_on,
            "charge_line_description": text_of(f.charge_line, "description_ar"),
            "created_by": person_name(f.created_by),
        }
        for f in queryset.order_by("-charged_on", "-id")
    ]


__all__ = [
    "DESCRIPTION_BY_TYPE",
    "FEE_SETTING_KEYS",
    "SHAREABLE_BY_TYPE",
    "FeeAmountUnknownError",
    "PriorAgreementRequiredError",
    "charge_extra_fee",
    "default_amount_for",
    "list_extra_fees",
    "replacement_fee_for",
]
