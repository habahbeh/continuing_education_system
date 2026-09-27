"""
ما يُحقن في كل قالب من البنية التحتية.

``branding`` معالج سياق لا مفتاحاً في كل view، للسبب نفسه الذي جعل القائمة
الجانبية معالج سياق: شعار المؤسسة يظهر في كل صفحة بما فيها صفحة الدخول
وصفحات الخطأ، وviewٌ واحد ينساه يطبع صفحة بلا هوية.

استعلامٌ واحد لكل خانة وقد لا يكون فيها شيء، فيُخزَّن الناتج على الطلب نفسه:
صفحة واحدة لا تسأل قاعدة البيانات عن الشعار مرّتين.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.http import HttpRequest

_CACHE_ATTR = "_brand_context"


def branding(request: HttpRequest) -> dict[str, Any]:
    cached = getattr(request, _CACHE_ATTR, None)
    if cached is None:
        from apps.core.services import branding_service

        cached = branding_service.brand_context(as_of=date.today())
        setattr(request, _CACHE_ATTR, cached)
    return {"brand": cached}
