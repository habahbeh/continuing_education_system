"""
The participant file's operational read (Sprint 8I) — three promises.

1. The hub adds up what ``get_account_state`` already says per enrolment and
   names the next step; it computes no money of its own.
2. Its blocks follow the screens' own permissions: a cashier — narrowed by
   BR-101 on the participant — gets no enrolment block here either, because
   the enrolments screen does not admit that role; a finance manager does,
   and still receives no personal field.
3. The enrolments list narrows to one participant exactly by number.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.operations.services import enrollment_service
from apps.operations.services.participant_hub_service import latest_enrollments, participant_hub
from apps.people.services import participant_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.operations.tests.conftest import _user
    from apps.people.models import Role

    return _user("hub.finance.manager", Role.FINANCE_MANAGER)


@pytest.fixture
def approved_cohort(make_cohort, approve_cohort):
    cohort = make_cohort("SC-NET", code="CO-HUB")
    approve_cohort(cohort, course_number="M-HUB")
    return cohort


def test_the_hub_sums_the_rows_and_points_at_the_open_balance(
    approved_cohort, make_enrollment, charge_and_pay, registrar, manager
) -> None:
    enrollment = make_enrollment(approved_cohort, index=1)
    charge_and_pay(enrollment, amount="100.000")  # 270 charged, 170 owed
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    number = enrollment.participant.participant_number

    hub = participant_hub(actor=manager, participant_number=number)

    assert hub["totals"]["total_due"] == Decimal("270.000")
    assert hub["totals"]["total_paid"] == Decimal("100.000")
    assert hub["totals"]["balance"] == Decimal("170.000")
    assert hub["totals"]["participant_owes"] is True
    assert hub["latest"]["code"] == enrollment.code
    assert hub["next_action"]["goto"] == "account"
    assert hub["next_action"]["arg"] == enrollment.code
    assert "170.000" in hub["next_action"]["text"]
    assert hub["clearances"] == [] and hub["certificates"] == []
    # The money behind it, item by item: the one receipt, nothing else yet.
    assert [r["amount"] for r in hub["receipts"]] == [Decimal("100.000")]
    assert hub["receipts"][0]["participant_number"] == number
    assert hub["extra_fees"] == [] and hub["discounts"] == [] and hub["refunds"] == []
    assert hub["transfers"] == []
    assert hub["totals"]["receipts_count"] == 1
    assert hub["totals"]["extra_fees_total"] == Decimal("0.000")


def test_the_hub_follows_each_screens_permission(
    approved_cohort, make_enrollment, charge_and_pay, cashier, finance_manager
) -> None:
    enrollment = make_enrollment(approved_cohort, index=2)
    charge_and_pay(enrollment)
    number = enrollment.participant.participant_number

    # Cashier: may open the participant (number + name) but not the
    # enrolments screen — so no enrolment block, no totals, no next step.
    hub = participant_hub(actor=cashier, participant_number=number)
    assert "enrollments" not in hub and "next_action" not in hub
    assert latest_enrollments(actor=cashier, participant_numbers=[number]) == {}

    # Finance manager: BR-101 narrows the participant, not the enrolments.
    hub = participant_hub(actor=finance_manager, participant_number=number)
    assert [row["code"] for row in hub["enrollments"]] == [enrollment.code]
    assert hub["totals"]["is_settled"] is True
    # ...and the matrix decides the money blocks: receipts yes (PAYMENTS V),
    # extra fees / discounts / refunds / transfers no — those screens do not
    # admit this role, so the file does not either.
    assert len(hub["receipts"]) == 1
    for key in ("extra_fees", "discounts", "refunds", "transfers"):
        assert key not in hub, key
    assert (
        latest_enrollments(actor=finance_manager, participant_numbers=[number])[number]["code"]
        == enrollment.code
    )
    # ...and the participant projection is still the narrow one.
    assert set(
        participant_service.get_participant(actor=finance_manager, participant_number=number)
    ) == {
        "participant_number",
        "name_ar",
    }


def test_the_list_counts_enrolments_and_the_enrolments_screen_filters_by_number(
    client, approved_cohort, make_enrollment, registrar
) -> None:
    first = make_enrollment(approved_cohort, index=3)
    other = make_enrollment(approved_cohort, index=4)

    rows = {
        row["participant_number"]: row
        for row in participant_service.list_participants(actor=registrar)
    }
    assert rows[first.participant.participant_number]["enrollment_count"] == 1
    assert rows[other.participant.participant_number]["enrollment_count"] == 1

    client.force_login(registrar)
    response = client.get(
        reverse("operations:enrollments"), {"participant": first.participant.participant_number}
    )
    codes = [row["code"] for row in response.context["enrollments"]]
    assert codes == [first.code]
    assert (
        "المشارك",
        first.participant.name_ar,
    ) in response.context["active_filters"]
