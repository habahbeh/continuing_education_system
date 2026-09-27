"""
Cashbox forms — shape and required-ness only (Sprint 8B).

These forms check that a field was filled and parses as a number or a date.
They deliberately do NOT check the minimum first payment, who may void a
receipt, or whether a till may close with a variance: those are business rules
with tests behind them in ``payment_service`` and ``closing_service``, and a
form that re-stated one would be a second copy free to drift from the first.

A refused submission therefore shows the SERVICE's message, which is the
message with the rule reference in it (BR-020, BR-027, BR-028).
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _


class PaymentForm(forms.Form):
    """
    استيفاء دفعة — BR-020's floor is checked by the service, not here.

    No date field: a receipt is dated the day it is issued, by the view, and
    the cashier neither types nor changes it.
    """

    #: The participant the enrolment cards were drawn for; the choices are
    #: rebuilt from it on POST so the posted code is checked against the very
    #: list the reader was offered.
    participant_number = forms.CharField(required=False, widget=forms.HiddenInput)
    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    amount = forms.DecimalField(
        label=_("المبلغ المقبوض"),
        max_digits=12,
        decimal_places=3,
        min_value=0,
        help_text=_(
            "أدخل كامل الرصيد أو دفعة جزئية حسب القواعد المالية. "
            "سيُوزّع النظام المبلغ على بنود الرسوم عند الحفظ."
        ),
    )
    payment_method = forms.ChoiceField(label=_("طريقة الدفع"), choices=[])
    #: Required or not by the setting (§5.2 · Q-03), decided in __init__.
    external_receipt_ref = forms.CharField(
        label=_("رقم سند الدائرة المالية"),
        max_length=32,
        required=False,
        help_text=_(
            "اختياري إذا وُجد سند خارجي من الدائرة المالية أو دفتر ورقي. "
            "رقم سند النظام يصدر تلقائيًا بعد الحفظ."
        ),
    )
    breakdown_text_ar = forms.CharField(
        label=_("البيان"),
        max_length=255,
        required=False,
        help_text=_("يُقترح من التسجيل المختار، ويمكن تعديله قبل الإصدار."),
    )

    def __init__(
        self,
        *args: Any,
        enrollment_choices: list[tuple[str, str]] | None = None,
        method_choices: list[tuple[str, str]] | None = None,
        voucher_required: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []
        cast(forms.ChoiceField, self.fields["payment_method"]).choices = method_choices or []
        if voucher_required:
            ref = self.fields["external_receipt_ref"]
            ref.required = True
            ref.help_text = _(
                "انقل الرقم من وصل الدائرة المالية الذي أحضره المشارك؛ لا يصدر السند بدونه (§5.2)."
            )


class VoidRequestForm(forms.Form):
    """A void names its reason — BR-025 keeps the original row either way."""

    reason_ar = forms.CharField(label=_("سبب الإلغاء"), widget=forms.Textarea({"rows": 2}))


class ExternalRefForm(forms.Form):
    """The finance department's voucher number, written once on an issued receipt (§5.2)."""

    external_receipt_ref = forms.CharField(
        label=_("رقم سند الدائرة المالية"),
        max_length=64,
        widget=forms.TextInput({"dir": "ltr", "autocomplete": "off", "inputmode": "numeric"}),
    )


class ClosingForm(forms.Form):
    """
    The vouchers in hand. The system total is computed, never typed: the
    reconciliation is what the centre recorded against what the finance
    department vouched for (§5.2).
    """

    cashier_id = forms.ChoiceField(label=_("الصندوق"), choices=[])
    closing_date = forms.DateField(
        label=_("تاريخ الإقفال"), widget=forms.DateInput({"type": "date"})
    )
    counted_total = forms.DecimalField(
        label=_("إجمالي الوصولات المستلمة"),
        max_digits=12,
        decimal_places=3,
        min_value=0,
        help_text=_("مجموع سندات الدائرة المالية التي بيد المركز لهذا اليوم."),
    )

    def __init__(
        self, *args: Any, cashier_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["cashier_id"]).choices = cashier_choices or []


class ReconcileForm(forms.Form):
    """
    BR-027 — a variance may be closed, but never silently.

    The resolution is optional HERE because whether it is required depends on
    the variance, which the service knows and this form does not.
    """

    variance_resolution_ar = forms.CharField(
        label=_("تسوية الفرق"), widget=forms.Textarea({"rows": 2}), required=False
    )


__all__ = ["ClosingForm", "PaymentForm", "ReconcileForm", "VoidRequestForm"]


class PaymentMethodForm(forms.Form):
    """
    One payment method — the reference row the till writes every receipt against.

    Two fields and a flag, and the code is among them only on creation: it is
    what ``CASH`` means throughout the seeds, the reports and the closing, so
    renaming it later would rewrite a reference that has already left this
    screen.
    """

    code = forms.CharField(
        label=_("الرمز"),
        max_length=32,
        help_text=_("بحروف لاتينية كبيرة، مثل CASH أو BANK — ولا يُعدَّل بعد الحفظ."),
    )
    name_ar = forms.CharField(label=_("الطريقة"), max_length=150)
    is_active = forms.BooleanField(label=_("نشطة"), required=False, initial=True)

    def clean_code(self) -> str:
        """Shape only — the collision itself is the database's to refuse."""
        return (self.cleaned_data["code"] or "").strip().upper()
