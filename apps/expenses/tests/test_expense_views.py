"""
The expenses screen (§9.7).

The matrix splits this screen between two people — the finance officer holds
``C E`` and the centre manager holds ``A`` — so the interesting assertions are
about what each of them is *not* offered. A screen that renders an approve
button to the person who recorded the entry has already lost the separation,
whatever the service does afterwards.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"
SCREEN = "expenses:expenses"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


def _record(client, *, code: str = "", amount: str = "120.000", **extra):
    """Post one entry; the code is the system's sequence and is never sent."""
    payload = {
        "action": "record",
        "category": "MARKETING",
        "amount": amount,
        "incurred_on": TERM_START.isoformat(),
        "description_ar": "إعلان في الصحف",
        "cohort_code": "",
        "reference": "INV-77",
    }
    payload.update(extra)
    return client.post(reverse(SCREEN), payload, follow=True)


def _row(response, code: str = "") -> dict:
    """The row with the given code — or the newest one when the code was generated."""
    rows = response.context["expenses"]
    if code and not code.startswith("EXP-UI"):
        return next(e for e in rows if e["code"] == code)
    return rows[0]


def _code(response) -> str:
    return response.context["expenses"][0]["code"]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def test_the_finance_officer_records_an_expense(signed_in, finance) -> None:
    response = _record(signed_in(finance))

    assert response.status_code == 200
    row = _row(response, "EXP-UI-1")
    assert row["amount"] == Decimal("120.000")
    assert row["status"] == "RECORDED"


def test_a_recorded_expense_is_not_yet_in_the_approved_total(signed_in, finance) -> None:
    """
    §9.2 reads the approved total, so this is the figure that must not move.

    Until somebody approves it, the entry is a claim about money rather than a
    fact about it.
    """
    response = _record(signed_in(finance))

    assert response.context["summary"]["approved_total"] == Decimal("0.000")
    # The category chips show the approved total per category — nothing yet.
    assert all(chip["total"] == 0 for chip in response.context["chips"])
    assert _row(response, "EXP-UI-1")["amount"] == Decimal("120.000")


def test_the_recorder_is_offered_no_way_to_approve(signed_in, finance) -> None:
    """
    The finance officer holds ``C E`` and not ``A``.

    Both halves are asserted — that the button is absent, and that posting the
    action anyway is a 403 rather than a message — because a screen is only as
    honest as the route behind it.
    """
    client = signed_in(finance)
    recorded = _record(client)

    page = client.get(reverse(SCREEN))
    assert page.context["can_create"] is True
    assert page.context["can_approve"] is False

    forced = client.post(reverse(SCREEN), {"action": "approve", "code": _code(recorded)})
    assert forced.status_code == 403


def test_the_centre_manager_is_offered_no_way_to_record(signed_in, manager) -> None:
    """They hold ``A`` alone — approving what somebody else wrote down."""
    page = signed_in(manager).get(reverse(SCREEN))
    assert page.context["can_approve"] is True
    assert page.context["can_create"] is False
    assert page.context["form"] is None

    forced = _record(signed_in(manager), code="EXP-UI-MGR")
    assert forced.status_code == 403


def test_the_manager_approves_and_the_total_moves(signed_in, finance, manager) -> None:
    recorded = _record(signed_in(finance), amount="80.000")

    response = signed_in(manager).post(
        reverse(SCREEN),
        {"action": "approve", "code": _code(recorded), "note_ar": "مطابق للفاتورة"},
        follow=True,
    )

    assert response.context["summary"]["approved_total"] == Decimal("80.000")
    assert _row(response, "EXP-UI-2")["status"] == "APPROVED"


def test_a_rejection_needs_a_reason_and_says_so_on_the_page(signed_in, finance, manager) -> None:
    """
    A refusal without a reason is unanswerable — the officer cannot correct
    what they were never told was wrong.

    The rule arrives as a message rather than a 403: the manager is entitled
    to reject, they simply have not finished doing it.
    """
    recorded = _record(signed_in(finance))

    response = signed_in(manager).post(
        reverse(SCREEN), {"action": "reject", "code": _code(recorded), "note_ar": ""}, follow=True
    )

    assert response.status_code == 200
    assert _row(response, "EXP-UI-3")["status"] == "RECORDED"
    assert any("سبب" in str(m) for m in response.context["messages"])


def test_a_rejected_expense_stays_on_the_record(signed_in, finance, manager) -> None:
    """
    §9.7 — rejected is an outcome, not a deletion.

    The entry and its reason both stay visible, which is what makes the
    decision auditable later.
    """
    recorded = _record(signed_in(finance))

    response = signed_in(manager).post(
        reverse(SCREEN),
        {"action": "reject", "code": _code(recorded), "note_ar": "فاتورة غير مرفقة"},
        follow=True,
    )

    row = _row(response, "EXP-UI-4")
    assert row["status"] == "REJECTED"
    assert row["decision_note_ar"] == "فاتورة غير مرفقة"
    assert response.context["summary"]["approved_total"] == Decimal("0.000")


# ---------------------------------------------------------------------------
# Who may not see it at all
# ---------------------------------------------------------------------------
def test_the_cashier_cannot_open_the_expenses_screen(signed_in, cashier) -> None:
    """§8 — «الصندوق: القبض فقط»."""
    assert signed_in(cashier).get(reverse(SCREEN)).status_code == 403


def test_the_audit_account_reads_but_records_nothing(signed_in, seeded_settings) -> None:
    """``V P`` — the whole point of the account is that it cannot write."""
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.exp", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    client = signed_in(auditor)

    page = client.get(reverse(SCREEN))
    assert page.status_code == 200
    assert page.context["can_create"] is False
    assert page.context["can_approve"] is False
    assert _record(client, code="EXP-UI-AUD").status_code == 403
