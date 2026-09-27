"""Authentication forms (Q-12). Presentation only — no business logic."""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _


class LoginForm(forms.Form):
    username = forms.CharField(
        label=_("اسم المستخدم"),
        max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label=_("كلمة المرور"),
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class UnlockForm(forms.Form):
    reason = forms.CharField(
        label=_("سبب فكّ القفل"),
        max_length=200,
        widget=forms.TextInput(attrs={"required": True}),
    )


# ---------------------------------------------------------------------------
# الفصل الدراسي والفترة المالية — الخطوة الأولى التي لم تكن لها شاشة
# ---------------------------------------------------------------------------
class SemesterForm(forms.Form):
    """
    One academic semester — the frame BR-001 counts participant numbers in.

    ``academic_year`` is shape-checked here and nowhere else in the form's
    lifetime, because ``participant_numbering.academic_year_digits`` RAISES on a
    value that does not open with four digits — and it raises at the moment a
    permanent number is being minted, which is the worst possible moment to
    discover a typo made months earlier.

    ``is_active`` is a checkbox and not a hidden consequence: naming the current
    semester stands the previous one down, and the service does that in one
    transaction (``core_semester_single_active`` allows no other way).
    """

    YEAR_SHAPE = re.compile(r"^\d{4}(/\d{4})?$")

    code = forms.CharField(
        label=_("الرمز"),
        max_length=32,
        help_text=_("رمزٌ قصير يظهر في التقارير، مثل 2026-1 — ولا يُعدَّل بعد الحفظ."),
        widget=forms.TextInput(attrs={"dir": "ltr"}),
    )
    name_ar = forms.CharField(
        label=_("اسم الفصل"),
        max_length=150,
        help_text=_("كما يسمّيه المركز، مثل «الفصل الأول 2026/2027»."),
    )
    type_code = forms.ChoiceField(label=_("النوع"), choices=[])
    academic_year = forms.CharField(
        label=_("السنة الدراسية"),
        max_length=9,
        help_text=_("بصيغة 2026/2027 — وأوّل أربعة أرقام منها هي أوّل أرقام الرقم الجامعي (BR-001)."),
        widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "numeric"}),
    )
    starts_on = forms.DateField(label=_("يبدأ في"), widget=forms.DateInput(attrs={"type": "date"}))
    ends_on = forms.DateField(label=_("ينتهي في"), widget=forms.DateInput(attrs={"type": "date"}))
    is_active = forms.BooleanField(
        label=_("اجعله الفصل الحالي"),
        required=False,
        help_text=_("الفصل الحالي وحده هو ما تُبنى منه أرقام المشاركين — وواحدٌ فقط يكون حالياً."),
    )

    def __init__(self, *args: Any, type_choices: Any = (), editing: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.fields["type_code"].choices = list(type_choices)
        if editing:
            # The code is what the reports and the saved report links name a
            # semester by, so it is set once and read thereafter.
            del self.fields["code"]

    def clean_code(self) -> str:
        return (self.cleaned_data["code"] or "").strip()

    def clean_academic_year(self) -> str:
        raw = (self.cleaned_data["academic_year"] or "").strip()
        if not self.YEAR_SHAPE.match(raw):
            raise forms.ValidationError(
                _("اكتبها بأرقام لاتينية بصيغة 2026 أو 2026/2027 — الرقم الجامعي يُبنى منها.")
            )
        return raw

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        starts_on, ends_on = data.get("starts_on"), data.get("ends_on")
        if starts_on and ends_on and ends_on <= starts_on:
            # The database says the same thing (core_semester_ends_after_starts)
            # and says it as an IntegrityError, which reaches the reader as a
            # 500 rather than as a sentence beside the field.
            self.add_error("ends_on", _("نهاية الفصل يجب أن تكون بعد بدايته."))
        return data


class FinancialPeriodForm(forms.Form):
    """
    One financial period — the month D-23 lets a movement into or refuses.

    Two dates and nothing else. ``status`` is never a field: a period is opened
    OPEN and closed by the act of closing it, which records who closed it and
    when (``core_period_closed_requires_closer``). A form that could set the
    status would be a form that closes a month with nobody's name on it.
    """

    starts_on = forms.DateField(label=_("تبدأ في"), widget=forms.DateInput(attrs={"type": "date"}))
    ends_on = forms.DateField(label=_("تنتهي في"), widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        starts_on, ends_on = data.get("starts_on"), data.get("ends_on")
        if starts_on and ends_on and ends_on < starts_on:
            self.add_error("ends_on", _("نهاية الفترة لا تكون قبل بدايتها."))
        return data
