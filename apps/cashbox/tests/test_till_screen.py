"""
The till after its UX pass: who pays → on which enrolment → how much.

The screen starts at a participant search, arrives already filled from
``?enrollment=`` or ``?participant=``, draws one card per enrolment that still
owes, dates the receipt on issue, and suggests the statement. Every rule stays
in the services (BR-020 read through the function that enforces it); these
tests hold the shape of the screen and the lookups behind it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def enrolled(make_enrollment):
    """An SC-NET enrolment with its charge lines — still owing in full."""
    return make_enrollment()


@pytest.fixture
def diploma_enrolled(make_enrollment):
    """A diploma enrolment, where BR-020's floor applies to the first payment."""
    return make_enrollment(program_code="DIP-ID", category="CENTER")


@pytest.fixture
def make_participant(registrar, participant_data):
    from apps.people.services import participant_service

    def _make(name: str):
        data = {**participant_data, "name_ar": name, "id_document_number": f"ID-{name}"}
        return participant_service.create_participant(actor=registrar, data=data)

    return _make


def _page(response) -> str:
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


# ---------------------------------------------------------------------------
# Arrival
# ---------------------------------------------------------------------------
def test_a_bare_visit_starts_at_the_search_and_offers_recent_balances(
    signed_in, cashier, enrolled, cash_method
) -> None:
    enrollment, _ = enrolled

    response = signed_in(cashier).get(reverse("cashbox:payment-new"))
    page = _page(response)

    assert response.status_code == 200
    assert response.context["account"] is None
    assert 'name="q"' in page
    assert '<form method="post"' not in page  # nothing to pay yet
    assert [r["code"] for r in response.context["recent"]] == [enrollment.code]
    assert f"?enrollment={enrollment.code}" in page


def test_search_matches_number_name_and_phone(signed_in, cashier, enrolled, cash_method) -> None:
    enrollment, _ = enrolled
    person = enrollment.participant
    person.phone = "0791234567"
    person.save(update_fields=["phone"])

    for needle in (person.participant_number[:5], person.name_ar[:4], "07912"):
        response = signed_in(cashier).get(reverse("cashbox:payment-new"), {"q": needle})
        numbers = [m["participant_number"] for m in response.context["matches"]]
        assert person.participant_number in numbers, needle
        match = next(
            m
            for m in response.context["matches"]
            if m["participant_number"] == person.participant_number
        )
        assert match["owing_count"] == 1
        assert match["owing_total"] > 0
        assert f"?participant={person.participant_number}" in _page(response)


def test_a_search_that_matches_nobody_says_so(signed_in, cashier, enrolled) -> None:
    response = signed_in(cashier).get(reverse("cashbox:payment-new"), {"q": "zzz-nobody"})

    assert response.context["matches"] == []
    assert "لا مشارك يطابق البحث" in _page(response)


def test_an_enrolment_link_arrives_with_everything_chosen(
    signed_in, cashier, enrolled, cash_method
) -> None:
    enrollment, _ = enrolled

    response = signed_in(cashier).get(
        reverse("cashbox:payment-new"), {"enrollment": enrollment.code}
    )
    page = _page(response)
    ctx = response.context

    assert ctx["account"]["participant_number"] == enrollment.participant.participant_number
    assert ctx["preselected"] == enrollment.code
    assert ctx["selected"]["code"] == enrollment.code
    assert ctx["selected"]["balance"] > 0
    assert f'value="{enrollment.code}"' in page and " checked" in page
    # The statement is suggested from the enrolment, editable on screen.
    assert ctx["form"].initial["breakdown_text_ar"] == ctx["selected"]["default_text"]
    assert enrollment.cohort.program.name_ar in ctx["form"].initial["breakdown_text_ar"]
    assert 'name="breakdown_text_ar"' in page
    # No date field: the receipt is dated on issue.
    assert 'name="received_on"' not in page
    assert "يوم الإصدار" in page


def test_a_single_owing_enrolment_is_chosen_by_itself(signed_in, cashier, enrolled) -> None:
    enrollment, _ = enrolled

    response = signed_in(cashier).get(
        reverse("cashbox:payment-new"),
        {"participant": enrollment.participant.participant_number},
    )

    assert response.context["preselected"] == enrollment.code
    assert "اختير تلقائياً" in _page(response)


def test_a_settled_participant_has_no_cards_and_no_submit(
    signed_in, cashier, enrolled, cash_method
) -> None:
    from apps.cashbox.services import payment_service

    enrollment, quote = enrolled
    total = quote.course_fee + (quote.registration_fee or Decimal("0.000"))
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=total,
        payment_method=cash_method,
        received_on=date(2026, 9, 20),
    )

    response = signed_in(cashier).get(
        reverse("cashbox:payment-new"),
        {"participant": enrollment.participant.participant_number},
    )
    page = _page(response)

    assert response.context["rows"] == []
    assert "لا رصيد مستحق على هذا المشارك" in page
    assert 'name="enrollment_code"' not in page


def test_an_unknown_number_or_code_is_refused_gently(signed_in, cashier, enrolled) -> None:
    client = signed_in(cashier)

    response = client.get(reverse("cashbox:payment-new"), {"participant": "000000000"})
    assert response.status_code == 200
    assert response.context["account"] is None

    response = client.get(reverse("cashbox:payment-new"), {"enrollment": "EN-NOPE"})
    assert response.status_code == 200
    assert response.context["account"] is None


# ---------------------------------------------------------------------------
# The payment itself
# ---------------------------------------------------------------------------
def _pay(client, enrollment, amount: str, method, **extra):
    return client.post(
        reverse("cashbox:payment-new"),
        {
            "participant_number": enrollment.participant.participant_number,
            "enrollment_code": enrollment.code,
            "amount": amount,
            "payment_method": method.code,
            "external_receipt_ref": "",
            "breakdown_text_ar": "",
            **extra,
        },
        follow=True,
    )


def test_the_receipt_is_dated_today_whatever_was_posted(
    signed_in, cashier, enrolled, cash_method
) -> None:
    from django.utils import timezone

    enrollment, quote = enrolled

    response = _pay(
        signed_in(cashier), enrollment, str(quote.course_fee), cash_method, received_on="2020-01-01"
    )

    receipt = response.context["receipt"]
    assert receipt["received_on"] == timezone.localdate()
    assert receipt["amount"] == quote.course_fee


def test_the_posted_enrolment_must_belong_to_the_posted_participant(
    signed_in, cashier, enrolled, make_participant, cash_method
) -> None:
    """The card list is rebuilt from the participant; a code off it is refused."""
    enrollment, _ = enrolled
    stranger = make_participant("غريب")

    response = signed_in(cashier).post(
        reverse("cashbox:payment-new"),
        {
            "participant_number": stranger.participant_number,
            "enrollment_code": enrollment.code,
            "amount": "10.000",
            "payment_method": cash_method.code,
        },
    )

    assert response.status_code == 200
    assert response.context["form"].errors["enrollment_code"]
    from apps.cashbox.models import Receipt

    assert not Receipt.objects.exists()


def test_the_typed_statement_is_kept_and_the_blank_one_is_not_invented(
    signed_in, cashier, enrolled, cash_method
) -> None:
    enrollment, quote = enrolled

    response = _pay(
        signed_in(cashier),
        enrollment,
        str(quote.course_fee),
        cash_method,
        breakdown_text_ar="بياني",
    )

    assert response.context["receipt"]["breakdown_text_ar"] == "بياني"


def test_the_first_payment_minimum_is_repeated_from_the_service(
    signed_in, cashier, diploma_enrolled, cash_method, seeded_settings
) -> None:
    from apps.cashbox.services import payment_service

    enrollment, _ = diploma_enrolled
    response = signed_in(cashier).get(
        reverse("cashbox:payment-new"), {"enrollment": enrollment.code}
    )
    row = response.context["selected"]

    assert row["is_first_payment"] is True
    assert row["minimum_first_payment"] == payment_service.first_payment_minimum(
        enrollment=enrollment, as_of=date.today()
    )
    assert response.context["minimum_first_payment"] == Decimal("400.000")
    assert "أقل دفعة أولى للدبلوم" in _page(response)
    assert "دفعة أولى" in _page(response)

    refused = _pay(signed_in(cashier), enrollment, "300.000", cash_method)
    assert "BR-020" in refused.content.decode()


def test_a_programme_override_is_named_beside_the_general_figure(
    signed_in, cashier, diploma_enrolled, cash_method, seeded_settings
) -> None:
    enrollment, _ = diploma_enrolled
    program = enrollment.cohort.program
    program.minimum_first_payment_override = Decimal("650.000")
    program.save(update_fields=["minimum_first_payment_override"])

    response = signed_in(cashier).get(
        reverse("cashbox:payment-new"), {"enrollment": enrollment.code}
    )
    page = _page(response)

    assert response.context["selected_program_minimum"] == Decimal("650.000")
    assert response.context["selected"]["minimum_first_payment"] == Decimal("650.000")
    assert "حدّ أدنى خاص للدفعة الأولى" in page
    assert "يتقدّم على الحدّ العام" in page
    assert "400" in page


# ---------------------------------------------------------------------------
# Doors, shape, links
# ---------------------------------------------------------------------------
def test_the_manager_still_cannot_reach_the_lookups(enrolled) -> None:
    from django.core.exceptions import PermissionDenied

    from apps.operations.services import enrollment_service
    from apps.people.models import Role, User

    manager = User.objects.create_user(username="mgr.till", password="x", role=Role.CENTER_MANAGER)
    with pytest.raises(PermissionDenied):
        enrollment_service.payable_participant_search(actor=manager, query="a")
    with pytest.raises(PermissionDenied):
        enrollment_service.payable_participant_account(actor=manager, participant_number="1")


def test_the_lookups_sum_balances_in_bulk(
    signed_in, cashier, enrolled, django_assert_max_num_queries
) -> None:
    enrollment, _ = enrolled

    # 8L: ٤٠ → ٤١. الاستعلام الزائد ليس من هذه الشاشة: شعار المؤسسة صار يُقرأ
    # من قاعدة البيانات لكل صفحة (`core.BrandAsset`)، وهو ثمن أن يكون الشعار
    # قابلاً للتغيير من الشاشة بلا إصدار برمجي. استعلامٌ واحد للخانات الأربع
    # مجتمعةً، ولا يكبر بعدد الصفوف — وهو ما يهمّ هذا الاختبار.
    with django_assert_max_num_queries(41):
        response = signed_in(cashier).get(
            reverse("cashbox:payment-new"), {"enrollment": enrollment.code}
        )
    assert response.status_code == 200


def test_the_screen_draws_no_script_and_only_known_classes() -> None:
    import re
    from pathlib import Path

    source = Path("templates/cashbox/payment_new.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    assert "guided_help" not in source
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in source

    used = {
        c
        for m in re.finditer(r'(?<![:\w-])class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"


def test_the_related_pages_link_into_the_till_with_the_enrolment(
    signed_in, cashier, finance, enrolled
) -> None:
    enrollment, _ = enrolled
    till = reverse("cashbox:payment-new")

    body = (
        signed_in(finance)
        .get(reverse("operations:account", args=[enrollment.code]))
        .content.decode()
    )
    assert f"{till}?enrollment={enrollment.code}" in body

    body = signed_in(finance).get(reverse("operations:enrollments")).content.decode()
    assert f"{till}?enrollment={enrollment.code}" in body
