"""
The reports screen (§9).

**One screen, seven documents.** Granting the screen is not granting the
seven: ``policy.require_report`` decides each one and audits the refusal
(BR-080, BR-099). The finance manager reaches this screen and exactly one
report; the cashier reaches neither.

Views render and delegate. Every figure comes from the service that owns it —
nothing here adds up money.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.reporting.services import report_service


def _parse(raw: str, fallback: date) -> date:
    try:
        return date.fromisoformat(raw) if raw else fallback
    except ValueError:
        return fallback


def _period(request: HttpRequest) -> tuple[date, date]:
    today = date.today()
    return (
        _parse(request.GET.get("from", "").strip(), today - timedelta(days=30)),
        _parse(request.GET.get("to", "").strip(), today),
    )


def reports_index(request: HttpRequest) -> HttpResponse:
    """The menu of seven, each marked with whether this role may open it."""
    policy.require(request.user, Screen.REPORTS, Action.VIEW, request=request)
    return render(
        request,
        "reporting/index.html",
        {
            "title": _("التقارير"),
            "active_screen": Screen.REPORTS,
            "reports": report_service.available_reports(actor=request.user),
        },
    )


def _build(request: HttpRequest, number: int) -> dict[str, Any]:
    """
    Run one report.

    The gate is repeated here even though every report service calls
    ``require_report`` itself, because report 5 answers about ONE enrollment
    and returns its empty shell when no code is given — a branch that reaches
    no service, and so would otherwise render a report page to a role holding
    no report at all. Gating the route as well as the service costs one
    dictionary lookup and closes that door; the refusal is raised once,
    before the service is ever reached, so it is also audited once.
    """
    policy.require_report(request.user, number, request=request)
    date_from, date_to = _period(request)
    if number == 1:
        return report_service.revenue_report(
            actor=request.user, date_from=date_from, date_to=date_to, request=request
        )
    if number == 2:
        return report_service.net_income_report(
            actor=request.user, date_from=date_from, date_to=date_to, request=request
        )
    if number == 3:
        return report_service.partner_dues_report(
            actor=request.user,
            partner_code=request.GET.get("partner", "").strip(),
            request=request,
        )
    if number == 4:
        return report_service.overdue_report(
            actor=request.user,
            as_of=date_to,
            cohort_code=request.GET.get("cohort", "").strip(),
            request=request,
        )
    if number == 5:
        code = request.GET.get("enrollment", "").strip()
        if not code:
            return {"number": 5, "title": report_service.REPORT_TITLES[5], "statement": None}
        return report_service.participant_statement_report(
            actor=request.user, enrollment_code=code, request=request
        )
    if number == 6:
        return report_service.daily_closing_report(
            actor=request.user, on_date=date_to, request=request
        )
    return report_service.expenses_report(
        actor=request.user,
        date_from=date_from,
        date_to=date_to,
        category=request.GET.get("category", "").strip(),
        request=request,
    )


def report_view(request: HttpRequest, number: int) -> HttpResponse:
    if number not in report_service.REPORT_TITLES:
        raise Http404
    try:
        report = _build(request, number)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    date_from, date_to = _period(request)
    return render(
        request,
        f"reporting/report_{number}.html",
        {
            "title": report["title"],
            "active_screen": Screen.REPORTS,
            "report": report,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "exportable": number in report_service.EXPORTABLE,
        },
    )


def report_export(request: HttpRequest, number: int) -> HttpResponse:
    """
    CSV, from the standard library — no Excel dependency.

    The export runs the SAME ``require_report`` gate as the screen, inside the
    service. A download route that read more loosely than the page it exports
    would be a way around the matrix wearing a spreadsheet icon.
    """
    if number not in report_service.REPORT_TITLES:
        raise Http404
    try:
        report = _build(request, number)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    header, rows = report_service.csv_rows(report)
    if not header:
        raise Http404

    encoding = report_service.export_encoding(as_of=date.today())
    response = HttpResponse(content_type=f"text/csv; charset={encoding}")
    response["Content-Disposition"] = f'attachment; filename="report-{number}.csv"'
    response.charset = encoding

    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response
