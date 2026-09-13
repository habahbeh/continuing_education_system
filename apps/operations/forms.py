"""
Operations forms — cohorts and enrolments (Sprint 8B).

Shape only. Whether a levelled programme's cohort must name its level, whether
the ministry has approved a cohort before anyone may enrol on it (BR-013), and
whether a voucher was recorded before approval (BR-018) are all decided in the
services, which have tests behind them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from django import forms
from django.utils import timezone
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

    The participant is chosen the same way, and for a plainer reason: the
    field asked for a nine-digit number typed from memory, when the operator
    has just entered the application and knows the person by name. The value
    submitted is still ``participant_number`` — nothing downstream changes —
    and the number is read back in the label beside the name, so anyone
    working from a paper form still recognises the row.

    No code is asked for. ``EN-AHMAD-001`` was a QA example, not a register
    entry; the code is minted by the service (``next_enrollment_code``) so the
    sequence is the system's to keep rather than the operator's to remember.
    """

    participant_number = forms.ChoiceField(
        label=_("المشارك"),
        choices=[],
        help_text=_("اختر المشارك الذي أُدخل طلب التحاقه سابقاً."),
    )
    cohort_code = forms.ChoiceField(label=_("الدفعة"), choices=[])
    enrolled_on = forms.DateField(
        label=_("تاريخ التسجيل"), widget=forms.DateInput({"type": "date"})
    )

    def __init__(
        self,
        *args: Any,
        cohort_choices: list[tuple[str, str]] | None = None,
        participant_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["cohort_code"]).choices = cohort_choices or []
        cast(forms.ChoiceField, self.fields["participant_number"]).choices = (
            participant_choices or []
        )


class ClearanceOpenForm(forms.Form):
    """
    §6.4 — open a clearance on a finished enrolment.

    Only finished enrolments are offered (completed, withdrawn, dismissed) and
    only those with no live clearance, because the service refuses both and a
    dropdown leading straight to a refusal is a trap rather than a choice.

    No ``case_type`` field: the case follows from the enrolment's final status
    (``clearance_service.clearance_case_for_enrollment``). A choice here could
    only agree with that status or contradict it.
    """

    enrollment_code = forms.ChoiceField(
        label=_("التسجيل"),
        choices=[],
        help_text=_("سبب البراءة يُحدَّد تلقائياً من حالة التسجيل."),
    )
    # No ``code`` field: the number is the system's to mint (``CLR-YYYY-NNNNNN``),
    # not the operator's to invent — the same reason the enrolment form has none.
    opened_on = forms.DateField(
        label=_("تفتح في"),
        widget=forms.DateInput({"type": "date"}),
        initial=timezone.localdate,
        help_text=_("اليوم افتراضياً؛ يمكن إدخال تاريخ سابق لبراءة فُتحت ورقياً، لا تاريخ لاحق."),
    )

    def __init__(
        self, *args: Any, enrollment_choices: list[tuple[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["enrollment_code"]).choices = enrollment_choices or []

    def clean_opened_on(self) -> date:
        opened_on = cast(date, self.cleaned_data["opened_on"])
        if opened_on > timezone.localdate():
            raise forms.ValidationError(_("لا تُفتح براءة ذمة بتاريخ مستقبلي."))
        return opened_on


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


# ---------------------------------------------------------------------------
# Transfers (Sprint 8H — §3.2/6, §3.2/7)
# ---------------------------------------------------------------------------
class TransferRequestForm(forms.Form):
    """
    WORKFLOWS §5.2 X1 — the request, and the evidence it freezes.

    ``lectures_attended`` is NOT collected here. It lives on the enrolment,
    is documented under BR-095 with a source and a verifier, and
    ``validate_transfer`` copies it at the moment of the request. Offering it
    as an input would let the person asking for the transfer supply the number
    the rule is about to judge them on.
    """

    from_enrollment_code = forms.ChoiceField(label=_("التسجيل المنقول منه"), choices=[])
    to_cohort_code = forms.ChoiceField(label=_("الدفعة الهدف"), choices=[])
    reason = forms.ChoiceField(label=_("السبب"), choices=[])
    requested_on = forms.DateField(label=_("تاريخ الطلب"), widget=forms.DateInput({"type": "date"}))
    code = forms.CharField(label=_("رمز النقل"), max_length=32)
    grant_category_waiver = forms.BooleanField(
        label=_("منح استثناء قيد المجال"),
        required=False,
        help_text=_("BR-065 · C-12 — لا يكون إلا بإلغاء المركز للدورة، وبموافقة مدير المركز"),
    )
    category_waiver_reason_ar = forms.CharField(
        label=_("سبب الاستثناء"), required=False, widget=forms.Textarea({"rows": 2})
    )

    def __init__(
        self,
        *args: Any,
        enrollment_choices: list[tuple[str, str]] | None = None,
        cohort_choices: list[tuple[str, str]] | None = None,
        reason_choices: list[tuple[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["from_enrollment_code"]).choices = (
            enrollment_choices or []
        )
        cast(forms.ChoiceField, self.fields["to_cohort_code"]).choices = cohort_choices or []
        cast(forms.ChoiceField, self.fields["reason"]).choices = reason_choices or []

    def clean(self) -> dict[str, Any]:
        """
        The one thing the form can answer without the database.

        Every other rule — the category, the lecture deadline, the ministry
        approval on the target — is ``validate_transfer``'s, and asking it
        twice would mean maintaining two answers.
        """
        cleaned = super().clean() or {}
        if (
            cleaned.get("grant_category_waiver")
            and not (cleaned.get("category_waiver_reason_ar") or "").strip()
        ):
            self.add_error(
                "category_waiver_reason_ar",
                _("الاستثناء بلا سبب مسجَّل ليس استثناءً — سجّل السبب (C-12)."),
            )
        return cleaned


class TransferRejectForm(forms.Form):
    """WORKFLOWS §5.2 X5 — a rejection always carries its reason."""

    reason_ar = forms.CharField(
        label=_("سبب الرفض"), widget=forms.Textarea({"rows": 2}), max_length=1000
    )


class TransferExecuteForm(forms.Form):
    """
    §5.4 — the settlement, and the code the new enrolment will carry.

    The new enrolment's code is asked for rather than generated: enrolment
    codes are the centre's own numbering everywhere else in this system, and
    a transfer is not the place to invent a second convention.
    """

    executed_on = forms.DateField(
        label=_("تاريخ التنفيذ"), widget=forms.DateInput({"type": "date"})
    )
    new_code = forms.CharField(label=_("رمز التسجيل الجديد"), max_length=32)


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
    "TransferExecuteForm",
    "TransferRejectForm",
    "TransferRequestForm",
]
