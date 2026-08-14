"""seed_settings tests — idempotent, non-destructive, correct capability values."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from apps.core.models import EffectiveSetting
from apps.core.services.settings_service import get_setting

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 8, 14)


def _seed() -> str:
    out = StringIO()
    call_command("seed_settings", stdout=out)
    return out.getvalue()


def test_seed_creates_all_keys() -> None:
    _seed()
    from apps.core.management.commands.seed_settings import SEED

    assert EffectiveSetting.objects.count() == len(SEED)


def test_seed_is_idempotent() -> None:
    _seed()
    first = EffectiveSetting.objects.count()
    output = _seed()
    assert EffectiveSetting.objects.count() == first
    assert "لم يُمسّ" in output


def test_seed_does_not_overwrite_a_manually_changed_value() -> None:
    """An administrator's change must survive a re-run of the seeder."""
    _seed()
    EffectiveSetting.objects.filter(key="transfer_lecture_limit").update(value="5")
    _seed()
    assert get_setting("transfer_lecture_limit", as_of=AS_OF) == 5


# --- Approved capability values (revised 2026-08-14) ----------------------
def test_deposits_are_supported_not_disabled() -> None:
    """Q-01 revised: the client confirmed deposits are used."""
    _seed()
    assert get_setting("deposits_supported", as_of=AS_OF) is True
    assert get_setting("default_deposit_required", as_of=AS_OF) is False


def test_tax_is_supported_not_disabled() -> None:
    """Q-05 revised: 'the university accounts for its revenue with tax'."""
    _seed()
    assert get_setting("tax_supported", as_of=AS_OF) is True


def test_default_tax_rate_is_null_meaning_unknown_not_zero() -> None:
    """
    Q-25 is still open. NULL says "rate not yet known"; a zero would say
    "no tax", which is now known to be false.
    """
    _seed()
    value = get_setting("default_tax_rate", as_of=AS_OF)
    assert value is None
    assert value != Decimal("0")


def test_sprint_4_blocking_marker_is_set() -> None:
    _seed()
    assert get_setting("tax_details_required_before_sprint_4", as_of=AS_OF) is True


def test_partner_base_mode_defaults_to_net() -> None:
    """Q-28 pending; NET is the recommended professional default."""
    _seed()
    assert get_setting("partner_base_mode", as_of=AS_OF) == "NET"


def test_money_display_dp_is_two() -> None:
    _seed()
    assert get_setting("money_display_dp", as_of=AS_OF) == 2


def test_retired_keys_are_never_seeded() -> None:
    _seed()
    assert not EffectiveSetting.objects.filter(key__in=["deposits_enabled", "tax_enabled"]).exists()


def test_seeder_reports_a_retired_key_if_someone_inserts_one() -> None:
    from apps.core.models import SettingValueType

    EffectiveSetting.objects.create(
        key="tax_enabled",
        value="false",
        value_type=SettingValueType.BOOLEAN,
        effective_from=date(2026, 1, 1),
        note="سطر ملغى أُدخل يدوياً",
    )
    output = _seed()
    assert "tax_enabled" in output
    assert "ملغى" in output


def test_every_seeded_row_carries_a_note() -> None:
    _seed()
    assert not EffectiveSetting.objects.filter(note="").exists()


def test_every_seeded_key_fits_the_column() -> None:
    """
    Regression guard.

    `tax_details_required_before_sprint_4` (36 chars) once exceeded a 32-char
    column. STRICT_ALL_TABLES caught it — without strict mode MySQL would have
    truncated the key silently and created a setting under a name that
    get_setting() could never find. This test catches it at authoring time
    instead of at seed time.
    """
    from apps.core.management.commands.seed_settings import SEED

    max_length = EffectiveSetting._meta.get_field("key").max_length
    assert max_length is not None
    too_long = [(k, len(k)) for k, *_ in SEED if len(k) > max_length]
    assert not too_long, f"Setting keys exceed the {max_length}-char column: {too_long}"


def test_every_seeded_value_fits_the_column() -> None:
    from apps.core.management.commands.seed_settings import SEED

    max_length = EffectiveSetting._meta.get_field("value").max_length
    assert max_length is not None
    too_long = [(k, len(str(v))) for k, v, *_ in SEED if v is not None and len(str(v)) > max_length]
    assert not too_long, f"Setting values exceed the {max_length}-char column: {too_long}"


def test_seed_keys_are_unique() -> None:
    from apps.core.management.commands.seed_settings import SEED

    keys = [k for k, *_ in SEED]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"Duplicate keys in the SEED table: {duplicates}"
