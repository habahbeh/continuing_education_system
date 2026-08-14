"""Effective-dated settings tests (ADR-009, BR-086)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.core.exceptions import SettingNotFound
from apps.core.models import EffectiveSetting, SettingValueType
from apps.core.services.settings_service import close_setting, get_setting, set_setting

pytestmark = pytest.mark.django_db


def test_as_of_is_required_keyword() -> None:
    """
    Forgetting `as_of` must be a TypeError, not a silent read of today's value.
    That silent read is precisely the bug this signature exists to prevent.
    """
    with pytest.raises(TypeError):
        get_setting("diploma_minimum_first_payment")  # type: ignore[call-arg]


def test_past_as_of_returns_the_old_value() -> None:
    """A claim for July must be read with July's settings, not today's."""
    set_setting(
        "diploma_minimum_first_payment",
        Decimal("400.000"),
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 1, 1),
        note="القيمة الأصلية من العرض",
    )
    close_setting("diploma_minimum_first_payment", effective_to=date(2026, 7, 31))
    set_setting(
        "diploma_minimum_first_payment",
        Decimal("450.000"),
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 8, 1),
        note="رفع الحد بقرار إداري",
    )

    key = "diploma_minimum_first_payment"
    assert get_setting(key, as_of=date(2026, 7, 15)) == Decimal("400.000")
    assert get_setting(key, as_of=date(2026, 8, 15)) == Decimal("450.000")


def test_future_effective_date_does_not_affect_today() -> None:
    set_setting(
        "transfer_lecture_limit",
        3,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2026, 1, 1),
        note="قيمة العرض",
    )
    close_setting("transfer_lecture_limit", effective_to=date(2026, 12, 31))
    set_setting(
        "transfer_lecture_limit",
        5,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2027, 1, 1),
        note="تغيير مستقبلي",
    )
    assert get_setting("transfer_lecture_limit", as_of=date(2026, 8, 14)) == 3


def test_overlapping_periods_are_rejected() -> None:
    set_setting(
        "payment_overdue_days",
        30,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2026, 1, 1),
        note="أولي",
    )
    with pytest.raises(ValueError, match="Overlapping period"):
        set_setting(
            "payment_overdue_days",
            45,
            value_type=SettingValueType.INTEGER,
            effective_from=date(2026, 6, 1),
            note="محاولة تداخل",
        )


def test_missing_setting_raises_unless_default_given() -> None:
    with pytest.raises(SettingNotFound):
        get_setting("does_not_exist", as_of=date(2026, 8, 14))
    assert get_setting("does_not_exist", as_of=date(2026, 8, 14), default=7) == 7


def test_note_is_mandatory() -> None:
    with pytest.raises(ValueError, match="note"):
        set_setting(
            "some_key",
            1,
            value_type=SettingValueType.INTEGER,
            effective_from=date(2026, 1, 1),
            note="   ",
        )


def test_null_value_means_not_configured_not_zero() -> None:
    """
    Q-05: `default_tax_rate` is seeded NULL meaning "rate unknown" (Q-25).
    It must read back as None so callers can tell it apart from a real zero.
    """
    set_setting(
        "default_tax_rate",
        None,
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 1, 1),
        note="النسبة غير معروفة بعد — Q-25",
    )
    value = get_setting("default_tax_rate", as_of=date(2026, 8, 14))
    assert value is None
    assert value != Decimal("0")


@pytest.mark.parametrize(
    ("raw", "vtype", "expected"),
    [
        ("400.000", SettingValueType.DECIMAL, Decimal("400.000")),
        ("3", SettingValueType.INTEGER, 3),
        ("true", SettingValueType.BOOLEAN, True),
        ("false", SettingValueType.BOOLEAN, False),
        ("NET", SettingValueType.STRING, "NET"),
    ],
)
def test_value_types_cast_correctly(raw: str, vtype: str, expected: object) -> None:
    EffectiveSetting.objects.create(
        key="k",
        value=raw,
        value_type=vtype,
        effective_from=date(2026, 1, 1),
        note="n",
    )
    assert get_setting("k", as_of=date(2026, 8, 14)) == expected
