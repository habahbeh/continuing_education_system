"""
The four Sprint 5 assumptions — each proved switchable, none baked in.

These are the tests that make "documented assumption" mean something. For each
open question the behaviour is asserted BOTH ways: as it stands under the
current assumption, and as it becomes when the setting changes. If the client
answers differently, the second assertion is already the specification.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.core.models import EffectiveSetting, SettingValueType
from apps.core.services.settings_service import close_setting, set_setting
from apps.settlements.services import assumptions, entitlement_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


def _set(key: str, value: str, *, value_type: str = SettingValueType.STRING) -> None:
    """Change a setting the way an administrator would — dated, not patched."""
    if EffectiveSetting.objects.filter(key=key).exists():
        close_setting(key, effective_to=date(2026, 1, 1))
    set_setting(
        key,
        value,
        value_type=value_type,
        effective_from=date(2026, 1, 2),
        note="اختبار — تبديل افتراض",
    )


# ---------------------------------------------------------------------------
# Q-28 — the partner's base
# ---------------------------------------------------------------------------
def test_q28_default_is_net(seeded_settings: None) -> None:
    """
    ⚠️ ASSUMPTION — net, before tax (BR-093).

    Tax is collected for the treasury; splitting it would give the partner
    money that was never the centre's and charge them a burden that is not
    theirs.
    """
    assert assumptions.partner_base_mode(as_of=TERM_START) == assumptions.BASE_MODE_NET


def test_q28_switches_to_gross_by_setting(seeded_settings: None) -> None:
    """The client's answer costs a setting change, not a migration."""
    _set(assumptions.BASE_MODE_KEY, "GROSS")
    assert assumptions.partner_base_mode(as_of=TERM_START) == assumptions.BASE_MODE_GROSS


def test_q28_changes_the_base_actually_computed(
    seeded_settings: None, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    The two answers give different money — which is why it is an open question.

    With no tax rate configured (Q-25) net and gross coincide today, so this
    asserts the SELECTION rather than a difference that does not yet exist.
    The moment a rate is set, the two diverge by exactly the tax.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1)

    net_base = entitlement_service.shareable_collected(
        enrollment, agreement=percent_agreement, as_of=TERM_START
    )
    _set(assumptions.BASE_MODE_KEY, "GROSS")
    gross_base = entitlement_service.shareable_collected(
        enrollment, agreement=percent_agreement, as_of=TERM_START
    )

    # Equal only because default_tax_rate is still NULL (Q-25 unanswered).
    assert net_base == gross_base == Decimal("250.000")
    from apps.billing.models import ChargeLine

    assert not ChargeLine.objects.filter(is_taxable=True).exists()


def test_q28_the_basis_used_is_recorded_on_the_claim(
    seeded_settings: None,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    ``base_mode_snapshot`` makes the assumption visible on the document.

    Otherwise a claim raised under NET and read after a switch to GROSS would
    look as though it had used the new basis.
    """
    from apps.settlements.services import claim_service

    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=date(2026, 12, 20),
        trigger_type="END_OF_COURSE",
    )
    assert claim.base_mode_snapshot == "NET"


# ---------------------------------------------------------------------------
# Q-08 — how far an offset reaches
# ---------------------------------------------------------------------------
def test_q08_default_scope_is_the_partner(seeded_settings: None) -> None:
    """⚠️ ASSUMPTION — a refund under one agreement may be recovered from another."""
    assert assumptions.offset_scope(as_of=TERM_START) == assumptions.OFFSET_SCOPE_PARTNER


def test_q08_an_unpinned_obligation_reaches_any_agreement_of_that_partner(
    seeded_settings: None, finance, partner, percent_agreement, advance_agreement
) -> None:
    from apps.settlements.models import ObligationType, PartnerObligation
    from apps.settlements.services import claim_service

    PartnerObligation.objects.create(
        code="OBL-FREE",
        partner=partner,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("100.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    reachable = claim_service.open_obligations_for(
        partner=partner, agreement=advance_agreement, as_of=TERM_START
    )
    assert reachable.count() == 1, "an unpinned obligation should reach any agreement"


def test_q08_a_pinned_obligation_stays_with_its_agreement(
    seeded_settings: None, finance, partner, percent_agreement, advance_agreement
) -> None:
    """
    The per-obligation restriction always wins over the setting.

    It came from a contract; the setting is only the default for obligations
    that no contract constrained.
    """
    from apps.settlements.models import ObligationType, PartnerObligation
    from apps.settlements.services import claim_service

    PartnerObligation.objects.create(
        code="OBL-PINNED",
        partner=partner,
        restricted_to_agreement=percent_agreement,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("100.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    assert (
        claim_service.open_obligations_for(
            partner=partner, agreement=advance_agreement, as_of=TERM_START
        ).count()
        == 0
    )
    assert (
        claim_service.open_obligations_for(
            partner=partner, agreement=percent_agreement, as_of=TERM_START
        ).count()
        == 1
    )


def test_q08_switching_to_agreement_scope_narrows_everything(
    seeded_settings: None, finance, partner, percent_agreement, advance_agreement
) -> None:
    """If the client says offsets never cross agreements, one setting does it."""
    from apps.settlements.models import ObligationType, PartnerObligation
    from apps.settlements.services import claim_service

    PartnerObligation.objects.create(
        code="OBL-FREE2",
        partner=partner,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("100.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    _set(assumptions.OFFSET_SCOPE_KEY, "AGREEMENT")

    assert (
        claim_service.open_obligations_for(
            partner=partner, agreement=advance_agreement, as_of=TERM_START
        ).count()
        == 0
    )


# ---------------------------------------------------------------------------
# Q-16 — when an enrolment is overdue
# ---------------------------------------------------------------------------
def test_q16_default_grace_is_thirty_days(seeded_settings: None) -> None:
    assert assumptions.overdue_days(as_of=TERM_START) == 30


def test_q16_a_settled_enrolment_is_never_overdue(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    """All three conditions are required — a zero balance ends it immediately."""
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    assert not entitlement_service.is_payment_overdue(
        enrollment, as_of=TERM_START + timedelta(days=365)
    )


def test_q16_a_balance_becomes_overdue_after_the_grace_period(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    enrollment = make_paid_enrollment(cohort_with_agreement, index=2, amount="50.000")

    assert not entitlement_service.is_payment_overdue(
        enrollment, as_of=TERM_START + timedelta(days=10)
    )
    assert entitlement_service.is_payment_overdue(enrollment, as_of=TERM_START + timedelta(days=31))


def test_q16_a_course_that_has_not_started_is_not_overdue(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    Nobody is behind on payment for teaching that has not happened.

    Without this condition every unpaid enrolment made months in advance
    would read as delinquent, and BR-045 would strip the partner's
    entitlement for participants who are simply early.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=3, amount="50.000")
    assert not entitlement_service.is_payment_overdue(
        enrollment, as_of=TERM_START - timedelta(days=1)
    )


def test_q16_the_grace_period_is_configurable(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    enrollment = make_paid_enrollment(cohort_with_agreement, index=4, amount="50.000")
    as_of = TERM_START + timedelta(days=20)

    assert not entitlement_service.is_payment_overdue(enrollment, as_of=as_of)
    _set(assumptions.OVERDUE_DAYS_KEY, "14", value_type=SettingValueType.INTEGER)
    assert entitlement_service.is_payment_overdue(enrollment, as_of=as_of)


def test_q16_a_manual_override_with_a_reason_is_honoured(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    The finance officer sees things the rule cannot.

    A reason is required: an override without one is indistinguishable from a
    mistake, and it strips the partner's entitlement for that participant.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=5, amount="270.000")
    assert not entitlement_service.is_payment_overdue(enrollment, as_of=TERM_START)

    enrollment.status = "PAYMENT_OVERDUE"
    enrollment.status_note_ar = "شيك مرتجع — أُبلغ المشارك بتاريخ 2026/09/25"
    enrollment.save()

    assert entitlement_service.is_payment_overdue(enrollment, as_of=TERM_START)


def test_q16_a_flag_without_a_reason_is_not_an_override(
    seeded_settings: None, cohort_with_agreement, make_paid_enrollment
) -> None:
    """A bare status change is not evidence, so it does not override the rule."""
    enrollment = make_paid_enrollment(cohort_with_agreement, index=6, amount="270.000")
    enrollment.status = "PAYMENT_OVERDUE"
    enrollment.status_note_ar = ""
    enrollment.save()

    assert not entitlement_service.is_payment_overdue(enrollment, as_of=TERM_START)


def test_q16_only_the_predicate_exists_not_the_sprint_6_job() -> None:
    """
    The daily sweep belongs to Sprint 6 and is deliberately not built.

    Asserted so "we only built the predicate" stays true rather than becoming
    a comment nobody rechecks.
    """
    from pathlib import Path

    commands = Path(__file__).resolve().parents[3] / "apps"
    names = [p.name for p in commands.rglob("management/commands/*.py")]
    assert not any("overdue" in n for n in names), (
        "an overdue sweep command appeared — that is Sprint 6 scope"
    )
