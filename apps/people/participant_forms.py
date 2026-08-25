"""
Participant form (SPEC §6 — the enrolment application fields).

A plain Form rather than a ModelForm, because the qualification and city
choices come from effective-dated reference lists (Q-31) and the participant
number is never user input — it is allocated by the service.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.people.models import Gender, IdDocumentType, ParticipantCategory
from apps.people.services import reference_data

#: Typed loosely on purpose: choice labels are lazily translated strings,
#: which are not ``str`` until they are rendered.
BLANK: list[tuple[str, Any]] = [("", "—")]


class ParticipantForm(forms.Form):
    category = forms.ChoiceField(
        choices=ParticipantCategory.choices,
        label=_("الفئة"),
        help_text=_("تحدّد سعر البرنامج عند التسجيل، ورمز النوع داخل الرقم الجامعي."),
    )
    name_ar = forms.CharField(max_length=150, label=_("الاسم رباعياً بالعربية"))
    name_en = forms.CharField(max_length=150, required=False, label=_("الاسم بالإنجليزية"))

    id_document_type = forms.ChoiceField(
        choices=IdDocumentType.choices, label=_("نوع وثيقة الهوية")
    )
    id_document_number = forms.CharField(
        max_length=32,
        label=_("رقم وثيقة الهوية"),
        help_text=_("إن تكرّر الرقم عرض النظام السجل المطابق وسمح بالمتابعة بسبب موثّق (BR-005)."),
    )

    nationality = forms.CharField(max_length=60, required=False, label=_("الجنسية"))
    gender = forms.ChoiceField(
        choices=BLANK + list(Gender.choices), required=False, label=_("الجنس")
    )
    date_of_birth = forms.DateField(
        required=False,
        label=_("تاريخ الميلاد"),
        widget=forms.DateInput({"type": "date"}),
    )

    qualification = forms.ChoiceField(required=False, choices=[], label=_("المؤهل العلمي"))
    city = forms.ChoiceField(required=False, choices=[], label=_("المدينة"))

    phone = forms.CharField(max_length=32, required=False, label=_("الهاتف"))
    po_box = forms.CharField(max_length=32, required=False, label=_("صندوق البريد"))
    email = forms.EmailField(required=False, label=_("البريد الإلكتروني"))
    employer = forms.CharField(max_length=150, required=False, label=_("جهة العمل"))

    registered_on = forms.DateField(
        label=_("تاريخ التسجيل"),
        widget=forms.DateInput({"type": "date"}),
        help_text=_("تاريخ تقديم طلب الالتحاق."),
    )

    no_refund_pledge_accepted = forms.BooleanField(
        required=False,
        label=_("أقرّ بالتعهّد بعدم استرداد الرسوم"),
        help_text=_("إقرار إلزامي: لا يُحفظ الطلب بدونه."),
    )

    is_exempt = forms.BooleanField(
        required=False,
        label=_("معفى من الرسوم"),
        help_text=_("يُفعَّل بموافقة رئيس الجامعة وحدها؛ اتركه فارغاً في الحالة العادية."),
    )
    exemption_approval_ref = forms.CharField(
        max_length=64,
        required=False,
        label=_("رقم موافقة رئيس الجامعة"),
        help_text=_("إلزامي متى فُعِّل الإعفاء."),
    )
    exemption_approval_date = forms.DateField(
        required=False,
        label=_("تاريخ الموافقة"),
        widget=forms.DateInput({"type": "date"}),
    )

    #: BR-005 — filled in only when the user is confirming a known duplicate.
    duplicate_override_reason = forms.CharField(
        max_length=200,
        required=False,
        label=_("سبب المتابعة رغم تكرار وثيقة الهوية"),
        help_text=_("يُملأ عند تأكيد المتابعة رغم وجود سجل بنفس الوثيقة، ويبقى فارغاً عداها."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Q-31 — loaded per request, so answering the question takes effect
        # without a deploy.
        qualification = cast(forms.ChoiceField, self.fields["qualification"])
        city = cast(forms.ChoiceField, self.fields["city"])
        qualification.choices = BLANK + list(reference_data.qualifications())
        city.choices = BLANK + list(reference_data.cities())

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        # BR-003 and BR-004 are enforced by the service and by the database.
        # Repeating them here buys a message next to the field rather than at
        # the top of the page — it does not replace either.
        if not cleaned.get("no_refund_pledge_accepted"):
            self.add_error("no_refund_pledge_accepted", _("يجب الإقرار بالتعهّد قبل الحفظ"))
        if cleaned.get("is_exempt") and not (cleaned.get("exemption_approval_ref") or "").strip():
            self.add_error("exemption_approval_ref", _("الإعفاء يتطلب رقم موافقة رئيس الجامعة"))
        return cleaned

    def to_service_data(self) -> dict[str, Any]:
        """The payload participant_service expects, minus form-only fields."""
        data = dict(self.cleaned_data)
        data.pop("duplicate_override_reason", None)
        return data


class ParticipantEditForm(ParticipantForm):
    """Editing does not re-ask for the pledge already on record (BR-003)."""

    def clean(self) -> dict[str, Any]:
        cleaned = forms.Form.clean(self) or {}
        if cleaned.get("is_exempt") and not (cleaned.get("exemption_approval_ref") or "").strip():
            self.add_error("exemption_approval_ref", _("الإعفاء يتطلب رقم موافقة رئيس الجامعة"))
        return cleaned
