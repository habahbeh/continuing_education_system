"""
The refunds screen after its UX pass (§5.3 · BR-034 · BR-071 · D-18).

Codes come from the system's yearly sequence; each form offers only the
enrolments its service would accept, carrying the figure the form previews;
a FULL refund is the amount collected and is never typed; the register
carries tiles, live search and the whole decision trail; and every act —
request, approve, reject with a reason, execute, return a credit — goes
through a dialog.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.billing.models import CreditReturn, Refund
from apps.billing.services import credit_service, refund_service
from apps.cashbox.services import payment_service

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 20)
PASSWORD = "x-pass-1234"


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.rf", password=PASSWORD, role=Role.CENTER_MANAGER, full_name_ar="مدير المركز"
    )


@pytest.fixture
def paid(make_enrollment, cashier, cash_method):
    enrollment, quote = make_enrollment()
    total = quote.course_fee + (quote.registration_fee or Decimal("0.000"))
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=total,
        payment_method=cash_method,
        received_on=DAY,
    )
    return enrollment, total


def _docs():
    return {
        "reason_ar": "لم يكتمل العدد",
        "official_letter_ref": "LT-9",
        "official_letter_date": DAY,
        "president_approval_ref": "PR-9",
        "president_approval_date": DAY,
    }


# --- numbering and candidates --------------------------------------------


def test_refund_codes_are_drawn_from_the_yearly_sequence(finance, paid) -> None:
    enrollment, total = paid
    first = refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("10"), **_docs()
    )
    second = refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("10"), **_docs()
    )
    year = date.today().year
    assert first.code == f"RF-{year}-00001" and second.code == f"RF-{year}-00002"


def test_the_refund_form_offers_only_enrolments_with_money_collected(
    client, finance, paid, make_enrollment
) -> None:
    enrollment, total = paid
    rows = refund_service.refundable_rows(actor=finance)
    assert [r["code"] for r in rows] == [enrollment.code]
    assert rows[0]["paid"] == total and rows[0]["in_flight"] == Decimal("0")

    refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("10"), **_docs()
    )
    assert refund_service.refundable_rows(actor=finance)[0]["in_flight"] == Decimal("10.000")

    client.force_login(finance)
    page = client.get(reverse("billing:refunds")).content.decode("utf-8")
    assert f'value="{enrollment.code}"' in page and f'data-paid="{total}"' in page
    assert 'name="code"' not in page.split("</nav>", 1)[-1].replace('name="code" value=', "")


def test_a_full_refund_takes_the_collected_amount_without_typing_it(client, finance, paid) -> None:
    enrollment, total = paid
    client.force_login(finance)
    response = client.post(
        reverse("billing:refunds"),
        {
            "action": "request",
            "enrollment_code": enrollment.code,
            "refund_type": "FULL",
            "amount": "",
            "reason_ar": "إلغاء",
            "official_letter_ref": "LT-1",
            "official_letter_date": "2026-09-20",
            "president_approval_ref": "PR-1",
            "president_approval_date": "2026-09-20",
        },
    )
    assert response.status_code == 302
    refund = Refund.objects.get()
    assert refund.refund_type == "FULL" and refund.amount == total
    assert refund.code.startswith("RF-")


def test_a_partial_refund_must_say_how_much(client, finance, paid) -> None:
    enrollment, _ = paid
    client.force_login(finance)
    response = client.post(
        reverse("billing:refunds"),
        {
            "action": "request",
            "enrollment_code": enrollment.code,
            "refund_type": "PARTIAL",
            "amount": "",
            "reason_ar": "إلغاء",
            "official_letter_ref": "LT-1",
            "official_letter_date": "2026-09-20",
            "president_approval_ref": "PR-1",
            "president_approval_date": "2026-09-20",
        },
    )
    assert response.status_code == 200 and not Refund.objects.exists()
    assert "الاسترداد الجزئي يحتاج مبلغاً" in response.content.decode("utf-8")


# --- the decision trail and the dialogs ----------------------------------


def test_the_row_carries_the_whole_trail_and_the_dialogs_follow_the_roles(
    client, finance, manager, paid
) -> None:
    enrollment, _ = paid
    refund = refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("50"), **_docs()
    )
    url = reverse("billing:refunds")

    client.force_login(finance)
    page = client.get(url).content.decode("utf-8")
    assert f'id="ra-{refund.code}"' not in page and f'id="rx-{refund.code}"' not in page

    client.force_login(manager)
    page = client.get(url).content.decode("utf-8")
    assert f'id="ra-{refund.code}"' in page and f'id="rr-{refund.code}"' in page
    assert "alert(" not in page and "confirm(" not in page

    # A refusal without its reason reopens the dialog and changes nothing.
    response = client.post(url, {"action": "reject", "code": refund.code, "reason_ar": " "})
    assert response.status_code == 200 and "$el.showModal()" in response.content.decode("utf-8")
    assert Refund.objects.get().status == "REQUESTED"

    assert client.post(url, {"action": "approve", "code": refund.code}).status_code == 302
    refund.refresh_from_db()
    assert refund.approved_by == manager and refund.approved_at is not None

    client.force_login(finance)
    page = client.get(url).content.decode("utf-8")
    assert f'id="rx-{refund.code}"' in page and "مدير المركز" in page
    assert client.post(url, {"action": "execute", "code": refund.code}).status_code == 302
    refund.refresh_from_db()
    assert refund.status == "EXECUTED" and refund.executed_by == finance


def test_a_rejection_is_kept_on_the_row(client, finance, manager, paid) -> None:
    enrollment, _ = paid
    refund = refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("50"), **_docs()
    )
    refund_service.reject_refund(actor=manager, refund=refund, reason_ar="الكتاب غير مختوم")
    refund.refresh_from_db()
    assert refund.rejected_by == manager and refund.rejection_reason_ar == "الكتاب غير مختوم"

    client.force_login(finance)
    page = client.get(reverse("billing:refunds")).content.decode("utf-8")
    assert "الكتاب غير مختوم" in page


def test_tiles_search_and_status_follow_the_rows(client, finance, manager, paid) -> None:
    enrollment, _ = paid
    a = refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("20"), **_docs()
    )
    refund_service.request_refund(
        actor=finance, enrollment=enrollment, refund_type="PARTIAL", amount=Decimal("30"), **_docs()
    )
    refund_service.approve_refund(actor=manager, refund=a)
    refund_service.execute_refund(actor=finance, refund=a, executed_on=DAY)

    client.force_login(finance)
    response = client.get(reverse("billing:refunds"))
    tiles = {t["label"]: t["value"] for t in response.context["tiles"]}
    assert tiles["بانتظار الاعتماد"] == 1 and tiles["المنفَّذ"] == Decimal("20.000")

    only = client.get(reverse("billing:refunds"), {"status": "EXECUTED"})
    assert [r["code"] for r in only.context["refunds"]] == [a.code]
    by_doc = client.get(reverse("billing:refunds"), {"q": "PR-9"})
    assert len(by_doc.context["refunds"]) == 2
    miss = client.get(reverse("billing:refunds"), {"q": "zzz"})
    assert miss.context["refunds"] == [] and "لا استردادات تطابق التصفية" in miss.content.decode(
        "utf-8"
    )
    assert 'hx-get="' + reverse("billing:refunds") in response.content.decode("utf-8")


# --- credit returns ------------------------------------------------------


@pytest.fixture
def overpaid(make_enrollment, cashier, cash_method):
    enrollment, quote = make_enrollment()
    total = quote.course_fee + (quote.registration_fee or Decimal("0.000"))
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=total + Decimal("40.000"),
        payment_method=cash_method,
        received_on=DAY,
    )
    return enrollment


def test_the_credit_form_offers_only_credit_holders_and_numbers_the_return(
    client, finance, overpaid
) -> None:
    rows = credit_service.creditable_rows(actor=finance)
    assert [(r["code"], r["credit"]) for r in rows] == [(overpaid.code, Decimal("40.000"))]

    client.force_login(finance)
    page = client.get(reverse("billing:refunds"), {"enrollment": overpaid.code}).content.decode(
        "utf-8"
    )
    assert f'data-credit="40.000"' in page
    assert "x-data=\"{ tab: 'credit' }\"" in page

    response = client.post(
        reverse("billing:refunds"),
        {
            "action": "return-credit",
            "enrollment_code": overpaid.code,
            "returned_on": "2026-09-20",
            "reason_ar": "فائض",
        },
    )
    assert response.status_code == 302
    record = CreditReturn.objects.get()
    assert record.code == "CR-2026-00001" and record.amount == Decimal("40.000")
    assert credit_service.creditable_rows(actor=finance) == []


def test_the_account_page_offers_the_refund_door(client, finance, paid) -> None:
    client.force_login(finance)
    enrollment, _ = paid
    page = client.get(reverse("operations:account", args=[enrollment.code])).content.decode("utf-8")
    assert f"?enrollment={enrollment.code}#request" in page and "طلب استرداد" in page


def test_the_account_page_offers_the_credit_door(client, finance, overpaid) -> None:
    client.force_login(finance)
    page = client.get(reverse("operations:account", args=[overpaid.code])).content.decode("utf-8")
    assert "ردّ الفائض للمشارك" in page and "tab=credit" in page


def test_readers_without_create_get_no_forms(client, manager, paid) -> None:
    client.force_login(manager)
    response = client.get(reverse("billing:refunds"))
    page = response.content.decode("utf-8")
    assert response.context["refundable"] == [] and response.context["creditable"] == []
    assert 'id="refund-form"' not in page and 'id="credit-form"' not in page
