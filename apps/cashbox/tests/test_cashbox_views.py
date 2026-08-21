"""
Cashbox screens — the till, and the two controls around it.

The screens under test carry money in and close the day on it, so what is
proved here is that the SERVICE still decides: BR-020's floor refuses a short
first payment through the form as surely as through a direct call, and
BR-028 keeps the cashier away from certifying their own count no matter which
button they press.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def enrolled(make_enrollment):
    """An SC-NET enrolment with its charge lines — 20 registration + 250 tuition."""
    enrollment, quote = make_enrollment()
    return enrollment, quote


@pytest.fixture
def diploma_enrolled(make_enrollment):
    """A diploma enrolment, where BR-020's 400-dinar floor applies."""
    enrollment, quote = make_enrollment(program_code="DIP-ID", category="CENTER")
    return enrollment, quote


# ---------------------------------------------------------------------------
# Taking payment
# ---------------------------------------------------------------------------
def test_a_payment_issues_a_receipt_and_allocates_it(
    signed_in, cashier, enrolled, cash_method
) -> None:
    enrollment, quote = enrolled
    total = quote.course_fee + (quote.registration_fee or Decimal("0.000"))

    response = signed_in(cashier).post(
        reverse("cashbox:payment-new"),
        {
            "enrollment_code": enrollment.code,
            "amount": str(total),
            "payment_method": cash_method.code,
            "received_on": "2026-09-20",
            "external_receipt_ref": "FIN-9001",
            "breakdown_text_ar": "",
        },
        follow=True,
    )
    receipt = response.context["receipt"]
    assert receipt["amount"] == total
    assert receipt["external_receipt_ref"] == "FIN-9001"
    assert receipt["allocations"]


def test_the_minimum_first_payment_is_refused_on_screen(
    signed_in, cashier, diploma_enrolled, cash_method
) -> None:
    """
    BR-020 — «أقل دفعة أولى للدبلوم 400 (300 تسجيل + 100 أول مادة)».

    The rule is the service's; the screen's job is to repeat its message,
    reference and all, so the cashier knows what to ask for.
    """
    enrollment, _ = diploma_enrolled

    response = signed_in(cashier).post(
        reverse("cashbox:payment-new"),
        {
            "enrollment_code": enrollment.code,
            "amount": "300.000",
            "payment_method": cash_method.code,
            "received_on": "2026-09-20",
            "external_receipt_ref": "",
            "breakdown_text_ar": "",
        },
        follow=True,
    )
    assert "BR-020" in response.content.decode()


def test_the_centre_manager_may_not_take_money(signed_in, enrolled, cash_method, db) -> None:
    """
    D-01 · BR-081 — «مدير المركز: لا يقبض نقداً».

    Refused on the POST, not merely absent from the menu.
    """
    from apps.people.models import Role, User

    enrollment, _ = enrolled
    manager = User.objects.create_user(
        username="mgr.cash.ui", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    response = signed_in(manager).post(
        reverse("cashbox:payment-new"),
        {
            "enrollment_code": enrollment.code,
            "amount": "100.000",
            "payment_method": cash_method.code,
            "received_on": "2026-09-20",
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# The receipt and its void
# ---------------------------------------------------------------------------
def test_a_void_takes_a_request_then_a_different_approver(
    signed_in, cashier, finance, enrolled, cash_method
) -> None:
    """BR-025 — and the original row survives either way."""
    from apps.cashbox.services import payment_service

    enrollment, quote = enrolled
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    url = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])

    # Δ-06 — the cashier asks and the finance officer decides. One person
    # doing both would make the second signature decorative.
    signed_in(cashier).post(url, {"action": "request", "reason_ar": "قُبض مرتين"}, follow=True)
    page = signed_in(cashier).get(url)
    assert page.context["receipt"]["void_reason_ar"] == "قُبض مرتين"
    assert page.context["receipt"]["void_is_approved"] is False

    signed_in(finance).post(url, {"action": "approve"}, follow=True)
    page = signed_in(finance).get(url)
    assert page.context["receipt"]["void_is_approved"] is True


# ---------------------------------------------------------------------------
# Daily closing
# ---------------------------------------------------------------------------
def test_a_closing_shows_the_variance_it_found(
    signed_in, cashier, finance, enrolled, cash_method
) -> None:
    """
    BR-027 — the difference is shown, never absorbed.

    250 taken and 240 counted is a ten-dinar hole, and the screen says so
    before anyone decides what to do about it.
    """
    from apps.cashbox.services import payment_service

    enrollment, quote = enrolled
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=TERM_START,
    )

    response = signed_in(finance).post(
        reverse("cashbox:closing"),
        {
            "action": "open",
            "cashier_id": str(cashier.pk),
            "closing_date": "2026-09-20",
            "counted_total": str(quote.course_fee - Decimal("10.000")),
        },
        follow=True,
    )
    row = response.context["closings"][0]
    assert row["system_total"] == quote.course_fee
    assert row["variance"] == Decimal("-10.000")


def test_whoever_held_the_cash_cannot_certify_the_count(
    signed_in, cashier, finance, enrolled, cash_method
) -> None:
    """
    BR-028 — «لا يجوز لأمين الصندوق اعتماد إقفال يومه».

    A constraint at the database and a message on the screen; this proves the
    message arrives.
    """
    from apps.cashbox.services import closing_service, payment_service

    # The finance officer may take payment AND certify a count, so they are
    # the only role that can reach BR-028 at all — the cashier is kept out of
    # certification by the matrix long before the rule is consulted.
    enrollment, quote = enrolled
    payment_service.take_payment(
        actor=finance,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    closing = closing_service.open_closing(
        actor=finance,
        cashier=finance,
        closing_date=TERM_START,
        counted_total=quote.course_fee,
        request=None,
    )

    response = signed_in(finance).post(
        reverse("cashbox:closing"),
        {"action": "reconcile", "code": closing.code, "variance_resolution_ar": ""},
        follow=True,
    )
    assert "BR-028" in response.content.decode()


def test_closing_a_variance_without_a_resolution_is_refused(
    signed_in, cashier, finance, enrolled, cash_method
) -> None:
    """BR-027 — a variance may be closed, but never silently."""
    from apps.cashbox.services import closing_service, payment_service

    enrollment, quote = enrolled
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    closing = closing_service.open_closing(
        actor=finance,
        cashier=cashier,
        closing_date=TERM_START,
        counted_total=quote.course_fee - Decimal("5.000"),
        request=None,
    )

    response = signed_in(finance).post(
        reverse("cashbox:closing"),
        {"action": "reconcile", "code": closing.code, "variance_resolution_ar": ""},
        follow=True,
    )
    assert "BR-027" in response.content.decode()
