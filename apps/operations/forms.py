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


class ClearanceOpenForm(forms.Form):
    """
    §6.4 — open a clearance on a finished enrolment.

    Only finished enrolments are offered (completed, withdrawn, dismissed) and
    only those with no live clearance, because the service refuses both and a
    dropdown leading straight to a refusal is a trap rather than a choice.
    """

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    case_type = forms.ChoiceField(
        label=_("الحالة"),
        choices=[
            ("GRADUATION", _("تخرج")),
            ("WITHDRAWAL", _("انسحاب")),
            ("DISMISSAL", _("فصل")),
        ],
    )
    code = forms.CharField(label=_("رمز البراءة"), max_length=32)
    opened_on = forms.DateField(label=_("تفتح في"), widget=forms.DateInput({"type": "date"}))

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []


class CustodyForm(forms.Form):
    """
    Step 1 — the items recovered from the participant.

    Free text for now. §6.4 names two by example — «هوية المركز + بطاقة
    المواصلات» — and a STANDARD checklist driven by a setting is Sprint 8C's,
    together with the rest of form ``CS Fm 7.18 Rev A``'s exact output. Until
    then the operator types what was returned, which is what the paper form
    does today.
    """

    items = forms.CharField(
        label=_("العُهد المُسترجَعة — عنصر في كل سطر"),
        widget=forms.Textarea({"rows": 3}),
        help_text=_("مثال: هوية المركز · بطاقة المواصلات"),
    )

    def clean_items(self) -> list[dict[str, Any]]:
        """One line per item, all marked returned — an unreturned item is not typed."""
        lines = [line.strip() for line in self.cleaned_data["items"].splitlines() if line.strip()]
        if not lines:
            raise forms.ValidationError(_("يجب ذكر العُهد المُسترجَعة."))
        return [{"name_ar": line, "returned": True} for line in lines]


class HandoverForm(forms.Form):
    """
    §6.4 step 3 — «توقيع المشارك ومدير المركز».

    The manager's signature is their certification; this is the participant's,
    captured as the name of whoever actually took the certificate. The service
    refuses a blank one, so this field is the readable prompt rather than the
    rule.
    """

    participant_ack_name = forms.CharField(
        label=_("اسم مستلم الشهادة"),
        max_length=150,
        help_text=_("يُسجَّل اسم من استلم الشهادة فعلاً — إثبات التسليم على النموذج"),
    )


class ClearanceCancelForm(forms.Form):
    """A cancelled clearance names why — WORKFLOWS §6.3 C6."""

    reason_ar = forms.CharField(label=_("سبب الإلغاء"), widget=forms.Textarea({"rows": 2}))


class DepositSettlementForm(forms.Form):
    """
    Step 2 — settle the deposit before the financial step may close (Q-01).

    Returning with a partial deduction needs the deduction's reason; the
    service decides whether the policy even allows one (BR-097).
    """

    returned_on = forms.DateField(
        label=_("تاريخ التسوية"), widget=forms.DateInput({"type": "date"})
    )
    deduction_amount = forms.DecimalField(
        label=_("المحسوم"), max_digits=12, decimal_places=3, required=False, min_value=0
    )
    deduction_reason_ar = forms.CharField(label=_("سبب الحسم"), max_length=255, required=False)


class CreditReturnAtClearanceForm(forms.Form):
    """BR-071 — hand back a credit balance so step 2 can close."""

    code = forms.CharField(label=_("رمز الحركة"), max_length=32)
    returned_on = forms.DateField(label=_("تاريخ الردّ"), widget=forms.DateInput({"type": "date"}))


class CertificateIssueForm(forms.Form):
    """
    §7 — issue a certificate.

    The grade is a CHOICE from ``certificate_grades`` rather than free text:
    BR-078 says the system does not compute a grade, which is a different
    statement from letting anyone type anything on a sealed document.
    """

    enrollment_code = forms.ChoiceField(label=_("التسجيل"), choices=[])
    grade = forms.ChoiceField(label=_("التقدير"), choices=[])
    issued_on = forms.DateField(label=_("تاريخ الإصدار"), widget=forms.DateInput({"type": "date"}))
    duration_text = forms.CharField(label=_("مدة الدورة"), max_length=150, required=False)
    training_hours = forms.IntegerField(label=_("عدد الساعات"), required=False, min_value=0)

    def __init__(
        self,
        *args: Any,
        enrollment_choices: list[tuple[str, str]] | None = None,
        grade_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []
        cast(forms.ChoiceField, self.fields["grade"]).choices = grade_choices or []


class CertificateDateForm(forms.Form):
    """A date for a delivery or a replacement issue."""

    on_date = forms.DateField(label=_("التاريخ"), widget=forms.DateInput({"type": "date"}))


__all__ = [
    "CertificateDateForm",
    "CertificateIssueForm",
    "ClearanceCancelForm",
    "ClearanceOpenForm",
    "CohortForm",
    "CreditReturnAtClearanceForm",
    "CustodyForm",
    "DepositSettlementForm",
    "EnrollmentForm",
    "HandoverForm",
]
