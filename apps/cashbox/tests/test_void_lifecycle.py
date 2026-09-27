"""
The void, end to end — every case a cashier or a finance officer can meet.

A void request has no financial effect until decided, but it is a HOLD: the
steps that stand on the money (approving the enrolment, certifying a
clearance) wait for the decision. Approval reverses the money and rolls an
enrolment that stood on it alone back to «بانتظار الدفع»; it is refused once
the day's cash was counted or a clearance/certificate was built on the money
(then the instrument is a refund). A request may be rejected, and raised
again. D-18 keeps the asking and the deciding in different hands.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.cashbox.services import payment_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def paid(make_enrollment, cashier, cash_method):
    """An enrolment with one receipt covering its course fee (first payment)."""
    enrollment, quote = make_enrollment()
    receipt = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=quote.course_fee,
        payment_method=cash_method,
        received_on=date(2026, 9, 20),
    )
    return enrollment, receipt


def _balance(enrollment) -> Decimal:
    from apps.billing.services.account_service import get_account_state

    return get_account_state(enrollment).balance


# ---------------------------------------------------------------------------
# Pending: no money moves, but the money is on hold
# ---------------------------------------------------------------------------
def test_a_pending_request_moves_no_money_but_is_named_everywhere(
    signed_in, cashier, finance, registrar, paid
) -> None:
    enrollment, receipt = paid
    before = _balance(enrollment)

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")

    receipt.refresh_from_db()
    assert receipt.status == "ISSUED"
    assert _balance(enrollment) == before
    assert payment_service.pending_void_for(enrollment) == receipt.internal_receipt_number

    # The register, the enrolments, the statement and the receipt all say so.
    register = (
        signed_in(finance).get(reverse("cashbox:payments"), {"range": "all"}).content.decode()
    )
    assert "طلب إلغاء معلّق" in register
    rows = signed_in(registrar).get(reverse("operations:enrollments")).content.decode()
    assert "إلغاء معلّق" in rows and receipt.internal_receipt_number in rows
    statement = (
        signed_in(registrar)
        .get(reverse("operations:account", args=[enrollment.code]))
        .content.decode()
    )
    assert "بطلب إلغاء معلّق" in statement
    page = (
        signed_in(finance)
        .get(reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number]))
        .content.decode()
    )
    assert "طلب معلّق" in page and 'id="void-approve"' in page and 'id="void-reject"' in page


def test_the_steps_that_stand_on_the_money_wait_for_the_decision(
    cashier, finance, registrar, manager, paid
) -> None:
    from apps.operations.services import enrollment_service

    enrollment, receipt = paid
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")

    with pytest.raises(ValidationError, match="طلب إلغاء معلّق"):
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    # Once decided (rejected here), the step goes through.
    payment_service.reject_void(
        actor=finance,
        void_record=payment_service.void_instance(
            actor=finance, number=receipt.internal_receipt_number
        ),
        reason_ar="السند صحيح",
    )
    approved = enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    assert approved.status == "ACTIVE"


def test_the_second_finance_step_of_a_clearance_waits_too(cashier, finance, manager, paid) -> None:
    from apps.operations.models import EnrollmentStatus
    from apps.operations.services import clearance_service

    enrollment, receipt = paid
    enrollment.status = EnrollmentStatus.WITHDRAWN
    enrollment.save(update_fields=["status"])
    clearance = clearance_service.open_clearance(
        actor=manager, enrollment=enrollment, opened_on=date(2026, 9, 21)
    )
    clearance.steps.filter(step_number=1).update(is_done=True, certified_by=manager)
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")

    with pytest.raises(ValidationError, match="طلب إلغاء معلّق"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)


# ---------------------------------------------------------------------------
# Deciding: approve, reject, raise again — by the right hands
# ---------------------------------------------------------------------------
def test_approval_reverses_the_money_and_rolls_the_enrolment_back(
    cashier, finance, registrar, manager, paid
) -> None:
    from apps.operations.services import enrollment_service

    enrollment, receipt = paid
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    assert enrollment.status == "ACTIVE"
    owed_before = _balance(enrollment)

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مبلغ خاطئ")
    void = payment_service.void_instance(actor=finance, number=receipt.internal_receipt_number)
    consequences = payment_service.void_consequences(void)
    assert [c["code"] for c in consequences["reverting"]] == [enrollment.code]

    payment_service.approve_void(actor=finance, void_record=void)

    receipt.refresh_from_db()
    enrollment.refresh_from_db()
    assert receipt.status == "VOIDED"
    assert _balance(enrollment) == owed_before + receipt.amount
    assert enrollment.status == "PENDING_FINANCE"
    assert enrollment.status_history.latest("id").reason_ar.startswith("أُلغي السند")
    assert enrollment.status_history.latest("id").reference == receipt.internal_receipt_number


def test_an_enrolment_with_other_money_keeps_its_state(
    cashier, finance, registrar, manager, make_enrollment, cash_method
) -> None:
    from apps.operations.services import enrollment_service

    enrollment, _ = make_enrollment(program_code="DIP-ID", category="CENTER")
    first = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("400.000"),
        payment_method=cash_method,
        received_on=date(2026, 9, 20),
    )
    second = payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("100.000"),
        payment_method=cash_method,
        received_on=date(2026, 9, 21),
    )
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    payment_service.request_void(actor=cashier, receipt=second, reason_ar="مكرر")
    void = payment_service.void_instance(actor=finance, number=second.internal_receipt_number)
    assert payment_service.void_consequences(void)["reverting"] == []
    payment_service.approve_void(actor=finance, void_record=void)

    enrollment.refresh_from_db()
    assert enrollment.status == "ACTIVE"
    assert first.allocations.filter(reversed_by__isnull=False).count() == 0


def test_the_requester_may_neither_approve_nor_reject(cashier, paid) -> None:
    from apps.people.models import Role, User

    _, receipt = paid
    both = User.objects.create_user(username="both.hands", password="x", role=Role.SUPER_ADMIN)
    payment_service.request_void(actor=both, receipt=receipt, reason_ar="خطأ")
    void = payment_service.void_instance(actor=both, number=receipt.internal_receipt_number)

    with pytest.raises(PermissionDenied, match="D-18"):
        payment_service.approve_void(actor=both, void_record=void)
    with pytest.raises(PermissionDenied, match="D-18"):
        payment_service.reject_void(actor=both, void_record=void, reason_ar="لا")


def test_a_rejected_request_leaves_the_receipt_issued_and_may_be_raised_again(
    signed_in, cashier, finance, paid
) -> None:
    enrollment, receipt = paid
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    void = payment_service.void_instance(actor=finance, number=receipt.internal_receipt_number)

    with pytest.raises(ValidationError, match="سبب الرفض"):
        payment_service.reject_void(actor=finance, void_record=void, reason_ar=" ")
    payment_service.reject_void(actor=finance, void_record=void, reason_ar="السند صحيح")

    receipt.refresh_from_db()
    assert receipt.status == "ISSUED"
    assert payment_service.pending_void_for(enrollment) == ""
    page = (
        signed_in(cashier)
        .get(reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number]))
        .content.decode()
    )
    assert "طلب سابق مرفوض" in page and "السند صحيح" in page
    assert 'id="void-request"' in page  # may ask again

    again = payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ مؤكد")
    assert again.pk == void.pk and again.is_pending and again.reason_ar == "خطأ مؤكد"
    with pytest.raises(ValidationError, match="معلّق بالفعل"):
        payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="مرة ثالثة")


def test_the_screen_rejects_through_its_own_form(signed_in, cashier, finance, paid) -> None:
    _, receipt = paid
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    detail = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])

    response = signed_in(finance).post(
        detail, {"action": "reject", "reason_ar": "السند صحيح"}, follow=True
    )
    assert response.context["receipt"]["void_is_rejected"] is True
    assert "بقي السند صادراً" in response.content.decode()

    # The cashier's screen never carries the deciding controls.
    page = signed_in(cashier).get(detail).content.decode()
    assert 'name="action" value="reject"' not in page
    assert 'name="action" value="approve"' not in page


# ---------------------------------------------------------------------------
# Refused: when the void is the wrong instrument
# ---------------------------------------------------------------------------
def test_a_counted_day_cannot_be_voided(signed_in, cashier, finance, paid) -> None:
    from apps.cashbox.services import closing_service

    _, receipt = paid
    closing_service.open_closing(
        actor=finance,
        cashier=cashier,
        closing_date=receipt.received_on,
        counted_total=receipt.amount,
    )
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    void = payment_service.void_instance(actor=finance, number=receipt.internal_receipt_number)

    blockers = payment_service.void_blockers(void)
    assert blockers and "إقفال يومي" in blockers[0]
    with pytest.raises(ValidationError, match="إقفال يومي"):
        payment_service.approve_void(actor=finance, void_record=void)

    page = (
        signed_in(finance)
        .get(reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number]))
        .content.decode()
    )
    assert "لا يمكن اعتماد هذا الإلغاء" in page and "الاسترداد" in page
    assert 'id="void-approve"' not in page and 'id="void-reject"' in page


def test_money_a_clearance_was_certified_on_cannot_be_voided(
    cashier, finance, manager, paid
) -> None:
    from apps.operations.models import EnrollmentStatus
    from apps.operations.services import clearance_service

    enrollment, receipt = paid
    enrollment.status = EnrollmentStatus.WITHDRAWN
    enrollment.save(update_fields=["status"])
    clearance = clearance_service.open_clearance(
        actor=manager, enrollment=enrollment, opened_on=date(2026, 9, 21)
    )
    clearance.steps.filter(step_number=2).update(certified_by=finance)

    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    void = payment_service.void_instance(actor=finance, number=receipt.internal_receipt_number)

    assert any("براءة ذمة" in b for b in payment_service.void_blockers(void))
    with pytest.raises(ValidationError, match="براءة"):
        payment_service.approve_void(actor=finance, void_record=void)


def test_after_a_void_the_till_offers_a_replacement_receipt(
    signed_in, cashier, finance, paid
) -> None:
    enrollment, receipt = paid
    payment_service.request_void(actor=cashier, receipt=receipt, reason_ar="خطأ")
    payment_service.approve_void(
        actor=finance,
        void_record=payment_service.void_instance(
            actor=finance, number=receipt.internal_receipt_number
        ),
    )

    page = (
        signed_in(cashier)
        .get(reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number]))
        .content.decode()
    )
    assert "إصدار سند بديل" in page
    assert f"{reverse('cashbox:payment-new')}?enrollment={enrollment.code}" in page
    till = signed_in(cashier).get(reverse("cashbox:payment-new"), {"enrollment": enrollment.code})
    assert till.context["selected"]["code"] == enrollment.code  # owing again
