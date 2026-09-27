"""
The receipt after issue: a page that prints, and a paper receipt.

The detail page leads with the amount and the number, offers «طباعة السند»
to every reader and «دفعة أخرى» only to whoever may take money; the print
route draws two copies on one sheet with the amount in words, and prints a
voided receipt as voided (BR-025).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cashbox.services.amount_words import amount_in_words_ar

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def receipt(make_enrollment, cashier, cash_method):
    from apps.cashbox.services import payment_service

    enrollment, quote = make_enrollment()
    return payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=date(2026, 9, 20),
    )


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        ("0", "صفر دينار فقط لا غير"),
        ("1", "دينار واحد فقط لا غير"),
        ("2", "ديناران فقط لا غير"),
        ("3", "ثلاثة دنانير فقط لا غير"),
        ("21", "واحد وعشرون ديناراً فقط لا غير"),
        ("100", "مئة دينار فقط لا غير"),
        ("200", "مئتا دينار فقط لا غير"),
        ("270.000", "مئتان وسبعون ديناراً فقط لا غير"),
        ("1715.500", "ألف وسبعمئة وخمسة عشر ديناراً وخمسمئة فلس فقط لا غير"),
        ("2000", "ألفا دينار فقط لا غير"),
        ("11000", "أحد عشر ألف دينار فقط لا غير"),
        ("12345.005", "اثنا عشر ألفاً وثلاثمئة وخمسة وأربعون ديناراً وخمسة فلوس فقط لا غير"),
        ("0.250", "مئتان وخمسون فلساً فقط لا غير"),
    ],
)
def test_the_amount_reads_in_arabic_words(amount: str, words: str) -> None:
    assert amount_in_words_ar(Decimal(amount)) == words


def test_the_print_route_draws_two_copies_with_the_amount_in_words(
    signed_in, cashier, receipt
) -> None:
    response = signed_in(cashier).get(
        reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number])
    )
    page = response.content.decode("utf-8")

    assert response.status_code == 200
    assert page.count('class="doc rcpt"') == 2
    assert "الأصل — للمشارك" in page and "نسخة — للصندوق" in page
    assert page.count(receipt.internal_receipt_number) >= 2
    assert amount_in_words_ar(receipt.amount) in page
    assert receipt.participant.name_ar in page
    assert "رسم تسجيل" in page or "رسوم" in page  # the allocations table
    assert "ملغى" not in page
    assert 'class="rcpt-cut"' in page


def test_a_voided_receipt_prints_as_voided(signed_in, cashier, finance, receipt) -> None:
    from apps.cashbox.services import payment_service

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    payment_service.approve_void(
        actor=finance,
        void_record=payment_service.void_instance(
            actor=finance, number=receipt.internal_receipt_number
        ),
    )

    page = (
        signed_in(finance)
        .get(reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number]))
        .content.decode("utf-8")
    )

    assert 'class="doc rcpt is-void"' in page
    assert 'class="rcpt-void"' in page


def test_the_print_route_keeps_the_receipt_door(client, receipt) -> None:
    url = reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number])
    assert client.get(url).status_code == 403  # anonymous
    from apps.people.models import Role, User

    client.force_login(
        User.objects.create_user(username="rp.reg", password="x", role=Role.REGISTRATION_OFFICER)
    )
    assert client.get(url).status_code == 200  # may read the register, may print
    assert client.get(reverse("cashbox:receipt-print", args=["R-0000-00000"])).status_code == 404


def test_the_detail_page_leads_with_print_and_the_next_payment(
    signed_in, cashier, finance, receipt
) -> None:
    detail = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])
    print_url = reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number])
    again = f"{reverse('cashbox:payment-new')}?participant={receipt.participant.participant_number}"

    page = signed_in(cashier).get(detail).content.decode("utf-8").split("</nav>", 1)[-1]
    assert print_url in page
    assert again in page  # the cashier takes the next payment
    assert 'class="kpi-grid rcpt-kpis"' in page
    assert "إجراءات سريعة" in page
    assert "الخطوة التالية" not in page

    from apps.people.models import Role, User

    reader = User.objects.create_user(username="rp.audit", password="x", role=Role.AUDIT_ACCOUNT)
    page = signed_in(reader).get(detail).content.decode("utf-8").split("</nav>", 1)[-1]
    assert print_url in page  # everyone who reads may print
    assert again not in page  # …but not everyone takes money
    assert "<form" not in page and "<button" not in page


def test_the_voucher_step_is_offered_to_the_registrar_not_the_cashier(
    signed_in, cashier, registrar, receipt
) -> None:
    detail = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])
    code = receipt.allocations.first().enrollment.code

    page = signed_in(cashier).get(detail, {"enrollment": code}).content.decode("utf-8")
    assert "استلام الوصل" in page and "خطوة موظف التسجيل" in page
    assert f"{reverse('operations:enrollments')}?q={code}" not in page

    page = signed_in(registrar).get(detail, {"enrollment": code}).content.decode("utf-8")
    assert f"{reverse('operations:enrollments')}?q={code}" in page


def test_a_fresh_issue_is_greeted_with_the_print_button(signed_in, cashier, receipt) -> None:
    detail = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])
    print_url = reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number])

    fresh = signed_in(cashier).get(detail, {"issued": "1"})
    assert fresh.context["just_issued"] is True
    assert "طباعة الآن" in fresh.content.decode("utf-8")

    later = signed_in(cashier).get(detail)
    assert later.context["just_issued"] is False
    assert "طباعة الآن" not in later.content.decode("utf-8")
    assert print_url in later.content.decode("utf-8")


def test_the_page_says_the_amount_in_words_and_names_the_enrolment_once(
    signed_in, cashier, receipt
) -> None:
    page = (
        signed_in(cashier)
        .get(reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )
    code = receipt.allocations.first().enrollment.code

    assert amount_in_words_ar(receipt.amount) in page
    # Named in the facts card, not repeated on every allocation line.
    assert page.count(f">{code}<") == 1
    assert "<th>التسجيل</th>" not in page


def test_the_void_is_asked_and_decided_through_dialogs_by_the_right_hands(
    signed_in, cashier, finance, receipt
) -> None:
    from apps.cashbox.services import payment_service

    detail = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])

    page = signed_in(cashier).get(detail).content.decode("utf-8")
    assert 'id="void-request"' in page and 'id="void-approve"' not in page
    assert "confirm(" not in page and "alert(" not in page

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")

    page = signed_in(cashier).get(detail).content.decode("utf-8")
    assert 'id="void-request"' not in page and 'id="void-approve"' not in page
    assert "مبلغ خاطئ" in page

    page = signed_in(finance).get(detail).content.decode("utf-8")
    assert 'id="void-approve"' in page and "مبلغ خاطئ" in page

    approved = signed_in(finance).post(detail, {"action": "approve"}, follow=True)
    page = approved.content.decode("utf-8")
    assert approved.context["receipt"]["is_voided"] is True
    assert "سند ملغى" in page
    assert 'class="dash-cols rcpt-page is-void"' in page


def test_the_paper_carries_the_statement_the_programme_and_the_decimal_point(
    signed_in, cashier, receipt
) -> None:
    receipt.breakdown_text_ar = "دفعة على حساب"
    receipt.external_receipt_ref = "FIN-42"
    receipt.save(update_fields=["breakdown_text_ar", "external_receipt_ref"])

    page = (
        signed_in(cashier)
        .get(reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number]))
        .content.decode("utf-8")
    )

    assert "دفعة على حساب" in page  # the statement, as a line of its own
    assert (
        receipt.allocations.first().enrollment.cohort.program.name_ar in page
    )  # and the programme
    assert "FIN-42" in page
    assert str(receipt.amount) in page  # 250.000 — the decimal point on paper
    assert "الرقم الجامعي" in page and receipt.participant.participant_number in page
    assert "رقم الإقفال اليومي" in page  # on the till's copy
    assert page.count("توقيع المستلم") == 1  # on the participant's copy only


def test_a_voided_paper_says_why_who_and_when_and_hides_the_reversals(
    signed_in, cashier, finance, receipt
) -> None:
    from apps.cashbox.services import payment_service

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")
    payment_service.approve_void(
        actor=finance,
        void_record=payment_service.void_instance(
            actor=finance, number=receipt.internal_receipt_number
        ),
    )

    page = (
        signed_in(finance)
        .get(reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number]))
        .content.decode("utf-8")
    )

    assert "أُلغي هذا السند" in page and "مبلغ خاطئ" in page
    assert "الموظف المالي" in page or finance.username in page
    assert f"-{receipt.amount}" not in page  # the reversing lines stay off the paper


def test_print_now_asks_the_page_to_open_the_dialog(signed_in, cashier, receipt) -> None:
    url = reverse("cashbox:receipt-print", args=[receipt.internal_receipt_number])

    assert signed_in(cashier).get(url).context["auto_print"] is False
    auto = signed_in(cashier).get(url, {"auto": "1"})
    assert auto.context["auto_print"] is True
    assert "window.print()" in auto.content.decode("utf-8")
    assert "onclick=" not in auto.content.decode("utf-8")
