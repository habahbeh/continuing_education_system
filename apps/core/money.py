"""
Money arithmetic — the only place in the system where rounding is allowed.

Approved decision Q-04 / ADR-005:

* Storage is ``DECIMAL(12,3)``; display defaults to 2 decimal places via the
  ``money_display_dp`` EffectiveSetting.
* ``Decimal`` only. ``float`` is forbidden — every public function here rejects
  it loudly rather than coercing, because a silent ``float`` conversion is
  exactly the class of bug this system must not have.
* Rounding is ``ROUND_HALF_UP`` and happens only at the documented points
  below. There is no intermediate rounding.
* **Invariant:** when an amount is split, the parts sum to the original
  EXACTLY. Any rounding remainder is carried by the FIRST part, which by
  convention is the university's share.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from apps.core.fields import MONEY_DECIMAL_PLACES

#: Quantiser for stored money: three decimal places (fils).
MONEY_QUANT = Decimal(1).scaleb(-MONEY_DECIMAL_PLACES)  # Decimal('0.001')

ZERO = Decimal("0.000")


class MoneyTypeError(TypeError):
    """Raised when a non-Decimal numeric type reaches a money function."""


def _ensure_decimal(value: object, *, name: str = "value") -> Decimal:
    """
    Accept Decimal, int and numeric str. Reject float explicitly.

    ``int`` is safe (exact) and convenient in tests and constants.
    ``float`` is never safe and is rejected with a message that names the
    offending argument, so the traceback points at the real mistake.
    """
    if isinstance(value, bool):  # bool is a subclass of int; never a money value
        raise MoneyTypeError(f"{name}: bool is not a monetary value")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, float):
        raise MoneyTypeError(
            f"{name}: float is forbidden for money (ADR-005). Use Decimal('{value}') instead."
        )
    raise MoneyTypeError(f"{name}: expected Decimal, got {type(value).__name__}")


def round_money(value: object, *, places: int = MONEY_DECIMAL_PLACES) -> Decimal:
    """
    Round to the stored precision using ROUND_HALF_UP.

    This is a *documented rounding point*. Do not round anywhere else.
    """
    amount = _ensure_decimal(value, name="value")
    quant = Decimal(1).scaleb(-places)
    return amount.quantize(quant, rounding=ROUND_HALF_UP)


def split_amount(total: object, ratios: Sequence[object]) -> list[Decimal]:
    """
    Split ``total`` across ``ratios`` so that the parts sum to ``total`` exactly.

    ``ratios`` are relative weights, not required to sum to 100. Each part is
    rounded to the stored precision; the residual left by rounding is added to
    the FIRST part.

    Why the first part: by convention the first ratio is the university's
    share. Giving the university the remainder means the partner is never
    short-changed by a rounding artefact, which is the direction a partner
    would otherwise dispute.

    >>> split_amount(Decimal("175.000"), [Decimal(50), Decimal(50)])
    [Decimal('87.500'), Decimal('87.500')]
    >>> sum(split_amount(Decimal("100.000"), [Decimal(1), Decimal(1), Decimal(1)]))
    Decimal('100.000')
    """
    amount = round_money(total)
    weights = [_ensure_decimal(r, name=f"ratios[{i}]") for i, r in enumerate(ratios)]

    if not weights:
        raise ValueError("split_amount requires at least one ratio")
    if any(w < 0 for w in weights):
        raise ValueError("split_amount does not accept negative ratios")

    weight_total = sum(weights, ZERO)
    if weight_total == 0:
        raise ValueError("split_amount requires the ratios to sum to more than zero")

    parts = [round_money(amount * w / weight_total) for w in weights]

    # Carry the residual on the first part so the invariant always holds.
    residual = amount - sum(parts, ZERO)
    if residual != 0:
        parts[0] = parts[0] + residual

    assert sum(parts, ZERO) == amount, "split_amount broke the sum invariant"
    return parts


def sum_money(values: Iterable[object]) -> Decimal:
    """Sum monetary values, rejecting floats, and return stored precision."""
    total = ZERO
    for index, value in enumerate(values):
        total += _ensure_decimal(value, name=f"values[{index}]")
    return round_money(total)


def format_money(value: object, *, places: int | None = None) -> str:
    """
    Format for display.

    ``places`` defaults to the ``money_display_dp`` EffectiveSetting (2), which
    is a business decision and therefore configurable (ADR-009) rather than a
    constant. The stored value keeps all three decimals regardless.
    """
    if places is None:
        # Imported lazily: money.py must stay importable without the database,
        # and core.services imports core.money.
        from datetime import date

        from apps.core.services.settings_service import get_setting

        places = int(get_setting("money_display_dp", as_of=date.today()))

    amount = round_money(value, places=places)
    return f"{amount:,.{places}f}"


__all__ = [
    "MONEY_QUANT",
    "ZERO",
    "MoneyTypeError",
    "format_money",
    "round_money",
    "split_amount",
    "sum_money",
]
