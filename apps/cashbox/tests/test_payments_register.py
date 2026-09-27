"""
The receipts register after its UX pass: it answers «كم قبضت اليوم؟» first.

A bare visit opens on today; a link that names a search or a status opens the
whole register. The cards at the top are the totals and the status filter in
one; the range buttons, from–to and the method narrow the rest; the footer
sums the issued money by method; long registers page.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.cashbox.services import payment_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def three_days(make_enrollment, cashier, cash_method):
    """Receipts today, yesterday and last week — one by card."""
    from apps.cashbox.models import PaymentMethod

    visa = PaymentMethod.objects.create(code="VISA", name_ar="فيزا")
    today = timezone.localdate()
    enrollment, _ = make_enrollment(program_code="DIP-ID", category="CENTER")
    rows = []
    for offset, amount, method in (
        (0, "400.000", cash_method),
        (1, "50.000", visa),
        (8, "30.000", cash_method),
    ):
        rows.append(
            payment_service.take_payment(
                actor=cashier,
                enrollment=enrollment,
                amount=Decimal(amount),
                payment_method=method,
                received_on=today - timedelta(days=offset),
            )
        )
    return rows


def test_a_bare_visit_opens_on_today(signed_in, cashier, three_days) -> None:
    today_receipt, *_ = three_days

    response = signed_in(cashier).get(reverse("cashbox:payments"))

    assert response.context["range_key"] == "today"
    assert [r["internal_receipt_number"] for r in response.context["receipts"]] == [
        today_receipt.internal_receipt_number
    ]
    assert response.context["summary"]["total"] == Decimal("400.000")
    assert response.context["is_filtered"] is False


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"range": "yesterday"}, 1),
        ({"range": "week"}, 2),
        ({"range": "all"}, 3),
        ({"q": "R-"}, 3),  # a named search opens the whole register
        ({"method": "VISA", "range": "all"}, 1),
        ({"from": "2000-01-01", "to": "2100-01-01"}, 3),
    ],
)
def test_the_ranges_and_filters_narrow_as_named(
    signed_in, cashier, three_days, params: dict, expected: int
) -> None:
    response = signed_in(cashier).get(reverse("cashbox:payments"), params)
    assert len(response.context["receipts"]) == expected, params


def test_the_cards_count_the_conditions_and_filter_on_click(
    signed_in, cashier, finance, three_days
) -> None:
    _, yesterday_receipt, _ = three_days
    payment_service.request_void(actor=cashier, receipt=yesterday_receipt, reason_ar="خطأ")

    response = signed_in(finance).get(reverse("cashbox:payments"), {"range": "all"})
    tiles = {t["key"]: t for t in response.context["tiles"]}

    assert tiles[""]["value"] == Decimal("480.000")
    assert tiles["issued"]["value"] == 3
    assert tiles["unclosed"]["value"] == 3
    assert tiles["void_pending"]["value"] == 1
    assert tiles["voided"]["value"] == 0
    assert tiles["void_pending"]["url"].endswith("status=void_pending")

    narrowed = signed_in(finance).get(tiles["void_pending"]["url"])
    assert [r["internal_receipt_number"] for r in narrowed.context["receipts"]] == [
        yesterday_receipt.internal_receipt_number
    ]
    # Clicking the lit card again widens.
    lit = {t["key"]: t for t in narrowed.context["tiles"]}["void_pending"]
    assert lit["on"] is True and "status=" not in lit["url"]


def test_the_search_reaches_number_name_participant_phone_and_reference(
    signed_in, cashier, three_days
) -> None:
    receipt = three_days[0]
    person = receipt.participant
    person.phone = "0790001111"
    person.save(update_fields=["phone"])
    receipt.external_receipt_ref = "FIN-77"
    receipt.save(update_fields=["external_receipt_ref"])

    for needle in (
        receipt.internal_receipt_number,
        person.name_ar[:4],
        person.participant_number[:5],
        "07900",
        "FIN-77",
    ):
        response = signed_in(cashier).get(reverse("cashbox:payments"), {"q": needle})
        numbers = [r["internal_receipt_number"] for r in response.context["receipts"]]
        assert receipt.internal_receipt_number in numbers, needle


def test_the_footer_sums_issued_money_by_method_and_strikes_the_voided(
    signed_in, cashier, finance, three_days
) -> None:
    _, yesterday_receipt, _ = three_days
    payment_service.request_void(actor=cashier, receipt=yesterday_receipt, reason_ar="خطأ")
    payment_service.approve_void(
        actor=finance,
        void_record=payment_service.void_instance(
            actor=finance, number=yesterday_receipt.internal_receipt_number
        ),
    )

    response = signed_in(finance).get(reverse("cashbox:payments"), {"range": "all"})
    page = response.content.decode("utf-8")
    summary = response.context["summary"]

    assert summary["total"] == Decimal("430.000")
    assert summary["voided"] == 1
    assert [(m["code"], m["amount"]) for m in summary["by_method"]] == [
        ("CASH", Decimal("430.000"))
    ]
    assert 'class="is-void"' in page
    assert "إجمالي المقبوض في النطاق" in page


def test_a_long_register_pages_and_keeps_its_filters(
    signed_in, cashier, make_enrollment, cash_method
) -> None:
    from apps.cashbox.views import PAGE_SIZE

    enrollment, _ = make_enrollment()  # a course: no first-payment floor
    for _ in range(PAGE_SIZE + 3):
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal("1.000"),
            payment_method=cash_method,
            received_on=date(2026, 9, 20),
        )

    first = signed_in(cashier).get(reverse("cashbox:payments"), {"range": "all", "method": "CASH"})
    assert first.context["page"]["pages"] == 2
    assert len(first.context["receipts"]) == PAGE_SIZE
    assert "page=2" in first.content.decode("utf-8")

    second = signed_in(cashier).get(
        reverse("cashbox:payments"), {"range": "all", "method": "CASH", "page": "2"}
    )
    assert len(second.context["receipts"]) == 3
    assert second.context["page"]["start"] == PAGE_SIZE + 1
    assert second.context["summary"]["count"] == PAGE_SIZE + 3  # the summary is of the whole set


def test_the_dashboard_counter_opens_the_register_on_today(signed_in, cashier, three_days) -> None:
    body = signed_in(cashier).get(reverse("operations:dashboard")).content.decode("utf-8")
    assert f"{reverse('cashbox:payments')}?range=today" in body
