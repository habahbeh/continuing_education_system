"""
Billing screens — where Sprint 8A's rules meet a user.

The arithmetic these screens sit on is already pinned in the 8A suites. What
is proved HERE is that the screens neither restate a rule nor route around
one: the refusal that a service raises is the refusal the operator reads, and
a role without the permission is stopped on the POST rather than by a missing
button.
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
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.bill.ui", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def other_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr2.bill.ui", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def enrolled(make_enrollment):
    enrollment, quote = make_enrollment()
    return enrollment, quote


def _grant_payload(enrollment, amount="50.000"):
    return {
        "action": "grant",
        "enrollment_code": enrollment.code,
        "discount_type": "AMOUNT",
        "amount": amount,
        "rate": "",
        "reason_ar": "حالة اجتماعية",
        "president_approval_ref": "PR-2026-77",
        "president_approval_date": "2026-09-20",
    }


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------
def test_a_discount_is_granted_and_records_both_burdens(signed_in, manager, enrolled) -> None:
    """
    §5.1 — the screen shows who bore what, which is the point of the row.

    No agreement governs this cohort, so the university bears all of it and
    the screen says so rather than leaving the partner column blank.
    """
    enrollment, _ = enrolled
    response = signed_in(manager).post(
        reverse("billing:discounts"), _grant_payload(enrollment), follow=True
    )
    row = response.context["discounts"][0]
    assert row["amount"] == Decimal("50.000")
    assert row["university_burden"] == Decimal("50.000")
    assert row["partner_burden"] == Decimal("0.000")


def test_a_discount_after_payment_shows_the_refusal(
    signed_in, manager, enrolled, cashier, cash_method
) -> None:
    """
    Sprint 8A's boundary, reaching the operator intact.

    Once money has landed on a shared line the partner's absorption is already
    fixed, so the discount is refused — and §6.2 puts the manager's
    recommendation before the payment anyway.
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

    response = signed_in(manager).post(
        reverse("billing:discounts"), _grant_payload(enrollment), follow=True
    )
    assert "§6.2" in response.content.decode() or "الخصم يسبق القبض" in response.content.decode()


def test_a_discount_beyond_the_tuition_is_refused(signed_in, manager, enrolled) -> None:
    """§5.1 — «على الرسوم الدراسية فقط، لا على رسوم التسجيل»."""
    enrollment, quote = enrolled
    payload = _grant_payload(enrollment, amount=str(quote.course_fee + Decimal("1.000")))

    response = signed_in(manager).post(reverse("billing:discounts"), payload, follow=True)
    assert "BR-029" in response.content.decode()


def test_the_finance_officer_cannot_grant_a_discount(signed_in, finance, enrolled) -> None:
    """§8 — granting a discount is the centre manager's, and only theirs."""
    enrollment, _ = enrolled
    response = signed_in(finance).post(reverse("billing:discounts"), _grant_payload(enrollment))
    assert response.status_code == 403


def test_nobody_approves_the_discount_they_raised(
    signed_in, manager, other_manager, enrolled
) -> None:
    """D-18 — refused on the POST, and the button is not offered either."""
    enrollment, _ = enrolled
    signed_in(manager).post(reverse("billing:discounts"), _grant_payload(enrollment), follow=True)

    page = signed_in(manager).get(reverse("billing:discounts"))
    row = page.context["discounts"][0]
    assert row["created_by_id"] == manager.pk

    refused = signed_in(manager).post(
        reverse("billing:discounts"), {"action": "approve", "id": str(row["id"])}, follow=True
    )
    assert "D-18" in refused.content.decode()

    approved = signed_in(other_manager).post(
        reverse("billing:discounts"), {"action": "approve", "id": str(row["id"])}, follow=True
    )
    assert approved.context["discounts"][0]["is_approved"] is True


# ---------------------------------------------------------------------------
# Refunds and credit returns
# ---------------------------------------------------------------------------
def test_a_refund_runs_request_approve_execute(
    signed_in, finance, manager, enrolled, cashier, cash_method
) -> None:
    """
    §5.3 — three acts and two people, ending in money actually moving.

    The manager approves and the finance officer executes, so the person who
    authorised the refund is not the person who paid it out.
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

    signed_in(finance).post(
        reverse("billing:refunds"),
        {
            "action": "request",
            "enrollment_code": enrollment.code,
            "code": "RF-UI-1",
            "refund_type": "PARTIAL",
            "amount": "100.000",
            "reason_ar": "إلغاء الدورة لعدم اكتمال العدد",
            "official_letter_ref": "LT-2026-9",
            "official_letter_date": "2026-09-20",
            "president_approval_ref": "PR-2026-9",
            "president_approval_date": "2026-09-20",
        },
        follow=True,
    )
    signed_in(manager).post(
        reverse("billing:refunds"), {"action": "approve", "code": "RF-UI-1"}, follow=True
    )
    page = signed_in(finance).post(
        reverse("billing:refunds"), {"action": "execute", "code": "RF-UI-1"}, follow=True
    )

    row = next(r for r in page.context["refunds"] if r["code"] == "RF-UI-1")
    assert row["status"] == "EXECUTED"


def test_a_refund_without_its_documents_is_refused_by_the_form(
    signed_in, finance, enrolled
) -> None:
    """BR-034 — both external documents, or nothing moves."""
    enrollment, _ = enrolled
    response = signed_in(finance).post(
        reverse("billing:refunds"),
        {
            "action": "request",
            "enrollment_code": enrollment.code,
            "code": "RF-UI-2",
            "refund_type": "PARTIAL",
            "amount": "50.000",
            "reason_ar": "بلا مستندات",
            "official_letter_ref": "",
            "official_letter_date": "",
            "president_approval_ref": "",
            "president_approval_date": "",
        },
        follow=True,
    )
    from apps.billing.models import Refund

    assert not Refund.objects.filter(code="RF-UI-2").exists()
    assert response.status_code == 200


def test_a_credit_return_is_offered_beside_a_refund_and_is_not_one(
    signed_in, finance, enrolled, cashier, cash_method
) -> None:
    """
    BR-071 — handing back an overpayment needs no presidential decree.

    Both live on one screen so the difference is visible; they are separate
    forms so the heavy process cannot be applied to the light case.
    """
    from apps.cashbox.services import payment_service

    enrollment, quote = enrolled
    over = quote.course_fee + (quote.registration_fee or Decimal("0.000")) + Decimal("30.000")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=over,
        payment_method=cash_method,
        received_on=TERM_START,
    )

    response = signed_in(finance).post(
        reverse("billing:refunds"),
        {
            "action": "return-credit",
            "enrollment_code": enrollment.code,
            "code": "CR-UI-1",
            "returned_on": "2026-09-20",
            "reason_ar": "ردّ زيادة",
        },
        follow=True,
    )
    row = next(r for r in response.context["credit_returns"] if r["code"] == "CR-UI-1")
    assert row["amount"] == Decimal("30.000")


# ---------------------------------------------------------------------------
# Extra fees
# ---------------------------------------------------------------------------
def test_an_extra_fee_uses_the_configured_amount(signed_in, manager, enrolled) -> None:
    """BR-037 — 75 dinars from the setting, with the field left blank."""
    enrollment, _ = enrolled
    response = signed_in(manager).post(
        reverse("billing:extra-fees"),
        {
            "enrollment_code": enrollment.code,
            "fee_type": "SUBJECT_REPEAT",
            "amount": "",
            "subject_name": "مبادئ المحاسبة",
            "charged_on": "2026-09-20",
            "prior_agreement_with_participant": "",
            "is_partner_shareable": "unknown",
        },
        follow=True,
    )
    row = response.context["fees"][0]
    assert row["amount"] == Decimal("75.000")
    assert row["is_partner_shareable"] is True
    assert row["charge_line_description"]


def test_an_international_exam_fee_needs_the_prior_agreement(signed_in, manager, enrolled) -> None:
    """BR-040 — «إلا باتفاق مسبق مع الطالب»، and the screen says so."""
    enrollment, _ = enrolled
    response = signed_in(manager).post(
        reverse("billing:extra-fees"),
        {
            "enrollment_code": enrollment.code,
            "fee_type": "INTERNATIONAL_EXAM",
            "amount": "120.000",
            "subject_name": "",
            "charged_on": "2026-09-20",
            "prior_agreement_with_participant": "",
            "is_partner_shareable": "unknown",
        },
        follow=True,
    )
    assert "BR-040" in response.content.decode()


def test_the_registration_officer_cannot_charge_a_fee(signed_in, registrar, enrolled) -> None:
    """§8 — data entry is not the authority to charge money."""
    enrollment, _ = enrolled
    response = signed_in(registrar).post(
        reverse("billing:extra-fees"),
        {
            "enrollment_code": enrollment.code,
            "fee_type": "SUBJECT_REPEAT",
            "charged_on": "2026-09-20",
        },
    )
    assert response.status_code == 403
