"""
Exceptional refunds — the three steps and their controls (BR-033 … BR-036).

§5.3 begins «الأصل: لا استرداد». Everything here is about making the exception
expensive enough to be deliberate: two external documents, three separate
acts, and money that moves by reversal so the original stays readable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.billing.models import RefundStatus
from apps.billing.services import refund_service
from apps.billing.services.account_service import get_account_state

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.refund", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def paid(make_enrollment, cashier, cash_method):
    """An enrolment that has paid its way in full."""
    from apps.cashbox.services import payment_service

    enrollment, quote = make_enrollment()
    total = quote.course_fee + (quote.registration_fee or Decimal("0.000"))
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=total,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    return enrollment, total


def _request(finance, enrollment, amount, **overrides):
    kwargs = {
        "actor": finance,
        "enrollment": enrollment,
        "refund_type": "PARTIAL",
        "amount": amount,
        "reason_ar": "إلغاء الدورة لعدم اكتمال العدد",
        "official_letter_ref": "LT-2026-9",
        "official_letter_date": TERM_START,
        "president_approval_ref": "PR-2026-9",
        "president_approval_date": TERM_START,
        "code": "RF-001",
    }
    kwargs.update(overrides)
    return refund_service.request_refund(**kwargs)


def test_the_three_steps_run_in_order(finance, manager, paid) -> None:
    """Requested by finance, approved by the manager, executed by finance."""
    enrollment, _total = paid

    refund = _request(finance, enrollment, Decimal("100.000"))
    assert refund.status == RefundStatus.REQUESTED

    refund = refund_service.approve_refund(actor=manager, refund=refund)
    assert refund.status == RefundStatus.APPROVED

    refund = refund_service.execute_refund(actor=finance, refund=refund, executed_on=TERM_START)
    assert refund.status == RefundStatus.EXECUTED
    assert refund.executed_at is not None


def test_execution_cannot_precede_approval(finance, paid) -> None:
    """BR-034 — approval is the authority the execution acts on."""
    enrollment, _ = paid
    refund = _request(finance, enrollment, Decimal("100.000"))

    with pytest.raises(refund_service.RefundStateError):
        refund_service.execute_refund(actor=finance, refund=refund, executed_on=TERM_START)


def test_both_external_documents_are_required(finance, paid) -> None:
    """BR-034 — the letter AND the president's approval, or no refund."""
    enrollment, _ = paid

    with pytest.raises(ValidationError):
        _request(finance, enrollment, Decimal("50.000"), official_letter_ref="  ")

    with pytest.raises(ValidationError):
        _request(finance, enrollment, Decimal("50.000"), president_approval_ref="  ")


def test_nobody_approves_their_own_refund(finance, paid) -> None:
    """D-18 — and the database refuses it too."""
    enrollment, _ = paid
    refund = _request(finance, enrollment, Decimal("50.000"))

    with pytest.raises((ValidationError, PermissionDenied)):
        refund_service.approve_refund(actor=finance, refund=refund)


def test_more_cannot_be_returned_than_was_received(finance, paid) -> None:
    """The ceiling is what was collected, not what was invoiced."""
    enrollment, total = paid

    with pytest.raises(refund_service.RefundExceedsPaidError):
        _request(finance, enrollment, total + Decimal("1.000"))


def test_the_money_moves_by_reversal_and_nothing_is_edited(finance, manager, paid) -> None:
    """
    BR-025 — the original allocation stays beside its reversal.

    A refund that edited or deleted the original row would leave a receipt in
    the participant's hand with no counterpart in the ledger.
    """
    from apps.cashbox.models import PaymentAllocation

    enrollment, total = paid
    before = PaymentAllocation.objects.filter(enrollment=enrollment).count()

    refund = _request(finance, enrollment, Decimal("100.000"))
    refund_service.approve_refund(actor=manager, refund=refund)
    refund_service.execute_refund(actor=finance, refund=refund, executed_on=TERM_START)

    after = PaymentAllocation.objects.filter(enrollment=enrollment)
    assert after.count() > before
    assert after.filter(amount__lt=0).exists()

    state = get_account_state(enrollment)
    assert state.total_paid == total - Decimal("100.000")


def test_a_rejection_is_recorded_as_a_decision(finance, manager, paid) -> None:
    enrollment, _ = paid
    refund = _request(finance, enrollment, Decimal("50.000"))

    refund = refund_service.reject_refund(actor=manager, refund=refund, reason_ar="لا سند نظامي")
    assert refund.status == RefundStatus.REJECTED


def test_a_rejection_needs_its_reason(finance, manager, paid) -> None:
    enrollment, _ = paid
    refund = _request(finance, enrollment, Decimal("50.000"))

    with pytest.raises(ValidationError):
        refund_service.reject_refund(actor=manager, refund=refund, reason_ar="   ")


def test_the_cashier_cannot_raise_a_refund(cashier, paid) -> None:
    """§8 — the cashier receives money and does nothing else with it."""
    enrollment, _ = paid

    with pytest.raises(PermissionDenied):
        _request(cashier, enrollment, Decimal("50.000"))
