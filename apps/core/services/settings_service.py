"""
Effective-dated settings service (ADR-009, BR-086).

The single rule that makes this work: ``as_of`` is a REQUIRED keyword argument
with no default. Reading a setting "as of today" when you meant "as of the
event date" is the silent bug this signature is designed to make impossible —
forgetting it raises TypeError at the call site instead of quietly returning
the wrong historical value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q

from apps.core.exceptions import SettingNotFound
from apps.core.models import EffectiveSetting, SettingValueType

#: Sentinel distinguishing "no default supplied" from "default is None",
#: because None is a meaningful value here (e.g. default_tax_rate).
_UNSET = object()


def _cast(raw: str | None, value_type: str) -> Any:
    """Convert the stored string to its declared type."""
    if raw is None:
        return None
    if value_type == SettingValueType.DECIMAL:
        return Decimal(raw)
    if value_type == SettingValueType.INTEGER:
        return int(raw)
    if value_type == SettingValueType.BOOLEAN:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return raw


def get_setting(key: str, *, as_of: date, default: Any = _UNSET) -> Any:
    """
    Return the value of ``key`` in effect on ``as_of``.

    ``as_of`` must be the date of the EVENT being processed, not today. A claim
    for July is computed with July's settings.

    Returns None when the setting exists but has a NULL value — that means
    "not configured yet" (e.g. ``default_tax_rate`` pending Q-25), which is
    different from "absent" and different from zero.
    """
    row = (
        EffectiveSetting.objects.filter(key=key, effective_from__lte=as_of)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
        .order_by("-effective_from")
        .first()
    )
    if row is None:
        if default is not _UNSET:
            return default
        raise SettingNotFound(f"No value for setting '{key}' in effect on {as_of}.")
    return _cast(row.value, row.value_type)


@transaction.atomic
def set_setting(
    key: str,
    value: Any,
    *,
    value_type: str,
    effective_from: date,
    note: str,
    user: Any = None,
    effective_to: date | None = None,
) -> EffectiveSetting:
    """
    Create a new effective-dated value for ``key``.

    Rejects overlapping periods for the same key. MySQL cannot express that as
    a table constraint (no exclusion constraints), so it is enforced here and
    covered by a test.
    """
    if not note or not note.strip():
        raise ValueError("A setting change requires a note explaining why.")

    overlapping = (
        EffectiveSetting.objects.select_for_update()
        .filter(key=key)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=effective_from))
    )
    if effective_to is not None:
        overlapping = overlapping.filter(effective_from__lte=effective_to)

    if overlapping.exists():
        raise ValueError(
            f"Overlapping period for setting '{key}' starting {effective_from}. "
            f"Close the current period first."
        )

    stored = None if value is None else str(value)
    return EffectiveSetting.objects.create(
        key=key,
        value=stored,
        value_type=value_type,
        effective_from=effective_from,
        effective_to=effective_to,
        note=note,
        created_by=user,
    )


def close_setting(key: str, *, effective_to: date) -> int:
    """Close the currently open period for ``key``. Returns rows updated."""
    return EffectiveSetting.objects.filter(key=key, effective_to__isnull=True).update(
        effective_to=effective_to
    )


__all__ = ["close_setting", "get_setting", "set_setting"]
