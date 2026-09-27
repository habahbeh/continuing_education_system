"""
مسارات core — ما يُقدَّم من البنية التحتية لا من شاشات العمل.

``home`` و``health`` تبقيان مركَّبتين في ``config/urls.py`` بلا نطاق اسم، كما
كانتا؛ ما يُضاف هنا مسارٌ واحد له نطاقه، فلا يزاحم اسماً قائماً.
"""

from __future__ import annotations

from django.urls import path, register_converter

from apps.core import views

app_name = "core"


class _SlugSegment:
    """موضع الهوية: حروف كبيرة وشرطات سفلية فقط، فلا يلتقط المسار ما ليس له."""

    regex = "[A-Z_]{1,32}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return str(value)


register_converter(_SlugSegment, "brandslot")

urlpatterns = [
    path(
        "brand/<brandslot:slot>/<str:digest>.<str:extension>",
        views.brand_asset,
        name="brand-asset",
    ),
]
