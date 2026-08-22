"""
Migration forms — shape only.

Whether a batch may be validated, whether a link is permitted and whether the
reviewer's reason is adequate are all decided in the services, which carry the
tests and the Arabic messages that name their rule.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _


class ImportForm(forms.Form):
    """Read a workbook already placed on the server."""

    code = forms.CharField(label=_("رمز الدفعة"), max_length=32)
    path = forms.CharField(label=_("مسار الملف"), max_length=255)
    note_ar = forms.CharField(label=_("ملاحظة"), max_length=255, required=False)


class LinkForm(forms.Form):
    """
    Confirm an identity.

    ``participant_number`` is typed rather than chosen from a ranked list: a
    suggested best match is a wrong answer wearing a confidence score, and
    eight legacy numbers in these workbooks carry two different names.
    """

    historical_id = forms.IntegerField(widget=forms.HiddenInput)
    participant_number = forms.CharField(label=_("الرقم الجامعي الحالي"), max_length=9)
    note_ar = forms.CharField(
        label=_("مسوّغ الربط"),
        max_length=255,
        help_text=_("لماذا هذا السجل وهذا المشارك شخص واحد — يُقرأ عند المراجعة لاحقاً"),
    )


class RowFilterForm(forms.Form):
    """The three filters a reviewer actually uses."""

    state = forms.ChoiceField(label=_("الحالة"), choices=[], required=False)
    finding = forms.ChoiceField(label=_("الملاحظة"), choices=[], required=False)
    sheet = forms.ChoiceField(label=_("الورقة"), choices=[], required=False)

    def __init__(
        self,
        *args: Any,
        state_choices: list[tuple[str, str]] | None = None,
        finding_choices: list[tuple[str, str]] | None = None,
        sheet_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        blank = [("", "—")]
        cast(forms.ChoiceField, self.fields["state"]).choices = blank + (state_choices or [])
        cast(forms.ChoiceField, self.fields["finding"]).choices = blank + (finding_choices or [])
        cast(forms.ChoiceField, self.fields["sheet"]).choices = blank + (sheet_choices or [])


__all__ = ["ImportForm", "LinkForm", "RowFilterForm"]
