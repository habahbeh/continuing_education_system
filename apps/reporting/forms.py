"""Report filters — shape only (Sprint 8C-2)."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class PeriodForm(forms.Form):
    """The from/to pair most reports take."""

    date_from = forms.DateField(label=_("من تاريخ"), widget=forms.DateInput({"type": "date"}))
    date_to = forms.DateField(label=_("إلى تاريخ"), widget=forms.DateInput({"type": "date"}))


class DayForm(forms.Form):
    on_date = forms.DateField(label=_("التاريخ"), widget=forms.DateInput({"type": "date"}))


class EnrollmentPickForm(forms.Form):
    enrollment_code = forms.CharField(label=_("رمز التسجيل"), max_length=32)


__all__ = ["DayForm", "EnrollmentPickForm", "PeriodForm"]
