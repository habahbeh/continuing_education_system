"""
Settlement forms — shape only (Sprint 8B-2).

None of these re-states a rule. Whether a period may be claimed, whether a
claim may still be edited after approval (BR-051), whether a settlement may be
signed with a balance on it (BR-053) — all decided in the services, which have
tests behind them and messages carrying their own references.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _


class ClaimBuildForm(forms.Form):
    """
    Draft a claim over one cohort and one period.

    The cohort list is limited to cohorts that CARRY an agreement — a cohort
    with no partner has no claim to build, and offering one would lead only to
    a refusal the user could have been spared.
    """

    cohort_code = forms.ChoiceField(label=_("الدفعة"), choices=[])
    period_from = forms.DateField(label=_("من"), widget=forms.DateInput({"type": "date"}))
    period_to = forms.DateField(label=_("إلى"), widget=forms.DateInput({"type": "date"}))
    trigger_type = forms.ChoiceField(
        label=_("مُحفّز المطالبة"),
        choices=[
            ("END_OF_COURSE", _("نهاية الدورة")),
            ("END_OF_SUBJECT", _("نهاية المادة")),
            ("PERIODIC", _("دورية")),
        ],
    )
    trigger_reference_ar = forms.CharField(label=_("المرجع"), max_length=255, required=False)

    def __init__(
        self, *args: Any, cohort_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = cohort_choices or []


class SettlementOpenForm(forms.Form):
    """
    Open a cycle.

    ``cohort_code`` is optional HERE because whether it is required depends on
    the agreement's cycle — «نهاية الدورة» needs the cohort it closes and
    «كل أربعة أشهر» does not. The service knows which; this form does not.
    """

    agreement_number = forms.ChoiceField(label=_("الاتفاقية"), choices=[])
    code = forms.CharField(label=_("رمز المخالصة"), max_length=32)
    opens_on = forms.DateField(label=_("تبدأ في"), widget=forms.DateInput({"type": "date"}))
    cohort_code = forms.ChoiceField(
        label=_("الدفعة (لدورة نهاية الدورة)"), choices=[], required=False
    )

    def __init__(
        self,
        *args: Any,
        agreement_choices: list[tuple[str, str]] | None = None,
        cohort_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["agreement_number"]).choices = agreement_choices or []
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = [("", "—")] + (
            cohort_choices or []
        )


class SettlementPaymentForm(forms.Form):
    """Money paid to the partner against an open cycle."""

    amount = forms.DecimalField(
        label=_("المبلغ المدفوع"), max_digits=12, decimal_places=3, min_value=0
    )


class SettlementSignForm(forms.Form):
    """BR-053 — a settlement carries the date the parties signed it."""

    signed_on = forms.DateField(label=_("تاريخ التوقيع"), widget=forms.DateInput({"type": "date"}))


__all__ = [
    "ClaimBuildForm",
    "SettlementOpenForm",
    "SettlementPaymentForm",
    "SettlementSignForm",
]
