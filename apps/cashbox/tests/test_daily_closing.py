"""
Daily closing — T-074 … T-081 (BR-026 … BR-028).

Two controls, and both are enforced twice: once in the service for a readable
message, once in the database so no path can go around them.

BR-028 in particular — the cashier does not approve their own day — is the
control that stops the person holding the cash also certifying how much of it
there was.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.cashbox.models import ClosingStatus, DailyClosing
from apps.cashbox.services import closing_service, payment_service
from apps.core.models import AuditEvent
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PAY_DAY = date(2026, 9, 25)
PASSWORD = "probe-password-1234"


@pytest.fixture
def day_with_receipts(cashier, cash_method, make_enrollment):
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    for amount in ("100.000", "70.000", "50.000"):
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=Decimal(amount),
            payment_method=cash_method,
            received_on=PAY_DAY,
        )
    return cashier


def test_the_system_total_comes_from_the_receipts(day_with_receipts) -> None:
    total, count = closing_service.system_total_for(cashier=day_with_receipts, closing_date=PAY_DAY)
    assert total == Decimal("220.000")
    assert count == 3


def test_a_voided_receipt_leaves_the_days_total(
    day_with_receipts, finance, cash_method, make_enrollment
) -> None:
    """A void is a reversal, so the day's takings drop by the voided amount."""
    from apps.cashbox.models import Receipt

    receipt = Receipt.objects.order_by("id").first()
    assert receipt is not None
    void = payment_service.request_void(actor=day_with_receipts, receipt=receipt, reason_ar="خطأ")
    payment_service.approve_void(actor=finance, void_record=void)

    total, count = closing_service.system_total_for(cashier=day_with_receipts, closing_date=PAY_DAY)
    assert total == Decimal("120.000")
    assert count == 2


def test_a_matching_count_opens_cleanly(day_with_receipts, finance) -> None:
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    assert closing.variance == Decimal("0.000")
    assert closing.status == ClosingStatus.OPEN
    assert closing.receipt_count == 3


def test_a_shortfall_is_flagged_rather_than_absorbed(day_with_receipts, finance) -> None:
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("200.000"),
    )
    assert closing.variance == Decimal("-20.000")
    assert closing.status == ClosingStatus.VARIANCE_PENDING


def test_t077_closing_with_a_variance_needs_a_written_resolution(
    day_with_receipts, finance
) -> None:
    """
    BR-027 — a till may close short, but never silently.

    Twenty dinars missing with no explanation is the entry that becomes
    unanswerable a month later.
    """
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("200.000"),
    )
    with pytest.raises(ValidationError, match="تسوية"):
        closing_service.reconcile(actor=finance, closing=closing)

    closing.refresh_from_db()
    assert closing.status == ClosingStatus.VARIANCE_PENDING


def test_a_variance_closes_once_it_is_explained(day_with_receipts, finance) -> None:
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("200.000"),
    )
    closing_service.reconcile(
        actor=finance,
        closing=closing,
        variance_resolution_ar="نقص 20 ديناراً — عُهدة سُلّمت نقداً وسُجّلت في اليوم التالي",
    )
    closing.refresh_from_db()
    assert closing.status == ClosingStatus.RECONCILED
    assert closing.approved_by_id == finance.pk


def test_t079_whoever_took_the_cash_cannot_approve_the_day(
    finance, cash_method, make_enrollment
) -> None:
    """
    BR-028, in the case that actually bites.

    A cashier is stopped by the permission matrix alone — row 18 gives them
    ``V C`` and no approve. The real exposure is footnote 13: the FINANCE
    OFFICER holds approve AND may have taken the cash himself that day. Then
    only BR-028 stands between one person and both halves of the control, and
    the manager must sign instead.
    """
    enrollment, _quote = make_enrollment("SC-NET", category="UNIVERSITY")
    payment_service.take_payment(
        actor=finance,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=PAY_DAY,
    )
    closing = closing_service.open_closing(
        actor=finance,
        cashier=finance,
        closing_date=PAY_DAY,
        counted_total=Decimal("100.000"),
    )

    with pytest.raises(PermissionDenied, match="BR-028"):
        closing_service.reconcile(actor=finance, closing=closing)

    manager = User.objects.create_user(
        username="mgr.closing", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    closing_service.reconcile(actor=manager, closing=closing)
    closing.refresh_from_db()
    assert closing.status == ClosingStatus.RECONCILED
    assert closing.approved_by_id == manager.pk


def test_a_cashier_is_stopped_by_the_matrix_before_br028_is_reached(
    day_with_receipts, finance
) -> None:
    """Two independent barriers, and the outer one answers first."""
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    with pytest.raises(PermissionDenied):
        closing_service.reconcile(actor=day_with_receipts, closing=closing)


def test_t079_the_database_refuses_it_too(day_with_receipts, finance) -> None:
    """
    The same control, one layer down.

    A management command or a future API bypassing the service still cannot
    write a closing signed off by the person who counted it.
    """
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    closing.approved_by = day_with_receipts
    closing.status = ClosingStatus.RECONCILED
    with pytest.raises(IntegrityError), transaction.atomic():
        closing.save()


def test_a_reconciled_closing_must_have_an_approver(day_with_receipts, finance) -> None:
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    closing.status = ClosingStatus.RECONCILED
    closing.approved_by = None
    with pytest.raises(IntegrityError), transaction.atomic():
        closing.save()


def test_the_variance_must_equal_the_difference(
    day_with_receipts, finance, seeded_settings
) -> None:
    """A variance that does not equal counted − system is a typo in evidence."""
    with pytest.raises(IntegrityError), transaction.atomic():
        DailyClosing.objects.create(
            code="CL-BAD",
            closing_date=PAY_DAY,
            cashier=day_with_receipts,
            system_total=Decimal("220.000"),
            counted_total=Decimal("200.000"),
            variance=Decimal("0.000"),
            receipt_count=3,
        )


def test_one_closing_per_cashier_per_day(day_with_receipts, finance) -> None:
    closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        closing_service.open_closing(
            actor=finance,
            cashier=day_with_receipts,
            closing_date=PAY_DAY,
            counted_total=Decimal("220.000"),
        )


def test_reconciling_attaches_the_days_receipts(day_with_receipts, finance) -> None:
    """The closing must be reproducible from the rows it closed."""
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("220.000"),
    )
    closing_service.reconcile(actor=finance, closing=closing)
    assert closing.receipts.count() == 3


def test_the_closing_is_audited_with_both_totals(day_with_receipts, finance) -> None:
    closing = closing_service.open_closing(
        actor=finance,
        cashier=day_with_receipts,
        closing_date=PAY_DAY,
        counted_total=Decimal("205.000"),
    )
    event = AuditEvent.objects.filter(entity_type="cashbox.DailyClosing").first()
    assert event is not None and event.changes is not None
    assert event.changes["system_total"] == "220.000"
    assert event.changes["counted_total"] == "205.000"
    assert event.changes["variance"] == "-15.000"
    assert closing.code


def test_the_cashier_cannot_open_a_closing_for_someone_else(
    day_with_receipts, seeded_settings
) -> None:
    """
    Footnote 14 — the cashier sees his own day and does not approve it.

    Row 18 gives the cashier ``V C`` only.
    """
    from apps.people.constants import Action, Screen
    from apps.people.permissions import policy

    other = User.objects.create_user(username="cash.other", password=PASSWORD, role=Role.CASHIER)
    assert policy.evaluate(other, Screen.CLOSING, Action.CREATE).allowed
    assert not policy.evaluate(other, Screen.CLOSING, Action.APPROVE).allowed
