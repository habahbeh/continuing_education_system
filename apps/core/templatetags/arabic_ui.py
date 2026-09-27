"""
Arabic counting for the screens: «سند واحد · سندان · ٣ سندات · ١١ سنداً».

Django's ``blocktranslate count`` falls back to English's two forms when no
catalogue is loaded, so every register said «2 سندات». Arabic counts in four:
one (the noun with «واحد/واحدة»), two (the dual), three to ten (the plural,
number first), eleven and up (the accusative singular, number first) — and
zero, which the filter phrases as «لا …».
"""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter(name="ar_count")
def ar_count(value: object, forms: str) -> str:
    """
    ``{{ n|ar_count:"سند واحد|سندان|سندات|سنداً" }}``

    ``forms`` is «one|two|3–10|11+» separated by ``|``. The 3–10 and 11+ forms
    are printed after the number; the first two stand alone. A form left
    empty falls back to the previous one.
    """
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    parts = [p.strip() for p in forms.split("|")]
    while len(parts) < 4:
        parts.append(parts[-1] if parts else "")
    one, two, few, many = parts[:4]

    if n == 0:
        # «لا سندات» — the plural after a negation.
        return f"لا {few}"
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n % 100 <= 10:
        return f"{n} {few}"
    if n % 100 == 0:
        # «مئة سند»، «ألف سند» — the bare singular after a round hundred.
        return f"{n} {one.replace(' واحدة', '').replace(' واحد', '')}"
    return f"{n} {many}"


__all__ = ["ar_count"]
