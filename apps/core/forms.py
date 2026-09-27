"""
نماذج البنية التحتية — الشكل وحده (Sprint 8L).

ما يُتحقَّق منه هنا شكلُ المُدخَل لا صلاحيةُ الملف: النوع والحجم وسلامة SVG
والأبعاد كلها في ``branding_service``، حيث تُختبر وحيث يصل إليها كل نداء لا
هذا النموذج وحده.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.models.brand_asset import BrandAssetSlot


class BrandAssetForm(forms.Form):
    """رفع ملف هوية واحد لخانة واحدة."""

    slot = forms.ChoiceField(label=_("الموضع"), choices=BrandAssetSlot.choices)
    file = forms.FileField(
        label=_("الملف"),
        help_text=_("PNG · JPEG · WEBP · SVG — بحدّ ٢ ميغابايت"),
    )
    alt_text_ar = forms.CharField(
        label=_("النص البديل"),
        max_length=200,
        required=False,
        help_text=_("ما يقرؤه قارئ الشاشة مكان الصورة"),
    )
    note = forms.CharField(
        label=_("مبرّر التغيير"),
        max_length=255,
        widget=forms.Textarea({"rows": 2}),
        help_text=_("يبقى في سجل التدقيق: مَن غيّر الهوية ومتى ولماذا"),
    )


__all__ = ["BrandAssetForm"]
