"""
Partner and agreement forms — shape only (Sprint 8F).

Whether the terms are complete, whether BY_RATIO is legal on this model and
whether an agreement may supersede another are all decided in
``partner_service``, which has the tests behind it and the messages carrying
their own rule references.

What lives here is the BRANCH. §3.4 supports three calculation models and the
database refuses an agreement missing the numbers its own model needs, so a
single flat form would let someone fill in a percentage, choose "fixed amount
per student", and meet a constraint violation on save. ``AgreementForm``
therefore marks every model-specific field optional at the field level and
requires them in ``clean()`` according to the model chosen — and the template
shows only the group that is in play.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, RATE_DECIMAL_PLACES
from apps.partners.models import (
    CalculationModel,
    DiscountSplitMode,
    PartnerStatus,
    PartnerType,
    PayoutTiming,
    SettlementCycle,
)
from apps.partners.services import partner_service


def _money(label: Any, **kwargs: Any) -> forms.DecimalField:
    return forms.DecimalField(
        label=label,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        min_value=0,
        **kwargs,
    )


class PartnerForm(forms.Form):
    """§3.5/23 — the partner as they appear on the contract."""

    code = forms.CharField(label=_("الرمز"), max_length=32)
    name_ar = forms.CharField(label=_("اسم الشريك"), max_length=150)
    name_en = forms.CharField(label=_("الاسم بالإنجليزية"), max_length=150, required=False)
    partner_type = forms.ChoiceField(label=_("النوع"), choices=PartnerType.choices)
    registry_number = forms.CharField(label=_("رقم التسجيل"), max_length=64, required=False)
    registry_date = forms.DateField(
        label=_("تاريخ التسجيل"), required=False, widget=forms.DateInput({"type": "date"})
    )
    contact_name = forms.CharField(label=_("جهة الاتصال"), max_length=150, required=False)
    phone = forms.CharField(label=_("الهاتف"), max_length=32, required=False)
    email = forms.EmailField(label=_("البريد الإلكتروني"), required=False)
    status = forms.ChoiceField(
        label=_("الحالة"), choices=PartnerStatus.choices, initial=PartnerStatus.ACTIVE
    )

    def to_service_data(self) -> dict[str, Any]:
        return dict(self.cleaned_data)


class AgreementForm(forms.Form):
    """
    §3.5/25 — one signed agreement, transcribed.

    Every field on the model that carries a term is here. The listing screen
    already argues why a summary will not do: the exclusions, the discount
    split and the settlement cycle are exactly what differs between the signed
    agreements in the client's file, so a form that collected only the
    headline rate would make the rest unrecordable.
    """

    partner_code = forms.ChoiceField(label=_("الشريك"), choices=[])
    agreement_number = forms.CharField(label=_("رقم الاتفاقية"), max_length=64)
    title_ar = forms.CharField(label=_("عنوان الاتفاقية"), max_length=255)
    signed_on = forms.DateField(label=_("تاريخ التوقيع"), widget=forms.DateInput({"type": "date"}))
    valid_from = forms.DateField(label=_("سارية من"), widget=forms.DateInput({"type": "date"}))
    valid_to = forms.DateField(label=_("سارية حتى"), widget=forms.DateInput({"type": "date"}))

    calculation_model = forms.ChoiceField(
        label=_("نموذج الاحتساب"), choices=CalculationModel.choices
    )
    # --- PERCENT ---------------------------------------------------------
    percent_rate = forms.DecimalField(
        label=_("النسبة %"),
        max_digits=7,
        decimal_places=RATE_DECIMAL_PLACES,
        min_value=0,
        max_value=100,
        required=False,
    )
    # --- FIXED_PER_STUDENT ------------------------------------------------
    fixed_amount_per_student = _money(_("حصة الشريك لكل طالب"), required=False)
    sell_price = _money(_("سعر البيع المرجعي"), required=False)
    # --- SERVICE_COMMISSION ----------------------------------------------
    service_name_ar = forms.CharField(label=_("اسم الخدمة"), max_length=255, required=False)
    service_price = _money(_("سعر الخدمة"), required=False)
    commission_amount = _money(_("مبلغ العمولة"), required=False)

    exclude_registration_fee = forms.BooleanField(
        label=_("استثناء رسم التسجيل من وعاء القسمة"), required=False, initial=True
    )
    exclude_deposits = forms.BooleanField(
        label=_("استثناء التأمينات من وعاء القسمة"),
        required=False,
        initial=True,
        help_text=_("Q-01 · BR-092 — تعطيله يتطلب نصاً صريحاً في الاتفاقية"),
    )
    exclude_consumables = forms.BooleanField(
        label=_("استثناء المستهلكات من وعاء القسمة"), required=False
    )
    consumables_cap_per_student = _money(_("سقف المستهلكات لكل طالب"), required=False)

    discount_split_mode = forms.ChoiceField(
        label=_("توزيع عبء الخصم"),
        choices=DiscountSplitMode.choices,
        initial=DiscountSplitMode.HALF,
    )
    payout_timing = forms.ChoiceField(
        label=_("توقيت الصرف"), choices=PayoutTiming.choices, initial=PayoutTiming.END_OF_COURSE
    )
    settlement_cycle = forms.ChoiceField(
        label=_("دورة المخالصة"),
        choices=SettlementCycle.choices,
        initial=SettlementCycle.END_OF_COURSE,
    )
    name_list_due_days = forms.IntegerField(
        label=_("مهلة كشف الأسماء بالأيام"),
        min_value=0,
        required=False,
        help_text=_("BR-054 — بعدها يُقفل الكشف ويُحتسب استرجاع الصرف المقدّم"),
    )
    entitlement_rule_ar = forms.CharField(
        label=_("نص قاعدة الاستحقاق"), required=False, widget=forms.Textarea({"rows": 3})
    )
    supersedes = forms.ChoiceField(
        label=_("ملحق لاتفاقية سارية"),
        choices=[],
        required=False,
        help_text=_("تُنهى الاتفاقية المختارة عند سريان هذا الملحق"),
    )

    #: The form, in the order and the sections a contract is read in. The
    #: three model-specific groups carry the calculation model they belong to
    #: and are shown only for it — taken from the same mapping the service
    #: uses to decide what to STORE, so the screen cannot offer a box whose
    #: value would be discarded.
    #:
    #: Grouping lives here rather than in the template because the template
    #: can only test membership of a real sequence; a comma-joined string and
    #: Django's substring ``in`` would put ``sell_price`` into any group whose
    #: text happened to contain it.
    GROUPS: tuple[tuple[Any, str, tuple[str, ...]], ...] = (
        (
            _("الأطراف والسريان"),
            "",
            ("partner_code", "agreement_number", "title_ar", "signed_on", "valid_from", "valid_to"),
        ),
        (_("نموذج الاحتساب"), "", ("calculation_model",)),
        (_("النسبة"), CalculationModel.PERCENT, partner_service.MODEL_FIELDS["PERCENT"]),
        (
            _("المبلغ الثابت لكل طالب"),
            CalculationModel.FIXED_PER_STUDENT,
            partner_service.MODEL_FIELDS["FIXED_PER_STUDENT"],
        ),
        (
            _("عمولة الخدمة"),
            CalculationModel.SERVICE_COMMISSION,
            partner_service.MODEL_FIELDS["SERVICE_COMMISSION"],
        ),
        (
            _("الاستثناءات من وعاء القسمة"),
            "",
            (
                "exclude_registration_fee",
                "exclude_deposits",
                "exclude_consumables",
                "consumables_cap_per_student",
            ),
        ),
        (
            _("التوقيت والتقاسم"),
            "",
            (
                "discount_split_mode",
                "payout_timing",
                "settlement_cycle",
                "name_list_due_days",
                "entitlement_rule_ar",
                "supersedes",
            ),
        ),
    )

    def grouped(self) -> list[dict[str, Any]]:
        """The bound fields, sectioned, for a template that stays dumb."""
        return [
            {"title": title, "model": model, "fields": [self[name] for name in names]}
            for title, model, names in self.GROUPS
        ]

    def __init__(
        self,
        *args: Any,
        partner_choices: list[tuple[str, str]] | None = None,
        agreement_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["partner_code"]).choices = partner_choices or []
        cast(forms.ChoiceField, self.fields["supersedes"]).choices = [("", "—")] + (
            agreement_choices or []
        )

    def clean(self) -> dict[str, Any]:
        """
        Ask the service the same question the database will ask.

        Calling ``check_agreement_terms`` rather than restating its rules is
        the point: a second copy here would be a second opinion, and the two
        would disagree the first time either changed.
        """
        cleaned = super().clean()
        if cleaned is None:  # pragma: no cover - Django always returns a dict
            return {}
        try:
            partner_service.check_agreement_terms(cleaned)
        except ValidationError as exc:
            for field, messages in getattr(exc, "message_dict", {}).items():
                for message in messages:
                    self.add_error(field if field in self.fields else None, message)
        return cleaned

    def to_service_data(self) -> dict[str, Any]:
        """The agreement's own fields — partner and successor link excluded."""
        data = dict(self.cleaned_data)
        data.pop("partner_code", None)
        data.pop("supersedes", None)
        return data


__all__ = ["AgreementForm", "PartnerForm"]
