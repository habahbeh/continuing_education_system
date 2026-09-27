"""
The expenses screen after its UX pass (§9.7 · §9.2 · D-18 · D-23).

Codes come from the system's yearly sequence; the tiles are the status
filter and the category chips a second one; the date is today by default
and a closed period is announced before the save; and every act — record,
approve with an optional note, reject with a reason — goes through a dialog.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.expenses.models import Expense
from apps.expenses.services import expense_service

pytestmark = pytest.mark.django_db

SCREEN = "expenses:expenses"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.exp.screen", password="x-pass-1234", role=Role.CENTER_MANAGER,
        full_name_ar="مدير المركز",
    )


def _record(client, **extra):
    data = {
        "action": "record",
        "category": "MARKETING",
        "amount": "120.000",
        "incurred_on": "2026-09-20",
        "description_ar": "إعلان",
        "cohort_code": "",
        "reference": "INV-1",
    }
    data.update(extra)
    return client.post(reverse(SCREEN), data)


def test_codes_are_drawn_from_the_yearly_sequence(signed_in, finance) -> None:
    client = signed_in(finance)
    assert _record(client).status_code == 302
    assert _record(client, amount="10").status_code == 302
    assert sorted(Expense.objects.values_list("code", flat=True)) == [
        "EX-2026-00001",
        "EX-2026-00002",
    ]
    page = signed_in(finance).get(reverse(SCREEN)).content.decode("utf-8")
    assert 'name="code"' not in page.split("</nav>", 1)[-1].replace('name="code" value=', "")


def test_the_form_defaults_to_today_and_the_cohort_from_the_link(signed_in, finance) -> None:
    response = signed_in(finance).get(reverse(SCREEN), {"cohort": "CO-X"})
    assert response.context["form"]["incurred_on"].value() == date.today()
    assert response.context["form"]["cohort_code"].value() == "CO-X"
    page = response.content.decode("utf-8")
    assert 'x-ref="confirm"' in page and "alert(" not in page and "confirm(" not in page
    assert 'name="category" value="MARKETING"' in page
    assert reverse("expenses:period-check") in page


def test_the_period_check_answers_for_a_date(signed_in, finance, approver) -> None:
    client = signed_in(finance)
    fragment = client.get(reverse("expenses:period-check"), {"on": "2026-09-20"}).content.decode(
        "utf-8"
    )
    assert 'data-open="1"' in fragment
    # Only who may record asks the question.
    assert signed_in(approver).get(reverse("expenses:period-check"), {"on": "2026-09-20"}).status_code == 403


def test_tiles_chips_and_search_follow_the_rows(signed_in, finance, approver) -> None:
    client = signed_in(finance)
    _record(client, description_ar="إعلان صحفي")
    _record(client, category="CONSUMABLES", amount="30", description_ar="أوراق")
    first = Expense.objects.get(description_ar="إعلان صحفي")
    signed_in(approver).post(reverse(SCREEN), {"action": "approve", "code": first.code})

    response = signed_in(finance).get(reverse(SCREEN))
    tiles = {t["label"]: t["value"] for t in response.context["tiles"]}
    assert tiles["بانتظار الاعتماد"] == 1 and tiles["المعتمَد"] == 1
    assert tiles["إجمالي المعتمَد"] == Decimal("120.000")
    chips = {c["code"]: c["total"] for c in response.context["chips"]}
    assert chips["MARKETING"] == Decimal("120.000") and chips["CONSUMABLES"] == 0

    pending = client.get(reverse(SCREEN), {"status": "RECORDED"})
    assert [r["description_ar"] for r in pending.context["expenses"]] == ["أوراق"]
    by_text = client.get(reverse(SCREEN), {"q": "صحفي"})
    assert len(by_text.context["expenses"]) == 1
    miss = client.get(reverse(SCREEN), {"q": "zzz"})
    assert miss.context["expenses"] == [] and "لا قيود تطابق التصفية" in miss.content.decode("utf-8")
    assert 'hx-get="' + reverse(SCREEN) in response.content.decode("utf-8")


def test_the_dialogs_follow_the_roles_and_the_trail_is_on_the_row(
    signed_in, finance, approver
) -> None:
    client = signed_in(finance)
    _record(client)
    code = Expense.objects.get().code

    page = client.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="ea-{code}"' not in page

    mgr = signed_in(approver)
    page = mgr.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="ea-{code}"' in page and f'id="er-{code}"' in page

    refused = mgr.post(reverse(SCREEN), {"action": "reject", "code": code, "note_ar": " "})
    assert refused.status_code == 200 and "$el.showModal()" in refused.content.decode("utf-8")
    assert Expense.objects.get().status == "RECORDED"

    assert mgr.post(reverse(SCREEN), {"action": "approve", "code": code, "note_ar": "مطابق"}).status_code == 302
    page = mgr.get(reverse(SCREEN)).content.decode("utf-8")
    assert "مطابق" in page and "يُخصم</span>" in page


def test_the_cohorts_list_offers_the_expense_door(signed_in, finance, cohort_with_agreement) -> None:
    page = signed_in(finance).get(reverse("operations:cohorts")).content.decode("utf-8")
    assert "#record" in page


def test_readers_without_create_get_no_form(signed_in, approver) -> None:
    response = signed_in(approver).get(reverse(SCREEN))
    assert response.status_code == 200 and response.context["form"] is None
    assert 'id="expense-form"' not in response.content.decode("utf-8")
