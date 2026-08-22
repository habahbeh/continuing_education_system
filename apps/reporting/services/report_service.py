"""
The seven reports §9 requires.

**Nothing here computes money.** Every figure is asked of the service that
owns it — ``account_service`` for a balance, ``closing_service`` for a day's
takings, ``claim_service`` for what a partner earned, ``expense_service`` for
what the centre spent. A report that derived its own total would be a second
opinion about money, which is the failure the whole service layer exists to
prevent; and a second opinion is worse here than anywhere, because a report is
what somebody prints and acts on.

Two consequences worth stating:

* report 1 and report 6 must AGREE for a single day. One counts receipts, the
  other counts what the till was closed on, and they read the same rows. A
  test asserts it, because if they ever diverge the bug is upstream of both.
* report 5 is not a new query at all. ``account_statement`` was built in
  Sprint 8B and already renders the participant's screen; the report is that
  same function on printable paper.

**Access is per report, not per screen** (BR-080, BR-099). ``require_report``
already enforces that and audits the refusal; every function here calls it
first, so a caller cannot reach report 2 by asking this module directly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from apps.billing.services.account_service import ZERO
from apps.people.permissions import policy

#: §9's seven, in the order the requirement lists them.
REPORTS: tuple[tuple[int, str], ...] = (
    (1, "إجمالي الإيرادات ضمن فترة زمنية"),
    (2, "صافي دخل المركز بعد حصص الشركاء"),
    (3, "كشف مستحقات كل شريك ومخالصاته"),
    (4, "قائمة المتأخرين في الدفع"),
    (5, "كشف حساب مالي شامل للطالب"),
    (6, "إجمالي القبض اليومي ومطابقة الإقفال"),
    (7, "المصروفات"),
)

REPORT_TITLES = dict(REPORTS)


def available_reports(*, actor: Any) -> list[dict[str, Any]]:
    """
    Which of the seven this role may open.

    The reports SCREEN is one row in the matrix and the seven behind it are
    not — the finance manager reaches the screen and exactly one report.
    """
    from apps.people.permissions.matrix import allowed_reports

    permitted = allowed_reports(getattr(actor, "role", "") or "")
    return [
        {"number": number, "title": title, "allowed": number in permitted}
        for number, title in REPORTS
    ]


# ---------------------------------------------------------------------------
# 1 — إجمالي الإيرادات (§9.1)
# ---------------------------------------------------------------------------
def revenue_report(
    *, actor: Any, date_from: date, date_to: date, request: Any = None
) -> dict[str, Any]:
    """
    What was COLLECTED in the period, split by what it was collected against.

    Cash basis, like everything else that answers "what came in": an invoice
    raised is not revenue received, and §9.1 asks for the latter.

    Registration fees are shown separately because §3.1 keeps them out of
    every partner's base — the centre's own share of the money is not the
    same question as the total.
    """
    policy.require_report(actor, 1, request=request)

    from apps.billing.models import ChargeType
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    allocations = (
        PaymentAllocation.objects.filter(
            receipt__status=ReceiptStatus.ISSUED,
            receipt__received_on__gte=date_from,
            receipt__received_on__lte=date_to,
        )
        .select_related("charge_line", "receipt", "enrollment__cohort__program")
        .order_by("receipt__received_on", "id")
    )

    by_type: dict[str, Decimal] = {}
    by_program_type: dict[str, Decimal] = {}
    total = ZERO
    unallocated = ZERO

    for allocation in allocations:
        total += allocation.amount
        line = allocation.charge_line
        if line is None:
            unallocated += allocation.amount
            continue
        by_type[line.charge_type] = by_type.get(line.charge_type, ZERO) + allocation.amount
        enrollment = allocation.enrollment
        if enrollment is not None:
            kind = enrollment.cohort.program.program_type
            by_program_type[kind] = by_program_type.get(kind, ZERO) + allocation.amount

    # Sprint 8D-3 · client decision 3 — «يُدرج تحصيل ذمم السنوات السابقة
    # بنداً مستقلاً لا ضمن إيراد السنة الجارية». Split by CHARGE TYPE, which
    # is what the ledger already records, rather than by weakening the
    # ``is_revenue`` constraint that C-21 depends on. The cash is real and it
    # arrived in this period; what it is NOT is this period's business.
    prior_year_settlements = by_type.get(ChargeType.OPENING_BALANCE, ZERO)

    return {
        "number": 1,
        "title": REPORT_TITLES[1],
        "date_from": date_from,
        "date_to": date_to,
        "total": total,
        "prior_year_settlements": prior_year_settlements,
        "current_period_total": total - prior_year_settlements,
        "registration_total": by_type.get(ChargeType.REGISTRATION, ZERO),
        "tuition_total": by_type.get(ChargeType.TUITION, ZERO),
        "extra_fee_total": by_type.get(ChargeType.EXTRA_FEE, ZERO),
        "deposit_total": by_type.get(ChargeType.DEPOSIT, ZERO),
        "unallocated_credit": unallocated,
        "by_type": sorted(by_type.items()),
        "by_program_type": sorted(by_program_type.items()),
    }


# ---------------------------------------------------------------------------
# 2 — صافي دخل المركز (§9.2)
# ---------------------------------------------------------------------------
def net_income_report(
    *, actor: Any, date_from: date, date_to: date, request: Any = None
) -> dict[str, Any]:
    """
    Revenue, less what partners earned, less what the centre spent.

    The one report that reads BOTH ledgers, and the only place they meet:
    partner shares come from approved claims, expenses from approved expense
    entries. Neither an unapproved claim nor an unapproved expense moves this
    number, because both are assertions nobody has signed.

    Deposits are excluded from revenue here: money held on a participant's
    behalf is a liability, never income (C-21), and counting it would inflate
    the centre's result by whatever it happens to be holding that month.
    """
    policy.require_report(actor, 2, request=request)

    from apps.billing.models import ChargeType
    from apps.billing.services import opening_balance_service
    from apps.expenses.services import expense_service
    from apps.settlements.models import ClaimStatus, PartnerClaim

    revenue = revenue_report(actor=actor, date_from=date_from, date_to=date_to, request=request)

    # Sprint 8D-3 · client decision 3. Two subtractions, for two different
    # reasons, and conflating them would be an accounting error rather than a
    # presentation one:
    #
    #   * deposits are not income at all — money held on someone's behalf
    #     (C-21);
    #   * prior-year settlements ARE income, but they are last year's. Cash
    #     recovered on a 2022 arrear does not tell the reader anything about
    #     how the centre traded this month, which is the question §9.2 asks.
    #
    # So net income is computed on ordinary current-period collections, and
    # the recovered arrears are reported beside it with their own total. The
    # cash that actually came through the door is ``total_cash_in``, and it is
    # shown too — hiding it would be its own kind of lie.
    prior_year_settlements = revenue["prior_year_settlements"]
    collected = revenue["total"] - revenue["deposit_total"] - prior_year_settlements

    claims = PartnerClaim.objects.filter(
        status__in=(ClaimStatus.APPROVED, ClaimStatus.PAID),
        period_to__gte=date_from,
        period_to__lte=date_to,
    ).select_related("partner")

    partner_total = ZERO
    by_partner: dict[str, Decimal] = {}
    for claim in claims:
        partner_total += claim.net_payable
        by_partner[claim.partner.name_ar] = (
            by_partner.get(claim.partner.name_ar, ZERO) + claim.net_payable
        )

    expenses_total = expense_service.approved_total(
        actor=actor, date_from=date_from, date_to=date_to, request=request
    )

    # Sprint 8D-4 — cash handed back on historical credits, DISCLOSED and not
    # subtracted. The distinction is real accounting rather than presentation:
    # the centre is holding money that was never its own, inherited from
    # before the system existed. Paying it out reduces cash AND reduces the
    # liability, so it costs the year nothing and must not depress net income.
    # Leaving it off the report entirely would be the other error — a reader
    # comparing the bank against these figures needs to see where it went.
    refunds_paid = opening_balance_service.refunds_paid_between(
        date_from=date_from, date_to=date_to
    )

    return {
        "number": 2,
        "title": REPORT_TITLES[2],
        "date_from": date_from,
        "date_to": date_to,
        "collected": collected,
        "deposits_excluded": revenue["deposit_total"],
        "prior_year_settlements": prior_year_settlements,
        "total_cash_in": collected + prior_year_settlements,
        "historical_refunds_paid": refunds_paid,
        "partner_total": partner_total,
        "by_partner": sorted(by_partner.items()),
        "expenses_total": expenses_total,
        "net_income": collected - partner_total - expenses_total,
        "charge_type_note": ChargeType.DEPOSIT,
    }


# ---------------------------------------------------------------------------
# 3 — مستحقات الشركاء (§9.3)
# ---------------------------------------------------------------------------
def partner_dues_report(
    *, actor: Any, partner_code: str = "", request: Any = None
) -> dict[str, Any]:
    """Claims, settlements and outstanding obligations, per partner."""
    policy.require_report(actor, 3, request=request)

    from apps.settlements.services import claim_service, clawback_service, settlement_service

    claims = claim_service.list_claims(actor=actor, partner_code=partner_code, request=request)
    settlements = settlement_service.list_settlements(
        actor=actor, partner_code=partner_code, request=request
    )
    obligations = clawback_service.list_obligations(
        actor=actor, partner_code=partner_code, request=request
    )

    return {
        "number": 3,
        "title": REPORT_TITLES[3],
        "partner_code": partner_code,
        "claims": claims,
        "settlements": settlements,
        "obligations": obligations,
        "claims_total": sum((c["net_payable"] for c in claims), ZERO),
        "settled_total": sum((s["total_paid"] for s in settlements), ZERO),
        "obligations_outstanding": sum((o["outstanding"] for o in obligations), ZERO),
    }


# ---------------------------------------------------------------------------
# 4 — المتأخرون (§9.4 · §5.4)
# ---------------------------------------------------------------------------
def overdue_report(
    *, actor: Any, as_of: date, cohort_code: str = "", request: Any = None
) -> dict[str, Any]:
    """
    Who is behind on payment — and therefore earns their partner nothing.

    Not merely a chasing list: §5.4 makes PAYMENT_OVERDUE one of the four
    statuses that void a partner's entitlement, so this is the report by which
    a financial consequence is reviewed.
    """
    policy.require_report(actor, 4, request=request)

    from apps.operations.services import enrollment_service
    from apps.settlements.services import entitlement_service

    rows: list[dict[str, Any]] = []
    for row in enrollment_service.list_enrollments(
        actor=actor, cohort_code=cohort_code, request=request
    ):
        if not row["participant_owes"]:
            continue
        enrollment = enrollment_service.get_enrollment(
            actor=actor, code=row["code"], request=request
        )
        if not entitlement_service.is_payment_overdue(enrollment, as_of=as_of):
            continue
        rows.append(
            {
                **row,
                "voids_partner_entitlement": enrollment.cohort.agreement_id is not None,
            }
        )

    return {
        "number": 4,
        "title": REPORT_TITLES[4],
        "as_of": as_of,
        "rows": rows,
        "total_outstanding": sum((r["balance"] for r in rows), ZERO),
        "count": len(rows),
    }


# ---------------------------------------------------------------------------
# 5 — كشف حساب الطالب (§9.5)
# ---------------------------------------------------------------------------
def participant_statement_report(
    *, actor: Any, enrollment_code: str, request: Any = None
) -> dict[str, Any]:
    """
    §9.5's statement — the same function the participant's screen uses.

    Deliberately not a second query. ``account_statement`` already assembles
    the charges, payments, discounts, refunds and credit returns §9.5 asks to
    be visible; re-deriving them for print is how two documents come to
    disagree about one account.
    """
    policy.require_report(actor, 5, request=request)

    from apps.billing.services import account_service
    from apps.operations.services import enrollment_service

    enrollment = enrollment_service.get_enrollment(
        actor=actor, code=enrollment_code, request=request
    )
    statement = account_service.account_statement(
        actor=actor, enrollment=enrollment, request=request
    )
    return {"number": 5, "title": REPORT_TITLES[5], "statement": statement}


# ---------------------------------------------------------------------------
# 6 — القبض اليومي والإقفال (§9.6 · §5.2)
# ---------------------------------------------------------------------------
def daily_closing_report(*, actor: Any, on_date: date, request: Any = None) -> dict[str, Any]:
    """
    What each till took, and whether the count matched.

    §5.2's «إقفال يومي: مطابقة المقبوض بالوصولات». The system total comes from
    ``system_total_for`` — the same function the closing screen uses — so this
    report and report 1 must agree for the same day.
    """
    policy.require_report(actor, 6, request=request)

    from apps.cashbox.services import closing_service, payment_service

    closings = closing_service.list_closings(actor=actor, on_date=on_date, request=request)
    receipts = payment_service.list_receipts(actor=actor, on_date=on_date, request=request)
    issued = [r for r in receipts if r["status"] == "ISSUED"]

    return {
        "number": 6,
        "title": REPORT_TITLES[6],
        "on_date": on_date,
        "closings": closings,
        "receipts": issued,
        "receipts_total": sum((r["amount"] for r in issued), ZERO),
        "counted_total": sum((c["counted_total"] for c in closings), ZERO),
        "variance_total": sum((c["variance"] for c in closings), ZERO),
        "unreconciled": [c for c in closings if c["status"] != "RECONCILED"],
    }


# ---------------------------------------------------------------------------
# 7 — المصروفات (§9.7)
# ---------------------------------------------------------------------------
def expenses_report(
    *,
    actor: Any,
    date_from: date,
    date_to: date,
    category: str = "",
    request: Any = None,
) -> dict[str, Any]:
    """
    What the centre spent (§9.7).

    Both approved and unapproved entries are listed, flagged — the centre
    should see what is pending — but only the approved total is the figure
    report 2 subtracts, and the report says which is which rather than
    presenting one number.
    """
    policy.require_report(actor, 7, request=request)

    from apps.expenses.services import expense_service

    rows = expense_service.list_expenses(
        actor=actor,
        category=category,
        date_from=date_from,
        date_to=date_to,
        request=request,
    )
    return {
        "number": 7,
        "title": REPORT_TITLES[7],
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "totals": expense_service.totals_by_category(
            actor=actor, date_from=date_from, date_to=date_to, request=request
        ),
        "approved_total": expense_service.approved_total(
            actor=actor, date_from=date_from, date_to=date_to, request=request
        ),
        "pending_total": sum((r["amount"] for r in rows if r["status"] == "RECORDED"), ZERO),
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
#: Which key on each report payload holds the exportable rows, and which
#: columns to write. A report with no natural row list is not exportable, and
#: saying so is better than exporting a summary that looks like data.
EXPORTABLE: dict[int, tuple[str, tuple[tuple[str, str], ...]]] = {
    3: (
        "claims",
        (
            ("code", "الرمز"),
            ("partner_name", "الشريك"),
            ("period_from", "من"),
            ("period_to", "إلى"),
            ("partner_share", "حصة الشريك"),
            ("total_deductions", "الحسومات"),
            ("net_payable", "الصافي"),
            ("status", "الحالة"),
        ),
    ),
    4: (
        "rows",
        (
            ("code", "التسجيل"),
            ("participant_name", "المشارك"),
            ("participant_number", "الرقم"),
            ("cohort_code", "الدفعة"),
            ("total_due", "المستحق"),
            ("total_paid", "المدفوع"),
            ("balance", "الرصيد"),
        ),
    ),
    6: (
        "receipts",
        (
            ("internal_receipt_number", "رقم السند"),
            ("participant_name", "المشارك"),
            ("received_on", "التاريخ"),
            ("amount", "المبلغ"),
            ("payment_method", "الطريقة"),
            ("cashier", "الصندوق"),
        ),
    ),
    7: (
        "rows",
        (
            ("code", "الرمز"),
            ("incurred_on", "التاريخ"),
            ("category_label", "التصنيف"),
            ("description_ar", "البيان"),
            ("cohort_code", "الدفعة"),
            ("amount", "المبلغ"),
            ("status_display", "الحالة"),
        ),
    ),
}


def export_encoding(*, as_of: date) -> str:
    """
    ``utf-8-sig`` — the BOM is what makes Excel read Arabic correctly.

    A setting rather than a literal because it is the kind of thing a site
    changes once, for one stubborn install, and should not need a release for.
    """
    from apps.core.services.settings_service import get_setting

    return str(get_setting("report_export_encoding", as_of=as_of, default="utf-8-sig"))


def csv_rows(report: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """
    Header and body for a report's CSV, or empty when it has no row list.

    Standard library only — no Excel dependency. §9 asks for reports, not for
    a spreadsheet format, and a CSV opens in Excel by double-clicking.
    """
    spec = EXPORTABLE.get(int(report["number"]))
    if spec is None:
        return [], []
    key, columns = spec
    header = [label for _field, label in columns]
    body = [[str(row.get(field, "")) for field, _label in columns] for row in report.get(key, [])]
    return header, body


__all__ = [
    "EXPORTABLE",
    "REPORTS",
    "REPORT_TITLES",
    "available_reports",
    "csv_rows",
    "daily_closing_report",
    "expenses_report",
    "export_encoding",
    "net_income_report",
    "overdue_report",
    "participant_statement_report",
    "partner_dues_report",
    "revenue_report",
]
