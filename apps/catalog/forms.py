"""
Catalogue forms — shape only.

Whether a diploma's subjects reconcile (BR-006), who may create or edit,
and what a code collides with are all the service's decisions.
"""

from __future__ import annotations

import re
from typing import Any, cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import ProgramType


class CourseCategoryForm(forms.Form):
    """
    A course category (BR-061's field). Two fields, and the code is one of them
    only on creation: it is what the transfer rule compares, and what every
    report prints, so renaming it later would rewrite history in place.
    """

    code = forms.CharField(
        label=_("الرمز"),
        max_length=32,
        help_text=_("بحروف لاتينية كبيرة، مثل CAT-IT — ولا يُعدَّل بعد الحفظ."),
    )
    name_ar = forms.CharField(label=_("المجال"), max_length=150)
    is_active = forms.BooleanField(label=_("نشط"), required=False, initial=True)

    def clean_code(self) -> str:
        """Shape only — the collision itself is the database's to refuse."""
        return (self.cleaned_data["code"] or "").strip().upper()


class KnowledgeFieldForm(forms.Form):
    """
    A ministry knowledge field. Same shape as a course category and a
    different meaning: this one is copied onto the ministry submission and
    governs nothing here, so its wording must match the ministry's own.
    """

    code = forms.CharField(
        label=_("الرمز"),
        max_length=32,
        help_text=_("بحروف لاتينية كبيرة، مثل KF-IT — ولا يُعدَّل بعد الحفظ."),
    )
    name_ar = forms.CharField(
        label=_("المجال المعرفي"),
        max_length=150,
        help_text=_("اكتبه كما تسمّيه الوزارة حرفاً بحرف — هو ذاهبٌ إليها في ملف الاعتماد."),
    )
    is_active = forms.BooleanField(label=_("نشط"), required=False, initial=True)

    def clean_code(self) -> str:
        return (self.cleaned_data["code"] or "").strip().upper()


class PriceListForm(forms.Form):
    """
    A dated price list (BR-008 · BR-012).

    ``status`` is NOT a field. A list is born a draft and becomes approved by
    recording the president's decision — an act with evidence behind it, not a
    dropdown. Offering the state here would let someone stamp a financial
    document with nothing on the record saying who stamped it.
    """

    code = forms.CharField(label=_("رمز القائمة"), max_length=32)
    name_ar = forms.CharField(label=_("اسم القائمة"), max_length=150)
    semester = forms.ChoiceField(label=_("الفصل"), choices=[])
    issued_on = forms.DateField(
        label=_("تاريخ الإصدار"), widget=forms.DateInput({"type": "date"})
    )
    effective_from = forms.DateField(
        label=_("تاريخ السريان"),
        widget=forms.DateInput({"type": "date"}),
        help_text=_("من هذا التاريخ تُسعَّر التسجيلات الجديدة بهذه القائمة (BR-012)."),
    )
    proposed_by_text = forms.CharField(
        label=_("جهة التنسيب"), max_length=150, required=False
    )

    def __init__(self, *args: Any, semester_choices: list[tuple[str, str]] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["semester"]).choices = semester_choices or []

    def clean_code(self) -> str:
        return (self.cleaned_data["code"] or "").strip().upper()


class PriceItemForm(forms.Form):
    """One programme's price on one list. The deposit is optional (Q-24)."""

    program = forms.ChoiceField(label=_("البرنامج"), choices=[])
    course_fee = forms.DecimalField(
        label=_("رسوم الدورة"), max_digits=12, decimal_places=3, min_value=0
    )
    deposit_amount = forms.DecimalField(
        label=_("مبلغ التأمين"),
        max_digits=12,
        decimal_places=3,
        min_value=0,
        required=False,
        help_text=_("يُترك فارغاً إن كان البرنامج بلا تأمين."),
    )
    deposit_policy = forms.ChoiceField(
        label=_("سياسة التأمين"),
        choices=[],
        required=False,
        help_text=_("إلزامية مع المبلغ: القيد C-26 يرفض مبلغاً بلا سياسة أو سياسةً بلا مبلغ."),
    )
    notes = forms.CharField(label=_("ملاحظات"), max_length=200, required=False)

    def __init__(
        self,
        *args: Any,
        program_choices: list[tuple[str, str]] | None = None,
        deposit_policy_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["program"]).choices = program_choices or []
        cast(forms.ChoiceField, self.fields["deposit_policy"]).choices = [
            ("", "— بلا تأمين —"),
            *(deposit_policy_choices or []),
        ]

    def clean(self) -> dict[str, Any]:
        """
        C-26 is a database constraint, and a constraint met at the database is a
        500 or a bare page-level refusal. Half a deposit is an ordinary typing
        mistake — the amount filled and the policy left alone — so it is caught
        here and reported on the field the reader must fix.

        And the deposit field had no policy input at ALL until now: C-26 refused
        every amount, so no programme in a fresh install could carry a deposit.
        """
        cleaned = super().clean() or {}
        amount, named = cleaned.get("deposit_amount"), cleaned.get("deposit_policy")
        if amount is not None and not named:
            self.add_error(
                "deposit_policy",
                _("مبلغ التأمين يحتاج سياسةً تحكمه — ماذا يُسترد ومتى يُصادر (C-26 · BR-096)."),
            )
        if named and amount is None:
            self.add_error(
                "deposit_amount",
                _("سياسة التأمين بلا مبلغ لا تفعل شيئاً — اكتب المبلغ أو ارفع السياسة (C-26)."),
            )
        return cleaned


class FeeRuleForm(forms.Form):
    """
    One registration-fee rule (BR-009). The fee is OPTIONAL on purpose.

    A blank fee is not zero: several documented courses (JCPA, PMP, drug
    registration) charge no registration fee at all, and a stored zero would
    read as «we charged nothing» rather than «no fee applies». So the field is
    left empty for that case and the reason is asked for beside it, because a
    rule that waives a fee without saying why is a rule nobody can audit.
    """

    program = forms.ChoiceField(
        label=_("نطاق القاعدة"),
        choices=[],
        required=False,
        help_text=_("القاعدة الخاصة ببرنامج تتقدّم على العامة (BR-009)."),
    )
    participant_category = forms.ChoiceField(label=_("فئة المشارك"), choices=[])
    fee = forms.DecimalField(
        label=_("رسم التسجيل"),
        max_digits=12,
        decimal_places=3,
        min_value=0,
        required=False,
        help_text=_("يُترك فارغاً إذا كانت الفئة بلا رسوم تسجيل — وهو غير الصفر."),
    )
    exception_note_ar = forms.CharField(
        label=_("سبب الاستثناء"), max_length=255, required=False
    )

    def __init__(
        self,
        *args: Any,
        program_choices: list[tuple[str, str]] | None = None,
        category_choices: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["program"]).choices = program_choices or []
        cast(forms.ChoiceField, self.fields["participant_category"]).choices = [
            ("", "—"),
            *(category_choices or []),
        ]

    def clean(self) -> dict[str, Any]:
        """
        A waived fee says why. The service stores either shape; what it cannot
        supply is the reason, and «بلا رسوم» with no reason beside it is the
        exact row that a year later nobody can explain.
        """
        cleaned = super().clean() or {}
        if cleaned.get("fee") is None and not (cleaned.get("exception_note_ar") or "").strip():
            self.add_error(
                "exception_note_ar",
                _("قاعدة بلا رسوم تحتاج سبباً مكتوباً — «بلا رسوم» وحدها لا تُدقَّق."),
            )
        return cleaned


class DepositPolicyForm(forms.Form):
    """
    A refundable-deposit policy (BR-096 · BR-097 · Q-30).

    ``refund_trigger`` is free text on purpose and stays free text here: Q-30 is
    open, the client owns the answer, and their instruction of 2026-08-15 was to
    keep these values configurable. A dropdown in this form would put the closed
    list back that the model deliberately refused to carry.

    ``forfeit_on`` was written as a CLOSED list of enrolment statuses, on the
    reasoning that a status typed by hand never matches anything. That reasoning
    was wrong about this field, and the proof was already in the database: the
    client's documented policy forfeits on ``CONFIRMED`` — the moment the
    participant confirmed, not any stored state, and not one of the twelve. A
    closed list could not express the one policy the client had specified.

    So it is both: twelve checkboxes for the typo-free path, and one free field
    beside them for anything the vocabulary has no word for. They are merged in
    ``clean``, and the free field is offered what other policies already say so
    that ``CONFIRMED`` and ``ON_CONFIRM`` do not end up living side by side.
    """

    code = forms.CharField(
        label=_("الرمز"),
        max_length=32,
        help_text=_("بحروف لاتينية كبيرة، مثل DEP-ENG — ولا يُعدَّل بعد الحفظ."),
    )
    name_ar = forms.CharField(label=_("اسم السياسة"), max_length=150)
    is_required = forms.BooleanField(label=_("إلزامي على المشارك"), required=False, initial=True)
    refund_trigger = forms.CharField(
        label=_("مُحفّز الاسترداد"),
        max_length=32,
        help_text=_("مثل ON_CENTRE_CANCELLATION — نصّ حرّ بقرار العميل (Q-30)."),
    )
    forfeit_on = forms.MultipleChoiceField(
        label=_("حالات المصادرة"),
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("الحالات التي يُصادر فيها التأمين — تُترك فارغةً إن كان يُسترد دائماً."),
    )
    forfeit_other = forms.CharField(
        label=_("حالات أخرى"),
        required=False,
        help_text=_("لحالةٍ ليست من القائمة، مثل CONFIRMED — بفاصلة بين كل حالتين (Q-30)."),
        # `list` يقرن الحقل بـ`<datalist>` في القالب: اقتراحاتٌ تُعرض ولا تُلزم —
        # وهو ما يمنع حفظ القيمة نفسها بهجاءين بلا قيدٍ يمنعه Q-30.
        widget=forms.TextInput(attrs={"dir": "ltr", "list": "forfeit-seen"}),
    )
    is_taxable = forms.BooleanField(label=_("خاضع للضريبة"), required=False)
    allows_partial_deduction = forms.BooleanField(
        label=_("يسمح بحسم جزئي"), required=False, initial=True
    )
    claim_deadline_days = forms.IntegerField(
        label=_("مهلة المطالبة بالأيام"),
        min_value=1,
        required=False,
        help_text=_("تُترك فارغةً إن لم تكن هناك مهلة سقوط."),
    )
    notes_ar = forms.CharField(
        label=_("ملاحظات"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    is_active = forms.BooleanField(label=_("نشطة"), required=False, initial=True)

    def __init__(
        self,
        *args: Any,
        forfeit_choices: list[tuple[str, str]] | None = None,
        editing: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.MultipleChoiceField, self.fields["forfeit_on"]).choices = forfeit_choices or []
        if editing:
            # The code is what every price item points at; renaming it later
            # would rewrite a reference that has already left this screen.
            del self.fields["code"]

    def clean_code(self) -> str:
        return (self.cleaned_data["code"] or "").strip().upper()

    def clean_refund_trigger(self) -> str:
        return (self.cleaned_data["refund_trigger"] or "").strip().upper()

    #: A forfeit value is a code, not a sentence: latin capitals, digits and the
    #: underscore. Checked so «حالة أخرى» cannot become a free note that no
    #: comparison will ever match, while Q-30 keeps the VOCABULARY open.
    OTHER_SHAPE = re.compile(r"^[A-Z0-9_]{2,32}$")

    def clean_forfeit_other(self) -> list[str]:
        """The extra values, split, upper-cased and shape-checked — never merged here."""
        raw = (self.cleaned_data.get("forfeit_other") or "").replace("،", ",")
        values: list[str] = []
        for piece in raw.split(","):
            text = piece.strip().upper().replace(" ", "_")
            if not text:
                continue
            if not self.OTHER_SHAPE.match(text):
                raise forms.ValidationError(
                    _("«%(value)s» ليست رمز حالة — بحروف لاتينية كبيرة وأرقام وشرطة سفلية.")
                    % {"value": piece.strip()}
                )
            if text not in values:
                values.append(text)
        return values

    def clean(self) -> dict[str, Any]:
        """
        The checkboxes and the free field are ONE list once they are stored.

        Merged here rather than in the view, because which values a policy
        forfeits on is the shape of this form's answer — and a caller that had
        to remember to merge them is a caller that one day forgets.
        """
        cleaned = super().clean() or {}
        chosen = list(cleaned.get("forfeit_on") or [])
        for extra in cleaned.get("forfeit_other") or []:
            if extra not in chosen:
                chosen.append(extra)
        cleaned["forfeit_on"] = chosen
        return cleaned


class PriceListApprovalForm(forms.Form):
    """
    The president's decision, recorded (D-31).

    Both fields are required by the service, and required here too so the
    refusal is a field error rather than a page-level message: they are the
    only evidence that an approval outside this system ever happened.
    """

    approved_by_text = forms.CharField(label=_("جهة الاعتماد"), max_length=150)
    decision_reference = forms.CharField(label=_("مرجع القرار"), max_length=150)


class ProgramForm(forms.Form):
    """One programme (§2.2). The type is fixed by the screen it is drawn on."""

    code = forms.CharField(label=_("الرمز"), max_length=32)
    name_ar = forms.CharField(label=_("الاسم"), max_length=150)
    name_en = forms.CharField(label=_("الاسم بالإنجليزية"), max_length=150, required=False)
    knowledge_field = forms.ChoiceField(label=_("المجال المعرفي"), choices=[], required=False)
    course_category = forms.ChoiceField(label=_("مجال الدورة"), choices=[], required=False)
    specialization = forms.CharField(label=_("التخصص"), max_length=150, required=False)
    training_hours = forms.IntegerField(label=_("الساعات التدريبية"), min_value=0, initial=0)
    is_leveled = forms.BooleanField(label=_("ذات مستويات"), required=False)
    levels_count = forms.IntegerField(label=_("عدد المستويات"), min_value=1, required=False)
    consumables_per_student = forms.DecimalField(
        label=_("المستهلكات لكل مشارك"), max_digits=12, decimal_places=3, min_value=0, initial=0
    )
    minimum_first_payment_override = forms.DecimalField(
        label=_("حد أدنى خاص للدفعة الأولى"),
        max_digits=12,
        decimal_places=3,
        min_value=0,
        required=False,
        help_text=_("يُترك فارغاً ليسري الإعداد العام (Q-15)."),
    )

    def __init__(
        self,
        *args: Any,
        program_type: str,
        field_choices: list[tuple[str, str]] | None = None,
        category_choices: list[tuple[str, str]] | None = None,
        editing: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.program_type = program_type
        cast(forms.ChoiceField, self.fields["knowledge_field"]).choices = [("", "—")] + (
            field_choices or []
        )
        cast(forms.ChoiceField, self.fields["course_category"]).choices = [("", "—")] + (
            category_choices or []
        )
        if editing:
            # The code is the programme's identity everywhere (cohorts, price
            # lists, archives); it is shown, never re-typed.
            self.fields["code"].disabled = True

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.program_type == ProgramType.SHORT_COURSE and not cleaned.get("course_category"):
            self.add_error(
                "course_category", _("مجال الدورة إلزامي للدورات القصيرة — يحدّ النقل المسموح (BR-061).")
            )
        if cleaned.get("is_leveled") and not cleaned.get("levels_count"):
            self.add_error("levels_count", _("البرنامج ذو مستويات يسمّي عددها."))
        if not cleaned.get("is_leveled"):
            cleaned["levels_count"] = None
        return cleaned


class SubjectForm(forms.Form):
    """A diploma subject (§2.3) — a price of zero is a price (BR-007)."""

    name_ar = forms.CharField(label=_("اسم المادة"), max_length=150)
    training_hours = forms.IntegerField(label=_("الساعات"), min_value=0, initial=0)
    price = forms.DecimalField(label=_("السعر"), max_digits=12, decimal_places=3, min_value=0, initial=0)


__all__ = [
    "CourseCategoryForm",
    "PriceItemForm",
    "PriceListApprovalForm",
    "PriceListForm",
    "KnowledgeFieldForm","ProgramForm", "SubjectForm"]
