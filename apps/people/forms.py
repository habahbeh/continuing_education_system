"""Authentication forms (Q-12). Presentation only — no business logic."""

from __future__ import annotations

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
