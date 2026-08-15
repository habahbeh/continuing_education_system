"""
Effective-dated reference lists for participant fields (Q-31).

SPEC §6 says the application form asks for a qualification ("six levels") and a
city ("twelve governorates") — and then never lists either set. Q-31 is open.

Hard-coding a guess would put invented codes into stored rows, and correcting
them later would be a data migration over live participants. So the lists are
EffectiveSettings: answering Q-31 becomes an afternoon of data entry, and the
model keeps no choices and no CHECK constraint on these two fields.

The seeded values are PROVISIONAL and marked as such in the setting note. They
exist so the form is usable, not because they are approved.
"""

from __future__ import annotations

import json
from datetime import date

from django.utils import timezone

from apps.core.services.settings_service import get_setting

QUALIFICATION_KEY = "participant_qualifications"
CITY_KEY = "participant_cities"

#: Used only when the setting row is missing entirely (an unseeded database).
#: Deliberately empty: an empty list makes the gap obvious at the form, whereas
#: a silent fallback list would look like an approved vocabulary.
_EMPTY: list[tuple[str, str]] = []


def _load(key: str, *, as_of: date | None = None) -> list[tuple[str, str]]:
    raw = get_setting(key, as_of=as_of or timezone.localdate(), default=None)
    if not raw:
        return list(_EMPTY)
    try:
        pairs = json.loads(raw)
    except (TypeError, ValueError):
        return list(_EMPTY)
    return [(str(code), str(label)) for code, label in pairs]


def qualifications(*, as_of: date | None = None) -> list[tuple[str, str]]:
    """(code, Arabic label) pairs for the qualification field."""
    return _load(QUALIFICATION_KEY, as_of=as_of)


def cities(*, as_of: date | None = None) -> list[tuple[str, str]]:
    """(code, Arabic label) pairs for the city field."""
    return _load(CITY_KEY, as_of=as_of)


def is_valid_qualification(code: str, *, as_of: date | None = None) -> bool:
    return not code or code in {c for c, _label in qualifications(as_of=as_of)}


def is_valid_city(code: str, *, as_of: date | None = None) -> bool:
    return not code or code in {c for c, _label in cities(as_of=as_of)}


def label_for(pairs: list[tuple[str, str]], code: str) -> str:
    return next((label for c, label in pairs if c == code), code)


__all__ = [
    "CITY_KEY",
    "QUALIFICATION_KEY",
    "cities",
    "is_valid_city",
    "is_valid_qualification",
    "label_for",
    "qualifications",
]
