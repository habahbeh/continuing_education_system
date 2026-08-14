"""
Canonical field types.

Every model in the system uses these instead of declaring field types inline,
so that a decision like "money is DECIMAL(12,3)" lives in exactly one place.

Q-04 (approved): money is stored with THREE decimal places (the Jordanian fils)
and displayed with two by default via the `money_display_dp` EffectiveSetting.

ADR-005: `float` is forbidden anywhere near money. This is enforced by the
static architecture test A-01, which fails the build if any model declares a
FloatField or a DecimalField that bypasses `Money`.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from django.db import models

# --- Money -----------------------------------------------------------------
# DECIMAL(12,3): up to 999,999,999.999 — the third place is the fils.
MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 3

Money = partial(
    models.DecimalField,
    max_digits=MONEY_MAX_DIGITS,
    decimal_places=MONEY_DECIMAL_PLACES,
)

# --- Rate ------------------------------------------------------------------
# Percentages such as a partner share (50.0000) or a tax rate (16.0000).
RATE_MAX_DIGITS = 7
RATE_DECIMAL_PLACES = 4

Rate = partial(
    models.DecimalField,
    max_digits=RATE_MAX_DIGITS,
    decimal_places=RATE_DECIMAL_PLACES,
)


# --- Text ------------------------------------------------------------------
def ShortCode(**kwargs: Any) -> models.CharField:
    """Machine code / enum-like value. Safe to index under utf8mb4."""
    kwargs.setdefault("max_length", 32)
    return models.CharField(**kwargs)


def DisplayRef(**kwargs: Any) -> models.CharField:
    """A number shown to users: receipt number, agreement number, decision ref."""
    kwargs.setdefault("max_length", 64)
    return models.CharField(**kwargs)


def SettingKey(**kwargs: Any) -> models.CharField:
    """
    An EffectiveSetting key.

    Deliberately wider than ShortCode: setting keys are descriptive
    identifiers, not enum codes, and legitimately run long
    (``tax_details_required_before_sprint_4`` is 36 characters).

    64 chars × 4 bytes = 256 bytes, comfortably under the 3072-byte index
    limit, so the key stays indexable.
    """
    kwargs.setdefault("max_length", 64)
    return models.CharField(**kwargs)


def NameAr(**kwargs: Any) -> models.CharField:
    """Arabic name. Kept at 150 so it stays under the 3072-byte index limit."""
    kwargs.setdefault("max_length", 150)
    return models.CharField(**kwargs)


def NameEn(**kwargs: Any) -> models.CharField:
    """Latin-script name."""
    kwargs.setdefault("max_length", 150)
    return models.CharField(**kwargs)


__all__ = [
    "MONEY_DECIMAL_PLACES",
    "MONEY_MAX_DIGITS",
    "RATE_DECIMAL_PLACES",
    "RATE_MAX_DIGITS",
    "DisplayRef",
    "Money",
    "NameAr",
    "NameEn",
    "Rate",
    "SettingKey",
    "ShortCode",
]
