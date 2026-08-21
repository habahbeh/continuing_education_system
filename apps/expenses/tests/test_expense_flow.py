"""
The expenses screen and its two-person flow (§9.7).

The separation from the partner ledger is proved in
``test_ledger_separation``. What is proved here is the flow the permission
matrix already described before this app existed: the finance officer records
and the centre manager approves, and only an approved entry counts against
the centre's net income.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.expenses.services import expense_service

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
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.exp.ui", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _payload(code="EXP-UI-1", category="MARKETING", amount="120.000"):
    return {
        "action": "record",
        "code": code,
        "category": category,
        "amount": amount,
        "incurred_on": TERM_START.isoformat(),
        "description_ar": "إعلان عن الدورات",
        "cohort_code": "",
        "reference": "INV-771",
    }


# ---------------------------------------------------------------------------
# The two-person flow
# ---------------------------------------------------------------------------
def test_the_finance_officer_records_and_the_manager_approves(signed_in, finance, approver) -> None:
    """
    §3.4 of the matrix: FIN holds ``C E`` here and MGR holds ``A``.

    Not a rule this sprint invented — it was encoded before the app existed,
    and the screen reads it.
    """
    signed_in(finance).post(reverse("expenses:expenses"), _payload(), follow=True)

    page = signed_in(finance).get(reverse("expenses:expenses"))
    row = page.context["expenses"][0]
    assert row["status"] == "RECORDED"
    assert row["counts_toward_net_income"] is False

    done = signed_in(approver).post(
        reverse("expenses:expenses"),
        {"action": "approve", "code": "EXP-UI-1", "note_ar": "مطابق للفاتورة"},
        follow=True,
    )
    approved = done.context["expenses"][0]
    assert approved["status"] == "APPROVED"
    assert approved["counts_toward_net_income"] is True


def test_the_manager_cannot_record_an_expense(signed_in, approver) -> None:
    """MGR holds APPROVE here and not CREATE — refused on the POST."""
    response = signed_in(approver).post(reverse("expenses:expenses"), _payload())
    assert response.status_code == 403


def test_the_finance_officer_cannot_approve(signed_in, finance) -> None:
    """And the converse — the recorder holds no APPROVE at all."""
    signed_in(finance).post(reverse("expenses:expenses"), _payload(), follow=True)

    response = signed_in(finance).post(
        reverse("expenses:expenses"), {"action": "approve", "code": "EXP-UI-1"}
    )
    assert response.status_code == 403


def test_nobody_approves_the_entry_they_recorded(finance, seeded_settings) -> None:
    """
    D-18 at the service, and again at the database.

    Reachable only if one person somehow held both permissions — which the
    matrix does not grant today, and the constraint refuses regardless.
    """
    from apps.people.models import Role, User

    both = User.objects.create_user(
        username="mgr.both", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    expense = expense_service.record(
        actor=finance,
        code="EXP-D18",
        category="OTHER",
        amount=Decimal("50.000"),
        incurred_on=TERM_START,
        description_ar="قيد",
    )
    expense.created_by = both
    expense.save(update_fields=["created_by"])

    with pytest.raises(ValidationError, match="D-18"):
        expense_service.approve(actor=both, expense=expense)


# ---------------------------------------------------------------------------
# The entry itself
# ---------------------------------------------------------------------------
def test_an_unknown_category_is_refused(finance) -> None:
    """§9.7's vocabulary is a setting, and a value outside it is a typo."""
    with pytest.raises(ValidationError, match="expense_categories"):
        expense_service.record(
            actor=finance,
            code="EXP-BAD",
            category="SPACE_TRAVEL",
            amount=Decimal("10.000"),
            incurred_on=TERM_START,
            description_ar="قيد",
        )


def test_an_entry_without_a_statement_is_refused(finance) -> None:
    """A payment with no description cannot be defended in report 7."""
    with pytest.raises(ValidationError):
        expense_service.record(
            actor=finance,
            code="EXP-NODESC",
            category="OTHER",
            amount=Decimal("10.000"),
            incurred_on=TERM_START,
            description_ar="   ",
        )


def test_an_entry_into_a_closed_period_is_refused(finance, approver) -> None:
    """
    A closed financial period is closed — that is what closing means.

    Same guard ``Receipt`` already carries; an expense backdated into a
    reconciled month would move a figure somebody has signed off.
    """
    from django.utils import timezone

    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    # A closed period carries who closed it and when — its own constraint
    # refuses one that does not, which is why the fixture supplies both.
    FinancialPeriod.objects.create(
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 9, 30),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=approver,
        closed_at=timezone.now(),
    )

    with pytest.raises(expense_service.ClosedPeriodError):
        expense_service.record(
            actor=finance,
            code="EXP-CLOSED",
            category="OTHER",
            amount=Decimal("10.000"),
            incurred_on=TERM_START,
            description_ar="قيد متأخر",
        )


def test_a_rejection_needs_its_reason(finance, approver) -> None:
    expense = expense_service.record(
        actor=finance,
        code="EXP-REJ",
        category="OTHER",
        amount=Decimal("10.000"),
        incurred_on=TERM_START,
        description_ar="قيد",
    )
    with pytest.raises(ValidationError):
        expense_service.reject(actor=approver, expense=expense, note_ar="  ")

    rejected = expense_service.reject(actor=approver, expense=expense, note_ar="بلا فاتورة")
    assert rejected.status == "REJECTED"
    assert rejected.counts_toward_net_income is False


def test_only_approved_entries_reach_the_subtraction(finance, approver) -> None:
    """
    §9.2 — report 2 subtracts what was APPROVED.

    An entry nobody has approved is a claim about money, not an agreed fact
    about it, and net income must not fall on the strength of one.
    """
    expense_service.record(
        actor=finance,
        code="EXP-A",
        category="MARKETING",
        amount=Decimal("100.000"),
        incurred_on=TERM_START,
        description_ar="أ",
    )
    second = expense_service.record(
        actor=finance,
        code="EXP-B",
        category="MARKETING",
        amount=Decimal("40.000"),
        incurred_on=TERM_START,
        description_ar="ب",
    )
    assert expense_service.approved_total(actor=finance) == Decimal("0.000")

    expense_service.approve(actor=approver, expense=second)
    assert expense_service.approved_total(actor=finance) == Decimal("40.000")


def test_the_cashier_cannot_see_the_expenses_screen(signed_in, cashier) -> None:
    """§8 — the cashier receives money and does nothing else with it."""
    assert signed_in(cashier).get(reverse("expenses:expenses")).status_code == 403


def test_the_audit_account_sees_it_and_can_do_nothing(signed_in, seeded_settings) -> None:
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.exp", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    page = signed_in(auditor).get(reverse("expenses:expenses"))

    assert page.status_code == 200
    assert page.context["can_create"] is False
    assert page.context["can_approve"] is False
