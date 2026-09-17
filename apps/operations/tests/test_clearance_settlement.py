"""
Settling the account so clearance can close — BR-071, BR-097, T-259 … T-263.

Two things have to be true before step 2 closes, and Sprint 6 deliberately
left both to this sprint:

* a **credit balance** must be handed back (BR-071). Sprint 6 created these
  when a transfer moved someone to a cheaper course; returning them is here.
* a **deposit** must be settled — returned or forfeited — where the programme
  has a policy at all (Q-01, BR-097). Where it has none there is no deposit
  line, and the question does not arise rather than being hidden.

⚠️ **ASSUMPTION under test**: the credit return runs inside step 2 and leans
on that step's existing FIN → FIM dual certification for its authorisation.
The documents name no approver. Not a settled client decision.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.billing.models import CreditReturn
from apps.billing.services import credit_service
from apps.billing.services.account_service import get_account_state
from apps.cashbox.models import PaymentAllocation, Receipt, ReceiptStatus
from apps.core.models import AuditEvent
from apps.operations.services import clearance_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fim.stl", password="probe-password-1234", role=Role.FINANCE_MANAGER
    )


@pytest.fixture
def overpaid(
    make_cohort, approve_cohort, make_enrollment, charge_and_pay, finish_enrollment, manager
):
    """
    An enrolment in credit, with its clearance already through step 1.

    SC-NET is 270 all in; paying 320 leaves the centre owing 50 — the shape a
    Sprint 6 transfer to a cheaper course produces.
    """

    def _make(paid: str = "320.000", code: str = "CO-CR"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=1)
        charge_and_pay(enrollment, amount=paid)
        finish_enrollment(enrollment, to_status="WITHDRAWN")
        clearance = clearance_service.open_clearance(
            actor=manager,
            enrollment=enrollment,
            opened_on=TERM_START,
            code="CLR-CR",
        )
        clearance_service.complete_custody_step(
            actor=manager,
            clearance=clearance,
            custody_items=[{"name_ar": "هوية المركز", "returned": True}],
        )
        return enrollment, clearance

    return _make


# ---------------------------------------------------------------------------
# BR-071 — the credit return
# ---------------------------------------------------------------------------
def test_a_credit_blocks_the_step_until_it_is_returned(
    overpaid, finance, finance_manager, manager
) -> None:
    """
    The whole point of BR-071 living at clearance.

    Blocked → returned → closes. Nothing else about the account changed.
    """
    enrollment, clearance = overpaid()
    assert get_account_state(enrollment).balance == Decimal("-50.000")

    with pytest.raises(clearance_service.ClearanceBlockedError, match="رصيد دائن"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-001"
    )
    assert get_account_state(enrollment).balance == Decimal("0.000")

    step = clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
    step.refresh_from_db()
    assert step.is_done is True
    assert step.credit_return is not None
    assert step.credit_return.amount == Decimal("50.000")


def test_the_money_moves_by_reversal_and_nothing_is_edited(overpaid, finance) -> None:
    """
    The condition the sprint was approved under.

    Every allocation row that existed still exists, unmodified; the return is
    a NEW negative row that names where the money went.
    """
    enrollment, clearance = overpaid()
    before = {row.pk: row.amount for row in PaymentAllocation.objects.filter(enrollment=enrollment)}

    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-002"
    )

    after = {row.pk: row.amount for row in PaymentAllocation.objects.filter(enrollment=enrollment)}
    for pk, amount in before.items():
        assert pk in after, "an allocation row was deleted"
        assert after[pk] == amount, "an original allocation was edited"

    reversal = PaymentAllocation.objects.get(enrollment=enrollment, amount__lt=0)
    assert reversal.amount == Decimal("-50.000")
    assert reversal.charge_line_id is None
    assert "CR-002" in reversal.manual_reason_ar


def test_the_allocation_invariant_after_a_return(overpaid, finance) -> None:
    """
    The adjusted invariant, asserted rather than left to drift.

    Before Sprint 7, `Σ allocations == receipt.amount` held for every ISSUED
    receipt. Once money is handed back that is no longer true and SHOULD not
    be: what the sum now equals is what the centre still HOLDS. The difference
    is exactly the credit returned, and it is accounted for by a row, not by
    an absence.
    """
    _enrollment, clearance = overpaid()
    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-003"
    )

    record = CreditReturn.objects.filter(code="CR-003").first()
    assert record is not None
    returned = record.amount
    for receipt in Receipt.objects.filter(status=ReceiptStatus.ISSUED):
        allocated = sum((row.amount for row in receipt.allocations.all()), Decimal("0.000"))
        assert allocated == receipt.amount - returned
        # And the face value of the receipt is untouched.
        assert receipt.amount == Decimal("320.000")


def test_the_return_links_to_the_row_that_moved_the_money(overpaid, finance) -> None:
    """Traceable from either end: the payout names its allocation, and back."""
    _enrollment, clearance = overpaid()
    record = clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-004"
    )

    assert record.reversal_allocation is not None
    assert record.reversal_allocation.amount == -record.amount
    assert record.reversal_allocation.credit_returns.first() == record


def test_a_return_is_refused_when_nothing_is_owed(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
) -> None:
    """A settled account produces no payout and no row."""
    cohort = make_cohort("SC-NET", code="CO-EVEN")
    approve_cohort(cohort, course_number="M-EVEN")
    enrollment = make_enrollment(cohort, index=2)
    charge_and_pay(enrollment, amount="270.000")

    with pytest.raises(credit_service.NoCreditToReturnError):
        credit_service.return_credit(
            actor=finance,
            enrollment=enrollment,
            returned_on=TERM_START,
            reason_ar="بلا سبب",
            code="CR-NONE",
        )
    assert not CreditReturn.objects.exists()


def test_a_return_is_not_a_refund_and_needs_no_presidential_approval(overpaid, finance) -> None:
    """
    BR-034 governs refunds of REVENUE, and this is not one.

    Requiring an official letter and the president's signature to hand back a
    participant's own fifty dinars would make BR-071 unusable in practice.
    """
    from apps.billing.models import Refund

    _enrollment, clearance = overpaid()
    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-005"
    )

    assert not Refund.objects.exists()
    assert CreditReturn.objects.count() == 1


def test_the_return_is_audited_with_the_resulting_balance(overpaid, finance) -> None:
    _enrollment, clearance = overpaid()
    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-006"
    )

    event = AuditEvent.objects.filter(
        entity_type="billing.CreditReturn", reference="CR-006"
    ).first()
    assert event is not None and event.changes is not None
    assert event.changes["amount"] == "50.000"
    assert event.changes["balance_after"] == "0.000"


def test_a_return_cannot_be_added_after_the_step_is_certified(
    overpaid, finance, finance_manager
) -> None:
    """Nothing is bolted onto a step two people have already signed."""
    _enrollment, clearance = overpaid()
    clearance_service.return_credit_at_clearance(
        actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-007"
    )
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)

    with pytest.raises(ValidationError, match="مغلقة"):
        clearance_service.return_credit_at_clearance(
            actor=finance, clearance=clearance, returned_on=TERM_START, code="CR-008"
        )


# ---------------------------------------------------------------------------
# Q-01 / BR-097 — deposit settlement at clearance
# ---------------------------------------------------------------------------
def test_a_programme_without_a_policy_raises_no_deposit_question(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
    finance_manager,
) -> None:
    """
    Q-01 — the fields are not hidden; there is nothing to settle.

    SC-NET has no DepositPolicy, so no DEPOSIT line exists and step 2 closes
    without ever asking. Visibility follows the DATA, never a global flag.
    """
    cohort = make_cohort("SC-NET", code="CO-NODEP")
    approve_cohort(cohort, course_number="M-NODEP")
    enrollment = make_enrollment(cohort, index=3)
    charge_and_pay(enrollment, amount="270.000")
    finish_enrollment(enrollment)

    state = clearance_service.deposit_settlement_state(enrollment)
    assert state["applies"] is False
    assert state["is_settled"] is True

    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        opened_on=TERM_START,
        code="CLR-NODEP",
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    step = clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
    assert step.is_done is True
    assert step.deposit_return_amount is None


def test_an_unsettled_deposit_holds_the_financial_step(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
) -> None:
    """
    BR-097 — the English course carries the seeded demo deposit policy.

    Its 25-dinar deposit is money the university owes back or has forfeited;
    closing the financial step without deciding which would leave a liability
    nobody is tracking.
    """
    cohort = make_cohort("SC-ENG-GEN", code="CO-DEP", level=1)
    approve_cohort(cohort, course_number="M-DEP")
    enrollment = make_enrollment(cohort, index=4)
    quote = charge_and_pay(enrollment, amount=None)
    assert quote.has_deposit is True

    from apps.billing.services.account_service import get_account_state as state_of
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service

    method = PaymentMethod.objects.first() or PaymentMethod.objects.create(
        code="CASH2", name_ar="نقداً"
    )
    payment_service.take_payment(
        actor=finance,
        enrollment=enrollment,
        amount=state_of(enrollment).total_due,
        payment_method=method,
        received_on=TERM_START,
    )
    finish_enrollment(enrollment)

    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        opened_on=TERM_START,
        code="CLR-DEP",
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )

    assert clearance_service.deposit_settlement_state(enrollment)["applies"] is True
    with pytest.raises(clearance_service.DepositNotSettledError, match="تسوية التأمين"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)


def test_returning_the_deposit_releases_the_step(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
    finance_manager,
) -> None:
    """T-262 — and the deposit's settled amount is recorded on the step."""
    from apps.billing.services import deposit_service
    from apps.billing.services.account_service import get_account_state as state_of
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service

    cohort = make_cohort("SC-ENG-GEN", code="CO-DEP2", level=1)
    approve_cohort(cohort, course_number="M-DEP2")
    enrollment = make_enrollment(cohort, index=5)
    charge_and_pay(enrollment, amount=None)

    method = PaymentMethod.objects.first() or PaymentMethod.objects.create(
        code="CASH3", name_ar="نقداً"
    )
    payment_service.take_payment(
        actor=finance,
        enrollment=enrollment,
        amount=state_of(enrollment).total_due,
        payment_method=method,
        received_on=TERM_START,
    )
    finish_enrollment(enrollment)

    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        opened_on=TERM_START,
        code="CLR-DEP2",
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )
    deposit_service.return_deposit(actor=finance, enrollment=enrollment, returned_on=TERM_START)

    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    step = clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
    assert step.is_done is True
    assert step.deposit_return_amount == Decimal("25.000")
