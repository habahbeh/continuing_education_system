"""
The daily closing after its UX pass — «مطابقة المقبوض بالوصولات» (§5.2).

The queue of cashier-days still open, the live preview of the system's side
(total, split by method, receipts without a voucher number), the closing
that opens clean only when the vouchers match and every receipt is vouched,
the approval through a dialog by someone other than the cashier, and the
paper record (report 6).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.cashbox.services import closing_service, payment_service

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 20)


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def a_day(make_enrollment, cashier, cash_method):
    """Two vouched receipts and one without a voucher, one cashier, one day."""
    enrollment, _ = make_enrollment()
    receipts = []
    for amount, ref in (("100.000", "FIN-1"), ("70.000", "FIN-2"), ("50.000", "")):
        receipts.append(
            payment_service.take_payment(
                actor=cashier,
                enrollment=enrollment,
                amount=Decimal(amount),
                payment_method=cash_method,
                received_on=DAY,
                external_receipt_ref=ref,
            )
        )
    return receipts


def test_the_queue_names_every_open_cashier_day(signed_in, finance, cashier, a_day) -> None:
    days = closing_service.open_days(actor=finance)
    assert [(d["cashier_id"], d["closing_date"], d["total"], d["count"]) for d in days] == [
        (cashier.pk, DAY, Decimal("220.000"), 3)
    ]

    response = signed_in(finance).get(reverse("cashbox:closing"))
    page = response.content.decode("utf-8")
    tiles = {t["key"]: t["value"] for t in response.context["tiles"]}
    assert tiles["open"] == 1 and tiles["pending"] == 0
    assert "أقفل هذا اليوم" in page
    assert f"?cashier={cashier.pk}&amp;on={DAY.isoformat()}" in page


def test_the_preview_shows_the_systems_side_and_the_unvouched(
    signed_in, finance, cashier, a_day
) -> None:
    response = signed_in(finance).get(
        reverse("cashbox:closing-preview"), {"cashier": cashier.pk, "on": DAY.isoformat()}
    )
    page = response.content.decode("utf-8")
    summary = response.context["summary"]

    assert summary["total"] == Decimal("220.000") and summary["count"] == 3
    assert summary["unvouched_count"] == 1 and summary["unvouched_total"] == Decimal("50.000")
    assert summary["vouched_total"] == Decimal("170.000")
    assert 'data-total="220.000"' in page
    assert "بلا رقم سند من الدائرة المالية" in page
    assert a_day[2].internal_receipt_number in page

    blank = signed_in(finance).get(reverse("cashbox:closing-preview"))
    assert blank.context["summary"] is None


def test_an_unvouched_receipt_keeps_the_closing_pending_until_explained(
    finance, cashier, a_day
) -> None:
    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("220.000")
    )
    assert closing.variance == 0
    assert closing.unvouched_count == 1
    assert closing.status == "VARIANCE_PENDING"
    assert closing.opened_by == finance

    with pytest.raises(ValidationError, match="بلا رقم سند"):
        closing_service.reconcile(actor=finance, closing=closing)
    closing_service.reconcile(
        actor=finance, closing=closing, variance_resolution_ar="الوصل الثالث وصل صباح الغد"
    )
    closing.refresh_from_db()
    assert closing.status == "RECONCILED"


def test_a_fully_vouched_matching_day_opens_clean(finance, cashier, a_day) -> None:
    a_day[2].external_receipt_ref = "FIN-3"
    a_day[2].save(update_fields=["external_receipt_ref"])

    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("220.000")
    )
    assert closing.status == "OPEN" and closing.unvouched_count == 0
    closing_service.reconcile(actor=finance, closing=closing)  # no resolution needed


def test_the_setting_can_make_vouchers_informational(finance, cashier, a_day) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    today = timezone.localdate()
    close_setting("closing_requires_vouchers", effective_to=today - timedelta(days=1))
    set_setting(
        "closing_requires_vouchers",
        False,
        value_type=SettingValueType.BOOLEAN,
        effective_from=today,
        note="test",
    )
    assert closing_service.vouchers_required(as_of=today) is False

    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("220.000")
    )
    assert closing.unvouched_count == 1
    # DAY is 2026-09-20 — the setting is read on the closing's own date.
    assert closing.status == ("OPEN" if today <= DAY else "VARIANCE_PENDING")


def test_a_day_cannot_be_closed_twice_nor_empty(finance, cashier, a_day) -> None:
    closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("220.000")
    )
    with pytest.raises(ValidationError, match="BR-026"):
        closing_service.open_closing(
            actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("1")
        )
    with pytest.raises(ValidationError, match="لا سندات"):
        closing_service.open_closing(
            actor=finance,
            cashier=cashier,
            closing_date=date(2026, 1, 1),
            counted_total=Decimal("0"),
        )


def test_the_screen_approves_through_a_dialog_by_other_hands(
    signed_in, finance, manager, cashier, a_day
) -> None:
    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("200.000")
    )
    url = reverse("cashbox:closing")

    page = signed_in(manager).get(url).content.decode("utf-8")
    assert f'id="rc-{closing.code}"' in page and "confirm(" not in page
    assert "عجز" in page and "بلا وصل" in page

    # The cashier of the day sees no approve control, only why.
    page = signed_in(cashier).get(url).content.decode("utf-8")
    assert f'id="rc-{closing.code}"' not in page and "BR-028" in page

    response = signed_in(manager).post(
        url,
        {"action": "reconcile", "code": closing.code, "variance_resolution_ar": "عجز 20 مسجَّل"},
        follow=True,
    )
    closing.refresh_from_db()
    assert closing.status == "RECONCILED" and closing.approved_by == manager
    assert "اعتُمد الإقفال" in response.content.decode("utf-8")


def test_the_form_arrives_prefilled_from_the_queue_and_defaults_to_today(
    signed_in, finance, cashier, a_day
) -> None:
    # A bare visit starts on the newest open day — the fixture's — not blank.
    response = signed_in(finance).get(reverse("cashbox:closing"))
    assert response.context["form"].initial["closing_date"] == DAY
    assert response.context["form"].initial["cashier_id"] == str(cashier.pk)

    response = signed_in(finance).get(
        reverse("cashbox:closing"), {"cashier": cashier.pk, "on": DAY.isoformat()}
    )
    assert response.context["form"].initial["cashier_id"] == str(cashier.pk)
    assert response.context["form"].initial["closing_date"] == DAY
    assert f'value="{DAY.isoformat()}"' in response.content.decode("utf-8")


def test_the_paper_record_lists_the_day_and_marks_the_unvouched(
    signed_in, finance, cashier, a_day
) -> None:
    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("220.000")
    )

    response = signed_in(finance).get(reverse("cashbox:closing-print", args=[closing.code]))
    page = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "محضر الإقفال اليومي" in page
    for r in a_day:
        assert r.internal_receipt_number in page
    assert page.count("بلا وصل") >= 2  # the count line and the row mark
    assert "FIN-1" in page and "220.000" in page
    assert (
        signed_in(finance).get(reverse("cashbox:closing-print", args=["CL-NOPE"])).status_code
        == 404
    )


def test_cashiers_are_whoever_took_money(finance, cashier, a_day) -> None:
    """The finance officer who took a payment can have their day closed too."""
    choices = dict(closing_service.cashier_choices())
    assert str(cashier.pk) in choices
    assert str(finance.pk) not in choices  # no receipts yet

    enrollment = a_day[0].allocations.first().enrollment
    payment_service.take_payment(
        actor=finance,
        enrollment=enrollment,
        amount=Decimal("5.000"),
        payment_method=a_day[0].payment_method,
        received_on=DAY,
    )
    assert str(finance.pk) in dict(closing_service.cashier_choices())


def test_a_backlog_is_closed_one_day_at_a_time_without_hunting(
    signed_in, finance, cashier, a_day, make_enrollment, cash_method
) -> None:
    """After one closing the form lands on the same cashier's next open day."""
    enrollment = a_day[0].allocations.first().enrollment
    earlier = date(2026, 9, 19)
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("10.000"),
        payment_method=cash_method,
        received_on=earlier,
        external_receipt_ref="FIN-9",
    )

    response = signed_in(finance).post(
        reverse("cashbox:closing"),
        {
            "action": "open",
            "cashier_id": cashier.pk,
            "closing_date": DAY.isoformat(),
            "counted_total": "220.000",
        },
    )
    assert response.status_code == 302
    assert (
        f"cashier={cashier.pk}" in response["Location"]
        and earlier.isoformat() in response["Location"]
    )

    response = signed_in(finance).post(
        reverse("cashbox:closing"),
        {
            "action": "open",
            "cashier_id": cashier.pk,
            "closing_date": earlier.isoformat(),
            "counted_total": "10.000",
        },
    )
    assert response["Location"].endswith(reverse("cashbox:closing"))  # nothing left


def test_the_dashboard_warns_about_a_till_left_open(signed_in, finance, a_day) -> None:
    from django.utils import timezone

    response = signed_in(finance).get(reverse("operations:dashboard"))
    page = response.content.decode("utf-8")
    queue = next(q for q in response.context["queues"] if q["key"] == "open_days")

    assert queue["count"] == 1
    assert "أيام صندوق بلا إقفال" in page
    age = (timezone.localdate() - DAY).days
    if age >= 1:
        assert response.context["alerts"] and "بلا إقفال" in response.context["alerts"][0]["title"]
    else:
        assert response.context["alerts"] == []
