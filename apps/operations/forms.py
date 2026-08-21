"""
Operations forms — cohorts and enrolments (Sprint 8B).

Shape only. Whether a levelled programme's cohort must name its level, whether
the ministry has approved a cohort before anyone may enrol on it (BR-013), and
whether a voucher was recorded before approval (BR-018) are all decided in the
services, which have tests behind them.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _


class CohortForm(forms.Form):
    """فتح دفعة — opens PLANNED; the ministry decides when it may run."""

    code = forms.CharField(label=_("رمز الدفعة"), max_length=32)
    program_code = forms.ChoiceField(label=_("البرنامج"), choices=[])
    semester_code = forms.ChoiceField(label=_("الفصل"), choices=[])
    name_ar = forms.CharField(label=_("اسم الدفعة"), max_length=255)
    level = forms.IntegerField(label=_("المستوى"), required=False, min_value=1)
    starts_on = forms.DateField(label=_("تبدأ في"), widget=forms.DateInput({"type": "date"}))
    ends_on = forms.DateField(label=_("تنتهي في"), widget=forms.DateInput({"type": "date"}))
    capacity = forms.IntegerField(label=_("السعة"), min_value=1, initial=30)
    trainer_name = forms.CharField(label=_("المدرب"), max_length=150, required=False)
    location = forms.CharField(label=_("المكان"), max_length=150, required=False)
    agreement_number = forms.ChoiceField(label=_("الاتفاقية"), choices=[], required=False)

    def __init__(
        self,
        *args: Any,
        program_choices: list[tuple[str, str]] | None = None,
        semester_choices: list[tuple[str, str]] | None = None,
        agreement_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["program_code"]).choices = program_choices or []
        cast(forms.ChoiceField, self.fields["semester_code"]).choices = semester_choices or []
        cast(forms.ChoiceField, self.fields["agreement_number"]).choices = [("", "—")] + (
            agreement_choices or []
        )


class EnrollmentForm(forms.Form):
    """
    تسجيل مشارك على دفعة.

    Only ministry-approved cohorts are offered, because BR-013 refuses the
    rest anyway and a dropdown that led straight to a refusal would be a trap
    rather than a choice.
    """

    code = forms.CharField(label=_("رمز التسجيل"), max_length=32)
    participant_number = forms.CharField(label=_("الرقم الجامعي"), max_length=9)
    cohort_code = forms.ChoiceField(label=_("الدفعة"), choices=[])
    enrolled_on = forms.DateField(
        label=_("تاريخ التسجيل"), widget=forms.DateInput({"type": "date"})
    )

    def __init__(
        self, *args: Any, cohort_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = cohort_choices or []


__all__ = ["CohortForm", "EnrollmentForm"]
