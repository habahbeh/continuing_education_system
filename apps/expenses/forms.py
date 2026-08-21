"""
Expense forms — shape only (Sprint 8C-2).

Whether the category exists, whether the period is closed, and whether the
approver is the recorder are all decided in the service, which has tests
behind it and messages carrying their own references.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _


class ExpenseForm(forms.Form):
    """§9.7 — one outgoing payment by the centre."""

    code = forms.CharField(label=_("رمز القيد"), max_length=32)
    category = forms.ChoiceField(label=_("التصنيف"), choices=[])
    amount = forms.DecimalField(label=_("المبلغ"), max_digits=12, decimal_places=3, min_value=0)
    incurred_on = forms.DateField(label=_("تاريخ الصرف"), widget=forms.DateInput({"type": "date"}))
    description_ar = forms.CharField(label=_("البيان"), max_length=255)
    cohort_code = forms.ChoiceField(label=_("الدفعة (اختياري)"), choices=[], required=False)
    reference = forms.CharField(label=_("رقم الفاتورة"), max_length=64, required=False)

    def __init__(
        self,
        *args: Any,
        category_choices: list[tuple[str, str]] | None = None,
        cohort_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["category"]).choices = category_choices or []
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = [("", "—")] + (
            cohort_choices or []
        )


class ExpenseDecisionForm(forms.Form):
    """
    The approver's note.

    Optional on approval and required on rejection — which the service decides,
    because only it knows which button was pressed.
    """

    note_ar = forms.CharField(label=_("ملاحظة القرار"), max_length=255, required=False)


__all__ = ["ExpenseDecisionForm", "ExpenseForm"]
