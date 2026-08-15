"""
Q-25 — the tax rate is unknown, and the system says so loudly.

This is the most important guarantee in Sprint 4. `default_tax_rate` is seeded
NULL, meaning "not decided yet" — which is NOT "no tax". Every mechanism here
exists to stop that distinction collapsing into a quiet zero:

* the service raises TaxRateNotConfigured before computing anything;
* constraint C-28 makes a taxable line with no captured rate unstorable, so
  even a caller who bypassed the service could not write the silent zero;
* and a non-taxable line, which is legitimately tax-free, still stores no rate.

If tax were treated as zero "for now", every receipt issued before the client
answers would be quietly wrong, and nothing would mark them for correction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.billing.models import ChargeLine, ChargeType
from apps.billing.services import charge_service
from apps.core.exceptions import TaxRateNotConfigured
from apps.core.models import EffectiveSetting, SettingValueType
from apps.core.services.settings_service import close_setting, set_setting

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 20)


def test_the_seeded_rate_is_null_not_zero(seeded_settings: None) -> None:
    """The seeded state means UNKNOWN. Nothing may read it as zero."""
    row = EffectiveSetting.objects.get(key="default_tax_rate")
    assert row.value is None
    assert charge_service.effective_tax_rate(as_of=AS_OF) is None


def test_a_taxable_line_without_a_rate_raises(seeded_settings: None) -> None:
    with pytest.raises(TaxRateNotConfigured) as exc:
        charge_service.compute_tax(net=Decimal("100.000"), is_taxable=True, as_of=AS_OF)
    assert "Q-25" in str(exc.value)


def test_the_failure_message_names_the_reason_not_just_the_symptom(
    seeded_settings: None,
) -> None:
    """
    Whoever hits this needs to know it is a pending decision, not a bug.

    "غير محدَّدة تختلف عن لا ضريبة" is the sentence that sends them to the
    client rather than to the code.
    """
    with pytest.raises(TaxRateNotConfigured) as exc:
        charge_service.compute_tax(net=Decimal("50.000"), is_taxable=True, as_of=AS_OF)
    message = str(exc.value)
    assert "default_tax_rate" in message
    assert "لا ضريبة" in message


def test_a_non_taxable_line_is_legitimately_tax_free(seeded_settings: None) -> None:
    """Zero tax is correct HERE — because the line is not taxable at all."""
    tax, rate = charge_service.compute_tax(net=Decimal("100.000"), is_taxable=False, as_of=AS_OF)
    assert tax == Decimal("0.000")
    assert rate is None


def test_creating_a_taxable_charge_line_raises_before_anything_is_written(
    seeded_settings: None, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment()
    before = ChargeLine.objects.count()

    with pytest.raises(TaxRateNotConfigured):
        charge_service.create_charge_line(
            actor=None,
            enrollment=enrollment,
            charge_type=ChargeType.TUITION,
            description_ar="بند خاضع بلا نسبة",
            net_amount=Decimal("100.000"),
            charged_on=AS_OF,
            is_taxable=True,
        )

    assert ChargeLine.objects.count() == before, "a line was written despite the refusal"


def test_the_database_refuses_a_silent_zero_even_bypassing_the_service(
    seeded_settings: None, make_enrollment
) -> None:
    """
    C-28 — the constraint is the backstop the service sits in front of.

    Written as raw SQL on purpose: this is what a management command, a bulk
    update or a future API would do, and none of them may store a taxable
    line whose rate nobody captured.
    """
    enrollment, _quote = make_enrollment()

    with pytest.raises(IntegrityError), transaction.atomic():
        ChargeLine.objects.create(
            enrollment=enrollment,
            charge_type=ChargeType.TUITION,
            description_ar="محاولة تجاوز",
            net_amount=Decimal("100.000"),
            is_taxable=True,
            tax_rate_snapshot=None,
            tax_amount=Decimal("0.000"),
            gross_amount=Decimal("100.000"),
            charged_on=AS_OF,
        )


def test_once_the_rate_is_configured_tax_is_computed_and_captured(
    seeded_settings: None, make_enrollment
) -> None:
    """
    The whole thing unblocks with a setting change — no migration, no deploy.

    Uses 16% purely as a test value; nothing is seeded and no rate is assumed
    anywhere in the application.
    """
    close_setting("default_tax_rate", effective_to=date(2026, 1, 1))
    set_setting(
        "default_tax_rate",
        "16.0000",
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 1, 2),
        note="قيمة اختبارية فقط — ليست جواب Q-25",
    )

    tax, rate = charge_service.compute_tax(net=Decimal("100.000"), is_taxable=True, as_of=AS_OF)
    assert tax == Decimal("16.000")
    assert rate == Decimal("16.0000")

    enrollment, _quote = make_enrollment()
    line = charge_service.create_charge_line(
        actor=None,
        enrollment=enrollment,
        charge_type=ChargeType.TUITION,
        description_ar="بند خاضع",
        net_amount=Decimal("100.000"),
        charged_on=AS_OF,
        is_taxable=True,
    )
    assert line.gross_amount == Decimal("116.000")
    assert line.tax_rate_snapshot == Decimal("16.0000")


def test_an_old_line_keeps_the_rate_it_was_charged_at(
    seeded_settings: None, make_enrollment
) -> None:
    """
    BR-098 — the rate is a snapshot, not a live lookup.

    Raising the rate later must not re-price a line that was already issued;
    otherwise every historical receipt silently disagrees with its paper copy.
    """
    close_setting("default_tax_rate", effective_to=date(2026, 1, 1))
    set_setting(
        "default_tax_rate",
        "16.0000",
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 1, 2),
        note="اختبار",
    )
    enrollment, _quote = make_enrollment()
    line = charge_service.create_charge_line(
        actor=None,
        enrollment=enrollment,
        charge_type=ChargeType.TUITION,
        description_ar="بند قديم",
        net_amount=Decimal("100.000"),
        charged_on=AS_OF,
        is_taxable=True,
    )

    close_setting("default_tax_rate", effective_to=date(2026, 10, 1))
    set_setting(
        "default_tax_rate",
        "20.0000",
        value_type=SettingValueType.DECIMAL,
        effective_from=date(2026, 10, 2),
        note="اختبار — رفع النسبة",
    )

    line.refresh_from_db()
    assert line.tax_rate_snapshot == Decimal("16.0000")
    assert line.gross_amount == Decimal("116.000")


def test_the_demo_seed_creates_no_taxable_lines(seeded_settings: None, make_enrollment) -> None:
    """
    Pending Q-26, no charge line is marked taxable anywhere.

    Which fee types are taxable is the client's answer. Guessing it would be
    exactly the silent assumption this module exists to prevent, so the seed
    path produces only non-taxable lines — and says so.
    """
    enrollment, _quote = make_enrollment()
    assert ChargeLine.objects.filter(enrollment=enrollment).exists()
    assert not ChargeLine.objects.filter(is_taxable=True).exists()
    assert not ChargeLine.objects.filter(tax_rate_snapshot__isnull=False).exists()
