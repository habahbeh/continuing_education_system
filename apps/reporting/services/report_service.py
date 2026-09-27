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

#: السؤال الذي يجيب عنه كل تقرير، بعبارة مَن سيقرؤه لا بعبارة §9.
#: يُعرض على بطاقة الفهرس قبل أن يُفتح التقرير: سبعة عناوين متشابهة الطول
#: تُقرأ قائمةً واحدة، والسؤال هو ما يفرّق بينها.
REPORT_QUESTIONS: dict[int, str] = {
    1: "كم قُبض في المدى، وعلى أيّ بند؟",
    2: "ماذا بقي للمركز بعد حصص الشركاء ومصروفاته؟",
    3: "كم لكل شريك، وكم صُرف له، وكم بقي؟",
    4: "مَن عليه رصيد، وما أثر تأخّره على استحقاق شريكه؟",
    5: "ماذا على هذا المشارك وله — بالخصم والنقل والاسترداد؟",
    6: "هل طابق المعدود ما سجّله النظام في الصندوق؟",
    7: "على أيّ بند أنفق المركز، وكم منه معتمَد؟",
}

#: أيقونة كل تقرير — من لوحة الرموز في `partials/_nav_icons.html`، لا من
#: مكتبة خارجية (ADR-003). سبعة عناوين متشابهة الطول في شريط واحد تُقرأ كتلةً
#: رمادية؛ الأيقونة هي ما يجعل «المتأخرون» يُلتقط بالعين قبل قراءته.
REPORT_ICONS: dict[int, str] = {
    1: "coins",
    2: "scale",
    3: "building",
    4: "alert",
    5: "receipt",
    6: "vault",
    7: "wallet",
}

#: لون البطاقة — من `.tone-*` التي تعرفها ورقة الأنماط.
REPORT_TONES: dict[int, str] = {
    1: "ok",
    2: "info",
    3: "violet",
    4: "danger",
    5: "brand",
    6: "teal",
    7: "warn",
}


def available_reports(*, actor: Any) -> list[dict[str, Any]]:
    """
    Which of the seven this role may open.

    The reports SCREEN is one row in the matrix and the seven behind it are
    not — the finance manager reaches the screen and exactly one report.
    """
    from apps.people.permissions.matrix import allowed_reports

    permitted = allowed_reports(getattr(actor, "role", "") or "")
    return [
        {
            "number": number,
            "title": title,
            "allowed": number in permitted,
            "question": REPORT_QUESTIONS[number],
            "icon": REPORT_ICONS[number],
            "tone": REPORT_TONES[number],
        }
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
    # Sprint 8L · A-1 — الإيراد مفصَّلاً حسب الدفعة، وهو نصف ما يقرؤه المدير:
    # «كم قُبض؟» سؤالٌ يليه دائماً «من أيّ دفعة؟». يُجمَّع في الحلقة القائمة
    # نفسها ومن الصفوف التي جُلبت أصلاً — لا استعلام إضافي، ولا رقم يُحتسب هنا
    # لم تحتسبه الخدمة التي تملكه.
    by_cohort: dict[str, dict[str, Any]] = {}
    receipts: set[int] = set()
    total = ZERO
    unallocated = ZERO

    for allocation in allocations:
        total += allocation.amount
        receipts.add(allocation.receipt_id)
        line = allocation.charge_line
        if line is None:
            unallocated += allocation.amount
            continue
        by_type[line.charge_type] = by_type.get(line.charge_type, ZERO) + allocation.amount
        enrollment = allocation.enrollment
        if enrollment is not None:
            kind = enrollment.cohort.program.program_type
            by_program_type[kind] = by_program_type.get(kind, ZERO) + allocation.amount

            cohort = enrollment.cohort
            row = by_cohort.setdefault(
                cohort.code,
                {
                    "code": cohort.code,
                    "name_ar": getattr(cohort, "name_ar", "") or cohort.code,
                    "program_type": cohort.program.program_type,
                    "program_type_display": cohort.program.get_program_type_display(),
                    "collected": ZERO,
                    "participants": set(),
                },
            )
            row["collected"] += allocation.amount
            row["participants"].add(enrollment.id)

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
        "receipt_count": len(receipts),
        "by_cohort": _cohort_rows_with_due(by_cohort),
    }


def _cohort_rows_with_due(by_cohort: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sprint 8L-2 · B-2 — «نسبة التحصيل» لا «حصّة الدفعة من الإيراد».

    الشاشة كانت تعرض `المقبوض من هذه الدفعة ÷ إيراد المدى` — رقمٌ إداري يقول
    أيّ دفعة أكبر. وسؤال مدير المركز اليومي غيره: `المحصَّل ÷ المستحق` — أي
    **أيّ دفعة متأخّرة عن تحصيل أقساطها**، وهي التي يُتابَع أهلها. الديمو كان
    يعرض الثاني، والفرق ليس تجميلياً: الأولى تكافئ الدفعة الكبيرة، والثانية
    تكشف الدفعة المتعثّرة ولو كانت صغيرة.

    المستحق لا يُحتسب هنا: ``get_account_states`` هي صاحبة معادلة الرصيد
    (DATA_MODEL §8.2)، وتُنادى مرّة واحدة لكل تسجيلات الدفعات الظاهرة — لا
    استعلام لكل دفعة ولا لكل مشارك.
    """
    from apps.billing.services.account_service import get_account_states
    from apps.operations.models import Enrollment

    codes = list(by_cohort)
    if not codes:
        return []

    enrollments = list(Enrollment.objects.filter(cohort__code__in=codes).select_related("cohort"))
    states = get_account_states(enrollments)

    due: dict[str, Decimal] = {}
    enrolled: dict[str, int] = {}
    for enrollment in enrollments:
        code = enrollment.cohort.code
        due[code] = due.get(code, ZERO) + states[enrollment.pk].total_due
        enrolled[code] = enrolled.get(code, 0) + 1

    rows = []
    for row in by_cohort.values():
        code = row["code"]
        rows.append(
            {
                **row,
                "participants": len(row["participants"]),
                # المسجّلون في الدفعة كلهم، لا الدافعون منهم وحدهم: النسبة
                # بلا مقامٍ كامل تكذب لصالح الدفعة التي لم يدفع فيها أحد.
                "enrolled": enrolled.get(code, 0),
                "total_due": due.get(code, ZERO),
                "outstanding": due.get(code, ZERO) - row["collected"],
            }
        )
    return sorted(rows, key=lambda r: -r["collected"])


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

    # Sprint 8L · A-2 — «صافي الدخل بعد حصص الشركاء» بلا بسطٍ لكل شريك نصفُ
    # جواب: المدير يريد أن يعرف أيّ شريك كلّف أكثر، وكم بقي للجامعة من كل
    # دينار حُصّل من برامجه. الأرقام الثلاثة على المطالبة نفسها منذ Sprint 6
    # (`gross_collected` و`partner_share` و`net_payable`) ولم تكن تُعرض.
    partner_total = ZERO
    by_partner: dict[str, dict[str, Any]] = {}
    for claim in claims:
        partner_total += claim.net_payable
        row = by_partner.setdefault(
            claim.partner.name_ar,
            {
                "name_ar": claim.partner.name_ar,
                "code": claim.partner.code,
                "claims": 0,
                "gross_collected": ZERO,
                "partner_share": ZERO,
                "net_payable": ZERO,
            },
        )
        row["claims"] += 1
        row["gross_collected"] += claim.gross_collected
        row["partner_share"] += claim.partner_share
        row["net_payable"] += claim.net_payable

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
    # Sprint 8D-5 — a reversed payout is not an active refund. ``refunds_paid``
    # already excludes them, so the two figures are independent rather than
    # one being netted out of the other: money handed back in this window, and
    # money taken back in this window, which are often different months.
    refunds_reversed = opening_balance_service.refunds_reversed_between(
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
        "historical_refunds_reversed": refunds_reversed,
        "historical_refunds_net": refunds_paid - refunds_reversed,
        "partner_total": partner_total,
        # حصة الجامعة = المحصَّل من برامج الشريك ناقص ما استحقّه فعلاً. تُشتقّ
        # هنا من رقمين تملكهما مطالبةٌ معتمدة، ولا تُحتسب حصّةٌ من جديد.
        "by_partner": [
            {
                **row,
                "university_share": row["gross_collected"] - row["net_payable"],
            }
            for row in sorted(by_partner.values(), key=lambda r: -r["net_payable"])
        ],
        "expenses_total": expenses_total,
        # Sprint 8L-2 — جدولٌ فارغ يقول «لا مطالبات» صادقٌ وناقص: السبب قد
        # يكون أن لا دفعة مرتبطة باتفاقية أصلاً، وهي حلقةٌ أعلى من التقرير.
        # القارئ الذي لا يعرف ذلك يبحث عن الخلل في التقرير.
        "any_cohort_has_agreement": _any_cohort_has_agreement(),
        # سطرٌ وسيط في السلّم: القارئ يريد أن يرى أين وقف الرقم بعد الشركاء
        # وقبل المصروفات، وحسابه في القالب كان سيضع طرحاً في قالب.
        "after_partners": collected - partner_total,
        "net_income": collected - partner_total - expenses_total,
        "charge_type_note": ChargeType.DEPOSIT,
    }


def _any_cohort_has_agreement() -> bool:
    """استعلام وجودٍ واحد: هل ثمّة دفعة على اتفاقية أصلاً؟"""
    from apps.operations.models import Cohort

    return Cohort.objects.filter(agreement__isnull=False).exists()


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
        "partners": _partner_dues_rows(claims, settlements, obligations),
        "any_cohort_has_agreement": _any_cohort_has_agreement(),
        "claims": claims,
        "settlements": settlements,
        "obligations": obligations,
        "claims_total": sum((c["net_payable"] for c in claims), ZERO),
        "settled_total": sum((s["total_paid"] for s in settlements), ZERO),
        "obligations_outstanding": sum((o["outstanding"] for o in obligations), ZERO),
    }


def _partner_dues_rows(
    claims: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Sprint 8L · A-3 — صفّ واحد لكل شريك يجمع الثلاثة.

    §9.3 يسأل عن «كشف مستحقات كل شريك ومخالصاته»، والجداول الثلاثة المسطّحة
    تجيب عن المطالبات والمخالصات والالتزامات كلٌّ على حدة وتترك القارئ يجمع
    بنفسه — وهو حسابٌ مالي يقع في رأس القارئ بدل أن يقع في خدمة.

    والتجميع هنا لا في القالب: جمعٌ في قالب حسابُ مالٍ في قالب.
    """
    rows: dict[str, dict[str, Any]] = {}

    def _row(name: str) -> dict[str, Any]:
        return rows.setdefault(
            name,
            {
                "name_ar": name,
                "claim_count": 0,
                "partner_share": ZERO,
                "deductions": ZERO,
                "net_payable": ZERO,
                "settled": ZERO,
                "settlement_balance": ZERO,
                "obligations_outstanding": ZERO,
                "last_settlement": None,
            },
        )

    for claim in claims:
        row = _row(claim["partner_name"])
        row["claim_count"] += 1
        row["partner_share"] += claim["partner_share"]
        row["deductions"] += claim["total_deductions"]
        row["net_payable"] += claim["net_payable"]

    for settlement in settlements:
        row = _row(settlement["partner_name"])
        row["settled"] += settlement["total_paid"]
        row["settlement_balance"] += settlement["balance"]
        # آخر مخالصة بالفترة لا بترتيب الإدراج: المخالصات تُنشأ بأثر رجعي
        # أحياناً، وآخر ما أُدخل ليس آخر ما جرى.
        current = row["last_settlement"]
        if current is None or settlement["period_to"] > current["period_to"]:
            row["last_settlement"] = settlement

    for obligation in obligations:
        _row(obligation["partner_name"])["obligations_outstanding"] += obligation["outstanding"]

    return sorted(rows.values(), key=lambda r: -r["net_payable"])


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

    from apps.operations.models import Enrollment
    from apps.operations.services import enrollment_service
    from apps.settlements.services import entitlement_service

    # Sprint 8L · A-6 — كان هنا ``get_enrollment`` داخل الحلقة، ومعه تقييمٌ
    # مفرد للتأخّر: ثلاثة عشر استعلاماً لكل متأخّر (٣٠ لصفّ واحد و٩٦ لستّة،
    # مقيسة في 8L-0). تقريرٌ يبطؤ كلّما زاد المتأخرون هو أبطأ ما يكون في اليوم
    # الذي يُحتاج فيه.
    #
    # الآن: استعلامٌ واحد يجلب التسجيلات المرشَّحة، وتقييمٌ مجمَّع يقرأ القاعدة
    # نفسها من ``entitlement_service`` — لا نسخة منها هنا.
    candidates = [
        row
        for row in enrollment_service.list_enrollments(
            actor=actor, cohort_code=cohort_code, request=request
        )
        if row["participant_owes"]
    ]

    enrollments = list(
        Enrollment.objects.filter(code__in=[row["code"] for row in candidates]).select_related(
            "participant", "cohort__agreement"
        )
    )
    overdue = entitlement_service.payment_overdue_map(enrollments, as_of=as_of)
    by_code = {enrollment.code: enrollment for enrollment in enrollments}

    rows: list[dict[str, Any]] = []
    for row in candidates:
        enrollment = by_code.get(row["code"])
        if enrollment is None or not overdue.get(enrollment.pk):
            continue
        rows.append(
            {
                **row,
                # الهاتف: هذا التقرير هو الذي تُلاحَق به الذمم، وملاحقةٌ بلا
                # رقمٍ تُرسل الموظّف إلى شاشة أخرى لكل صفّ.
                "participant_phone": enrollment.participant.phone,
                "voids_partner_entitlement": enrollment.cohort.agreement_id is not None,
            }
        )

    return {
        "number": 4,
        "title": REPORT_TITLES[4],
        "as_of": as_of,
        "rows": rows,
        "total_outstanding": sum((r["balance"] for r in rows), ZERO),
        "total_due_sum": sum((r["total_due"] for r in rows), ZERO),
        "total_paid_sum": sum((r["total_paid"] for r in rows), ZERO),
        "count": len(rows),
        # كم منهم يُسقط استحقاق شريك فعلاً — الرقم الذي يهمّ المدير قبل أن
        # يعتمد مطالبة، ولا يُعرف بعدّ الصفوف لأن ليس لكل تسجيل شريك.
        "voiding_count": sum(1 for r in rows if r["voids_partner_entitlement"]),
        # ومَن وُضع متأخراً بقرار موظّف لا بحساب المهلة: الحالتان تظهران في
        # الجدول نفسه، والفرق بينهما أن الثانية يدافع عنها إنسان.
        "manual_count": sum(1 for r in rows if r["status"] == "PAYMENT_OVERDUE"),
    }


# ---------------------------------------------------------------------------
# 5 — كشف حساب الطالب (§9.5)
# ---------------------------------------------------------------------------
def participant_dues_rows(*, actor: Any, query: str = "", request: Any = None) -> dict[str, Any]:
    """
    Sprint 8L · A-4 — صفٌّ لكل **مشارك**، لا لكل تسجيل.

    §9.5 نصّه «كشف حساب مالي شامل **للطالب**»، وطالبٌ له تسجيلان له حسابٌ
    واحد. وكانت الشاشة تطلب رمز تسجيلٍ يُكتب بالذاكرة: مَن لا يحفظ الرمز لا
    يصل إلى الكشف أصلاً.

    كل رقمٍ هنا مجموعُ ما تحتسبه الخدمة التي تملكه: الرصيد من
    ``get_account_states`` والخصم منه أيضاً، والرسوم الإضافية والاستردادات
    والنقل من جداولها — استعلامٌ مجمَّع لكلٍّ منها لا استعلامٌ لكل مشارك.

    البحث يمرّ بـ ``list_participants``، فيطوي همزة الألف («أحمد» و«احمد»
    سلسلتان مختلفتان عند ترتيب MySQL) ويحترم ما يراه الدور من حقول (BR-101).
    """
    policy.require_report(actor, 5, request=request)

    from django.db.models import Count, Sum

    from apps.billing.models import ExtraFee, Refund, RefundStatus
    from apps.billing.services.account_service import get_account_states
    from apps.operations.models import Enrollment, Transfer
    from apps.people.services import participant_service

    participants = participant_service.list_participants(actor=actor, query=query, request=request)
    numbers = [row["participant_number"] for row in participants]
    if not numbers:
        return {"rows": [], "query": query}

    enrollments = list(
        Enrollment.objects.filter(participant__participant_number__in=numbers).select_related(
            "participant"
        )
    )
    states = get_account_states(enrollments)

    def _by_participant(queryset: Any, field: str) -> dict[str, Decimal]:
        return {
            row["enrollment__participant__participant_number"]: row["total"] or ZERO
            for row in queryset.values("enrollment__participant__participant_number").annotate(
                total=Sum(field)
            )
        }

    extra_fees = _by_participant(ExtraFee.objects.filter(enrollment__in=enrollments), "amount")
    refunds = _by_participant(
        # المنفَّذ وحده: استردادٌ مطلوب أو معتمد لم يخرج من الصندوق بعد،
        # وإدراجه يُنقص رصيداً لم يُنقص فعلاً.
        Refund.objects.filter(enrollment__in=enrollments, status=RefundStatus.EXECUTED),
        "amount",
    )
    transfers = {
        row["from_enrollment__participant__participant_number"]: row["moves"]
        for row in Transfer.objects.filter(from_enrollment__in=enrollments)
        .values("from_enrollment__participant__participant_number")
        .annotate(moves=Count("id"))
    }

    rows: list[dict[str, Any]] = []
    for participant in participants:
        number = participant["participant_number"]
        mine = [e for e in enrollments if e.participant.participant_number == number]
        totals = [states[e.pk] for e in mine]
        rows.append(
            {
                **participant,
                "enrollments": len(mine),
                "total_due": sum((t.total_due for t in totals), ZERO),
                "total_paid": sum((t.total_paid for t in totals), ZERO),
                "total_discount": sum((t.total_discount for t in totals), ZERO),
                "balance": sum((t.balance for t in totals), ZERO),
                "extra_fees": extra_fees.get(number, ZERO),
                "refunds": refunds.get(number, ZERO),
                "transfers": transfers.get(number, 0),
                "codes": [e.code for e in mine],
            }
        )
    return {"rows": rows, "query": query}


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
def daily_closing_report(
    *,
    actor: Any,
    on_date: date,
    date_from: date | None = None,
    request: Any = None,
) -> dict[str, Any]:
    """
    What each till took, and whether the count matched.

    §5.2's «إقفال يومي: مطابقة المقبوض بالوصولات». The system total comes from
    ``system_total_for`` — the same function the closing screen uses — so this
    report and report 1 must agree for the same day.
    """
    policy.require_report(actor, 6, request=request)

    from apps.cashbox.services import closing_service, payment_service

    # Sprint 8L · A-5 — «اليومي» في §9.6 صفةُ الصفّ لا صفةُ التقرير: مطابقةُ
    # أسبوعٍ سؤالٌ حقيقي للمدقّق، وكان يكلّفه سبع صفحات. واليوم الواحد يبقى
    # الافتراضي، فمن كان يفتح الشاشة لليوم يجدها كما تركها.
    if date_from is None or date_from > on_date:
        date_from = on_date
    single_day = date_from == on_date

    closings = (
        closing_service.list_closings(actor=actor, on_date=on_date, request=request)
        if single_day
        else closing_service.list_closings(
            actor=actor, since=date_from, until=on_date, request=request
        )
    )
    receipts = (
        payment_service.list_receipts(actor=actor, on_date=on_date, request=request)
        if single_day
        else payment_service.list_receipts(
            actor=actor, since=date_from, until=on_date, request=request
        )
    )
    issued = [r for r in receipts if r["status"] == "ISSUED"]

    # مَن قبض وكم، ومطابقته: ملخّصان يقرآن الصفوف نفسها المعروضة تحتهما، فلا
    # استعلام ولا حساب جديد — ترتيبٌ لما هو معروض أصلاً.
    by_cashier: dict[str, dict[str, Any]] = {}
    for closing in closings:
        row = by_cashier.setdefault(
            closing["cashier"],
            {
                "cashier": closing["cashier"],
                "closings": 0,
                "system_total": ZERO,
                "counted_total": ZERO,
                "variance": ZERO,
            },
        )
        row["closings"] += 1
        row["system_total"] += closing["system_total"]
        row["counted_total"] += closing["counted_total"]
        row["variance"] += closing["variance"]

    by_method: dict[str, dict[str, Any]] = {}
    for receipt in issued:
        method = receipt.get("payment_method") or "—"
        row = by_method.setdefault(method, {"method": method, "count": 0, "amount": ZERO})
        row["count"] += 1
        row["amount"] += receipt["amount"]

    return {
        "number": 6,
        "title": REPORT_TITLES[6],
        "on_date": on_date,
        "date_from": date_from,
        "single_day": single_day,
        "closings": closings,
        "receipts": issued,
        "receipts_total": sum((r["amount"] for r in issued), ZERO),
        "system_total": sum((c["system_total"] for c in closings), ZERO),
        "counted_total": sum((c["counted_total"] for c in closings), ZERO),
        "variance_total": sum((c["variance"] for c in closings), ZERO),
        "unreconciled": [c for c in closings if c["status"] != "RECONCILED"],
        "unvouched_total": sum((c["unvouched_count"] for c in closings), 0),
        "by_cashier": sorted(by_cashier.values(), key=lambda r: -r["system_total"]),
        "by_method": sorted(by_method.values(), key=lambda r: -r["amount"]),
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
    grand_total = sum((r["amount"] for r in rows), ZERO)
    return {
        "number": 7,
        "title": REPORT_TITLES[7],
        "date_from": date_from,
        "date_to": date_to,
        "category": category,
        "rows": rows,
        # المجموع الكلّي للمعروض (معتمَداً كان أو منتظراً) — وهو مقام النِّسب
        # على البطاقات. غيره يجعل النسب لا تُجمع على مئة.
        "grand_total": grand_total,
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
    # Sprint 8L · A-9 — السبعة تُصدَّر، لا أربعة منها. وكلٌّ يصدّر صفوفه هو:
    # التقرير الأول يصدّر تفصيل الدفعات لا بطاقاته، والثاني صفوف الشركاء لا
    # سلّم الاحتساب — فملفٌّ يحمل ملخّصاً يبدو بياناتٍ وليس بياناتٍ.
    1: (
        "by_cohort",
        (
            ("code", "الدفعة"),
            ("name_ar", "اسم الدفعة"),
            ("program_type_display", "النوع"),
            ("participants", "المشاركون الدافعون"),
            ("collected", "المقبوض"),
        ),
    ),
    2: (
        "by_partner",
        (
            ("code", "رمز الشريك"),
            ("name_ar", "الشريك"),
            ("claims", "المطالبات"),
            ("gross_collected", "المحصَّل من برامجه"),
            ("net_payable", "حصته المستحقة"),
            ("university_share", "حصة الجامعة"),
        ),
    ),
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
            ("participant_phone", "الهاتف"),
            ("cohort_code", "الدفعة"),
            ("enrolled_on", "تاريخ التسجيل"),
            ("total_due", "المستحق"),
            ("total_paid", "المدفوع"),
            ("balance", "الرصيد"),
            ("status_display", "الحالة"),
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
    5: (
        "rows",
        (
            ("participant_number", "الرقم"),
            ("name_ar", "المشارك"),
            ("enrollments", "التسجيلات"),
            ("total_due", "المستحق"),
            ("total_paid", "المدفوع"),
            ("total_discount", "الخصومات"),
            ("refunds", "الاستردادات"),
            ("extra_fees", "رسوم إضافية"),
            ("transfers", "النقل"),
            ("balance", "الرصيد"),
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


class ExportTooLargeError(Exception):
    """التصدير يتجاوز الحدّ — يُرفض ولا يُبتر."""


def export_row_limit(*, as_of: date) -> int:
    """
    أقصى عدد صفوف يُصدَّر في ملف واحد.

    إعدادٌ لا ثابتٌ في الشيفرة، كالترميز: منشأةٌ واحدة عنيدة قد تحتاج رقماً
    آخر، وذلك لا يستحق إصداراً برمجياً.
    """
    from apps.core.services.settings_service import get_setting

    return int(get_setting("report_export_max_rows", as_of=as_of, default=5000))


def export_encoding(*, as_of: date) -> str:
    """
    ``utf-8-sig`` — the BOM is what makes Excel read Arabic correctly.

    A setting rather than a literal because it is the kind of thing a site
    changes once, for one stubborn install, and should not need a release for.
    """
    from apps.core.services.settings_service import get_setting

    return str(get_setting("report_export_encoding", as_of=as_of, default="utf-8-sig"))


def csv_rows(
    report: dict[str, Any], *, limit: int | None = None
) -> tuple[list[str], list[list[str]]]:
    """
    Header and body for a report's CSV, or empty when it has no row list.

    Standard library only — no Excel dependency. §9 asks for reports, not for
    a spreadsheet format, and a CSV opens in Excel by double-clicking.
    """
    spec = EXPORTABLE.get(int(report["number"]))
    if spec is None:
        return [], []
    key, columns = spec

    # Sprint 8L-2 · B-3 — **يُرفض ولا يُبتر.** ملفٌّ مبتور يفتحه محاسب فيرى
    # جدولاً كاملاً في ظاهره وناقصاً في حقيقته هو أسوأ ما يمكن أن يخرج من
    # تقرير مالي: لا شيء على الورقة يقول إن هناك بقيّة. والرفض يقول ما العمل.
    rows = report.get(key) or []
    if limit is not None and len(rows) > limit:
        raise ExportTooLargeError(
            f"التصدير يشمل {len(rows):,} صفّاً ويتجاوز الحدّ ({limit:,}). "
            "ضيّق المدى أو أضف مرشّحاً ثم أعد التصدير."
        )
    header = [label for _field, label in columns]
    body = [[str(row.get(field, "")) for field, _label in columns] for row in rows]
    return header, body


__all__ = [
    "EXPORTABLE",
    "REPORTS",
    "REPORT_ICONS",
    "REPORT_QUESTIONS",
    "REPORT_TITLES",
    "REPORT_TONES",
    "ExportTooLargeError",
    "available_reports",
    "csv_rows",
    "daily_closing_report",
    "expenses_report",
    "export_encoding",
    "export_row_limit",
    "net_income_report",
    "overdue_report",
    "participant_statement_report",
    "partner_dues_report",
    "revenue_report",
]
