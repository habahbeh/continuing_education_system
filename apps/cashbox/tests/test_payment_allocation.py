"""
Payments, allocation and voiding — T-010 … T-024 (BR-020 … BR-025).

The allocation table is what the demo lacked. These tests assert the three
things its absence broke: that a payment's split is STORED rather than guessed,
that the parts always equal the whole to the fils, and that voiding preserves
the original instead of erasing it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.billing.models import ChargeType
from apps.billing.services.account_service import ZERO, get_account_state
from apps.cashbox.models import AllocationType, PaymentAllocation, Receipt, ReceiptStatus
from apps.cashbox.services import payment_service
from apps.core.models import AuditEvent
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PAY_DAY = date(2026, 9, 25)
PASSWORD = "probe-password-1234"


def _allocations(receipt: Receipt) -> list[PaymentAllocation]:
    return list(receipt.allocations.order_by("id"))


def _types(receipt: Receipt) -> list[str]:
    """Charge types in allocation order; a credit line reads as CREDIT."""
    return [a.charge_line.charge_type if a.charge_line else "CREDIT" for a in _allocations(receipt)]


# ---------------------------------------------------------------------------
# BR-022 — the split is stored, and ordered
# ---------------------------------------------------------------------------
def test_t016_payment_is_split_across_lines_in_order(
    cashier: User, cash_method, make_enrollment
) -> None:
    """
    Registration first, then tuition — and both parts are ROWS.

    Network engineering for a university student: 20 registration + 250
    tuition = 270 due. Paying 100 must record 20 against registration and 80
    against tuition, not a single "paid 100" that later has to be re-derived.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )

    allocations = _allocations(receipt)
    assert len(allocations) == 2
    assert _types(receipt) == [ChargeType.REGISTRATION, ChargeType.TUITION]
    assert allocations[0].amount == Decimal("20.000")
    assert allocations[1].amount == Decimal("80.000")


def test_deposit_is_allocated_before_tuition(cashier: User, cash_method, make_enrollment) -> None:
    """
    BR-022 ordering with a deposit present — the client's demo policy.

    General English level 1: 15 registration + 25 deposit + 90 tuition.
    """
    enrollment, quote = make_enrollment("SC-ENG-GEN", level=1, category="UNIVERSITY")
    assert quote.has_deposit

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert _types(receipt) == [
        ChargeType.REGISTRATION,
        ChargeType.DEPOSIT,
        ChargeType.TUITION,
    ]


def test_the_parts_always_equal_the_whole(cashier: User, cash_method, make_enrollment) -> None:
    """BR-090 — Σ allocations == receipt.amount, exactly, no fils adrift."""
    enrollment, _quote = make_enrollment("SC-NET")

    for amount in ("1.000", "37.500", "269.999", "270.000", "500.000"):
        receipt = payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal(amount),
            payment_method=cash_method,
            received_on=PAY_DAY,
        )
        total = sum((a.amount for a in _allocations(receipt)), ZERO)
        assert total == Decimal(amount), f"allocation drift on {amount}"


def test_t018_overpayment_becomes_an_unallocated_credit(
    cashier: User, cash_method, make_enrollment
) -> None:
    """
    BR-023 — surplus is a credit line, never forced onto a charge.

    Forcing it would manufacture a debt that had already been settled.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    credit = [a for a in _allocations(receipt) if a.charge_line is None]
    assert len(credit) == 1
    assert credit[0].amount == Decimal("130.000")  # 400 − (20 + 250)

    state = get_account_state(enrollment)
    assert state.unallocated_credit == Decimal("130.000")
    assert state.centre_owes


def test_a_second_payment_does_not_re_pay_a_settled_line(
    cashier: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")

    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("20.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    second = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert _types(second) == [ChargeType.TUITION]


# ---------------------------------------------------------------------------
# BR-020 — the minimum first payment
# ---------------------------------------------------------------------------
def test_t010_diploma_first_payment_below_the_minimum_is_refused(
    cashier: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("DIP-ID")
    with pytest.raises(ValidationError, match="400"):
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal("300.000"),
            payment_method=cash_method,
            received_on=PAY_DAY,
        )
    assert not Receipt.objects.exists()


def test_t011_diploma_first_payment_at_the_minimum_is_accepted(
    cashier: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("DIP-ID")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert receipt.amount == Decimal("400.000")


def test_t012_the_minimum_governs_only_the_first_payment(
    cashier: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("DIP-ID")
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    second = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert second.amount == Decimal("100.000")


def test_t013_a_short_course_has_no_minimum(cashier: User, cash_method, make_enrollment) -> None:
    enrollment, _quote = make_enrollment("SC-NET")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert receipt.amount == Decimal("50.000")


# ---------------------------------------------------------------------------
# D-01 — the centre manager never takes cash
# ---------------------------------------------------------------------------
def test_t152_centre_manager_cannot_take_a_payment(
    seeded_settings: None, cash_method, make_enrollment
) -> None:
    """BR-081 — enforced at the service, so the API path is closed too."""
    enrollment, _quote = make_enrollment("SC-NET")
    manager = User.objects.create_user(
        username="mgr.cash", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    with pytest.raises(PermissionDenied):
        payment_service.take_payment(
            actor=manager,
            enrollment=enrollment,
            amount=Decimal("50.000"),
            payment_method=cash_method,
            received_on=PAY_DAY,
        )
    assert AuditEvent.objects.filter(denial_rule="D-01").exists()
    assert not Receipt.objects.exists()


def test_the_receipt_number_is_gapless_and_year_partitioned(
    cashier: User, cash_method, make_enrollment
) -> None:
    """Q-03 — the internal number is the system's own reference."""
    enrollment, _quote = make_enrollment("SC-NET")
    numbers = [
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal("10.000"),
            payment_method=cash_method,
            received_on=PAY_DAY,
        ).internal_receipt_number
        for _ in range(3)
    ]
    assert numbers == ["R-2026-00001", "R-2026-00002", "R-2026-00003"]


def test_a_zero_receipt_is_impossible(cashier: User, cash_method, make_enrollment) -> None:
    """The demo issued R-20260722-018 for zero."""
    enrollment, _quote = make_enrollment("SC-NET")
    with pytest.raises(ValidationError):
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=ZERO,
            payment_method=cash_method,
            received_on=PAY_DAY,
        )


# ---------------------------------------------------------------------------
# BR-025 — voiding is a reversal, not an erasure
# ---------------------------------------------------------------------------
def test_t021_voiding_reverses_and_preserves_the_original(
    cashier: User, finance: User, cash_method, make_enrollment
) -> None:
    """
    The demo zeroed the original receipt, erasing that money was ever taken.

    Here the amount and number survive untouched, mirrored by negative
    allocations — so the pair reads as a history.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    original_count = receipt.allocations.count()

    void = payment_service.request_void(
        actor=cashier, receipt=receipt, reason_ar="خطأ في المبلغ المُدخَل"
    )
    payment_service.approve_void(actor=finance, void_record=void)

    receipt.refresh_from_db()
    assert receipt.status == ReceiptStatus.VOIDED
    assert receipt.amount == Decimal("100.000"), "the original amount was altered"
    assert receipt.internal_receipt_number

    assert receipt.allocations.count() == original_count * 2
    assert sum((a.amount for a in receipt.allocations.all()), ZERO) == ZERO


def test_t021_a_voided_receipt_stops_counting_as_paid(
    cashier: User, finance: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    before = get_account_state(enrollment).balance

    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    assert get_account_state(enrollment).balance == before - Decimal("100.000")

    void = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="إلغاء")
    payment_service.approve_void(actor=finance, void_record=void)

    assert get_account_state(enrollment).balance == before


def test_the_cashier_cannot_approve_their_own_void(
    cashier: User, cash_method, make_enrollment
) -> None:
    """
    Δ-06 / D-18 — the demo let a cashier void alone.

    That puts the person who took the cash in sole control of unmaking the
    record of it.
    """
    enrollment, _quote = make_enrollment("SC-NET")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    void = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")

    with pytest.raises(PermissionDenied):
        payment_service.approve_void(actor=cashier, void_record=void)

    receipt.refresh_from_db()
    assert receipt.status == ReceiptStatus.ISSUED


def test_a_void_requires_a_reason(cashier: User, cash_method, make_enrollment) -> None:
    enrollment, _quote = make_enrollment("SC-NET")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    with pytest.raises(ValidationError):
        payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="   ")


def test_the_reversal_records_who_and_why(
    cashier: User, finance: User, cash_method, make_enrollment
) -> None:
    enrollment, _quote = make_enrollment("SC-NET")
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    void = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="شيك مرتجع")
    payment_service.approve_void(actor=finance, void_record=void)

    reversal = receipt.allocations.filter(amount__lt=0).first()
    assert reversal is not None
    assert reversal.allocation_type == AllocationType.MANUAL
    assert reversal.allocated_by_id == finance.pk
    assert "شيك مرتجع" in reversal.manual_reason_ar
