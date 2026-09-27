"""
عرض المبلغ كما يُقرأ على ورقة، لا كما يُخزَّن في عمود.

المال يُخزَّن بثلاث خانات (الفلس، Q-04) ويُعرض بخانتين عبر الإعداد
``money_display_dp``. و``format_money`` تفعل ذلك منذ Sprint 1 — ولا يستدعيها
قالبٌ واحد في المشروع: الشاشات تطبع ``{{ row.amount }}`` فيخرج ``270.000``،
وثلاث خانات على مستندٍ مالي خطأ عرضٍ لا دقّةٌ زائدة.

**ولماذا وسمٌ لا فلتر.** ``format_money`` تقرأ الإعداد في كل نداء، فجدولٌ فيه
اثنا عشر مبلغاً كان يستعلم عن ``money_display_dp`` اثنتي عشرة مرّة — قِيس في
Sprint 8L: ١٢ استعلاماً من أصل ٢٧ في تصيير واحد. الوسم يقرأ القيمة من سياق
الطلب، فتُقرأ مرّة واحدة مهما كثرت الصفوف.

ولا تخزين مؤقّت عابراً للطلبات: المشروع لا يخزّن قيمة إعدادٍ مؤقّتاً في أي
موضع، وتغيير الإعداد فعلٌ إداري يسري فوراً (ADR-009). النطاق هنا الطلب الواحد
وحده — وهو أقصر من أن يُقادم شيئاً.

A-03: يستورد Django و``apps.core.money`` ولا يعرف تطبيقاً تجارياً.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django import template

from apps.core.money import MoneyTypeError, format_money

register = template.Library()

#: ما يُطبع مكان قيمةٍ غائبة. الفراغ في عمود مالي يُقرأ صفراً، والشرطة لا.
ABSENT = "—"

_REQUEST_ATTR = "_money_display_places"


def _places(context: Any) -> int | None:
    """
    خانات العرض لهذا الطلب — استعلامٌ واحد لا استعلامٌ لكل مبلغ.

    ``None`` تعني «لم يُعرف»، فيتولّى ``format_money`` قراءته بنفسه: قالبٌ
    يُصيَّر خارج طلب (بريد، أمر إداري) يبقى صحيحاً وإن كلّف استعلاماً.
    """
    request = getattr(context, "request", None)
    if request is None:
        return None

    cached = getattr(request, _REQUEST_ATTR, None)
    if cached is None:
        from datetime import date

        from apps.core.services.settings_service import get_setting

        cached = int(get_setting("money_display_dp", as_of=date.today()))
        setattr(request, _REQUEST_ATTR, cached)
    return int(cached)


def _render(value: object, places: int | None) -> str:
    if value is None or value == "":
        return ABSENT
    try:
        return format_money(value, places=places) if places is not None else format_money(value)
    except (MoneyTypeError, InvalidOperation, ArithmeticError, TypeError, ValueError):
        return str(value)


@register.simple_tag(takes_context=True, name="money")
def money(context: Any, value: object) -> str:
    """
    ``{% money report.total %}`` → ``1,830.00``.

    القيمة الغائبة شرطة، لا صفر ولا فراغ: «لم يقع شيء» و«وقع صفر» جوابان
    مختلفان في تقرير مالي. وما ليس مبلغاً يُعاد كما هو بدل أن يُسقط الصفحة —
    فالقالب ليس المكان الذي يُكتشف فيه خطأ نوعٍ في خدمة.
    """
    return _render(value, _places(context))


@register.simple_tag(takes_context=True, name="money_signed")
def money_signed(context: Any, value: object) -> str:
    """
    كالسابق، ويسبق الموجبَ علامةُ ``+``.

    يُستعمل حيث تكون الإشارة هي المعنى — فرق الإقفال مثلاً: ``+12.00`` زيادة
    في الصندوق و``-12.00`` نقص، و``12.00`` وحدها لا تقول أيّهما.
    """
    text = _render(value, _places(context))
    if text == ABSENT:
        return text
    try:
        positive = Decimal(str(value)) > 0
    except (InvalidOperation, ArithmeticError, TypeError, ValueError):
        return text
    return f"+{text}" if positive else text


__all__ = ["money", "money_signed"]
