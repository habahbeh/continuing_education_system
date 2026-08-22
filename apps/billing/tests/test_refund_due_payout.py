"""
Paying a historical credit back (Sprint 8D-4).

Sprint 8D-3 could declare that the centre owed money on a credit inherited
from before the system existed, and then had nowhere to record the cash
leaving — so the obligation stood open forever. This closes it, and these
tests hold the two things that make the closure honest:

* **no receipt.** A receipt asserts cash ARRIVED. This is cash leaving, and
  issuing one would state the opposite of what happened and inflate the day's
  takings by the amount handed back.
* **once.** ``OpeningBalanceRefund.opening_balance`` is a OneToOne, so a
  second payout collides at the database even with every service guard gone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.utils import IntegrityError

from apps.billing.models import (
    OpeningBalance,
    OpeningBalanceDirection,
    OpeningBalanceRefund,
    OpeningBalanceStatus,
)
from apps.billing.services import opening_balance_service as obs

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
PASSWORD = "probe-password-1234"
CREDIT = Decimal("225.000")
DEBT = Decimal("650.000")


@pytest.fixture
def proposer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.p8d4", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def payer(seeded_settings):
    """A second finance officer — the one who actually hands the cash over."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.pay8d4", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.8d4", password=PASSWORD, role=Role.CENTER_MANAGER)


@pytest.fixture
def live_enrollment(cohort_with_agreement, make_paid_enrollment):
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


def _refund_due(proposer, payer, approver, enrollment, *, code="OB-8D4", amount=CREDIT):
    """A credit taken all the way to «مستحق الردّ نقداً»."""
    balance = obs.propose_manually(
        actor=proposer,
        code=code,
        direction=OpeningBalanceDirection.CREDIT,
        amount=amount,
        as_of=AS_OF,
        description_ar="رصيد دائن من 2022",
        legacy_number="202251024",
    )
    obs.review(actor=payer, balance=balance, enrollment=enrollment, note_ar="قوبل")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=approver, balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()
    return balance


def _pay(actor, balance, *, code="PAY-001", amount=None, method=None, **extra):
    kwargs = {
        "external_reference": "SND-2026-0042",
        "payee_name_ar": "انوار رضوان الاحمد",
    }
    kwargs.update(extra)
    return obs.pay_refund_due(
        actor=actor,
        balance=balance,
        code=code,
        amount=amount if amount is not None else balance.amount,
        paid_on=PERIOD_END,
        payment_method=method,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The happy path, and what it does NOT create
# ---------------------------------------------------------------------------
def test_a_refund_due_is_paid_out_once_and_closes(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment)
    assert balance.status == OpeningBalanceStatus.REFUND_DUE

    payout = _pay(payer, balance, method=cash_method)

    assert payout.amount == CREDIT
    assert payout.opening_balance_id == balance.pk
    assert payout.paid_by_id == payer.pk
    assert payout.external_reference == "SND-2026-0042"
    assert payout.payee_name_ar == "انوار رضوان الاحمد"
    assert payout.payment_method_id == cash_method.pk

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED
    # ``refund_payouts`` since Sprint 8D-5: the OneToOne became a FK so a
    # reversed payout can stand beside the corrected one that replaced it.
    # Uniqueness moved to (opening_balance, active_key), which still allows
    # exactly one LIVE payout.
    assert [p.pk for p in balance.refund_payouts.all()] == [payout.pk]
    assert payout.active_key == 1


def test_the_payout_creates_no_receipt_and_no_allocation(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The line the whole archive boundary is built on.

    A ``Receipt`` here would assert that cash came IN through the till in 2026
    for a 2022 overpayment this system never saw — and it would raise the
    day's system total by the amount handed OUT.
    """
    from apps.cashbox.models import PaymentAllocation, Receipt

    receipts = Receipt.objects.count()
    allocations = PaymentAllocation.objects.count()

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-NR")
    _pay(payer, balance, code="PAY-NR", method=cash_method)

    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations


def test_the_payout_stays_out_of_the_daily_closing(
    proposer, payer, approver, cashier, cash_method, live_enrollment
) -> None:
    """
    ``system_total_for`` reconciles issued receipts only.

    That is the established cashbox design — every existing payout sits
    outside the till count — and this one follows it rather than inventing a
    netting the cashier would have to explain.
    """
    from apps.cashbox.services import closing_service

    before, count_before = closing_service.system_total_for(
        cashier=cashier, closing_date=PERIOD_END
    )

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-DC")
    _pay(payer, balance, code="PAY-DC", method=cash_method)

    after, count_after = closing_service.system_total_for(cashier=cashier, closing_date=PERIOD_END)
    assert (after, count_after) == (before, count_before)


def test_the_payout_is_audited_under_its_real_model_name(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    from apps.core.models import AuditEvent

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-AU")
    _pay(payer, balance, code="PAY-AU", method=cash_method)

    event = AuditEvent.objects.filter(reference="PAY-AU").first()
    assert event is not None
    assert event.entity_type == "billing.OpeningBalanceRefund"

    changes = event.changes or {}
    assert changes["opening_balance"] == "OB-8D4-AU"
    assert changes["external_reference"] == "SND-2026-0042"
    assert "لا" in changes["receipt_created"]


# ---------------------------------------------------------------------------
# Once, and only once
# ---------------------------------------------------------------------------
def test_a_second_payout_is_refused(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-2X")
    _pay(payer, balance, code="PAY-2X-1", method=cash_method)
    balance.refresh_from_db()

    with pytest.raises(obs.AlreadyRefundedError):
        _pay(payer, balance, code="PAY-2X-2", method=cash_method)

    assert OpeningBalanceRefund.objects.filter(opening_balance=balance).count() == 1


def test_a_second_payout_is_impossible_even_without_the_service(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The guarantee is the OneToOne, not the guard.

    A service written next year could forget to check the status; the database
    cannot forget the constraint.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-DB")
    _pay(payer, balance, code="PAY-DB-1", method=cash_method)

    with pytest.raises(IntegrityError), transaction.atomic():
        OpeningBalanceRefund.objects.create(
            code="PAY-DB-2",
            opening_balance=balance,
            amount=CREDIT,
            paid_on=PERIOD_END,
            payment_method=cash_method,
            external_reference="SND-DUP",
            payee_name_ar="شخص آخر",
            paid_by=payer,
        )


# ---------------------------------------------------------------------------
# What may not be paid out
# ---------------------------------------------------------------------------
def test_a_receivable_is_never_paid_out(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """Paying out a debt would hand the participant money they OWE."""
    balance = obs.propose_manually(
        actor=proposer,
        code="OB-8D4-RECV",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        as_of=AS_OF,
        description_ar="ذمة",
    )
    obs.review(actor=payer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    balance.refresh_from_db()

    with pytest.raises(obs.NotACreditError):
        _pay(payer, balance, code="PAY-RECV", method=cash_method)


@pytest.mark.parametrize("stop_at", ["DRAFT", "REVIEWED", "APPROVED"])
def test_a_credit_short_of_refund_due_is_not_paid_out(
    proposer, payer, approver, cash_method, live_enrollment, stop_at
) -> None:
    """Only what the manager declared refundable may leave the drawer."""
    balance = obs.propose_manually(
        actor=proposer,
        code=f"OB-8D4-{stop_at}",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        as_of=AS_OF,
        description_ar="رصيد دائن",
    )
    if stop_at in {"REVIEWED", "APPROVED"}:
        obs.review(actor=payer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    if stop_at == "APPROVED":
        obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    balance.refresh_from_db()

    with pytest.raises(obs.NotRefundDueError):
        _pay(payer, balance, code=f"PAY-{stop_at}", method=cash_method)

    assert not OpeningBalanceRefund.objects.filter(opening_balance=balance).exists()


def test_a_credit_already_carried_forward_is_not_also_paid_out(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    It went onto a later registration. Paying it too would give it twice.
    """
    balance = obs.propose_manually(
        actor=proposer,
        code="OB-8D4-APPLIED",
        direction=OpeningBalanceDirection.CREDIT,
        amount=CREDIT,
        as_of=AS_OF,
        description_ar="رصيد دائن",
    )
    obs.review(actor=payer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    obs.carry_forward(actor=approver, balance=balance, enrollment=live_enrollment, note_ar="ترحيل")
    balance.refresh_from_db()

    with pytest.raises(obs.NotRefundDueError):
        _pay(payer, balance, code="PAY-APPLIED", method=cash_method)


def test_a_payout_without_a_voucher_or_a_payee_is_refused(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """Cash leaving the centre carries a document number and a named payee."""
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-DOC")

    with pytest.raises(obs.MissingPayoutDetailsError):
        _pay(payer, balance, code="PAY-DOC-1", method=cash_method, external_reference="  ")
    with pytest.raises(obs.MissingPayoutDetailsError):
        _pay(payer, balance, code="PAY-DOC-2", method=cash_method, payee_name_ar="")

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE


def test_a_partial_payout_is_refused(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    Out of scope, and refused loudly rather than silently accepted.

    Half a payout that closed the obligation would quietly cancel the rest of
    what the centre owes.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-PART")

    with pytest.raises(ValidationError, match="الجزئي"):
        _pay(
            payer,
            balance,
            code="PAY-PART",
            method=cash_method,
            amount=Decimal("100.000"),
        )


# ---------------------------------------------------------------------------
# Who may pay
# ---------------------------------------------------------------------------
def test_the_person_who_declared_it_does_not_pay_it(
    proposer, payer, approver, cash_method, live_enrollment, seeded_settings
) -> None:
    """
    D-18 — deciding money is owed and handing it over are two hands.

    The roles already split it (no role holds both ``A`` and ``E`` here), so
    this covers the case a role check cannot see: one person, two accounts.
    """
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-SOD")
    # Force the declarer to be the payer's own account and try again.
    balance.resolved_by = payer
    balance.save(update_fields=["resolved_by"])

    with pytest.raises(obs.SeparationOfDutiesError, match="D-18"):
        _pay(payer, balance, code="PAY-SOD", method=cash_method)


def test_no_single_role_can_both_declare_and_pay(seeded_settings) -> None:
    """The structural half of the same rule."""
    from apps.people.constants import Action, Screen
    from apps.people.models import Role
    from apps.people.permissions import matrix

    for role in Role.values:
        actions = matrix.allowed_actions(role, Screen.OPENING_BALANCES)
        assert not (Action.APPROVE in actions and Action.EDIT in actions), (
            f"{role} could declare a refund due and pay it to themselves"
        )


def test_the_manager_cannot_execute_the_payout(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    from apps.core.models import AuditEvent

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-MGR")
    before = AuditEvent.objects.filter(action="DENIED_ATTEMPT").count()

    with pytest.raises(PermissionDenied):
        _pay(approver, balance, code="PAY-MGR", method=cash_method)

    # BR-085 — the refusal survives, because the check ran outside the
    # transaction that would otherwise have rolled it back.
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT").count() == before + 1
    assert not OpeningBalanceRefund.objects.filter(opening_balance=balance).exists()


def test_the_cashier_cannot_reach_the_payout_at_all(
    proposer, payer, approver, cashier, cash_method, live_enrollment
) -> None:
    """§8 — the cashier takes money in; this screen is not theirs."""
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-CSH")

    with pytest.raises(PermissionDenied):
        _pay(cashier, balance, code="PAY-CSH", method=cash_method)


# ---------------------------------------------------------------------------
# What the payout leaves behind
# ---------------------------------------------------------------------------
def test_the_outstanding_queue_empties_when_the_cash_goes_out(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-Q")

    queue = obs.outstanding_refunds(actor=payer)
    assert [row["code"] for row in queue] == ["OB-8D4-Q"]
    assert queue[0]["amount"] == CREDIT

    _pay(payer, balance, code="PAY-Q", method=cash_method)
    assert obs.outstanding_refunds(actor=payer) == []


def test_the_totals_move_from_owed_to_paid(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-T")

    owed = obs.totals(actor=payer)
    assert owed["refund_due"] == CREDIT
    assert owed["refunded"] == Decimal("0.000")

    _pay(payer, balance, code="PAY-T", method=cash_method)

    paid = obs.totals(actor=payer)
    assert paid["refund_due"] == Decimal("0.000")
    assert paid["refunded"] == CREDIT


def test_a_paid_refund_changes_no_enrollment_balance(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    A refunded credit was never applied to the enrolment, so paying it out
    must not move the enrolment either — and the clearance guard must see the
    same zero it saw before.
    """
    from apps.billing.services import account_service

    before = account_service.get_account_state(live_enrollment)
    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-BAL")
    _pay(payer, balance, code="PAY-BAL", method=cash_method)

    after = account_service.get_account_state(live_enrollment)
    assert after.balance == before.balance
    assert after.opening_credit_applied == before.opening_credit_applied == Decimal("0.000")
    assert after.total_paid == before.total_paid


def test_a_paid_refund_does_not_block_a_clearance(
    proposer, payer, approver, cash_method, live_enrollment
) -> None:
    """
    The old-debt guard is about RECEIVABLES, and this was a credit.

    Asserted anyway: a refunded balance is a terminal credit state, and a
    guard that started catching it would stop a participant being cleared over
    money the centre had already handed them.
    """
    from apps.operations.services import clearance_service

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-CLR")
    _pay(payer, balance, code="PAY-CLR", method=cash_method)

    assert obs.unsettled_debt_for(live_enrollment.participant) == []

    clearance = clearance_service.open_clearance(
        actor=approver,
        enrollment=live_enrollment,
        case_type="GRADUATION",
        opened_on=PERIOD_END,
        code="CLR-8D4-1",
    )
    clearance_service.complete_custody_step(
        actor=approver,
        clearance=clearance,
        custody_items=[{"label": "هوية المركز", "returned": True}],
    )
    step = clearance_service.certify_finance_step(actor=payer, clearance=clearance)
    assert step is not None


# ---------------------------------------------------------------------------
# Reporting — disclosed, not netted
# ---------------------------------------------------------------------------
def test_the_payout_is_disclosed_and_not_subtracted_from_net_income(
    proposer, payer, approver, finance, cash_method, live_enrollment
) -> None:
    """
    Returning an inherited liability costs the year nothing.

    The centre was holding money that was never its own. Paying it reduces
    cash AND reduces the liability, so subtracting it from net income would
    depress a result it has no business touching. Leaving it off the report
    entirely would be the other error — somebody reconciling the bank needs to
    see where it went.
    """
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    before = report_service.net_income_report(actor=finance, **window)

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-REP")
    _pay(payer, balance, code="PAY-REP", method=cash_method)

    after = report_service.net_income_report(actor=finance, **window)

    assert after["historical_refunds_paid"] == CREDIT
    assert after["net_income"] == before["net_income"]
    assert after["collected"] == before["collected"]
    assert after["prior_year_settlements"] == before["prior_year_settlements"]


def test_the_payout_never_appears_as_revenue(
    proposer, payer, approver, finance, cash_method, live_enrollment
) -> None:
    """Report 1 counts money received. None was."""
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    before = report_service.revenue_report(actor=finance, **window)

    balance = _refund_due(proposer, payer, approver, live_enrollment, code="OB-8D4-REV")
    _pay(payer, balance, code="PAY-REV", method=cash_method)

    after = report_service.revenue_report(actor=finance, **window)
    assert after["total"] == before["total"]
    assert after["current_period_total"] == before["current_period_total"]
    assert after["prior_year_settlements"] == before["prior_year_settlements"]


# ---------------------------------------------------------------------------
# The boundaries 8D-1 … 8D-3 set, still standing
# ---------------------------------------------------------------------------
def test_the_archive_still_has_no_way_to_pay_anybody(committed_batch) -> None:
    """
    A-04 — and now there is a payout service to be tempted by.

    The archive app must not import billing, so it cannot reach
    ``pay_refund_due`` any more than it could reach ``post``.
    """
    import ast
    from pathlib import Path

    apps_dir = Path(__file__).resolve().parents[3] / "apps"
    offenders: list[str] = []
    for path in (apps_dir / "datamigration").rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module.startswith(("apps.billing", "apps.cashbox", "apps.settlements")):
                offenders.append(f"{path.name} → {module}")
    assert not offenders, ", ".join(offenders)

    # And the archive itself still holds exactly its six tables.
    from django.apps import apps as django_apps

    names = {m.__name__ for m in django_apps.get_app_config("datamigration").get_models()}
    assert "OpeningBalanceRefund" not in names
    assert len(names) == 6


def test_the_preserved_constraints_are_still_there() -> None:
    """The three the brief names, checked at the definition."""
    from apps.datamigration.models import HistoricalParticipant
    from apps.people.models import Participant

    number = Participant._meta.get_field("participant_number")
    assert number.max_length == 9
    assert "people_participant_number_format" in {c.name for c in Participant._meta.constraints}

    legacy = HistoricalParticipant._meta.get_field("legacy_number")
    assert legacy.unique is False
    assert not any(
        "format" in c.name or "regex" in c.name for c in HistoricalParticipant._meta.constraints
    )

    assert OpeningBalanceRefund._meta.app_label == "billing"
    assert OpeningBalance._meta.app_label == "billing"
