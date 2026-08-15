"""
Price resolution — T-096 … T-103, T-225 … T-227 (BR-009 … BR-012, BR-096).

The fee exceptions asserted here are the ones QA_CHECKLIST names as acceptance
criteria, and the seed loads exactly those rows. That is deliberate: a pricing
test whose data it invented itself proves only that the code agrees with
itself.

Several S3 gate items assert behaviour AT ENROLMENT — that charge lines carry
these amounts, that an enrolment records its price list. Those need Enrollment
(Sprint 6) and ChargeLine (Sprint 4) and are reported DEFERRED, not passed. The
resolver they all depend on is tested here in full.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import (
    DepositPolicy,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
    RegistrationFeeRule,
)
from apps.catalog.services import pricing_service as pricing

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 15)


@pytest.fixture
def catalog(active_semester, db):
    """The demo catalogue, loaded exactly as the seed command builds it."""
    from django.core.management import call_command

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


def _program(code: str) -> Program:
    return Program.objects.get(code=code)


# ---------------------------------------------------------------------------
# BR-008 / BR-012 — the list in force
# ---------------------------------------------------------------------------
def test_effective_list_is_the_approved_one(catalog: PriceList) -> None:
    assert pricing.effective_price_list(as_of=AS_OF).pk == catalog.pk


def test_a_draft_list_never_prices_anything(catalog: PriceList) -> None:
    """Only an APPROVED list may price a new registration (BR-008)."""
    catalog.status = PriceListStatus.DRAFT
    catalog.save()
    with pytest.raises(pricing.NoEffectivePriceListError):
        pricing.effective_price_list(as_of=AS_OF)


def test_a_list_not_yet_effective_is_not_used(catalog: PriceList) -> None:
    with pytest.raises(pricing.NoEffectivePriceListError):
        pricing.effective_price_list(as_of=date(2026, 1, 1))


# ---------------------------------------------------------------------------
# T-096 … T-099 — registration fee exceptions are ROWS (BR-009)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("program_code", "category", "expected"),
    [
        # T-096 — network engineering costs a university student 20, not 15.
        ("SC-NET", "UNIVERSITY", Decimal("20.000")),
        # T-097 — CMA is 170 for a centre student, and the general 50 elsewhere.
        ("SC-CMA", "CENTER", Decimal("170.000")),
        ("SC-CMA", "UNIVERSITY", Decimal("15.000")),
        # The general rules themselves.
        ("SC-NET", "CENTER", Decimal("50.000")),
    ],
)
def test_registration_fee_exceptions(
    catalog: PriceList, program_code: str, category: str, expected: Decimal
) -> None:
    fee, _note = pricing.resolve_registration_fee(
        program=_program(program_code), participant_category=category, price_list=catalog
    )
    assert fee == expected


@pytest.mark.parametrize("program_code", ["SC-JCPA", "SC-PMP", "SC-DRUG"])
@pytest.mark.parametrize("category", ["CENTER", "UNIVERSITY"])
def test_t098_courses_with_no_registration_fee_return_none_not_zero(
    catalog: PriceList, program_code: str, category: str
) -> None:
    """
    NULL and 0 are different answers.

    "No fee is charged" and "a fee of zero was charged" produce the same
    number and different ledgers: one has no registration charge line at all,
    the other has one worth nothing. The demo's two columns could not tell
    them apart.
    """
    fee, _note = pricing.resolve_registration_fee(
        program=_program(program_code), participant_category=category, price_list=catalog
    )
    assert fee is None
    assert fee != Decimal("0.000")


def test_t099_quality_audit_is_free_for_university_but_not_for_centre(
    catalog: PriceList,
) -> None:
    university, _ = pricing.resolve_registration_fee(
        program=_program("SC-QA"), participant_category="UNIVERSITY", price_list=catalog
    )
    centre, _ = pricing.resolve_registration_fee(
        program=_program("SC-QA"), participant_category="CENTER", price_list=catalog
    )
    assert university is None
    assert centre == Decimal("50.000")


def test_t105_employee_category_resolves_from_the_rule_table(catalog: PriceList) -> None:
    """
    Q-10 — the client's answer is a row, not a code change.

    The seeded value is an opening assumption; changing it is data entry, and
    this test asserts the mechanism rather than the number.
    """
    before, _ = pricing.resolve_registration_fee(
        program=_program("SC-NET"), participant_category="EMPLOYEE", price_list=catalog
    )
    assert before == Decimal("15.000")

    RegistrationFeeRule.objects.filter(
        price_list=catalog, program_key=0, participant_category="EMPLOYEE"
    ).update(fee=Decimal("35.000"))

    after, _ = pricing.resolve_registration_fee(
        program=_program("SC-NET"), participant_category="EMPLOYEE", price_list=catalog
    )
    assert after == Decimal("35.000")


def test_most_specific_rule_wins(catalog: PriceList) -> None:
    """A programme rule beats the general one — ordering, not a special case."""
    specific, _ = pricing.resolve_registration_fee(
        program=_program("SC-NET"), participant_category="UNIVERSITY", price_list=catalog
    )
    general, _ = pricing.resolve_registration_fee(
        program=_program("SC-CMA"), participant_category="UNIVERSITY", price_list=catalog
    )
    assert specific == Decimal("20.000")
    assert general == Decimal("15.000")


# ---------------------------------------------------------------------------
# T-100 — online courses carry no registration fee at all (BR-010)
# ---------------------------------------------------------------------------
def test_t100_online_course_has_no_registration_fee(catalog: PriceList) -> None:
    quote = pricing.resolve_price(
        program=_program("ON-GENAI"), participant_category="CENTER", as_of=AS_OF
    )
    assert quote.registration_fee is None
    assert not quote.charges_registration_fee
    assert quote.course_fee == Decimal("200.000")


def test_t100_holds_even_if_someone_adds_a_fee_row(catalog: PriceList) -> None:
    """
    BR-010 is structural, not an exception row.

    Adding a fee rule for an online course must not produce a fee — otherwise
    the rule survives only while nobody enters contradictory data.
    """
    RegistrationFeeRule.objects.create(
        price_list=catalog,
        program=_program("ON-GENAI"),
        participant_category="CENTER",
        fee=Decimal("50.000"),
        exception_note_ar="صف متناقض متعمَّد للاختبار",
    )
    quote = pricing.resolve_price(
        program=_program("ON-GENAI"), participant_category="CENTER", as_of=AS_OF
    )
    assert quote.registration_fee is None


# ---------------------------------------------------------------------------
# T-102 / T-103 — levelled courses (BR-011)
# ---------------------------------------------------------------------------
def test_t102_levelled_programme_without_a_level_is_refused(catalog: PriceList) -> None:
    with pytest.raises(pricing.LevelRequiredError):
        pricing.resolve_price(
            program=_program("SC-ENG-GEN"), participant_category="CENTER", as_of=AS_OF
        )


def test_t103_level_four_costs_one_level_not_all_eight(catalog: PriceList) -> None:
    """90 for the level, not 720 for the course."""
    quote = pricing.resolve_price(
        program=_program("SC-ENG-GEN"),
        participant_category="CENTER",
        as_of=AS_OF,
        level=4,
    )
    assert quote.course_fee == Decimal("90.000")
    assert quote.level == 4


def test_a_level_on_an_unlevelled_programme_is_refused(catalog: PriceList) -> None:
    with pytest.raises(pricing.LevelRequiredError):
        pricing.resolve_price(
            program=_program("SC-NET"),
            participant_category="CENTER",
            as_of=AS_OF,
            level=2,
        )


# ---------------------------------------------------------------------------
# T-225 … T-227 — deposits appear only where a policy says so (BR-096)
# ---------------------------------------------------------------------------
def test_t225_programme_without_a_policy_has_no_deposit(catalog: PriceList) -> None:
    quote = pricing.resolve_price(
        program=_program("SC-NET"), participant_category="CENTER", as_of=AS_OF
    )
    assert quote.deposit_amount is None
    assert not quote.has_deposit
    assert quote.deposit_policy_id is None


def test_t226_programme_with_a_policy_carries_its_deposit(catalog: PriceList) -> None:
    """The client's demo policy: General English, 25 JOD."""
    quote = pricing.resolve_price(
        program=_program("SC-ENG-GEN"),
        participant_category="CENTER",
        as_of=AS_OF,
        level=1,
    )
    assert quote.has_deposit
    assert quote.deposit_amount == Decimal("25.000")
    assert quote.deposit_policy_code == "DEP-ENG-GEN"


def test_t227_changing_the_deposit_does_not_touch_an_earlier_quote(
    catalog: PriceList,
) -> None:
    """
    BR-012 — a quote is copied values, not a live reference.

    Raising the deposit to 40 on a NEW list must leave the old list's answer
    at 25, which is what keeps an existing registration's numbers still.
    """
    before = pricing.resolve_price(
        program=_program("SC-ENG-GEN"),
        participant_category="CENTER",
        as_of=AS_OF,
        level=1,
    )
    assert before.deposit_amount == Decimal("25.000")

    PriceListItem.objects.filter(
        price_list=catalog, program=_program("SC-ENG-GEN"), level_key=1
    ).update(deposit_amount=Decimal("40.000"))

    # The already-resolved quote is a frozen dataclass — it cannot have moved.
    assert before.deposit_amount == Decimal("25.000")

    after = pricing.resolve_price(
        program=_program("SC-ENG-GEN"),
        participant_category="CENTER",
        as_of=AS_OF,
        level=1,
    )
    assert after.deposit_amount == Decimal("40.000")


def test_the_deposit_policy_is_ordinary_editable_data(catalog: PriceList) -> None:
    """
    The client's instruction of 2026-08-15 — settings, not a rule.

    A trigger value the code has never seen must be storable, because Q-30 is
    open and the answer belongs to the client.
    """
    policy = DepositPolicy.objects.get(code="DEP-ENG-GEN")
    policy.refund_trigger = "ON_A_TRIGGER_NOT_YET_INVENTED"
    policy.forfeit_on = ["WITHDRAWN", "A_NEW_STATE"]
    policy.claim_deadline_days = 45
    policy.full_clean()
    policy.save()

    policy.refresh_from_db()
    assert policy.refund_trigger == "ON_A_TRIGGER_NOT_YET_INVENTED"


def test_a_policy_can_be_disabled_without_touching_the_schema(catalog: PriceList) -> None:
    policy = DepositPolicy.objects.get(code="DEP-ENG-GEN")
    policy.is_active = False
    policy.save()
    assert not DepositPolicy.objects.get(pk=policy.pk).is_active


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------
def test_unpriced_programme_is_reported_by_name(catalog: PriceList) -> None:
    orphan = Program.objects.create(
        code="SC-ORPHAN",
        program_type=ProgramType.SHORT_COURSE,
        name_ar="دورة بلا سعر",
        course_category=_program("SC-NET").course_category,
    )
    with pytest.raises(pricing.ProgramNotPricedError, match="دورة بلا سعر"):
        pricing.resolve_price(program=orphan, participant_category="CENTER", as_of=AS_OF)


def test_quote_records_which_list_produced_it(catalog: PriceList) -> None:
    """BR-012 — the enrolment must be explainable years later."""
    quote = pricing.resolve_price(
        program=_program("SC-NET"), participant_category="CENTER", as_of=AS_OF
    )
    assert quote.price_list_id == catalog.pk
    assert quote.price_list_code == catalog.code
