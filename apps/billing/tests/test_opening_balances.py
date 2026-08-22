"""
The reviewed gateway from archive to ledger (BR-094 · D-24 · D-25).

Sprint 8D-1's promise was that reading the centre's history moves no money.
This sprint opens one door in that wall, and these tests are what keep it a
door rather than a hole: the ledger census is taken across every step of the
workflow and only ONE of them is allowed to change it.

The rest prove the things a reviewer would want to be true before signing:
four distinct hands, one balance at a time, a credit that cannot be posted
because posting it would mean inventing a receipt, and a post that can happen
exactly once.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.utils import IntegrityError

from apps.billing.models import (
    ChargeLine,
    ChargeType,
    OpeningBalance,
    OpeningBalanceDirection,
    OpeningBalanceStatus,
)
from apps.billing.services import opening_balance_service as obs
from apps.people.constants import Action, Screen

pytestmark = pytest.mark.django_db

AS_OF = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
DEBT = Decimal("650.000")
PASSWORD = "probe-password-1234"


# ---------------------------------------------------------------------------
# The census — every table the archive path must never touch
# ---------------------------------------------------------------------------
def _ledger_census() -> dict[str, int]:
    from apps.billing.models import CreditReturn, Discount, ExtraFee, Refund
    from apps.cashbox.models import DailyClosing, PaymentAllocation, Receipt, ReceiptVoid
    from apps.settlements.models import (
        ClaimDeduction,
        Entitlement,
        PartnerClaim,
        PartnerObligation,
        PartnerSettlement,
    )

    return {
        model.__name__: model.objects.count()
        for model in (
            ChargeLine,
            CreditReturn,
            Discount,
            ExtraFee,
            Refund,
            DailyClosing,
            PaymentAllocation,
            Receipt,
            ReceiptVoid,
            ClaimDeduction,
            Entitlement,
            PartnerClaim,
            PartnerObligation,
            PartnerSettlement,
        )
    }


@pytest.fixture
def proposer(seeded_settings):
    """FIN holds ``V C E P`` on this screen — they propose."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.ob.propose", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def reviewer(seeded_settings):
    """A SECOND finance officer. D-24 needs a different person, not a different role."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.ob.review", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def approver(seeded_settings):
    """MGR holds ``V A P`` — they approve, and they post."""
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.ob", password=PASSWORD, role=Role.CENTER_MANAGER)


@pytest.fixture
def live_enrollment(cohort_with_agreement, make_paid_enrollment):
    """A real enrolment for the old debt to land on."""
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


def _propose(actor, code="OB-001", direction=OpeningBalanceDirection.RECEIVABLE, amount=DEBT):
    return obs.propose_manually(
        actor=actor,
        code=code,
        direction=direction,
        amount=amount,
        as_of=AS_OF,
        description_ar="ذمة قديمة من دفعة 2022",
        legacy_number="202251024",
    )


def _through_approval(proposer, reviewer, approver, enrollment, code="OB-001", **kwargs):
    balance = _propose(proposer, code=code, **kwargs)
    obs.review(actor=reviewer, balance=balance, enrollment=enrollment, note_ar="قوبل بالصف")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    balance.refresh_from_db()
    return balance


# ---------------------------------------------------------------------------
# 1 · No ledger movement until post
# ---------------------------------------------------------------------------
def test_propose_review_and_approve_write_nothing_to_the_ledger(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """
    The census is taken four times. Only the fourth is allowed to differ.

    Approval especially: it is permission to post, not the posting, and
    keeping those apart is what makes the ledger write a deliberate act rather
    than a side effect of a click.
    """
    before = _ledger_census()

    balance = _propose(proposer)
    assert _ledger_census() == before

    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    assert _ledger_census() == before

    obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.APPROVED
    assert _ledger_census() == before  # approved, and still nothing exists

    obs.post(actor=approver, balance=balance)
    after = _ledger_census()
    assert after["ChargeLine"] == before["ChargeLine"] + 1
    assert {k: v for k, v in after.items() if k != "ChargeLine"} == {
        k: v for k, v in before.items() if k != "ChargeLine"
    }


def test_a_rejected_balance_writes_nothing_and_stays_on_the_record(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """Rejection is an outcome, not a deletion."""
    before = _ledger_census()
    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    obs.reject(actor=approver, balance=balance, note_ar="المبلغ لا يطابق الملف الورقي")

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REJECTED
    assert balance.decision_note_ar == "المبلغ لا يطابق الملف الورقي"
    assert _ledger_census() == before


def test_a_rejection_without_a_reason_is_refused(
    proposer, reviewer, approver, live_enrollment
) -> None:
    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")

    with pytest.raises(ValidationError, match="سبب الرفض"):
        obs.reject(actor=approver, balance=balance, note_ar="  ")


# ---------------------------------------------------------------------------
# 2 · Reports 1 and 2 do not move
# ---------------------------------------------------------------------------
def test_draft_opening_balances_do_not_touch_reports_one_and_two(
    proposer, reviewer, approver, finance, live_enrollment
) -> None:
    """
    §9.1 and §9.2 are cash-basis — they count what was collected.

    A draft is not even a charge, let alone a payment, so the two figures a
    reader would most fear seeing inflated by history stay exactly as they
    were.
    """
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    revenue_before = report_service.revenue_report(actor=finance, **window)["total"]
    net_before = report_service.net_income_report(actor=finance, **window)["net_income"]

    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    obs.approve(actor=approver, balance=balance, note_ar="معتمد")

    assert report_service.revenue_report(actor=finance, **window)["total"] == revenue_before
    assert report_service.net_income_report(actor=finance, **window)["net_income"] == net_before


def test_even_a_posted_balance_moves_no_revenue_until_somebody_pays(
    proposer, reviewer, approver, finance, live_enrollment
) -> None:
    """
    The posted row is a DEBT, and a debt is not revenue.

    Worth asserting separately from the draft case: it would be easy to assume
    that posting to the ledger inflates the income statement, and on a cash
    basis it does not. What moves is report 4 — the overdue list — which is
    precisely where an old arrear belongs.
    """
    from apps.reporting.services import report_service

    window = {"date_from": AS_OF, "date_to": PERIOD_END}
    revenue_before = report_service.revenue_report(actor=finance, **window)["total"]
    net_before = report_service.net_income_report(actor=finance, **window)["net_income"]

    balance = _through_approval(proposer, reviewer, approver, live_enrollment)
    obs.post(actor=approver, balance=balance)

    assert report_service.revenue_report(actor=finance, **window)["total"] == revenue_before
    assert report_service.net_income_report(actor=finance, **window)["net_income"] == net_before

    overdue = report_service.overdue_report(actor=finance, as_of=PERIOD_END)
    assert overdue["total_outstanding"] >= DEBT


def test_a_posted_balance_never_reaches_a_partner_base(
    proposer,
    reviewer,
    approver,
    finance,
    cashier,
    cash_method,
    percent_agreement,
    live_enrollment,
) -> None:
    """
    BR-046 — a debt from before the agreement is not the partner's business.

    The participant actually PAYS the old debt here, which is the only moment
    the question becomes real. ``shareable_collected`` filters on
    ``is_partner_shareable``, and the posted line carries False, so the
    partner's base is unchanged by money that was never theirs.
    """
    from apps.cashbox.services import payment_service
    from apps.settlements.services import entitlement_service

    before = entitlement_service.shareable_collected(
        live_enrollment, agreement=percent_agreement, as_of=PERIOD_END
    )

    balance = _through_approval(proposer, reviewer, approver, live_enrollment)
    obs.post(actor=approver, balance=balance)

    payment_service.take_payment(
        actor=cashier,
        enrollment=live_enrollment,
        amount=DEBT,
        payment_method=cash_method,
        received_on=AS_OF,
    )

    after = entitlement_service.shareable_collected(
        live_enrollment, agreement=percent_agreement, as_of=PERIOD_END
    )
    assert after == before


# ---------------------------------------------------------------------------
# 3 · Four hands (D-24)
# ---------------------------------------------------------------------------
def test_the_proposer_cannot_review_their_own_balance(proposer, live_enrollment) -> None:
    balance = _propose(proposer)

    with pytest.raises(obs.SeparationOfDutiesError, match="D-24"):
        obs.review(actor=proposer, balance=balance, enrollment=live_enrollment, note_ar="أنا")

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.DRAFT


def test_no_single_role_can_both_review_and_approve(seeded_settings) -> None:
    """
    The matrix separates the two steps before D-24 is even consulted.

    Written expecting the opposite: the first version of this test gave a
    manager the review, then had them approve their own review, and it failed
    because a manager cannot review at all. §3.7/36أ gives the officer ``E``
    and the manager ``A``, and no role holds both — so reviewer and approver
    are always different PEOPLE because they are always different ROLES.

    D-24's person-level check is the second layer, for the case the matrix
    cannot see: two officers where the proposer also reviews. That one has its
    own test above.
    """
    from apps.people.models import Role
    from apps.people.permissions import matrix

    for role in Role.values:
        actions = matrix.allowed_actions(role, Screen.OPENING_BALANCES)
        assert not (Action.EDIT in actions and Action.APPROVE in actions), (
            f"{role} holds both review and approval on the opening-balance screen — "
            "the four hands of BR-094 collapse into two"
        )

    assert Action.EDIT in matrix.allowed_actions(Role.FINANCE_OFFICER, Screen.OPENING_BALANCES)
    assert Action.APPROVE in matrix.allowed_actions(Role.CENTER_MANAGER, Screen.OPENING_BALANCES)


def test_the_manager_cannot_review_because_review_is_not_theirs(
    proposer, approver, live_enrollment
) -> None:
    """The consequence of the row above, met by a caller rather than a matrix read."""
    balance = _propose(proposer)

    with pytest.raises(PermissionDenied):
        obs.review(actor=approver, balance=balance, enrollment=live_enrollment, note_ar="راجعت")

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.DRAFT


def test_the_database_refuses_a_self_review_even_without_the_service(
    proposer, live_enrollment
) -> None:
    """
    The constraint, not just the guard.

    A service written next year could forget the check; the CHECK constraint
    cannot.
    """
    from django.db import transaction

    balance = _propose(proposer)
    with pytest.raises(IntegrityError), transaction.atomic():
        OpeningBalance.objects.filter(pk=balance.pk).update(reviewed_by=proposer)


def test_a_review_without_a_note_is_refused(proposer, reviewer, live_enrollment) -> None:
    balance = _propose(proposer)
    with pytest.raises(ValidationError, match="ملاحظة المراجعة"):
        obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="")


def test_approval_needs_an_enrollment_to_land_on(proposer, reviewer, approver) -> None:
    """A debt with nowhere to go is a debt nobody can collect."""
    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=None, note_ar="لم أجد التسجيل بعد")

    with pytest.raises(obs.NoEnrollmentError):
        obs.approve(actor=approver, balance=balance, note_ar="معتمد")


def test_the_steps_run_in_order(proposer, approver, live_enrollment) -> None:
    """No approving a draft, no posting something unapproved."""
    balance = _propose(proposer)

    with pytest.raises(obs.OpeningBalanceStateError):
        obs.approve(actor=approver, balance=balance, note_ar="معتمد")
    with pytest.raises(obs.OpeningBalanceStateError):
        obs.post(actor=approver, balance=balance)

    assert not ChargeLine.objects.filter(charge_type=ChargeType.OPENING_BALANCE).exists()


# ---------------------------------------------------------------------------
# 4 · One at a time (D-25) and one source row (no double collection)
# ---------------------------------------------------------------------------
def test_there_is_no_bulk_proposal_service(proposer) -> None:
    """
    D-25 — the absence IS the control, so it is asserted.

    Two hundred balances approved in a batch is two hundred balances nobody
    read. A future contributor adding ``propose_all`` will fail here and go
    and read BR-094 first.
    """
    exported = set(obs.__all__)
    assert {"propose_from_archive", "propose_manually"} <= exported

    # Substring matching was tried first and flagged ``propose_manually`` for
    # the "all" inside "manually" — so the check names what it means instead.
    proposers = {name for name in exported if name.startswith("propose")}
    assert proposers == {"propose_from_archive", "propose_manually"}
    assert not any(
        marker in name for name in exported for marker in ("_all", "bulk", "batch", "_many")
    )


def test_one_archive_row_yields_one_balance(proposer, committed_batch) -> None:
    """A second proposal from the same source is how a debt gets collected twice."""
    from apps.datamigration.models import HistoricalEnrollment

    historical = HistoricalEnrollment.objects.filter(batch=committed_batch).first()
    assert historical is not None
    obs.propose_from_archive(
        actor=proposer,
        historical_enrollment=historical,
        code="OB-ARC-1",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        as_of=AS_OF,
        description_ar="ذمة من الأرشيف",
    )

    with pytest.raises(ValidationError, match="سلفاً"):
        obs.propose_from_archive(
            actor=proposer,
            historical_enrollment=historical,
            code="OB-ARC-2",
            direction=OpeningBalanceDirection.RECEIVABLE,
            amount=DEBT,
            as_of=AS_OF,
            description_ar="مرة ثانية",
        )


def test_a_balance_from_the_archive_carries_its_provenance(proposer, committed_batch) -> None:
    """
    Workbook, sheet and row are copied as text beside the FK.

    A batch can be superseded by a better reading of the same file; the
    balance somebody approved must still be able to say where it came from.
    """
    from apps.datamigration.models import HistoricalEnrollment

    historical = HistoricalEnrollment.objects.filter(batch=committed_batch).first()
    assert historical is not None
    balance = obs.propose_from_archive(
        actor=proposer,
        historical_enrollment=historical,
        code="OB-ARC-3",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=DEBT,
        as_of=AS_OF,
        description_ar="ذمة من الأرشيف",
    )

    assert balance.source_enrollment_id == historical.pk
    assert balance.source_workbook == "sample.xlsx"
    assert balance.source_sheet == historical.source_row.sheet_name
    assert balance.source_row == historical.source_row.source_row
    assert balance.source_legacy_number == historical.participant.legacy_number


# ---------------------------------------------------------------------------
# 5 · Posting: once, receivable only, by the right hand
# ---------------------------------------------------------------------------
def test_posting_creates_exactly_one_unshared_charge_line(
    proposer, reviewer, approver, live_enrollment
) -> None:
    balance = _through_approval(proposer, reviewer, approver, live_enrollment)
    line = obs.post(actor=approver, balance=balance)

    assert line.charge_type == ChargeType.OPENING_BALANCE
    assert line.gross_amount == DEBT
    assert line.net_amount == DEBT
    assert line.tax_amount == Decimal("0.000")
    assert line.is_partner_shareable is False
    assert line.charged_on == AS_OF

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.POSTED
    assert balance.posted_charge_line_id == line.pk
    assert balance.posted_by_id == approver.pk


def test_posting_twice_is_refused(proposer, reviewer, approver, live_enrollment) -> None:
    balance = _through_approval(proposer, reviewer, approver, live_enrollment)
    obs.post(actor=approver, balance=balance)
    balance.refresh_from_db()

    with pytest.raises(obs.AlreadyPostedError):
        obs.post(actor=approver, balance=balance)

    assert ChargeLine.objects.filter(charge_type=ChargeType.OPENING_BALANCE).count() == 1


def test_a_credit_is_recorded_reviewed_and_refused_at_posting(
    proposer, reviewer, approver, live_enrollment
) -> None:
    """
    The scope boundary of 8D-2, asserted rather than assumed.

    Posting a credit would mean creating a receipt for money this system never
    received — the exact fabrication Sprint 8D-1 refused. It is recorded and
    carried, and the refusal names the reason.
    """
    before = _ledger_census()
    balance = _through_approval(
        proposer,
        reviewer,
        approver,
        live_enrollment,
        code="OB-CR-1",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("225.000"),
    )

    with pytest.raises(obs.CreditNotPostableError, match="سند قبض"):
        obs.post(actor=approver, balance=balance)

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.APPROVED
    assert balance.posted_charge_line_id is None
    assert _ledger_census() == before


def test_the_database_refuses_a_posted_credit_even_without_the_service(
    proposer, reviewer, approver, live_enrollment
) -> None:
    from django.db import transaction

    receivable = _through_approval(proposer, reviewer, approver, live_enrollment)
    line = obs.post(actor=approver, balance=receivable)
    credit = _through_approval(
        proposer,
        reviewer,
        approver,
        live_enrollment,
        code="OB-CR-2",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("50.000"),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        OpeningBalance.objects.filter(pk=credit.pk).update(
            status=OpeningBalanceStatus.POSTED, posted_charge_line=line
        )


# ---------------------------------------------------------------------------
# 6 · Access, and the audit that survives a refusal
# ---------------------------------------------------------------------------
def test_the_finance_officer_may_propose_but_not_approve_or_post(
    proposer, reviewer, live_enrollment
) -> None:
    """§3.7/36أ — FIN holds ``V C E P``. Approval and posting are the manager's."""
    from apps.core.models import AuditEvent

    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")

    with pytest.raises(PermissionDenied):
        obs.approve(actor=proposer, balance=balance, note_ar="معتمد")
    with pytest.raises(PermissionDenied):
        obs.post(actor=proposer, balance=balance)

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="BR-080").exists()


def test_a_refused_post_leaves_its_denial_behind(proposer, reviewer, live_enrollment) -> None:
    """
    BR-085 — the refusal survives, because the check ran outside the transaction.

    This is the defect that made ``user_service.set_role`` lose a blocked
    privilege escalation in Sprint 2A: audit, then raise, then roll back, and
    the evidence goes with it.
    """
    from apps.core.models import AuditEvent

    balance = _propose(proposer)
    obs.review(actor=reviewer, balance=balance, enrollment=live_enrollment, note_ar="قوبل")
    before = AuditEvent.objects.filter(action="DENIED_ATTEMPT").count()

    with pytest.raises(PermissionDenied):
        obs.post(actor=proposer, balance=balance)

    after = AuditEvent.objects.filter(action="DENIED_ATTEMPT").count()
    assert after == before + 1
    denial = AuditEvent.objects.filter(action="DENIED_ATTEMPT").order_by("-id").first()
    assert denial is not None
    assert denial.denial_rule
    assert denial.summary_ar


def test_the_cashier_cannot_reach_opening_balances_at_all(seeded_settings) -> None:
    from apps.people.models import Role, User

    cash = User.objects.create_user(username="cash.ob", password=PASSWORD, role=Role.CASHIER)
    with pytest.raises(PermissionDenied):
        obs.list_balances(actor=cash)


def test_the_audit_account_reads_and_writes_nothing(seeded_settings) -> None:
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.ob", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    assert obs.list_balances(actor=auditor) == []
    with pytest.raises(PermissionDenied):
        _propose(auditor, code="OB-AUD")


def test_the_audit_entity_names_the_real_model(proposer) -> None:
    """
    No shortened alias.

    ``billing.OpeningBalance`` is what the audit trail says, because an audit
    row naming an entity that does not exist is worse than a long string —
    and the column was widened to 64 in 8D-1 precisely so it need not lie.
    """
    from apps.core.models import AuditEvent

    balance = _propose(proposer, code="OB-AUD-NAME")
    event = AuditEvent.objects.filter(reference="OB-AUD-NAME").first()

    assert event is not None
    assert event.entity_type == "billing.OpeningBalance"
    assert event.entity_type == f"{OpeningBalance._meta.app_label}.{OpeningBalance.__name__}"
    assert balance.code == event.reference
