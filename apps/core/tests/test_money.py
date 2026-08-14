"""Money arithmetic tests — QA gate T-001 … T-009 (all P0)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.money import (
    MoneyTypeError,
    format_money,
    round_money,
    split_amount,
    sum_money,
)


# --- T-007: Decimal is exact where float is not ----------------------------
def test_t007_decimal_addition_is_exact() -> None:
    result = sum_money([Decimal("0.1"), Decimal("0.2")])
    assert result == Decimal("0.300")
    assert str(result) == "0.300"


# --- T-002: float is rejected, not coerced ---------------------------------
@pytest.mark.parametrize("bad", [0.1, 1.0, 100.5])
def test_t002_float_is_rejected(bad: float) -> None:
    with pytest.raises(MoneyTypeError, match="float is forbidden"):
        round_money(bad)


def test_bool_is_rejected_as_money() -> None:
    with pytest.raises(MoneyTypeError):
        round_money(True)


def test_int_and_str_are_accepted() -> None:
    assert round_money(400) == Decimal("400.000")
    assert round_money("1450.5") == Decimal("1450.500")


# --- T-006: split invariant -----------------------------------------------
def test_t006_split_of_odd_amount_sums_exactly() -> None:
    parts = split_amount(Decimal("175.000"), [Decimal(50), Decimal(50)])
    assert parts == [Decimal("87.500"), Decimal("87.500")]
    assert sum(parts) == Decimal("175.000")


def test_t006_remainder_goes_to_the_first_part() -> None:
    """
    100 / 3 cannot divide evenly. The parts must still sum to 100 exactly,
    and the residual belongs to the FIRST part — the university's share — so
    a rounding artefact never short-changes the partner.
    """
    parts = split_amount(Decimal("100.000"), [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(parts) == Decimal("100.000")
    assert parts[0] >= parts[1]
    assert parts[1] == parts[2]


@pytest.mark.parametrize(
    "total",
    ["0.001", "0.002", "1.000", "99.999", "1450.000", "3900.333", "999999999.999"],
)
@pytest.mark.parametrize("weights", [[1, 1], [1, 1, 1], [50, 50], [70, 30], [1, 2, 3, 4]])
def test_split_sum_invariant_holds_broadly(total: str, weights: list[int]) -> None:
    """Property-style sweep: the sum invariant must never break."""
    amount = Decimal(total)
    parts = split_amount(amount, [Decimal(w) for w in weights])
    assert sum(parts) == amount, f"{total} split by {weights} broke the invariant"


def test_split_rejects_empty_or_zero_ratios() -> None:
    with pytest.raises(ValueError):
        split_amount(Decimal("100.000"), [])
    with pytest.raises(ValueError):
        split_amount(Decimal("100.000"), [Decimal(0), Decimal(0)])


def test_split_rejects_negative_ratio() -> None:
    with pytest.raises(ValueError):
        split_amount(Decimal("100.000"), [Decimal(-1), Decimal(2)])


# --- Rounding is ROUND_HALF_UP --------------------------------------------
@pytest.mark.parametrize(
    ("raw", "places", "expected"),
    [
        ("2.5", 0, "3"),
        ("1.005", 2, "1.01"),
        ("1.0005", 3, "1.001"),
        ("-2.5", 0, "-3"),
    ],
)
def test_round_money_uses_half_up(raw: str, places: int, expected: str) -> None:
    assert round_money(Decimal(raw), places=places) == Decimal(expected)


# --- format_money ---------------------------------------------------------
def test_format_money_with_explicit_places() -> None:
    assert format_money(Decimal("1450.500"), places=2) == "1,450.50"
    assert format_money(Decimal("1450.500"), places=3) == "1,450.500"


@pytest.mark.django_db
def test_format_money_defaults_to_money_display_dp(seeded_settings: None) -> None:
    """Display precision comes from the EffectiveSetting, not a constant."""
    assert format_money(Decimal("1450.555")) == "1,450.56"
