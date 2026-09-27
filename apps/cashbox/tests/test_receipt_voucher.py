"""
Completing a receipt with the finance department's voucher number (§5.2).

The till issues the receipt before the paper voucher is always in hand, and
the closing lists it as «بلا وصل» until the number is written. Writing it is
a completion, never an edit: once, on an issued receipt, before its day is
approved — and the pending closing follows the receipt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.cashbox.models import DailyClosing, Receipt
from apps.cashbox.services import closing_service, payment_service
from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 20)


@pytest.fixture
def enrollment(make_enrollment):
    return make_enrollment()[0]


@pytest.fixture
def unvouched(enrollment, cashier, cash_method):
    return payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=DAY,
    )


def _receipt(number: str) -> Receipt:
    return Receipt.objects.get(internal_receipt_number=number)


def test_the_number_is_written_once_and_audited(cashier, unvouched) -> None:
    payment_service.record_external_ref(
        actor=cashier, receipt=unvouched, external_receipt_ref=" FIN-77 "
    )
    fresh = _receipt(unvouched.internal_receipt_number)
    assert fresh.external_receipt_ref == "FIN-77" and fresh.external_ref_key == "FIN-77"
    assert AuditEvent.objects.filter(
        reference=unvouched.internal_receipt_number, summary_ar__contains="FIN-77"
    ).exists()

    with pytest.raises(ValidationError, match="بالفعل"):
        payment_service.record_external_ref(
            actor=cashier, receipt=fresh, external_receipt_ref="FIN-78"
        )
    assert not payment_service.voucher_addable(fresh)


def test_a_number_already_on_another_receipt_is_refused(
    cashier, unvouched, enrollment, cash_method
) -> None:
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("10.000"),
        payment_method=cash_method,
        received_on=DAY,
        external_receipt_ref="FIN-1",
    )
    with pytest.raises(ValidationError, match="مسجَّل على السند"):
        payment_service.record_external_ref(
            actor=cashier, receipt=unvouched, external_receipt_ref="FIN-1"
        )


def test_a_pending_closing_follows_the_receipt(cashier, finance, unvouched) -> None:
    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("50.000")
    )
    assert closing.status == "VARIANCE_PENDING" and closing.unvouched_count == 1

    payment_service.record_external_ref(
        actor=cashier, receipt=unvouched, external_receipt_ref="FIN-9"
    )
    closing = DailyClosing.objects.get(pk=closing.pk)
    assert closing.status == "OPEN" and closing.unvouched_count == 0


def test_an_approved_day_is_never_touched(cashier, finance, unvouched) -> None:
    closing = closing_service.open_closing(
        actor=finance, cashier=cashier, closing_date=DAY, counted_total=Decimal("50.000")
    )
    closing_service.reconcile(
        actor=finance, closing=closing, variance_resolution_ar="الوصل ضاع؛ بموافقة المدير"
    )
    with pytest.raises(ValidationError, match="BR-026"):
        payment_service.record_external_ref(
            actor=cashier, receipt=_receipt(unvouched.internal_receipt_number), external_receipt_ref="FIN-9"
        )
    assert not payment_service.voucher_addable(_receipt(unvouched.internal_receipt_number))


def test_the_receipt_page_offers_the_dialog_then_shows_the_number(
    client, cashier, unvouched
) -> None:
    client.force_login(cashier)
    url = reverse("cashbox:receipt-detail", args=[unvouched.internal_receipt_number])
    page = client.get(url).content.decode("utf-8")
    assert 'id="voucher-add"' in page and "تسجيل رقم الوصل…" in page
    assert "بلا وصل" in page

    response = client.post(url, {"action": "voucher", "external_receipt_ref": "FIN-5"})
    assert response.status_code == 302
    page = client.get(url).content.decode("utf-8")
    assert "FIN-5" in page and 'id="voucher-add"' not in page


def test_the_dialog_reopens_on_an_empty_number(client, cashier, unvouched) -> None:
    client.force_login(cashier)
    url = reverse("cashbox:receipt-detail", args=[unvouched.internal_receipt_number])
    response = client.post(url, {"action": "voucher", "external_receipt_ref": "  "})
    assert response.status_code == 200
    assert "$el.showModal()" in response.content.decode("utf-8")
    assert _receipt(unvouched.internal_receipt_number).external_receipt_ref == ""


def test_the_manager_who_never_takes_cash_sees_no_button(client, manager, unvouched) -> None:
    client.force_login(manager)
    url = reverse("cashbox:receipt-detail", args=[unvouched.internal_receipt_number])
    page = client.get(url).content.decode("utf-8")
    assert "يُستكمل من الصندوق" in page and 'id="voucher-add"' not in page
    assert client.post(url, {"action": "voucher", "external_receipt_ref": "X"}).status_code == 403


# --- §5.2 in force: the till refuses a receipt without the voucher number ---


def test_with_the_rule_on_the_service_refuses_a_bare_receipt(
    voucher_rule, enrollment, cashier, cash_method
) -> None:
    with pytest.raises(ValidationError, match="لا يصدر سند بلا رقم سند الدائرة المالية"):
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal("50.000"),
            payment_method=cash_method,
            received_on=DAY,
        )
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=DAY,
        external_receipt_ref="FIN-1",
    )
    assert receipt.external_receipt_ref == "FIN-1"


def test_with_the_rule_on_the_till_shows_the_voucher_as_a_required_step(
    voucher_rule, client, cashier, enrollment
) -> None:
    client.force_login(cashier)
    url = reverse("cashbox:payment-new") + f"?enrollment={enrollment.code}"
    page = client.get(url).content.decode("utf-8")
    assert "وصل الدائرة المالية" in page
    assert 'name="external_receipt_ref" maxlength="32" dir="ltr" required' in page
    assert "!ref.trim()" in page
    assert "بلا (اختياري)" not in page

    response = client.post(
        url,
        {
            "participant_number": enrollment.participant.participant_number,
            "enrollment_code": enrollment.code,
            "amount": "50.000",
            "payment_method": "CASH",
            "external_receipt_ref": "",
        },
    )
    assert response.status_code == 200
    assert not Receipt.objects.exists()
    assert "has-error" in response.content.decode("utf-8")


def test_the_seed_ships_the_rule_on(db) -> None:
    from django.core.management import call_command

    from apps.core.models import EffectiveSetting

    call_command("seed_settings", verbosity=0)
    assert EffectiveSetting.objects.get(key="external_receipt_required").value == "True"
