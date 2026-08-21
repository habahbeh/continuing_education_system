"""
Billing forms — the exceptional money (Sprint 8B).

Same discipline as the cashbox forms: shape here, rules in the services. In
particular none of these re-checks §5.1's "tuition only", BR-030's presidential
approval, BR-034's two external documents, or Sprint 8A's refusal to discount
after collection. Every one of those has a test behind it in a service, and a
form repeating it would be a copy that can drift.

The president's approval reference is marked required at the form layer as
well — not as a duplicated rule, but because leaving it blank is the single
most likely slip on this screen and a field-level message is kinder than a
refused submission (the database constraint refuses it regardless).
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.billing.models import DiscountType, ExtraFeeType


class DiscountForm(forms.Form):
    """§5.1 — a discount on tuition, approved by the president from outside."""

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    discount_type = forms.ChoiceField(label=_("نوع الخصم"), choices=DiscountType.choices)
    amount = forms.DecimalField(
        label=_("المبلغ"), max_digits=12, decimal_places=3, required=False, min_value=0
    )
    rate = forms.DecimalField(
        label=_("النسبة %"), max_digits=7, decimal_places=4, required=False, min_value=0
    )
    reason_ar = forms.CharField(label=_("السبب"), max_length=255)
    president_approval_ref = forms.CharField(label=_("رقم موافقة رئيس الجامعة"), max_length=64)
    president_approval_date = forms.DateField(
        label=_("تاريخ الموافقة"), widget=forms.DateInput({"type": "date"})
    )

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []

    def clean(self) -> dict[str, Any]:
        """
        One of amount or rate, matching the chosen type.

        Shape, not policy: the service decides what the rate resolves to and
        whether the result fits inside the tuition.
        """
        cleaned = super().clean() or {}
        kind = cleaned.get("discount_type")
        if kind == DiscountType.PERCENT and cleaned.get("rate") is None:
            self.add_error("rate", _("الخصم النسبي يحتاج نسبة."))
        if kind == DiscountType.AMOUNT and cleaned.get("amount") is None:
            self.add_error("amount", _("الخصم بمبلغ يحتاج مبلغاً."))
        return cleaned


class RefundForm(forms.Form):
    """§5.3 — the exception, carrying both of its external documents."""

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    code = forms.CharField(label=_("رمز الاسترداد"), max_length=32)
    refund_type = forms.ChoiceField(
        label=_("نوع الاسترداد"),
        choices=[("FULL", _("كامل")), ("PARTIAL", _("جزئي"))],
    )
    amount = forms.DecimalField(label=_("المبلغ"), max_digits=12, decimal_places=3, min_value=0)
    reason_ar = forms.CharField(label=_("السبب"), widget=forms.Textarea({"rows": 2}))
    official_letter_ref = forms.CharField(label=_("مرجع الكتاب الرسمي"), max_length=64)
    official_letter_date = forms.DateField(
        label=_("تاريخ الكتاب"), widget=forms.DateInput({"type": "date"})
    )
    president_approval_ref = forms.CharField(label=_("رقم موافقة الرئيس"), max_length=64)
    president_approval_date = forms.DateField(
        label=_("تاريخ الموافقة"), widget=forms.DateInput({"type": "date"})
    )

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []


class CreditReturnForm(forms.Form):
    """
    BR-071 — handing back a credit balance.

    Separate from :class:`RefundForm` on purpose. A refund reverses revenue
    and needs a presidential decree; returning someone their own overpayment
    does not, and putting them on one form would invite the heavier process to
    be applied to the lighter case, or the reverse.
    """

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    code = forms.CharField(label=_("رمز الحركة"), max_length=32)
    returned_on = forms.DateField(label=_("تاريخ الردّ"), widget=forms.DateInput({"type": "date"}))
    reason_ar = forms.CharField(label=_("السبب"), max_length=255)

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []


class ExtraFeeForm(forms.Form):
    """
    §5.5 — an additional fee.

    ``amount`` is optional because the repeat-subject and replacement fees
    have seeded amounts; leaving it blank asks the service for the configured
    value rather than for zero.
    """

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    fee_type = forms.ChoiceField(label=_("نوع الرسم"), choices=ExtraFeeType.choices)
    amount = forms.DecimalField(
        label=_("المبلغ (يُترك فارغاً للقيمة المُعدّة)"),
        max_digits=12,
        decimal_places=3,
        required=False,
        min_value=0,
    )
    subject_name = forms.CharField(label=_("اسم المادة"), max_length=150, required=False)
    charged_on = forms.DateField(label=_("تاريخ التحميل"), widget=forms.DateInput({"type": "date"}))
    prior_agreement_with_participant = forms.BooleanField(
        label=_("اتفاق مسبق مع المشارك"), required=False
    )
    is_partner_shareable = forms.NullBooleanField(
        label=_("قابل للقسمة مع الشريك (لنوع «أخرى» فقط)"), required=False
    )

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []


__all__ = ["CreditReturnForm", "DiscountForm", "ExtraFeeForm", "RefundForm"]
