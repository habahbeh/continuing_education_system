"""
The discounts screen after its UX pass (§5.1 · BR-029 … BR-032 · D-18).

The form offers only enrolments a discount can still land on, each carrying
what the live preview needs; the register carries tiles, a live search and a
status filter; granting and countersigning both go through a dialog; and the
account page opens the form with its enrolment chosen.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.billing.models import Discount, DiscountType
from apps.billing.services import discount_service
from apps.cashbox.services import payment_service

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 20)


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.disc", password="x-pass-1234", role=Role.CENTER_MANAGER,
        full_name_ar="مدير أول",
    )


@pytest.fixture
def other_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.disc.2", password="x-pass-1234", role=Role.CENTER_MANAGER,
        full_name_ar="مدير ثانٍ",
    )


@pytest.fixture
def enrollment(make_enrollment, manager):
    return make_enrollment(actor=manager)[0]


def _grant(actor, enrollment, amount="25.000"):
    return discount_service.grant_discount(
        actor=actor,
        enrollment=enrollment,
        discount_type=DiscountType.AMOUNT,
        amount=Decimal(amount),
        reason_ar="حالة اجتماعية",
        president_approval_ref="PR-77",
        president_approval_date=DAY,
    )


# --- the candidates the form offers --------------------------------------


def test_the_form_offers_only_what_the_save_would_accept(
    client, manager, enrollment, cashier, cash_method
) -> None:
    rows = discount_service.discountable_rows(actor=manager)
    assert [r["code"] for r in rows] == [enrollment.code]
    row = rows[0]
    assert row["tuition_base"] == discount_service.tuition_base(enrollment)
    assert row["already_discounted"] == Decimal("0")
    assert row["split"]["supported"] is True

    _grant(manager, enrollment, "10.000")
    assert discount_service.discountable_rows(actor=manager)[0]["already_discounted"] == Decimal(
        "10.000"
    )

    # Money on a shareable line closes the door (§6.2: the discount precedes payment).
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=DAY,
    )
    assert discount_service.discountable_rows(actor=manager) == []

    client.force_login(manager)
    page = client.get(reverse("billing:discounts")).content.decode("utf-8")
    assert "لا تسجيل قابل للخصم الآن" in page


def test_a_closed_enrolment_is_never_offered(client, manager, enrollment) -> None:
    enrollment.status = "CANCELLED"
    enrollment.save(update_fields=["status"])
    assert discount_service.discountable_rows(actor=manager) == []


def test_each_option_carries_the_preview_data(client, manager, enrollment) -> None:
    client.force_login(manager)
    page = client.get(reverse("billing:discounts")).content.decode("utf-8")
    base = discount_service.tuition_base(enrollment)
    assert f'value="{enrollment.code}"' in page
    assert f'data-base="{base}"' in page
    assert 'data-ppct="0"' in page or 'data-ppct="0.000"' in page
    assert 'class="disc-preview"' in page and "يبقى من الرسوم الدراسية" in page
    assert 'x-ref="confirm"' in page and "تأكيد منح الخصم" in page
    assert "alert(" not in page and "confirm(" not in page


# --- the register --------------------------------------------------------


def test_tiles_search_and_status_follow_the_rows(client, manager, other_manager, enrollment) -> None:
    first = _grant(manager, enrollment, "25.000")
    discount_service.approve_discount(actor=other_manager, discount=first)
    _grant(manager, enrollment, "10.000")

    client.force_login(manager)
    response = client.get(reverse("billing:discounts"))
    tiles = {t["label"]: t["value"] for t in response.context["tiles"]}
    assert tiles["الخصومات"] == 2 and tiles["بانتظار الاعتماد"] == 1
    assert response.context["summary"]["total"] == Decimal("35.000")

    pending = client.get(reverse("billing:discounts"), {"status": "pending"})
    assert [r["amount"] for r in pending.context["discounts"]] == [Decimal("10.000")]

    by_ref = client.get(reverse("billing:discounts"), {"q": "PR-7"})
    assert len(by_ref.context["discounts"]) == 2
    miss = client.get(reverse("billing:discounts"), {"q": "nothing-here"})
    assert miss.context["discounts"] == []
    assert "لا خصومات تطابق التصفية" in miss.content.decode("utf-8")

    page = response.content.decode("utf-8")
    assert 'hx-get="' + reverse("billing:discounts") in page
    assert reverse("operations:account", args=[enrollment.code]) in page


def test_the_row_says_who_granted_and_who_countersigned(
    client, manager, other_manager, enrollment
) -> None:
    d = _grant(manager, enrollment)

    client.force_login(manager)
    page = client.get(reverse("billing:discounts")).content.decode("utf-8")
    assert "منحته أنت؛ يعتمده غيرك" in page and f'id="dc-{d.pk}"' not in page

    client.force_login(other_manager)
    page = client.get(reverse("billing:discounts")).content.decode("utf-8")
    assert f'id="dc-{d.pk}"' in page and "مدير أول" in page

    response = client.post(reverse("billing:discounts"), {"action": "approve", "id": d.pk})
    assert response.status_code == 302
    page = client.get(reverse("billing:discounts")).content.decode("utf-8")
    assert "معتمَد" in page and "مدير ثانٍ" in page


def test_granting_from_the_screen_lands_and_redirects(client, manager, enrollment) -> None:
    client.force_login(manager)
    response = client.post(
        reverse("billing:discounts"),
        {
            "action": "grant",
            "enrollment_code": enrollment.code,
            "discount_type": "PERCENT",
            "rate": "10",
            "amount": "",
            "reason_ar": "منحة",
            "president_approval_ref": "PR-9",
            "president_approval_date": "2026-09-01",
        },
    )
    assert response.status_code == 302
    d = Discount.objects.get()
    assert d.rate == Decimal("10") and d.amount == discount_service.tuition_base(enrollment) / 10


def test_the_account_page_opens_the_form_on_its_enrolment(client, manager, enrollment) -> None:
    client.force_login(manager)
    account = client.get(reverse("operations:account", args=[enrollment.code]))
    link = reverse("billing:discounts") + f"?enrollment={enrollment.code}#grant"
    assert link in account.content.decode("utf-8")

    response = client.get(reverse("billing:discounts"), {"enrollment": enrollment.code})
    assert response.context["form"]["enrollment_code"].value() == enrollment.code
    assert "خصومات التسجيل" in response.content.decode("utf-8")


def test_readers_without_create_get_no_form_and_no_candidates(client, finance, enrollment) -> None:
    client.force_login(finance)
    response = client.get(reverse("billing:discounts"))
    page = response.content.decode("utf-8")
    assert response.context["candidates"] == []
    assert 'id="grant-form"' not in page
    assert "لمدير المركز (§3.4/19)" in page
