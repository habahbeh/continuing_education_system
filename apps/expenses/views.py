"""
The expenses screen (§9.7).

Views render and delegate. Permission refusals and business refusals are kept
apart the way Sprint 8B settled it: ``policy.require`` runs BEFORE the service
call so a role violation is a 403, while a rule arrives as a message carrying
its own reference.

**Two people, from the matrix.** The finance officer holds ``C E`` here and
the centre manager holds ``A``, so the record form and the decide buttons are
never offered to the same person. That split predates this app; the screen
reads it rather than inventing it.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.expenses.forms import ExpenseDecisionForm, ExpenseForm
from apps.expenses.services import expense_service
from apps.operations.services import cohort_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: Which permission each POST action needs, checked before the service.
EXPENSE_ACTIONS = {
    "record": Action.CREATE,
    "approve": Action.APPROVE,
    "reject": Action.APPROVE,
}


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


@require_http_methods(["GET", "POST"])
def expenses_view(request: HttpRequest) -> HttpResponse:
    """
    The register beside the entry form. Tiles are the status filter; the
    category chips narrow the rows; the search is live. ``?cohort=`` from
    the cohorts list preselects the cohort on the form.
    """
    today = timezone.localdate()
    can_create = policy.is_allowed(request.user, Screen.EXPENSES, Action.CREATE)
    cohort_code = request.GET.get("cohort", "").strip()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if status not in expense_service.EXPENSE_FILTERS:
        status = ""
    category = request.GET.get("category", "").strip()
    categories = expense_service.category_choices(as_of=today)
    if category not in {c for c, _l in categories}:
        category = ""

    form = None
    cohort_rows: list[tuple[str, str]] = []
    if can_create:
        cohort_rows = (
            cohort_service.cohort_choices(actor=request.user, request=request)
            if policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW)
            else []
        )
        form = ExpenseForm(
            request.POST if request.POST.get("action") == "record" else None,
            initial={"incurred_on": today, "cohort_code": cohort_code},
            category_choices=categories,
            cohort_choices=cohort_rows,
        )

    if request.method == "POST":
        response = _handle(request, form)
        if response is not None:
            return response

    date_from = _parse_date(request.GET.get("from", "").strip())
    date_to = _parse_date(request.GET.get("to", "").strip())
    common = {
        "actor": request.user,
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
        "query": query,
        "request": request,
    }
    rows = expense_service.list_expenses(status=status, **common)
    summary = expense_service.expenses_summary(
        expense_service.list_expenses(**common) if status else rows
    )

    base_url = reverse("expenses:expenses")
    keep = {
        k: v
        for k, v in (
            ("q", query),
            ("from", request.GET.get("from", "").strip()),
            ("to", request.GET.get("to", "").strip()),
            ("category", category),
        )
        if v
    }

    def _tile(key: str, label: str, value: object, foot: object, tone: str, icon: str) -> dict:
        params = dict(keep)
        if key and status != key:
            params["status"] = key
        qs = urlencode(params)
        return {
            "key": key,
            "label": label,
            "value": value,
            "foot": foot,
            "tone": tone,
            "icon": icon,
            "on": bool(key) and status == key,
            "url": base_url + (f"?{qs}" if qs else ""),
        }

    tiles = [
        _tile("RECORDED", _("بانتظار الاعتماد"), summary["recorded"], summary["recorded_total"], "amber", "checks"),
        _tile("APPROVED", _("المعتمَد"), summary["approved"], summary["approved_total"], "ok", "wallet"),
        _tile("REJECTED", _("المرفوض"), summary["rejected"], None, "danger", "undo"),
        _tile("", _("إجمالي المعتمَد"), summary["approved_total"], _("يُخصم من صافي دخل المركز"), "violet", "chart"),
    ]

    # Category chips: the approved total per category in the shown range.
    by_category = {
        t["category"]: t
        for t in expense_service.totals_by_category(
            actor=request.user, date_from=date_from, date_to=date_to, request=request
        )
    }
    chips = []
    for code, label in categories:
        params = dict(keep)
        params.pop("category", None)
        if category != code:
            params["category"] = code
        if status:
            params["status"] = status
        qs = urlencode(params)
        chips.append(
            {
                "code": code,
                "label": label,
                "total": by_category.get(code, {}).get("total", 0),
                "on": category == code,
                "url": base_url + (f"?{qs}" if qs else ""),
            }
        )

    return render(
        request,
        "expenses/expenses.html",
        {
            "title": _("المصروفات"),
            "active_screen": Screen.EXPENSES,
            "expenses": rows,
            "summary": summary,
            "tiles": tiles,
            "chips": chips,
            "categories": categories,
            "form": form,
            "cohort_rows": cohort_rows,
            "query": query,
            "status": status,
            "category": category,
            "is_filtered": bool(query or status or category or date_from or date_to),
            "today": today,
            "period_now": expense_service.period_state(today),
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.EXPENSES, Action.APPROVE),
            "current_user_id": request.user.pk,
            "date_from": request.GET.get("from", ""),
            "date_to": request.GET.get("to", ""),
            "posted_action": request.POST.get("action", ""),
            "posted_code": request.POST.get("code", ""),
        },
    )


@require_http_methods(["GET"])
def period_check_view(request: HttpRequest) -> HttpResponse:
    """The period state for a typed date, for the form's live hint (D-23)."""
    policy.require(request.user, Screen.EXPENSES, Action.CREATE, request=request)
    on = _parse_date(request.GET.get("on", "").strip())
    state = expense_service.period_state(on) if on else {"open": True, "label": "", "period": ""}
    return render(request, "expenses/_period_check.html", {"state": state, "on": on})


def _handle(request: HttpRequest, form: ExpenseForm | None) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in EXPENSE_ACTIONS:
        policy.require(request.user, Screen.EXPENSES, EXPENSE_ACTIONS[action], request=request)
    try:
        if action == "record":
            if form is None or not form.is_valid():
                return None
            _record(request, form)
        elif action in {"approve", "reject"}:
            _decide(request, action)
        else:
            return None
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("expenses:expenses")


def _record(request: HttpRequest, form: ExpenseForm) -> None:
    data = form.cleaned_data
    cohort = (
        cohort_service.get_cohort_instance(
            actor=request.user, code=data["cohort_code"], request=request
        )
        if data["cohort_code"]
        else None
    )
    expense_service.record(
        actor=request.user,
        category=data["category"],
        amount=data["amount"],
        incurred_on=data["incurred_on"],
        description_ar=data["description_ar"],
        cohort=cohort,
        reference=data["reference"],
        request=request,
    )
    messages.success(request, _("قُيّد المصروف — بانتظار اعتماد مدير المركز"))


def _decide(request: HttpRequest, action: str) -> None:
    decision_form = ExpenseDecisionForm(request.POST)
    decision_form.is_valid()
    note = decision_form.cleaned_data.get("note_ar", "")
    expense = expense_service.expense_instance(
        actor=request.user, code=request.POST.get("code", ""), request=request
    )
    if action == "approve":
        expense_service.approve(actor=request.user, expense=expense, note_ar=note, request=request)
        messages.success(request, _("اعتُمد المصروف"))
    else:
        expense_service.reject(actor=request.user, expense=expense, note_ar=note, request=request)
        messages.success(request, _("رُفض المصروف"))
