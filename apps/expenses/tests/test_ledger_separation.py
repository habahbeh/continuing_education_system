"""
The first test of Sprint 8C-2, and the one the whole sprint is arranged around.

Three things in this system carry the word «مستهلكات»:

* an ``Expense`` — money the CENTRE paid out (§9.7);
* ``ChargeType.CONSUMABLES`` — money charged TO a participant, which is
  REVENUE (§5.5);
* ``Agreement.exclude_consumables`` — whether that revenue enters the
  partner's distribution base (§3.1).

Confusing the first with the second makes "net centre income" subtract a
figure that was income. Confusing it with the third makes an expense entry
move a partner's share. Both failures are SILENT — the arithmetic stays
internally consistent and the wrong number is signed on a settlement.

So this module asserts the boundary in both directions before anything is
built on top of it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.expenses.services import expense_service
from apps.settlements.services import claim_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
FULL = Decimal("270.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def approver(seeded_settings):
    """A second centre manager, so nobody approves their own entry (D-18)."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.exp2", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _claim(finance, agreement, cohort, period_to=PERIOD_END):
    return claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=cohort,
        period_from=TERM_START,
        period_to=period_to,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )


def _record_expense(finance, cohort=None, code="EXP-1", amount="100.000"):
    return expense_service.record(
        actor=finance,
        code=code,
        category="CONSUMABLES",
        amount=Decimal(amount),
        incurred_on=TERM_START,
        description_ar="شراء مستهلكات للدفعة",
        cohort=cohort,
    )


# ---------------------------------------------------------------------------
# The critical assertion
# ---------------------------------------------------------------------------
def test_recording_an_expense_does_not_move_the_partner_share_by_one_dinar(
    finance, manager, approver, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """
    §9.7 vs §3.1 — an expense is the centre's money, not a deduction from the
    partner's base.

    250 tuition shared at 50% is 125. A hundred dinars of consumables bought
    FOR THAT COHORT, recorded and then approved, must leave the partner's
    share at exactly 125 — because what the centre spent on materials is not
    what the participant paid for tuition.

    The tempting bug is real: ``exclude_consumables`` exists on the agreement,
    an expense can carry a cohort, and both use the same Arabic word. Nothing
    connects them, and this test is what keeps it that way.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    before = _claim(finance, percent_agreement, cohort_with_agreement)
    assert before.partner_share == Decimal("125.000")
    assert before.distribution_base == Decimal("250.000")

    expense = _record_expense(finance, cohort=cohort_with_agreement)
    expense_service.approve(actor=approver, expense=expense)

    after = _claim(finance, percent_agreement, cohort_with_agreement, period_to=date(2026, 11, 10))
    assert after.partner_share == before.partner_share
    assert after.distribution_base == before.distribution_base
    assert after.gross_collected == before.gross_collected
    assert after.excluded_consumables == before.excluded_consumables


def test_an_expense_does_not_move_a_participant_balance(
    finance, approver, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    The other direction — an expense is not a charge line.

    Nothing the centre buys appears on anyone's account, however tightly the
    purchase is tied to their cohort.
    """
    from apps.billing.services.account_service import get_account_state

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    before = get_account_state(enrollment)

    expense = _record_expense(finance, cohort=cohort_with_agreement)
    expense_service.approve(actor=approver, expense=expense)

    after = get_account_state(enrollment)
    assert after.balance == before.balance
    assert after.total_due == before.total_due
    assert after.partner_base == before.partner_base


def test_a_participant_consumables_charge_is_revenue_not_an_expense(
    finance, manager, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §5.5 — «المواد المستهلكة ≤50 د/طالب» charged to a participant is REVENUE.

    It reaches the ledger as a ``ChargeLine``, never as an ``Expense``, so the
    expenses screen and report 7 do not see it. The same word, the opposite
    side of the books.
    """
    from apps.billing.models import ChargeType
    from apps.billing.services import charge_service

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    charge_service.create_charge_line(
        actor=manager,
        enrollment=enrollment,
        charge_type=ChargeType.CONSUMABLES,
        description_ar="مواد مستهلكة",
        net_amount=Decimal("50.000"),
        charged_on=TERM_START,
    )

    rows = expense_service.list_expenses(actor=finance)
    assert rows == []


def test_the_expense_ledger_is_blind_to_partner_obligations(
    finance, approver, partner, percent_agreement
) -> None:
    """
    §5.6 vs §9.7 — an amount the PARTNER owes the university is not something
    the university SPENT.

    An obligation deducted from a later claim recovers revenue; it never
    appears among the centre's outgoings, and report 7 must not show it.
    """
    from apps.settlements.models import ObligationType, PartnerObligation

    PartnerObligation.objects.create(
        code="OBL-SEP-1",
        partner=partner,
        restricted_to_agreement=percent_agreement,
        obligation_type=ObligationType.FIELD_TRAINING_EXPENSE,
        amount=Decimal("300.000"),
        statement_reference="كشف التدريب العملي",
        occurred_on=TERM_START,
        created_by=finance,
    )

    rows = expense_service.list_expenses(actor=finance)
    assert rows == []
    assert expense_service.approved_total(actor=finance) == Decimal("0.000")
