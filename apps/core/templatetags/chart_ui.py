"""
شريط النسبة — أصنافٌ لا عرضٌ محسوب.

`input.css` يشرح القرار عند تعريف `.bar`: «الخانة الممتلئة صنف، لا عرضٌ محسوب.
لذلك لا نمط سطري ولا جافاسكربت ولا مكتبة رسم — ويتحوّل إلى شريط فارغ عند غياب
البيانات بدل أن ينكسر». وSprint 8L كان قد خالف ذلك بـ`style="width:…"` في
ثلاثة قوالب؛ هذا الوسم يعيدها إلى القاعدة.

عشر خانات لأن القارئ يقرأ «سبعة من عشرة» أسرع من أن يقيس طول شريط بعينه، ولأن
الدقة الزائدة على شريطٍ عرضُه ٦٤ بكسل دقّةٌ لا تُرى. والنسبة الرقمية مكتوبة
بجانبه على كل حال، فالشريط للمقارنة السريعة بين الصفوف لا للقراءة الدقيقة.

A-03: يستورد Django وحده.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django import template

register = template.Library()

SEGMENTS = 10


def _ratio(part: Any, whole: Any) -> int:
    """
    النسبة المئوية، مصحّحةً إلى المدى 0–100.

    القسمة على صفر تعطي صفراً لا خطأ: «لا إيراد في المدى» حالةٌ صحيحة تُرسم
    شريطاً فارغاً، لا صفحةً ساقطة.
    """
    try:
        whole_value = Decimal(str(whole or 0))
        if whole_value == 0:
            return 0
        percent = Decimal(str(part or 0)) / whole_value * 100
    except (InvalidOperation, ArithmeticError, TypeError, ValueError):
        return 0
    return max(0, min(100, int(percent.to_integral_value())))


@register.inclusion_tag("partials/_bar.html")
def percent_bar(part: Any, whole: Any, label: str = "") -> dict[str, Any]:
    """``{% percent_bar row.collected report.total %}`` — شريط ونسبة."""
    percent = _ratio(part, whole)
    filled = round(percent * SEGMENTS / 100)
    return {
        "percent": percent,
        "segments": [index < filled for index in range(SEGMENTS)],
        "label": label,
    }


__all__ = ["percent_bar"]
