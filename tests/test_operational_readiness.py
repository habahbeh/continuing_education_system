"""
The end-to-end readiness run (Sprint 8E).

Every other test in this repository proves one rule. This module proves the
system: six flows walked from beginning to end, through the same services the
screens call, in the order a person would actually perform them.

It is deliberately at the top level rather than inside an app, because none of
these flows belongs to one app. Flow B alone crosses datamigration, billing,
cashbox and operations, and the whole point of walking it is that the seams
between them hold.

**Nothing new is asserted here.** Each step has a unit test somewhere already.
What this catches is the thing unit tests structurally cannot: a flow that
works in pieces and not in sequence — a service whose preconditions nobody
can actually reach, a screen that cannot be got to, an order of operations
that only ever ran backwards in a fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
PASSWORD = "probe-password-1234"


# ---------------------------------------------------------------------------
# The centre's people, once
# ---------------------------------------------------------------------------
@pytest.fixture
def staff(seeded_settings):
    """One of each role, named the way the centre would name them."""
    from apps.people.models import Role, User

    def _user(username: str, role: str):
        return User.objects.create_user(username=username, password=PASSWORD, role=role)

    return {
        "manager": _user("mgr.8e", Role.CENTER_MANAGER),
        "registrar": _user("reg.8e", Role.REGISTRATION_OFFICER),
        "finance": _user("fin.8e", Role.FINANCE_OFFICER),
        "finance_two": _user("fin.8e.two", Role.FINANCE_OFFICER),
        "finance_manager": _user("fim.8e", Role.FINANCE_MANAGER),
        "cashier": _user("cash.8e", Role.CASHIER),
        "auditor": _user("aud.8e", Role.AUDIT_ACCOUNT),
    }


def _ledger_census() -> dict[str, int]:
    from apps.billing.models import ChargeLine, CreditReturn, Discount, ExtraFee, Refund
    from apps.cashbox.models import DailyClosing, PaymentAllocation, Receipt, ReceiptVoid
    from apps.settlements.models import PartnerClaim, PartnerSettlement

    return {
        m.__name__: m.objects.count()
        for m in (
            ChargeLine,
            CreditReturn,
            Discount,
            ExtraFee,
            Refund,
            DailyClosing,
            PaymentAllocation,
            Receipt,
            ReceiptVoid,
            PartnerClaim,
            PartnerSettlement,
        )
    }


def _credit_to_refund_due(obs, staff, enrollment, *, code, amount):
    """Propose → review → approve → declare refundable, in four hands."""
    from apps.billing.models import OpeningBalanceDirection

    balance = obs.propose_manually(
        actor=staff["finance"],
        code=code,
        direction=OpeningBalanceDirection.CREDIT,
        amount=amount,
        as_of=TERM_START,
        description_ar="رصيد دائن من دفعة 2022",
        legacy_number="202251024",
    )
    obs.review(actor=staff["finance_two"], balance=balance, enrollment=enrollment, note_ar="قوبل")
    obs.approve(actor=staff["manager"], balance=balance, note_ar="معتمد")
    obs.mark_refund_due(actor=staff["manager"], balance=balance, note_ar="لا تسجيل لاحق")
    balance.refresh_from_db()
    return balance


# ===========================================================================
# FLOW A — the historical archive, and the wall around it
# ===========================================================================
def test_flow_a_the_archive_is_read_validated_and_committed_without_touching_money(
    staff, sample_workbook
) -> None:
    """
    A · «قراءة الأرشيف والتحقق منه دون أي أثر مالي».

    Read → validate → commit, with the ledger counted at every step. This is
    the promise the whole 8D series rests on, walked as one sequence rather
    than three unit tests.
    """
    from apps.datamigration.models import RowState
    from apps.datamigration.services import archive_service, batch_service, validation_service

    before = _ledger_census()

    batch = batch_service.import_workbook(
        actor=staff["manager"], path=sample_workbook, code="MB-8E", note_ar="عرض تشغيلي"
    )
    assert batch.row_count == 8
    assert _ledger_census() == before

    report = validation_service.validate(actor=staff["manager"], batch=batch)
    assert report["reconciles"] is True
    assert report["archivable"] == 6
    assert report["unidentified"] == 2  # #REF! and «مركز» — kept, not dropped
    assert _ledger_census() == before

    summary = archive_service.commit(actor=staff["manager"], batch=batch)
    assert summary["archived"] == 6
    assert _ledger_census() == before

    # And the rows that could not be identified are still there to reconcile
    # against the sheet, rather than quietly gone.
    assert batch.rows.filter(state=RowState.UNIDENTIFIED).count() == 2


# ===========================================================================
# FLOW B — an old debt, from proposal to certificate
# ===========================================================================
def test_flow_b_an_old_debt_blocks_the_certificate_until_it_is_paid(
    staff, cash_method, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    B · «الذمة القديمة تُرحَّل، وتُسدَّد أولاً، ولا شهادة قبلها».

    The longest flow in the system: a debt carried in from the workbooks
    stops a participant graduating, and paying it lets them through. Eight
    steps, four roles, four apps.
    """
    from apps.billing.models import OpeningBalanceDirection
    from apps.billing.services import account_service
    from apps.billing.services import opening_balance_service as obs
    from apps.cashbox.services import payment_service
    from apps.operations.services import certificate_service, clearance_service

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    assert account_service.get_account_state(enrollment).balance == Decimal("0.000")

    # 1-3 · propose, review, approve — four hands, no ledger row yet.
    balance = obs.propose_manually(
        actor=staff["finance"],
        code="OB-8E-DEBT",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=Decimal("650.000"),
        as_of=TERM_START,
        description_ar="ذمة دبلوم إدارة أعمال 2022",
        legacy_number="202251004",
    )
    obs.review(
        actor=staff["finance_two"], balance=balance, enrollment=enrollment, note_ar="قوبل بالكشف"
    )
    obs.approve(actor=staff["manager"], balance=balance, note_ar="معتمد")
    balance.refresh_from_db()

    # 4 · the clearance is blocked while the debt is unposted and unpaid.
    clearance = clearance_service.open_clearance(
        actor=staff["manager"],
        enrollment=enrollment,
        case_type="GRADUATION",
        opened_on=PERIOD_END,
        code="CLR-8E-1",
    )
    clearance_service.complete_custody_step(
        actor=staff["manager"],
        clearance=clearance,
        custody_items=[{"label": "هوية المركز", "returned": True}],
    )
    with pytest.raises(clearance_service.ClearanceBlockedError, match="ذمة قديمة"):
        clearance_service.certify_finance_step(actor=staff["finance"], clearance=clearance)

    # 5 · and so is the certificate, through BR-075 rather than a second rule.
    with pytest.raises(certificate_service.ClearanceRequiredError):
        certificate_service.issue_certificate(
            actor=staff["manager"], enrollment=enrollment, grade="GOOD", issued_on=PERIOD_END
        )

    # 6 · post it. Now it is a charge line and BR-073 owns it.
    obs.post(actor=staff["manager"], balance=balance)
    assert account_service.get_account_state(enrollment).balance == Decimal("650.000")
    with pytest.raises(clearance_service.ClearanceBlockedError):
        clearance_service.certify_finance_step(actor=staff["finance"], clearance=clearance)

    # 7 · the participant pays.
    payment_service.take_payment(
        actor=staff["cashier"],
        enrollment=enrollment,
        amount=Decimal("650.000"),
        payment_method=cash_method,
        received_on=PERIOD_END,
    )
    assert account_service.get_account_state(enrollment).balance == Decimal("0.000")

    # 8 · the clearance completes and the certificate issues.
    clearance_service.certify_finance_step(actor=staff["finance"], clearance=clearance)
    clearance_service.second_certify_finance_step(
        actor=staff["finance_manager"], clearance=clearance
    )
    clearance_service.complete_handover_step(
        actor=staff["manager"],
        clearance=clearance,
        participant_ack_name="انوار رضوان الاحمد",
    )
    # Closing is a FOURTH act, not a consequence of the third — found while
    # writing this flow. WORKFLOWS §6.3 C5 re-reads the balance between
    # certification and close, because it can move in between, and BR-075
    # wants a COMPLETED clearance rather than a finished-looking one. The
    # screen offers it as its own button; the demo script has to press it.
    clearance_service.close_clearance(actor=staff["manager"], clearance=clearance)
    clearance.refresh_from_db()
    assert clearance.status == "COMPLETED"

    certificate = certificate_service.issue_certificate(
        actor=staff["manager"], enrollment=enrollment, grade="GOOD", issued_on=PERIOD_END
    )
    assert certificate.certificate_number


# ===========================================================================
# FLOW C — the arrear is reported apart from this year's trade
# ===========================================================================
def test_flow_c_one_payment_splits_between_the_arrear_and_this_term(
    staff, cash_method, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    C · «تحصيل ذمم سنوات سابقة يُدرج مستقلاً لا ضمن إيراد السنة».

    One 750 payment against 650 of arrear and 270 of current fees. The old
    debt clears first, and the two reports take the money apart correctly.
    """
    from apps.billing.models import OpeningBalanceDirection
    from apps.billing.services import opening_balance_service as obs
    from apps.cashbox.services import payment_service
    from apps.reporting.services import report_service

    enrollment = make_paid_enrollment(cohort_with_agreement, index=2, amount="")

    balance = obs.propose_manually(
        actor=staff["finance"],
        code="OB-8E-C",
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=Decimal("650.000"),
        as_of=TERM_START,
        description_ar="ذمة 2022",
    )
    obs.review(actor=staff["finance_two"], balance=balance, enrollment=enrollment, note_ar="قوبل")
    obs.approve(actor=staff["manager"], balance=balance, note_ar="معتمد")
    balance.refresh_from_db()
    line = obs.post(actor=staff["manager"], balance=balance)

    payment_service.take_payment(
        actor=staff["cashier"],
        enrollment=enrollment,
        amount=Decimal("750.000"),
        payment_method=cash_method,
        received_on=TERM_START,
    )

    # The arrear is settled first — the client's decision, exercised.
    from apps.billing.services import account_service

    assert account_service.outstanding_for_line(line) == Decimal("0.000")

    window = {"date_from": TERM_START, "date_to": PERIOD_END}
    revenue = report_service.revenue_report(actor=staff["finance"], **window)
    assert revenue["total"] == Decimal("750.000")
    assert revenue["prior_year_settlements"] == Decimal("650.000")
    assert revenue["current_period_total"] == Decimal("100.000")

    net = report_service.net_income_report(actor=staff["finance"], **window)
    assert net["collected"] == Decimal("100.000")
    assert net["prior_year_settlements"] == Decimal("650.000")
    assert net["total_cash_in"] == Decimal("750.000")


# ===========================================================================
# FLOW D — a credit carried forward
# ===========================================================================
def test_flow_d_a_credit_is_carried_onto_a_later_registration(
    staff, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    D · «إذا سجّل الطالب لاحقاً يُرحَّل له الرصيد» — and no receipt is invented.
    """
    from apps.billing.models import OpeningBalanceDirection, OpeningBalanceStatus
    from apps.billing.services import account_service
    from apps.billing.services import opening_balance_service as obs
    from apps.cashbox.models import PaymentAllocation, Receipt

    enrollment = make_paid_enrollment(cohort_with_agreement, index=3, amount="270.000")
    before = account_service.get_account_state(enrollment)
    receipts, allocations = Receipt.objects.count(), PaymentAllocation.objects.count()

    balance = obs.propose_manually(
        actor=staff["finance"],
        code="OB-8E-D",
        direction=OpeningBalanceDirection.CREDIT,
        amount=Decimal("225.000"),
        as_of=TERM_START,
        description_ar="دفع زائد 2022",
    )
    obs.review(actor=staff["finance_two"], balance=balance, enrollment=enrollment, note_ar="قوبل")
    obs.approve(actor=staff["manager"], balance=balance, note_ar="معتمد")
    balance.refresh_from_db()
    obs.carry_forward(
        actor=staff["manager"],
        balance=balance,
        enrollment=enrollment,
        note_ar="له تسجيل لاحق في 2026",
    )

    after = account_service.get_account_state(enrollment)
    assert after.opening_credit_applied == Decimal("225.000")
    assert after.balance == before.balance - Decimal("225.000")
    assert after.total_paid == before.total_paid  # not a payment
    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations

    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.APPLIED


# ===========================================================================
# FLOW E — a credit refunded, and the correction
# ===========================================================================
def test_flow_e_a_credit_is_refunded_reversed_and_refunded_again(
    staff, cash_method, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    E · «إذا لم يكن له تسجيل لاحق يُعاد له الرصيد» — with the cheque bouncing.

    Declare → pay out → reverse → pay again, and the closed-period guard
    holding throughout. No receipt, no allocation, and the first voucher
    still on the record at the end.
    """
    from django.utils import timezone

    from apps.billing.models import OpeningBalanceRefund, OpeningBalanceStatus
    from apps.billing.services import opening_balance_service as obs
    from apps.cashbox.models import PaymentAllocation, Receipt
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus
    from apps.core.services.period_service import ClosedPeriodError

    enrollment = make_paid_enrollment(cohort_with_agreement, index=4, amount="270.000")
    receipts, allocations = Receipt.objects.count(), PaymentAllocation.objects.count()

    balance = _credit_to_refund_due(
        obs, staff, enrollment, code="OB-8E-E", amount=Decimal("225.000")
    )
    assert [r["code"] for r in obs.outstanding_refunds(actor=staff["finance"])] == ["OB-8E-E"]

    # A closed month refuses the payout, and says so on the record.
    FinancialPeriod.objects.create(
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        status=FinancialPeriodStatus.CLOSED,
        closed_by=staff["manager"],
        closed_at=timezone.now(),
    )
    with pytest.raises(ClosedPeriodError):
        obs.pay_refund_due(
            actor=staff["finance"],
            balance=balance,
            code="PAY-8E-BAD",
            amount=balance.amount,
            paid_on=date(2026, 10, 5),
            payment_method=cash_method,
            external_reference="SND-BAD",
            payee_name_ar="مستلم",
        )

    # Paid in an open month instead.
    first = obs.pay_refund_due(
        actor=staff["finance"],
        balance=balance,
        code="PAY-8E-1",
        amount=balance.amount,
        paid_on=date(2026, 11, 3),
        payment_method=cash_method,
        external_reference="SND-8E-1",
        payee_name_ar="انوار رضوان الاحمد",
    )
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUNDED
    assert obs.outstanding_refunds(actor=staff["finance"]) == []

    # The cheque bounces; the correction reopens the obligation.
    obs.reverse_refund_payout(
        actor=staff["finance"],
        balance=balance,
        reversed_on=date(2026, 11, 20),
        reason_ar="شيك مرتجع من البنك",
    )
    balance.refresh_from_db()
    assert balance.status == OpeningBalanceStatus.REFUND_DUE
    assert [r["code"] for r in obs.outstanding_refunds(actor=staff["finance"])] == ["OB-8E-E"]

    # Paid again, and both rows survive.
    obs.pay_refund_due(
        actor=staff["finance"],
        balance=balance,
        code="PAY-8E-2",
        amount=balance.amount,
        paid_on=date(2026, 11, 25),
        payment_method=cash_method,
        external_reference="SND-8E-2",
        payee_name_ar="انوار رضوان الاحمد",
    )
    first.refresh_from_db()
    assert first.external_reference == "SND-8E-1"  # untouched
    assert first.active_key is None
    assert OpeningBalanceRefund.objects.filter(opening_balance=balance).count() == 2

    # And the whole flow invented no incoming cash.
    assert Receipt.objects.count() == receipts
    assert PaymentAllocation.objects.count() == allocations


# ===========================================================================
# FLOW F — an ordinary participant, start to finish
# ===========================================================================
def test_flow_f_the_ordinary_life_of_a_participant(
    staff, cash_method, priced_catalog, active_semester
) -> None:
    """
    F · التسجيل ثم القبض ثم براءة الذمة ثم الشهادة.

    The flow the centre runs every day, with no archive and no arrear in it —
    the baseline that must keep working while everything above is added.
    """
    from apps.billing.services import account_service, charge_service
    from apps.cashbox.services import payment_service
    from apps.catalog.models import Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.operations.services import certificate_service, clearance_service
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service

    participant = participant_service.create_participant(
        actor=staff["registrar"],
        data={
            "category": ParticipantCategory.UNIVERSITY,
            "name_ar": "ليان علاء الدين تيسير حمدان",
            "id_document_type": IdDocumentType.NATIONAL_ID,
            "id_document_number": "9998887770",
            "registered_on": TERM_START,
            "no_refund_pledge_accepted": True,
        },
    )
    assert len(participant.participant_number) == 9

    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-8E-F",
        program=program,
        semester=active_semester,
        name_ar="دفعة العرض",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
    )
    enrollment = Enrollment.objects.create(
        code="EN-8E-F",
        participant=participant,
        cohort=cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
        status="ACTIVE",
    )
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=TERM_START
    )
    charge_service.charge_lines_from_quote(
        actor=staff["registrar"], enrollment=enrollment, quote=quote, charged_on=TERM_START
    )
    due = account_service.get_account_state(enrollment).total_due
    assert due > Decimal("0.000")

    payment_service.take_payment(
        actor=staff["cashier"],
        enrollment=enrollment,
        amount=due,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    assert account_service.get_account_state(enrollment).balance == Decimal("0.000")

    clearance = clearance_service.open_clearance(
        actor=staff["manager"],
        enrollment=enrollment,
        case_type="GRADUATION",
        opened_on=PERIOD_END,
        code="CLR-8E-F",
    )
    clearance_service.complete_custody_step(
        actor=staff["manager"],
        clearance=clearance,
        custody_items=[{"label": "هوية المركز", "returned": True}],
    )
    clearance_service.certify_finance_step(actor=staff["finance"], clearance=clearance)
    clearance_service.second_certify_finance_step(
        actor=staff["finance_manager"], clearance=clearance
    )
    clearance_service.complete_handover_step(
        actor=staff["manager"],
        clearance=clearance,
        participant_ack_name=participant.name_ar,
    )
    clearance_service.close_clearance(actor=staff["manager"], clearance=clearance)

    certificate = certificate_service.issue_certificate(
        actor=staff["manager"], enrollment=enrollment, grade="EXCELLENT", issued_on=PERIOD_END
    )
    assert certificate.certificate_number


# ===========================================================================
# The screens the demo will actually be driven from
# ===========================================================================
def test_every_navigable_screen_opens_for_the_role_that_holds_it(client, staff) -> None:
    """
    The menu is not a promise the system can break.

    ``nav`` is built from the permission matrix, so an entry appears exactly
    when the role may VIEW the screen. This walks every entry for every role
    and asserts the page actually opens — a dead link in a live demo is worse
    than a missing one.
    """
    from apps.people import nav

    failures: list[str] = []
    for user in staff.values():
        client.force_login(user)
        for group in nav.nav_for(user):
            for item in group["items"]:
                response = client.get(item["url"])
                if response.status_code != 200:
                    failures.append(f"{user.role} · {item['url']} → {response.status_code}")
        client.logout()

    assert not failures, "navigable screens that do not open: " + ", ".join(failures)


def test_the_dashboard_is_reachable_from_the_menu(client, staff) -> None:
    """
    Found during the readiness run: ``operations:dashboard`` had a route, a
    view and a permission row, and no way to reach it except by typing the
    URL. It is the first screen a demo opens.
    """
    from apps.people import nav

    client.force_login(staff["manager"])
    urls = {item["url"] for group in nav.nav_for(staff["manager"]) for item in group["items"]}
    assert reverse("operations:dashboard") in urls
    assert client.get(reverse("operations:dashboard")).status_code == 200
