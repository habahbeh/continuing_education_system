"""
``seed_settlements_demo`` — the command that gives §3 data to stand on.

The demo database held four agreements and not one cohort carrying any of
them, so the partner half of the system — and report 3 of §9, «كشف مستحقات كل
شريك ومخالصاته» — had nothing to show. The command links the two and raises a
claim through the services.

It is written against the demo's own codes, which do not exist here, so the
pairing table is pointed at fixtures instead: what is under test is the
command's behaviour, not the demo's row names.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command

from apps.people.models import Role, User
from apps.settlements.models import PartnerClaim, PartnerSettlement

pytestmark = pytest.mark.django_db

PASSWORD = "seed-probe-1234"


@pytest.fixture
def seed_actor(seeded_settings):
    """
    Two actors, because the matrix says one cannot do this.

    The finance officer holds ``C`` on claims and settlements; the centre
    manager holds ``A`` and the cohort edit. A seed run under one hand would
    write a claim raised and approved by the same person — which no screen
    would ever produce.
    """
    User.objects.create_user(
        username="seed.finance", password=PASSWORD, role=Role.FINANCE_OFFICER
    )
    return User.objects.create_user(
        username="seed.manager", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _run() -> str:
    out = StringIO()
    call_command("seed_settlements_demo", stdout=out)
    return out.getvalue()


def test_it_writes_nothing_when_the_demo_rows_are_not_there(seed_actor) -> None:
    """
    Safety first: the command names demo codes, and a database without them
    must come out of it untouched rather than half-seeded.
    """
    output = _run()

    assert "تُخطّيت" in output
    assert PartnerClaim.objects.count() == 0
    assert PartnerSettlement.objects.count() == 0


def test_it_refuses_without_an_actor_to_attribute_the_seed_to(db) -> None:
    """Every write here is audited; there is nobody to audit it against."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="لا مستخدم فعّال"):
        _run()


def test_it_refuses_when_only_one_of_the_two_hands_exists(seeded_settings) -> None:
    """
    Separation of duties is not something a seed may quietly route around: with
    no finance officer there is nobody who MAY raise a claim, and inventing a
    super-administrator to do it would seed a row no workflow could produce.
    """
    from django.core.management.base import CommandError

    User.objects.create_user(
        username="lonely.manager", password=PASSWORD, role=Role.CENTER_MANAGER
    )

    with pytest.raises(CommandError, match="الموظف المالي"):
        _run()


def test_it_attaches_the_agreement_and_raises_an_approved_claim(
    monkeypatch, seed_actor, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    The whole point: a cohort that carries an agreement, and a claim built from
    what was actually collected — through ``cohort_service`` and
    ``claim_service``, never the ORM.
    """
    from apps.settlements.management.commands import seed_settlements_demo

    # A cohort the command has to ATTACH, not one that arrives attached.
    cohort_with_agreement.agreement = None
    cohort_with_agreement.save(update_fields=["agreement"])
    make_paid_enrollment(cohort_with_agreement, index=1)
    make_paid_enrollment(cohort_with_agreement, index=2)

    monkeypatch.setattr(
        seed_settlements_demo,
        "PAIRINGS",
        ((cohort_with_agreement.code, percent_agreement.agreement_number, "اختبار"),),
    )

    output = _run()
    cohort_with_agreement.refresh_from_db()

    assert cohort_with_agreement.agreement_id == percent_agreement.pk
    claim = PartnerClaim.objects.get()
    assert claim.cohort_id == cohort_with_agreement.pk
    assert claim.status == "APPROVED"
    assert claim.lines.count() == 2, "both paying participants are on the claim"
    assert claim.net_payable > 0, "50% of what two participants paid is not nil"
    assert "مطالبة" in output


def test_running_it_twice_changes_nothing(
    monkeypatch, seed_actor, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """A seed that doubles its own rows cannot be run on a live demo."""
    from apps.settlements.management.commands import seed_settlements_demo

    cohort_with_agreement.agreement = None
    cohort_with_agreement.save(update_fields=["agreement"])
    make_paid_enrollment(cohort_with_agreement, index=3)
    monkeypatch.setattr(
        seed_settlements_demo,
        "PAIRINGS",
        ((cohort_with_agreement.code, percent_agreement.agreement_number, "اختبار"),),
    )

    _run()
    claims, settlements = PartnerClaim.objects.count(), PartnerSettlement.objects.count()
    second = _run()

    assert PartnerClaim.objects.count() == claims
    assert PartnerSettlement.objects.count() == settlements
    assert "رُفعت 0" in second and "وُجدت 1" in second


def test_a_cohort_nobody_paid_on_still_gets_its_claim_and_no_settlement(
    monkeypatch, seed_actor, cohort_with_agreement, percent_agreement
) -> None:
    """
    §5.4 — a partner earns only on what the participant actually paid, so a
    percentage of nothing is nothing. The claim is still raised, because the
    partner is entitled to see that the period was considered; the settlement
    is not, because a cycle with nothing owed is a document that says nothing.
    """
    from apps.settlements.management.commands import seed_settlements_demo

    cohort_with_agreement.agreement = None
    cohort_with_agreement.save(update_fields=["agreement"])
    monkeypatch.setattr(
        seed_settlements_demo,
        "PAIRINGS",
        ((cohort_with_agreement.code, percent_agreement.agreement_number, "اختبار"),),
    )

    _run()

    assert PartnerClaim.objects.get().net_payable == 0
    assert PartnerSettlement.objects.count() == 0


def test_the_pairings_name_the_demo_codes_the_survey_found(seed_actor) -> None:
    """
    The table is the command's whole configuration, and it is checked here so a
    rename in the demo seeds cannot leave it pointing at nothing in silence.
    """
    from apps.settlements.management.commands.seed_settlements_demo import PAIRINGS

    assert {cohort for cohort, _agreement, _why in PAIRINGS} == {"CO-DIP-ID-1", "CO-QA-1"}
    assert {agreement for _cohort, agreement, _why in PAIRINGS} == {
        "TNG-2026/14",
        "SRH-2026/03",
    }
    assert all(why for _cohort, _agreement, why in PAIRINGS), "every pairing says why"


def test_the_command_is_registered_where_the_operator_will_look_for_it() -> None:
    """A seed nobody can find is a seed nobody runs."""
    from django.core.management import get_commands

    assert get_commands()["seed_settlements_demo"] == "apps.settlements"


def test_the_period_never_runs_past_today(
    monkeypatch, seed_actor, cohort_with_agreement, percent_agreement
) -> None:
    """
    A claim covering days that have not happened yet would invite a second
    claim over the same period once they do.
    """
    from apps.settlements.management.commands import seed_settlements_demo

    cohort_with_agreement.agreement = None
    cohort_with_agreement.save(update_fields=["agreement"])
    monkeypatch.setattr(
        seed_settlements_demo,
        "PAIRINGS",
        ((cohort_with_agreement.code, percent_agreement.agreement_number, "اختبار"),),
    )

    _run()

    claim = PartnerClaim.objects.get()
    assert claim.period_to <= date.today()
    assert claim.period_from == cohort_with_agreement.starts_on
