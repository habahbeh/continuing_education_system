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


# ---------------------------------------------------------------------------
# The ministry file (Sprint 8G — §3.3/14, §3.3/15)
# ---------------------------------------------------------------------------
class MoheSubmissionForm(forms.Form):
    """
    The ministry's own form, transcribed (BR-014).

    Every content field is optional here and that is deliberate rather than
    lax: ``create_submission`` says a draft may be saved incomplete, because
    the file is assembled over days from what different people supply. What is
    gated is SENDING — BR-016 refuses a file missing either required document,
    and the send button on the detail page is where that is answered.
    """

    cohort_code = forms.ChoiceField(label=_("الدفعة"), choices=[])
    training_axes_ar = forms.CharField(
        label=_("محاور التدريب"), required=False, widget=forms.Textarea({"rows": 3})
    )
    practical_aspects_ar = forms.CharField(
        label=_("الجوانب العملية"), required=False, widget=forms.Textarea({"rows": 3})
    )
    target_audience_ar = forms.CharField(
        label=_("الفئة المستهدفة"), required=False, widget=forms.Textarea({"rows": 2})
    )
    trainer_name = forms.CharField(label=_("المدرب"), max_length=150, required=False)
    trainer_qualifications = forms.CharField(
        label=_("مؤهلات المدرب"), required=False, widget=forms.Textarea({"rows": 2})
    )
    training_location = forms.CharField(label=_("مكان التدريب"), max_length=150, required=False)
    responsible_entity = forms.CharField(label=_("الجهة المسؤولة"), max_length=150, required=False)

    def __init__(
        self, *args: Any, cohort_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = cohort_choices or []

    def content(self) -> dict[str, Any]:
        """The submission's own fields — the cohort is resolved by the view."""
        data = dict(self.cleaned_data)
        data.pop("cohort_code", None)
        return data


class MoheResubmissionForm(MoheSubmissionForm):
    """
    The same seven fields, answering a rejection.

    The cohort is not chosen: ``resubmit`` takes it from the file being
    answered, because a resubmission that could name a different cohort would
    not be a resubmission.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("cohort_choices", None)
        super().__init__(*args, **kwargs)
        del self.fields["cohort_code"]

    def content(self) -> dict[str, Any]:
        return dict(self.cleaned_data)


class MoheAttachmentForm(forms.Form):
    """One of the two documents BR-016 will not let the file leave without."""

    purpose = forms.ChoiceField(label=_("نوع المستند"), choices=[])
    upload = forms.FileField(label=_("الملف"))

    def __init__(
        self, *args: Any, purpose_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["purpose"]).choices = purpose_choices or []


class MoheSendForm(forms.Form):
    """The date the file actually went to the ministry."""

    submitted_on = forms.DateField(
        label=_("تاريخ الإرسال"), widget=forms.DateInput({"type": "date"})
    )


class MoheDecisionForm(forms.Form):
    """
    What the ministry said (BR-014, C-16).

    Both outcomes share one form because both come off one letter, and which
    fields are required depends on which button was pressed — a question only
    the service can answer, so it answers it: an approval without a ministry
    number and a rejection without its reason are both refused there, by name.
    """

    decided_on = forms.DateField(label=_("تاريخ القرار"), widget=forms.DateInput({"type": "date"}))
    mohe_course_number = forms.CharField(
        label=_("الرقم الوزاري"),
        max_length=64,
        required=False,
        help_text=_("إلزامي عند الاعتماد — لا اعتماد بلا رقم (C-16)"),
    )
    registration_deadline = forms.DateField(
        label=_("مهلة التسجيل"),
        required=False,
        widget=forms.DateInput({"type": "date"}),
        help_text=_("BR-015 · BR-019 — بعدها يُمنع رفع أسماء جديدة"),
    )
    rejection_reason_ar = forms.CharField(
        label=_("سبب الرفض كما ورد"),
        required=False,
        widget=forms.Textarea({"rows": 3}),
        help_text=_("إلزامي عند الرفض · يُحفظ نصاً كما ورد من الوزارة (BR-014)"),
    )


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
    "MoheAttachmentForm",
    "MoheDecisionForm",
    "MoheResubmissionForm",
    "MoheSendForm",
    "MoheSubmissionForm",
]
