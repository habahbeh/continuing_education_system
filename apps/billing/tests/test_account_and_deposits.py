"""
The balance equation and deposit settlement (DATA_MODEL §8.2, BR-092 … BR-097).

Four different questions come out of the same rows, and conflating any two is
a real accounting error: what the participant owes (gross), what the partner
shares (net), what counts as revenue (net where is_revenue), and what the
university still owes back (deposits held).

The deposit tests use the client's demo policy — General English, 25 JOD,
non-refundable after confirmation — and read it from the SNAPSHOT on the
charge line, never the live row.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.billing.models import ChargeLine, ChargeType
from apps.billing.services import deposit_service
from apps.billing.services.account_service import ZERO, get_account_state
from apps.cashbox.services import payment_service
from apps.catalog.models import DepositPolicy
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PAY_DAY = date(2026, 9, 25)
PASSWORD = "probe-password-1234"


# ---------------------------------------------------------------------------
# The balance equation
# ---------------------------------------------------------------------------
def test_balance_is_due_minus_paid(cashier, cash_method, make_enrollment) -> None:
    """Network engineering, university student: 20 + 250 = 270 owed."""
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")

    state = get_account_state(enrollment)
    assert state.total_due == Decimal("270.000")
    assert state.total_paid == ZERO
    assert state.balance == Decimal("270.000")
    assert state.participant_owes

    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("270.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    settled = get_account_state(enrollment)
    assert settled.balance == ZERO
    assert settled.is_settled


def test_the_sign_convention_distinguishes_who_owes_whom(
    cashier, cash_method, make_enrollment
) -> None:
    """
    WORKFLOWS §6.5 — positive means the participant owes; negative means we do.

    The demo used the same negative number for both, so a credit balance and a
    debt were indistinguishable in the clearance screen.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("300.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    state = get_account_state(enrollment)
    assert state.balance == Decimal("-30.000")
    assert state.centre_owes
    assert not state.participant_owes
    assert not state.is_settled, "a credit balance also blocks clearance (BR-073)"


def test_revenue_excludes_the_deposit(cashier, cash_method, make_enrollment) -> None:
    """
    BR-092 — a refundable deposit is a liability, never income.

    English level 1: 15 registration + 90 tuition are revenue; the 25 deposit
    is not. Counting it would inflate both income and every partner's share.
    """
    enrollment, _quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")

    state = get_account_state(enrollment)
    assert state.total_due == Decimal("130.000")  # 15 + 25 + 90
    assert state.revenue == Decimal("105.000")  # 15 + 90 — deposit excluded


def test_the_deposit_liability_tracks_what_is_still_held(
    cashier, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")
    assert get_account_state(enrollment).deposit_liability == Decimal("25.000")

    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("130.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert get_account_state(enrollment).deposit_liability == Decimal("25.000")


def test_the_partner_base_excludes_registration(cashier, cash_method, make_enrollment) -> None:
    """
    BR-009 / BR-044 — registration is outside the partner's base, and the
    base is what was RECEIVED, not what was invoiced.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("120.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    # 20 goes to registration (not shareable), 100 to tuition (shareable).
    assert get_account_state(enrollment).partner_base == Decimal("100.000")


def test_nothing_invoiced_but_unpaid_reaches_the_partner(make_enrollment) -> None:
    """Cash basis — an invoice earns a partner nothing until it is collected."""
    enrollment, _quote = make_enrollment("SC-NET")
    assert get_account_state(enrollment).partner_base == ZERO


# ---------------------------------------------------------------------------
# Deposits — returning
# ---------------------------------------------------------------------------
@pytest.fixture
def paid_deposit(cashier, cash_method, make_enrollment):
    enrollment, _quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("130.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    return enrollment


def test_the_deposit_line_carries_its_policy_snapshot(paid_deposit) -> None:
    """
    C-26 / ADR-012 — the terms travel with the charge.

    Years later the question is not what the policy says now, but what this
    participant agreed to.
    """
    line = deposit_service.deposit_line_for(paid_deposit)
    assert line is not None
    snapshot = line.deposit_policy_snapshot
    assert snapshot is not None
    assert snapshot["code"] == "DEP-ENG-GEN"
    assert snapshot["refund_trigger"] == "ON_CENTRE_CANCELLATION"
    assert snapshot["allows_partial_deduction"] is False


def test_editing_the_policy_does_not_change_an_existing_charge(paid_deposit) -> None:
    policy_row = DepositPolicy.objects.get(code="DEP-ENG-GEN")
    policy_row.refund_trigger = "ON_SOMETHING_ELSE"
    policy_row.save()

    line = deposit_service.deposit_line_for(paid_deposit)
    assert line is not None
    assert line.deposit_policy_snapshot is not None
    assert line.deposit_policy_snapshot["refund_trigger"] == "ON_CENTRE_CANCELLATION"


def test_returning_a_deposit_uses_the_snapshot_trigger(finance, paid_deposit) -> None:
    record = deposit_service.return_deposit(
        actor=finance, enrollment=paid_deposit, returned_on=date(2026, 12, 20)
    )
    assert record.amount == Decimal("25.000")
    assert record.trigger == "ON_CENTRE_CANCELLATION"
    assert get_account_state(paid_deposit).deposit_liability == ZERO


def test_a_deposit_is_settled_only_once(finance, paid_deposit) -> None:
    deposit_service.return_deposit(
        actor=finance, enrollment=paid_deposit, returned_on=date(2026, 12, 20)
    )
    with pytest.raises(ValidationError, match="مُسوّى"):
        deposit_service.return_deposit(
            actor=finance, enrollment=paid_deposit, returned_on=date(2026, 12, 21)
        )


def test_the_policy_can_forbid_partial_deduction(finance, paid_deposit) -> None:
    """
    The client's demo policy sets allows_partial_deduction = False.

    Read from the snapshot as DATA — no code branch names this policy.
    """
    with pytest.raises(ValidationError, match="الحسم الجزئي"):
        deposit_service.return_deposit(
            actor=finance,
            enrollment=paid_deposit,
            returned_on=date(2026, 12, 20),
            deduction_amount=Decimal("5.000"),
            deduction_reason_ar="عهدة غير مُعادة",
        )


def test_a_permitting_policy_allows_a_deduction_with_a_reason(finance, paid_deposit) -> None:
    """Changing the policy row changes the behaviour — no code, no migration."""
    line = deposit_service.deposit_line_for(paid_deposit)
    assert line is not None
    assert line.deposit_policy_snapshot is not None
    line.deposit_policy_snapshot = {
        **line.deposit_policy_snapshot,
        "allows_partial_deduction": True,
    }
    line.save(update_fields=["deposit_policy_snapshot"])

    record = deposit_service.return_deposit(
        actor=finance,
        enrollment=paid_deposit,
        returned_on=date(2026, 12, 20),
        deduction_amount=Decimal("5.000"),
        deduction_reason_ar="بطاقة مواصلات لم تُعَد",
    )
    assert record.amount == Decimal("20.000")
    assert record.deduction_amount == Decimal("5.000")


def test_a_deduction_without_a_reason_is_refused(finance, paid_deposit) -> None:
    """BR-097 — money withheld silently is indistinguishable from a shortfall."""
    line = deposit_service.deposit_line_for(paid_deposit)
    assert line is not None
    assert line.deposit_policy_snapshot is not None
    line.deposit_policy_snapshot = {
        **line.deposit_policy_snapshot,
        "allows_partial_deduction": True,
    }
    line.save(update_fields=["deposit_policy_snapshot"])

    with pytest.raises(ValidationError, match="مبرراً"):
        deposit_service.return_deposit(
            actor=finance,
            enrollment=paid_deposit,
            returned_on=date(2026, 12, 20),
            deduction_amount=Decimal("5.000"),
            deduction_reason_ar="",
        )


def test_t225_a_programme_without_a_policy_has_no_deposit_to_settle(
    finance, make_enrollment
) -> None:
    """BR-096 — absent, not zero. There is nothing to return."""
    enrollment, _quote = make_enrollment("SC-NET")
    assert deposit_service.deposit_line_for(enrollment) is None
    with pytest.raises(ValidationError, match="بلا سياسة تأمين"):
        deposit_service.return_deposit(
            actor=finance, enrollment=enrollment, returned_on=date(2026, 12, 20)
        )


# ---------------------------------------------------------------------------
# Deposits — forfeiture
# ---------------------------------------------------------------------------
def test_forfeiture_creates_a_new_revenue_line_and_leaves_the_deposit_intact(
    finance, paid_deposit, seeded_settings
) -> None:
    """
    C-21 — a deposit line can never become revenue, so conversion is a SECOND
    line, not an edit.

    Editing the original would breach the constraint and erase the fact that
    the money arrived as a deposit. Two rows tell the truth.
    """
    approver = User.objects.create_user(
        username="mgr.forfeit", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    deposit_line = deposit_service.deposit_line_for(paid_deposit)
    assert deposit_line is not None

    record = deposit_service.forfeit_deposit(
        actor=finance,
        enrollment=paid_deposit,
        reason="CONFIRMED",
        justification_ar="غير مسترد بعد تأكيد التسجيل حسب السياسة",
        forfeited_on=date(2026, 12, 20),
        approved_by=approver,
    )

    deposit_line.refresh_from_db()
    assert deposit_line.is_revenue is False, "the deposit line was altered"
    assert deposit_line.gross_amount == Decimal("25.000")

    revenue = record.revenue_charge_line
    assert revenue is not None
    assert revenue.is_revenue is True
    assert revenue.net_amount == Decimal("25.000")

    state = get_account_state(paid_deposit)
    assert state.deposit_liability == ZERO
    assert state.revenue == Decimal("130.000")  # 15 + 90 + 25 forfeited


def test_the_forfeit_reason_must_appear_in_the_policy(finance, paid_deposit) -> None:
    """
    Q-30 — driven by the policy row, not a hardcoded list of states.

    The demo policy forfeits on CONFIRMED. Anything else is refused, and the
    message names what the policy does allow.
    """
    with pytest.raises(ValidationError, match="غير مذكور"):
        deposit_service.forfeit_deposit(
            actor=finance,
            enrollment=paid_deposit,
            reason="DISMISSED",
            justification_ar="سبب غير مذكور في السياسة",
            forfeited_on=date(2026, 12, 20),
        )


def test_a_new_reason_becomes_valid_by_editing_the_policy_data(finance, paid_deposit) -> None:
    """The client can introduce a forfeiture trigger with no code change."""
    line = deposit_service.deposit_line_for(paid_deposit)
    assert line is not None
    assert line.deposit_policy_snapshot is not None
    line.deposit_policy_snapshot = {
        **line.deposit_policy_snapshot,
        "forfeit_on": ["CONFIRMED", "A_BRAND_NEW_REASON"],
    }
    line.save(update_fields=["deposit_policy_snapshot"])

    record = deposit_service.forfeit_deposit(
        actor=finance,
        enrollment=paid_deposit,
        reason="A_BRAND_NEW_REASON",
        justification_ar="حالة جديدة أضافها العميل",
        forfeited_on=date(2026, 12, 20),
    )
    assert record.reason == "A_BRAND_NEW_REASON"


def test_forfeiture_requires_a_justification(finance, paid_deposit) -> None:
    with pytest.raises(ValidationError, match="مبرراً"):
        deposit_service.forfeit_deposit(
            actor=finance,
            enrollment=paid_deposit,
            reason="CONFIRMED",
            justification_ar="   ",
            forfeited_on=date(2026, 12, 20),
        )


def test_nobody_approves_a_forfeiture_they_executed(finance, paid_deposit) -> None:
    """D-18 — taking a participant's money and signing it off yourself."""
    with pytest.raises(PermissionDenied):
        deposit_service.forfeit_deposit(
            actor=finance,
            enrollment=paid_deposit,
            reason="CONFIRMED",
            justification_ar="مبرر",
            forfeited_on=date(2026, 12, 20),
            approved_by=finance,
        )


def test_a_deposit_never_reaches_the_partner_base(cashier, cash_method, make_enrollment) -> None:
    """
    BR-092 — the deposit is excluded from the partner's base by default.

    The agreement may override it (Q-01), but nothing here opts in silently.
    """
    enrollment, _quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("130.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    deposit_line = ChargeLine.objects.get(enrollment=enrollment, charge_type=ChargeType.DEPOSIT)
    assert deposit_line.is_partner_shareable is False
    # 90 tuition only — registration and deposit are both outside the base.
    assert get_account_state(enrollment).partner_base == Decimal("90.000")
