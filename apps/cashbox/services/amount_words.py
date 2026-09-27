"""
An amount in Arabic words, for the printed receipt — «ألف وسبعمئة وخمسة عشر
ديناراً و500 فلس» read as a sentence, so the figure and its words can be
checked against each other on paper.

Dinars and fils are named separately (three-decimal currency). Numbers are
spelled in the accusative («ديناراً») after 11–99 and singular after 100,
1000, …, following ordinary Arabic counting rules.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_ONES = [
    "",
    "واحد",
    "اثنان",
    "ثلاثة",
    "أربعة",
    "خمسة",
    "ستة",
    "سبعة",
    "ثمانية",
    "تسعة",
    "عشرة",
    "أحد عشر",
    "اثنا عشر",
    "ثلاثة عشر",
    "أربعة عشر",
    "خمسة عشر",
    "ستة عشر",
    "سبعة عشر",
    "ثمانية عشر",
    "تسعة عشر",
]
_TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_HUNDREDS = [
    "",
    "مئة",
    "مئتان",
    "ثلاثمئة",
    "أربعمئة",
    "خمسمئة",
    "ستمئة",
    "سبعمئة",
    "ثمانمئة",
    "تسعمئة",
]
#: (singular, dual, plural 3–10, form after 11+) per scale.
_SCALES = [
    ("ألف", "ألفان", "آلاف", "ألفاً"),
    ("مليون", "مليونان", "ملايين", "مليوناً"),
    ("مليار", "ملياران", "مليارات", "ملياراً"),
]


def _under_thousand(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        if n % 10:
            parts.append(_ONES[n % 10])
        parts.append(_TENS[n // 10])
    elif n:
        parts.append(_ONES[n])
    return " و".join(parts)


def _scale_words(count: int, scale: tuple[str, str, str, str]) -> str:
    singular, dual, plural, after_eleven = scale
    if count == 1:
        return singular
    if count == 2:
        return dual
    words = _under_thousand(count)
    if words.endswith("ان"):
        words = words[:-1]  # «مئتا ألف», the dual governing the scale word
    if 3 <= count % 100 <= 10:
        return f"{words} {plural}"
    if count % 100 == 0:
        return f"{words} {singular}"
    return f"{words} {after_eleven}"


def integer_words(n: int) -> str:
    """0 ≤ n < 10¹² in Arabic words."""
    if n == 0:
        return "صفر"
    groups: list[str] = []
    rest, low = divmod(n, 1000)
    for scale in _SCALES:
        if rest == 0:
            break
        rest, count = divmod(rest, 1000)
        if count:
            groups.append(_scale_words(count, scale))
    groups.reverse()
    if low:
        groups.append(_under_thousand(low))
    return " و".join(groups)


def _unit(n: int, singular: str, dual: str, plural: str, accusative: str) -> str:
    """The currency word for a count, by Arabic counting rules."""
    if n == 0:
        return singular
    if n == 1:
        return f"{singular} واحد"
    if n == 2:
        return dual
    if 3 <= n % 100 <= 10:
        return plural
    if n % 100 == 0:
        return singular
    return accusative


def _counted(n: int, singular: str, dual: str, plural: str, accusative: str) -> str:
    """The number and its currency word, the dual of a scale shortened before
    the noun it governs («ألفا دينار», «مئتا دينار»)."""
    unit = _unit(n, singular, dual, plural, accusative)
    if n in (1, 2):
        return unit
    words = integer_words(n)
    if unit == singular:
        if words.endswith("ان"):
            words = words[:-1]  # «ألفا دينار»
        elif words.endswith("اً"):
            words = words[:-2]  # «أحد عشر ألف دينار»
    return f"{words} {unit}"


def amount_in_words_ar(amount: Decimal) -> str:
    """«ألف وسبعمئة وخمسة عشر ديناراً وخمسمئة فلس» — or «صفر دينار»."""
    quantised = Decimal(amount).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    dinars = int(quantised)
    fils = int((quantised - dinars) * 1000)

    parts: list[str] = []
    if dinars or not fils:
        parts.append(_counted(dinars, "دينار", "ديناران", "دنانير", "ديناراً"))
    if fils:
        parts.append(_counted(fils, "فلس", "فلسان", "فلوس", "فلساً"))
    return " و".join(parts) + " فقط لا غير"


__all__ = ["amount_in_words_ar", "integer_words"]
