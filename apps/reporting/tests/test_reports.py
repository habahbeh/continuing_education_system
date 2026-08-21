"""
The seven reports (§9).

Two kinds of assertion here. The first is agreement: a report must produce the
same number as the screen or service that owns it, because a report is what
somebody prints and acts on, and two documents disagreeing about one figure is
the failure the service layer exists to prevent.

The second is access. §9's seven sit behind one matrix row, and granting the
row is not granting the seven (BR-080, BR-099) — the finance manager reaches
this screen and exactly one report, and the cashier reaches neither.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.expenses.services import expense_service
from apps.reporting.services import report_service
from apps.settlements.services import claim_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
FULL = Decimal("270.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.rep2", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _url(number: int, **params: str) -> str:
    base = reverse("reporting:report", args=[number])
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{query}" if query else base


# ---------------------------------------------------------------------------
# Agreement between reports and the services that own the numbers
# ---------------------------------------------------------------------------
def test_report_1_and_report_6_agree_for_one_day(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    One counts receipts, the other counts what the till was closed on.

    They read the same rows, so they must produce the same total. If these
    ever diverge the bug is upstream of both, which is why the assertion lives
    here rather than inside either one.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    revenue = (
        signed_in(finance)
        .get(_url(1, **{"from": TERM_START.isoformat(), "to": TERM_START.isoformat()}))
        .context["report"]
    )
    closing = (
        signed_in(finance)
        .get(_url(6, **{"from": TERM_START.isoformat(), "to": TERM_START.isoformat()}))
        .context["report"]
    )

    assert revenue["total"] == closing["receipts_total"] == FULL


def test_report_5_is_the_screen_the_participant_already_sees(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §9.5 — not a second query.

    ``account_statement`` was built in Sprint 8B and renders the account
    screen; re-deriving it for print is how two documents come to disagree
    about one account.
    """
    from apps.billing.services import account_service
    from apps.operations.services import enrollment_service

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    report = (
        signed_in(finance).get(_url(5, enrollment=enrollment.code)).context["report"]["statement"]
    )
    direct = account_service.account_statement(
        actor=finance,
        enrollment=enrollment_service.get_enrollment(actor=finance, code=enrollment.code),
    )

    assert report["state"].balance == direct["state"].balance
    assert len(report["charges"]) == len(direct["charges"])


def test_report_2_subtracts_only_approved_expenses(
    signed_in, finance, approver, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §9.2 — an entry nobody approved is a claim about money, not a fact.

    Net income must not fall on the strength of one, and must fall on the
    strength of an approved one.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    window = {"from": TERM_START.isoformat(), "to": PERIOD_END.isoformat()}

    baseline = signed_in(finance).get(_url(2, **window)).context["report"]["net_income"]

    expense = expense_service.record(
        actor=finance,
        code="EXP-R2",
        category="MARKETING",
        amount=Decimal("40.000"),
        incurred_on=TERM_START,
        description_ar="إعلان",
    )
    pending = signed_in(finance).get(_url(2, **window)).context["report"]
    assert pending["net_income"] == baseline
    assert pending["expenses_total"] == Decimal("0.000")

    expense_service.approve(actor=approver, expense=expense)
    after = signed_in(finance).get(_url(2, **window)).context["report"]
    assert after["expenses_total"] == Decimal("40.000")
    assert after["net_income"] == baseline - Decimal("40.000")


def test_report_2_subtracts_only_approved_partner_claims(
    signed_in, finance, approver, cohort_with_agreement, percent_agreement, make_paid_enrollment
) -> None:
    """A draft claim carries no seal, so it moves no reported figure."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    window = {"from": TERM_START.isoformat(), "to": PERIOD_END.isoformat()}

    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    draft = signed_in(finance).get(_url(2, **window)).context["report"]
    assert draft["partner_total"] == Decimal("0.000")

    claim_service.approve_claim(actor=approver, claim=claim)
    approved = signed_in(finance).get(_url(2, **window)).context["report"]
    assert approved["partner_total"] == Decimal("125.000")


def test_report_2_excludes_deposits_from_revenue(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """C-21 — money held on someone's behalf is a liability, never income."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    window = {"from": TERM_START.isoformat(), "to": PERIOD_END.isoformat()}

    report = signed_in(finance).get(_url(2, **window)).context["report"]
    revenue = signed_in(finance).get(_url(1, **window)).context["report"]

    assert report["collected"] == revenue["total"] - revenue["deposit_total"]


def test_report_4_flags_the_partner_consequence(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §5.4 — being overdue does not merely mean chasing.

    It voids the partner's entitlement for that participant, so the report
    says so rather than leaving the reader to know it.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="100.000")  # 270 due

    report = (
        signed_in(finance)
        .get(_url(4, **{"from": TERM_START.isoformat(), "to": PERIOD_END.isoformat()}))
        .context["report"]
    )

    assert report["count"] == 1
    assert report["rows"][0]["voids_partner_entitlement"] is True


def test_report_7_separates_approved_from_pending(signed_in, finance, approver) -> None:
    """§9.7 shows both, and says which figure report 2 will use."""
    expense_service.record(
        actor=finance,
        code="EXP-P",
        category="OTHER",
        amount=Decimal("70.000"),
        incurred_on=TERM_START,
        description_ar="قيد معلّق",
    )
    approved = expense_service.record(
        actor=finance,
        code="EXP-Q",
        category="OTHER",
        amount=Decimal("30.000"),
        incurred_on=TERM_START,
        description_ar="قيد معتمد",
    )
    expense_service.approve(actor=approver, expense=approved)

    report = (
        signed_in(finance)
        .get(_url(7, **{"from": TERM_START.isoformat(), "to": PERIOD_END.isoformat()}))
        .context["report"]
    )

    assert report["approved_total"] == Decimal("30.000")
    assert report["pending_total"] == Decimal("70.000")


# ---------------------------------------------------------------------------
# Access — per report, not per screen
# ---------------------------------------------------------------------------
def test_the_finance_manager_reaches_one_report_only(signed_in, seeded_settings) -> None:
    """
    Q-14 · BR-099 — report 5 is the tool they need to confirm a zero balance
    before certifying a clearance. Revenue and net income are outside their
    scope, and the refusal is audited under the rule that made it.
    """
    from apps.core.models import AuditEvent
    from apps.people.models import Role, User

    fim = User.objects.create_user(username="fim.rep", password=PASSWORD, role=Role.FINANCE_MANAGER)
    client = signed_in(fim)

    assert client.get(reverse("reporting:reports")).status_code == 200
    assert client.get(_url(5)).status_code == 200
    for blocked in (1, 2, 3, 4, 6, 7):
        assert client.get(_url(blocked)).status_code == 403

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="BR-099").exists()


def test_the_cashier_reaches_no_report_at_all(signed_in, cashier) -> None:
    """§8 — «الصندوق: القبض فقط»، and the screen itself is closed to them."""
    client = signed_in(cashier)
    assert client.get(reverse("reporting:reports")).status_code == 403
    for number in range(1, 8):
        assert client.get(_url(number)).status_code == 403


def test_the_registrar_reaches_four_and_five(signed_in, registrar) -> None:
    """The overdue list and the participant statement — their two tools."""
    client = signed_in(registrar)
    assert client.get(_url(4)).status_code == 200
    assert client.get(_url(5)).status_code == 200
    for blocked in (1, 2, 3, 6, 7):
        assert client.get(_url(blocked)).status_code == 403


def test_the_index_marks_what_the_role_may_not_open(signed_in, seeded_settings) -> None:
    """The menu tells the truth rather than offering a link that 403s."""
    from apps.people.models import Role, User

    fim = User.objects.create_user(username="fim.idx", password=PASSWORD, role=Role.FINANCE_MANAGER)
    rows = signed_in(fim).get(reverse("reporting:reports")).context["reports"]

    allowed = {r["number"] for r in rows if r["allowed"]}
    assert allowed == {5}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def test_the_export_runs_the_same_gate_as_the_page(signed_in, cashier, registrar) -> None:
    """
    A download route reading more loosely than the page it exports would be a
    way around the matrix wearing a spreadsheet icon.
    """
    export = reverse("reporting:report-export", args=[7])
    assert signed_in(cashier).get(export).status_code == 403
    assert signed_in(registrar).get(export).status_code == 403


def test_the_csv_carries_a_bom_so_excel_reads_arabic(signed_in, finance, approver) -> None:
    """
    ``utf-8-sig`` — no Excel dependency, and the BOM is what makes it open
    correctly by double-clicking.
    """
    expense = expense_service.record(
        actor=finance,
        code="EXP-CSV",
        category="MARKETING",
        amount=Decimal("25.000"),
        incurred_on=TERM_START,
        description_ar="إعلان في الصحف",
    )
    expense_service.approve(actor=approver, expense=expense)

    response = signed_in(finance).get(
        reverse("reporting:report-export", args=[7])
        + f"?from={TERM_START.isoformat()}&to={PERIOD_END.isoformat()}"
    )

    assert response.status_code == 200
    assert response["Content-Disposition"].endswith('filename="report-7.csv"')
    body = response.content
    assert body.startswith(b"\xef\xbb\xbf")  # BOM
    assert "إعلان في الصحف" in body.decode("utf-8-sig")


def test_a_report_with_no_row_list_is_not_exportable(signed_in, finance) -> None:
    """
    Report 2 is a calculation, not a table.

    Exporting a summary as though it were data would invite someone to pivot
    on it, so the route refuses rather than inventing rows.
    """
    assert 2 not in report_service.EXPORTABLE
    response = signed_in(finance).get(reverse("reporting:report-export", args=[2]))
    assert response.status_code == 404


def test_an_unknown_report_number_is_a_404(signed_in, finance) -> None:
    assert signed_in(finance).get(reverse("reporting:report", args=[9])).status_code == 404
