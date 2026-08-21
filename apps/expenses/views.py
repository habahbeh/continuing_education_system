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

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
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
    can_create = policy.is_allowed(request.user, Screen.EXPENSES, Action.CREATE)
    form = None
    if can_create:
        form = ExpenseForm(
            request.POST if request.POST.get("action") == "record" else None,
            category_choices=expense_service.category_choices(as_of=date.today()),
            cohort_choices=cohort_service.cohort_choices(actor=request.user, request=request)
            if policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW)
            else [],
        )

    if request.method == "POST":
        response = _handle(request, form)
        if response is not None:
            return response

    date_from = _parse_date(request.GET.get("from", "").strip())
    date_to = _parse_date(request.GET.get("to", "").strip())

    return render(
        request,
        "expenses/expenses.html",
        {
            "title": _("المصروفات"),
            "active_screen": Screen.EXPENSES,
            "expenses": expense_service.list_expenses(
                actor=request.user,
                category=request.GET.get("category", "").strip(),
                status=request.GET.get("status", "").strip(),
                date_from=date_from,
                date_to=date_to,
                request=request,
            ),
            "totals": expense_service.totals_by_category(
                actor=request.user, date_from=date_from, date_to=date_to, request=request
            ),
            "approved_total": expense_service.approved_total(
                actor=request.user, date_from=date_from, date_to=date_to, request=request
            ),
            "categories": expense_service.category_choices(as_of=date.today()),
            "form": form,
            "decision_form": ExpenseDecisionForm(),
            "can_create": can_create,
            "can_approve": policy.is_allowed(request.user, Screen.EXPENSES, Action.APPROVE),
            "current_user_id": request.user.pk,
            "date_from": request.GET.get("from", ""),
            "date_to": request.GET.get("to", ""),
        },
    )


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
        code=data["code"],
        category=data["category"],
        amount=data["amount"],
        incurred_on=data["incurred_on"],
        description_ar=data["description_ar"],
        cohort=cohort,
        reference=data["reference"],
        request=request,
    )
    messages.success(request, _("قُيّد المصروف — بانتظار الاعتماد"))


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
