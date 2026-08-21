"""
Granting a discount — the controls around it (BR-029 … BR-032, §5.1).

The arithmetic of who bears a discount is pinned in
``settlements/tests/test_discount_partner_effect.py``. This module is about
everything that has to be true before that arithmetic is allowed to happen:
the discount is on tuition alone, the president's reference is present, two
different people touch it, and it arrives before the money does.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.billing.models import DiscountType
from apps.billing.services import discount_service
from apps.billing.services.account_service import get_account_state

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.discount", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def other_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr2.discount", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _grant(manager, enrollment, **overrides):
    kwargs = {
        "actor": manager,
        "enrollment": enrollment,
        "discount_type": DiscountType.AMOUNT,
        "amount": Decimal("50.000"),
        "reason_ar": "حالة اجتماعية",
        "president_approval_ref": "PR-2026-77",
        "president_approval_date": TERM_START,
    }
    kwargs.update(overrides)
    return discount_service.grant_discount(**kwargs)


def test_a_discount_reduces_what_the_participant_owes(manager, make_enrollment) -> None:
    """The Discount row IS the mechanism — the account reads it directly."""
    enrollment, _quote = make_enrollment()
    before = get_account_state(enrollment).balance

    _grant(manager, enrollment)

    after = get_account_state(enrollment)
    assert after.total_discount == Decimal("50.000")
    assert after.balance == before - Decimal("50.000")


def test_the_base_is_tuition_alone(manager, make_enrollment) -> None:
    """
    §5.1 — «على الرسوم الدراسية فقط، لا على رسوم التسجيل».

    The registration fee is not merely excluded from the result; it is not in
    the question, so ``base_amount`` never sees it.
    """
    enrollment, quote = make_enrollment()

    discount = _grant(manager, enrollment)

    assert discount.base_amount == quote.course_fee
    assert discount.base_amount != quote.course_fee + quote.registration_fee


def test_a_percentage_discount_is_stored_as_the_money_it_came_to(manager, make_enrollment) -> None:
    """BR-031 — the rate is kept, but the amount is what the ledger reads."""
    enrollment, quote = make_enrollment()

    discount = _grant(
        manager,
        enrollment,
        discount_type=DiscountType.PERCENT,
        amount=None,
        rate=Decimal("10.0000"),
    )

    assert discount.rate == Decimal("10.0000")
    assert discount.amount == (quote.course_fee / Decimal("10")).quantize(Decimal("0.001"))


def test_a_discount_larger_than_the_tuition_is_refused(manager, make_enrollment) -> None:
    """Discounting past the tuition would reach the registration fee."""
    enrollment, quote = make_enrollment()

    with pytest.raises(discount_service.DiscountBaseError):
        _grant(manager, enrollment, amount=quote.course_fee + Decimal("1.000"))


def test_a_discount_without_the_presidents_reference_is_refused(manager, make_enrollment) -> None:
    """BR-030 · D-31 — the president approves from outside; the ref is the evidence."""
    enrollment, _ = make_enrollment()

    with pytest.raises(ValidationError):
        _grant(manager, enrollment, president_approval_ref="   ")


def test_a_discount_without_a_reason_is_refused(manager, make_enrollment) -> None:
    """Revenue waived says why it was waived."""
    enrollment, _ = make_enrollment()

    with pytest.raises(ValidationError):
        _grant(manager, enrollment, reason_ar="  ")


def test_a_discount_after_payment_is_refused(
    manager, make_enrollment, cashier, cash_method
) -> None:
    """
    The approved Sprint 8A boundary.

    Once money has landed on a shared line the partner's absorption has
    already happened against a base that a later discount would move, and
    unwinding that needs a mechanism this sprint does not build. §6.2 puts the
    manager's recommendation before the participant pays anyway.
    """
    from apps.cashbox.services import payment_service

    enrollment, quote = make_enrollment()
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee + (quote.registration_fee or Decimal("0.000")),
        payment_method=cash_method,
        received_on=TERM_START,
    )

    with pytest.raises(discount_service.DiscountAfterPaymentError):
        _grant(manager, enrollment)


def test_a_registration_only_payment_still_blocks_nothing(
    manager, make_enrollment, cashier, cash_method
) -> None:
    """
    Paying the registration fee alone does not block a tuition discount.

    Registration is not partner-shareable (BR-009), so nothing about the
    partner's base has been fixed yet and the discount is still safe.
    """
    from apps.cashbox.services import payment_service

    enrollment, quote = make_enrollment()
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.registration_fee,
        payment_method=cash_method,
        received_on=TERM_START,
    )

    discount = _grant(manager, enrollment)
    assert discount.amount == Decimal("50.000")


def test_nobody_approves_their_own_discount(manager, other_manager, make_enrollment) -> None:
    """D-18, refused in the service and again by the database constraint."""
    enrollment, _ = make_enrollment()
    discount = _grant(manager, enrollment)

    with pytest.raises(ValidationError):
        discount_service.approve_discount(actor=manager, discount=discount)

    approved = discount_service.approve_discount(actor=other_manager, discount=discount)
    assert approved.approved_by == other_manager
    assert approved.approved_at is not None


def test_the_finance_officer_cannot_grant_a_discount(finance, make_enrollment) -> None:
    """§8 — granting a discount is the centre manager's, and only theirs."""
    enrollment, _ = make_enrollment()

    with pytest.raises(PermissionDenied):
        _grant(finance, enrollment)


def test_a_grant_is_audited_with_its_split(manager, make_enrollment) -> None:
    from apps.core.models import AuditEvent

    enrollment, _ = make_enrollment()
    discount = _grant(manager, enrollment)

    event = AuditEvent.objects.filter(
        entity_type="billing.Discount", entity_id=str(discount.pk), action="CREATE"
    ).first()
    assert event is not None
    changes = event.changes or {}
    assert changes["university_burden"] == "50.000"
    assert changes["president_approval_ref"] == "PR-2026-77"
