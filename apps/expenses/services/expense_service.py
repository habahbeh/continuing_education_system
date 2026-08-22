"""
Recording and approving what the centre spent (§9.7, §5.5).

**This module touches no partner and no participant.** It writes to one table
that nothing else reads except report 7 and report 2's subtraction. An expense
is not a charge line, does not enter a distribution base, and cannot move a
claim — and the first test written against it says exactly that, because the
failure it guards is silent: "net centre income" quietly subtracting a figure
that was never the centre's to subtract.

Two people, taken from the permission matrix rather than invented: the finance
officer holds ``C E`` here and the centre manager holds ``A``. Money leaving
the centre gets the same separation that waiving revenue already had (D-18).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.display import person_name, text_of
from apps.core.services import period_service
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.expenses.models import Expense, ExpenseStatus
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "expenses.Expense"
ZERO = Decimal("0.000")

#: §9.7 — «مستهلكات · تسويق · شهادات · غيرها», as data.
CATEGORIES_KEY = "expense_categories"


#: Sprint 8D-5 — the rule moved to ``core.period_service`` so the refund
#: payout could share it instead of copying it. Re-exported under the name
#: this module has always used, because callers and tests name it that way and
#: renaming a public exception to celebrate a refactor helps nobody.
ClosedPeriodError = period_service.ClosedPeriodError


class ExpenseStateError(ValidationError):
    """A decision attempted on an expense that is not awaiting one."""


def category_choices(*, as_of: date) -> list[tuple[str, str]]:
    """(code, label) pairs from the setting — empty when unconfigured."""
    import json

    raw = get_setting(CATEGORIES_KEY, as_of=as_of, default=None)
    if not raw:
        return []
    try:
        pairs = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [(str(code), str(label)) for code, label in pairs]


def _period_for(incurred_on: date, *, actor: Any = None, request: Any = None) -> Any:
    """
    The period covering the date — refuses a closed one (D-23).

    Audits the refusal since Sprint 8D-6, like every other money movement:
    "who tried to post into a month we had signed off?" is a question the
    trail has to be able to answer.
    """
    return period_service.require_open(
        incurred_on,
        actor=actor,
        what_ar="مصروف",
        entity_type="expenses.Expense",
        request=request,
    )


def record(
    *,
    actor: Any,
    code: str,
    category: str,
    amount: Decimal,
    incurred_on: date,
    description_ar: str,
    cohort: Any = None,
    reference: str = "",
    request: Any = None,
) -> Expense:
    """Record an expense as RECORDED — approval is somebody else's act."""
    policy.require(actor, Screen.EXPENSES, Action.CREATE, request=request)

    if amount <= ZERO:
        raise ValidationError("مبلغ المصروف يجب أن يكون موجباً.")
    if not description_ar.strip():
        raise ValidationError("بيان المصروف إلزامي — قيد بلا بيان لا يُدافَع عنه في التقرير.")

    known = {value for value, _label in category_choices(as_of=incurred_on)}
    if known and category not in known:
        raise ValidationError(
            f"تصنيف غير معرَّف: {category}. التصنيفات تُدار من الإعدادات ({CATEGORIES_KEY})."
        )
    if Expense.objects.filter(code=code).exists():
        raise ValidationError(f"رمز القيد {code} مستعمل سلفاً.")

    period = _period_for(incurred_on, actor=actor, request=request)

    return _record(
        actor=actor,
        code=code,
        category=category,
        amount=amount,
        incurred_on=incurred_on,
        description_ar=description_ar.strip(),
        cohort=cohort,
        reference=reference.strip(),
        period=period,
        request=request,
    )


@transaction.atomic
def _record(
    *,
    actor: Any,
    code: str,
    category: str,
    amount: Decimal,
    incurred_on: date,
    description_ar: str,
    cohort: Any,
    reference: str,
    period: Any,
    request: Any,
) -> Expense:
    expense = Expense.objects.create(
        code=code,
        category=category,
        amount=amount,
        incurred_on=incurred_on,
        description_ar=description_ar,
        cohort=cohort,
        reference=reference,
        financial_period=period,
        status=ExpenseStatus.RECORDED,
        created_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(expense.pk),
        reference=code,
        summary_ar=f"قيد مصروف {amount} — {description_ar}",
        actor=actor,
        changes={
            "category": category,
            "amount": str(amount),
            "incurred_on": incurred_on.isoformat(),
            "cohort": text_of(cohort, "code"),
            "reference": reference,
        },
        request=request,
    )
    return expense


def approve(*, actor: Any, expense: Expense, note_ar: str = "", request: Any = None) -> Expense:
    """
    Approve — and only then does the expense reduce net income (§9.2).

    D-18 refused here for a readable message and by the database for
    everything else.
    """
    policy.require(actor, Screen.EXPENSES, Action.APPROVE, request=request)

    if expense.status != ExpenseStatus.RECORDED:
        raise ExpenseStateError(f"لا يُعتمد مصروف حالته {expense.status}.")
    if expense.created_by_id == getattr(actor, "pk", None):
        raise ValidationError("لا يعتمد المصروفَ من قيّده (D-18).")

    return _decide(
        actor=actor,
        expense=expense,
        status=ExpenseStatus.APPROVED,
        note_ar=note_ar.strip(),
        request=request,
    )


def reject(*, actor: Any, expense: Expense, note_ar: str, request: Any = None) -> Expense:
    """A refusal is a decision and gets recorded like one."""
    policy.require(actor, Screen.EXPENSES, Action.APPROVE, request=request)

    if expense.status != ExpenseStatus.RECORDED:
        raise ExpenseStateError(f"لا يُرفض مصروف حالته {expense.status}.")
    if not note_ar.strip():
        raise ValidationError("سبب الرفض إلزامي.")

    return _decide(
        actor=actor,
        expense=expense,
        status=ExpenseStatus.REJECTED,
        note_ar=note_ar.strip(),
        request=request,
    )


@transaction.atomic
def _decide(*, actor: Any, expense: Expense, status: str, note_ar: str, request: Any) -> Expense:
    expense.status = status
    expense.approved_by = actor
    expense.approved_at = timezone.now()
    expense.decision_note_ar = note_ar
    expense.save(update_fields=["status", "approved_by", "approved_at", "decision_note_ar"])

    approved = status == ExpenseStatus.APPROVED
    verb = "اعتماد" if approved else "رفض"
    write_audit(
        action="APPROVE" if approved else "REJECT",
        entity_type=ENTITY,
        entity_id=str(expense.pk),
        reference=expense.code,
        summary_ar=f"{verb} مصروف {expense.amount}",
        actor=actor,
        changes={
            "status": status,
            "amount": str(expense.amount),
            "recorded_by": expense.created_by_id,
            "note": note_ar,
        },
        request=request,
    )
    return expense


# ---------------------------------------------------------------------------
# Reads (A-05 — views may not touch models)
# ---------------------------------------------------------------------------
def list_expenses(
    *,
    actor: Any,
    category: str = "",
    status: str = "",
    cohort_code: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    request: Any = None,
) -> list[dict[str, Any]]:
    """Expenses as rows for the screen and for report 7."""
    policy.require(actor, Screen.EXPENSES, Action.VIEW, request=request)

    queryset = Expense.objects.select_related("cohort", "created_by", "approved_by")
    if category:
        queryset = queryset.filter(category=category)
    if status:
        queryset = queryset.filter(status=status)
    if cohort_code:
        queryset = queryset.filter(cohort__code=cohort_code)
    if date_from is not None:
        queryset = queryset.filter(incurred_on__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(incurred_on__lte=date_to)

    labels = dict(category_choices(as_of=date_to or date.today()))
    return [
        {
            "id": e.pk,
            "code": e.code,
            "category": e.category,
            "category_label": labels.get(e.category, e.category),
            "amount": e.amount,
            "incurred_on": e.incurred_on,
            "description_ar": e.description_ar,
            "cohort_code": text_of(e.cohort, "code"),
            "reference": e.reference,
            "status": e.status,
            "status_display": e.get_status_display(),
            "counts_toward_net_income": e.counts_toward_net_income,
            "created_by": person_name(e.created_by),
            "created_by_id": e.created_by_id,
            "approved_by": person_name(e.approved_by),
            "decision_note_ar": e.decision_note_ar,
        }
        for e in queryset.order_by("-incurred_on", "-id")
    ]


def totals_by_category(
    *,
    actor: Any,
    date_from: date | None = None,
    date_to: date | None = None,
    approved_only: bool = True,
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Category totals for report 7's summary.

    ``approved_only`` defaults True: report 2 subtracts these from net income,
    and an entry nobody has approved is a claim about money rather than an
    agreed fact about it.
    """
    rows = list_expenses(
        actor=actor,
        status=ExpenseStatus.APPROVED if approved_only else "",
        date_from=date_from,
        date_to=date_to,
        request=request,
    )
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = totals.setdefault(
            row["category"],
            {
                "category": row["category"],
                "label": row["category_label"],
                "total": ZERO,
                "count": 0,
            },
        )
        bucket["total"] += row["amount"]
        bucket["count"] += 1
    return sorted(totals.values(), key=lambda b: b["label"])


def approved_total(
    *,
    actor: Any,
    date_from: date | None = None,
    date_to: date | None = None,
    request: Any = None,
) -> Decimal:
    """
    What report 2 subtracts — approved expenses only, in the period.

    A single entry point so the net-income report cannot decide for itself what
    counts. There is one answer to "what did the centre spend", the same way
    there is one answer to "what does this participant owe".
    """
    return sum(
        (
            bucket["total"]
            for bucket in totals_by_category(
                actor=actor, date_from=date_from, date_to=date_to, request=request
            )
        ),
        ZERO,
    )


def expense_instance(*, actor: Any, code: str, request: Any = None) -> Expense:
    """The Expense object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.EXPENSES, Action.VIEW, request=request)
    return Expense.objects.get(code=code)


__all__ = [
    "CATEGORIES_KEY",
    "ClosedPeriodError",
    "ExpenseStateError",
    "approve",
    "approved_total",
    "category_choices",
    "expense_instance",
    "list_expenses",
    "record",
    "reject",
    "totals_by_category",
]
