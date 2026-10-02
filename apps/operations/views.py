"""
Operational screens — cohorts, enrolments, and the landing dashboard.

Views render and delegate. Every permission question goes through
``policy.require``; nothing here decides anything by inspecting a role, and
A-05 keeps models out of this file entirely — every row on every screen comes
from a service function that projected it.

**Refusals are shown, not translated.** A service that refuses says why, with
the rule reference in the message (BR-013, BR-018, BR-020). These views catch
the exception and put that message on the screen unchanged. A friendlier
paraphrase would drop the reference the centre needs in order to act.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.billing.services import account_service
from apps.catalog.services import pricing_service
from apps.billing.services.account_service import ZERO
from apps.core.pagination import page_of
from apps.operations.forms import (
    CertificateDateForm,
    CertificateIssueForm,
    ClearanceCancelForm,
    ClearanceOpenForm,
    CohortCancelForm,
    CohortEditForm,
    CohortForm,
    CreditReturnAtClearanceForm,
    CustodyForm,
    DepositSettlementForm,
    EnrollmentForm,
    HandoverForm,
    MoheAttachmentForm,
    MoheDecisionForm,
    MoheResubmissionForm,
    MoheSendForm,
    MoheSubmissionForm,
    QuickEnrollmentForm,
    TransferExecuteForm,
    TransferRejectForm,
    TransferRequestForm,
)
from apps.operations.services import (
    certificate_service,
    clearance_service,
    cohort_service,
    enrollment_service,
    mohe_service,
    transfer_service,
)
from apps.partners.services import partner_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.people.services import participant_service


def _message_of(exc: Exception) -> str:
    """The service's own words, flattened out of Django's error containers."""
    detail = getattr(exc, "messages", None)
    if detail:
        return " · ".join(str(m) for m in detail)
    return str(exc)


# ---------------------------------------------------------------------------
# Dashboard — the landing page after sign-in
# ---------------------------------------------------------------------------
#: Segments in a dashboard bar. A bar is drawn as N cells with the first K
#: filled, which is why nothing here needs an inline width, a script or a
#: charting library — and why it degrades to an empty strip rather than to a
#: broken graphic when there is nothing to show.
DASHBOARD_BAR_SEGMENTS = 12

#: Rows rendered per pending-work tab. The counter on the tab is the full
#: count; the table under it is a preview and its «افتح الشاشة» button opens
#: the rest. A queue of four hundred enrolments rendering four hundred rows —
#: five of them in hidden tabs — is what made the landing page slow, not the
#: queries behind it.
DASHBOARD_QUEUE_ROWS = 8


def _stack(parts: list[tuple[Any, int, str]], total: int, cells: int = 24) -> list[dict[str, Any]]:
    """
    A one-line stacked bar as tone-classed cells — the dashboard draws with
    classes, never with widths (no inline style survives 8J-5). Integer
    arithmetic throughout (A-01b); the last part absorbs the rounding, and any
    part above zero lights at least one cell.
    """
    if not total:
        return []
    out: list[dict[str, Any]] = []
    used = 0
    for index, (label, value, tone) in enumerate(parts):
        if index == len(parts) - 1:
            n = cells - used
        else:
            n = min(max(value * cells // total, 1 if value else 0), cells - used)
        out.append({"label": label, "value": value, "tone": tone, "cells": range(n)})
        used += n
    return out


#: Resolution of a solid bar: the fill is one of twenty-one classes
#: (``lv-0`` … ``lv-20``), never a computed width — the same reason the cell
#: bars exist, with a smoother fill.
DASHBOARD_LEVELS = 20


def _level(value: int, scale: int) -> int:
    """``value`` out of ``scale`` in twentieths; anything above zero shows."""
    if value <= 0 or scale <= 0:
        return 0
    return max(1, min(DASHBOARD_LEVELS, (value * DASHBOARD_LEVELS + scale - 1) // scale))


def _donut(parts: list[tuple[Any, int, str]], total: int) -> list[dict[str, Any]]:
    """
    Segments of a ring as whole percentages: each part's share and where it
    starts, so the template writes two SVG attributes and no style. The last
    part absorbs the rounding so the ring always closes at 100.
    """
    if not total:
        return []
    out: list[dict[str, Any]] = []
    used = 0
    for index, (label, value, tone) in enumerate(parts):
        pct = 100 - used if index == len(parts) - 1 else value * 100 // total
        out.append({"label": label, "value": value, "tone": tone, "pct": pct, "offset": used})
        used += pct
    return out


def _bar(value: int, scale: int) -> list[bool]:
    """
    ``value`` out of ``scale``, as filled/empty cells.

    Integer arithmetic throughout: A-01b keeps ``float()`` out of application
    code, and a bar has no business being the one place it creeps back in.
    Anything above zero lights at least one cell, so a queue of one is visible
    beside a queue of forty rather than rounding away to nothing.
    """
    if value <= 0 or scale <= 0:
        return [False] * DASHBOARD_BAR_SEGMENTS
    filled = min(DASHBOARD_BAR_SEGMENTS, (value * DASHBOARD_BAR_SEGMENTS + scale - 1) // scale)
    return [i < filled for i in range(DASHBOARD_BAR_SEGMENTS)]


def dashboard_view(request: HttpRequest) -> HttpResponse:
    """
    Counters and nothing more.

    Deliberately NOT a report: §9's seven reports are Sprint 8C, and a
    dashboard that started answering "net income after partner shares" would
    be one of them wearing a different name. Every number here is a count of
    rows the reader may already open, and clicking it goes to those rows.

    **Role-aware without a single role test.** The cashier gets a till screen
    and the registrar gets a registration screen because ``is_allowed`` filters
    the candidates, not because anything here asks who they are — the same
    principle the sidebar is built on. A permission change therefore moves this
    screen too, and a dashboard that disagreed with the matrix is not
    expressible.

    **Reads only.** Every service called is a list-and-count on the allowed
    path; each one calls ``policy.require``, which is why the ``is_allowed``
    check comes first — an unguarded call would write a DENIED_ATTEMPT row
    (BR-085) for a screen the reader never asked to open.
    """
    policy.require(request.user, Screen.DASHBOARD, Action.VIEW, request=request)

    def _may(screen: str) -> bool:
        return policy.is_allowed(request.user, screen, Action.VIEW)

    actor = request.user
    today = timezone.localdate()
    kpis: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    distribution: list[dict[str, Any]] = []

    def _kpi(
        label: Any,
        value: int,
        foot: Any,
        route: str,
        *,
        lead: bool = False,
        icon: str = "dot",
        tone: str = "brand",
        query: str = "",
    ) -> None:
        kpis.append(
            {
                "label": label,
                "value": value,
                "foot": foot,
                "url": reverse(route) + (f"?{query}" if query else ""),
                "lead": lead,
                "icon": icon,
                "tone": tone,
            }
        )

    def _waiting(label: Any, value: int, foot: Any, route: str, query: str = "") -> None:
        """A queue only earns a line while something is actually in it."""
        if value:
            url = reverse(route) + (f"?{query}" if query else "")
            waiting.append({"label": label, "value": value, "foot": foot, "url": url})

    # Sprint 8I — the pending-work table: one tab per queue, each a list of
    # rows the reader may already open. Filled only from rows the blocks
    # below have already fetched for their counters — no second query.
    queues: list[dict[str, Any]] = []
    charts: dict[str, Any] = {}

    def _queue(
        key: str,
        label: Any,
        rows_: list[dict[str, Any]],
        route: str,
        columns: Any,
        *,
        icon: str = "dot",
        tone: str = "brand",
    ) -> None:
        """A tab exists even when empty — an empty queue is news too."""
        queues.append(
            {
                "key": key,
                "label": label,
                "rows": rows_[:DASHBOARD_QUEUE_ROWS],
                "count": len(rows_),
                "more": max(len(rows_) - DASHBOARD_QUEUE_ROWS, 0),
                "url": reverse(route),
                "columns": columns,
                "icon": icon,
                "tone": tone,
            }
        )

    if _may(Screen.ENROLLMENTS):
        rows = enrollment_service.list_enrollments(actor=actor, request=request)
        owed = sum((r["balance"] for r in rows if r["participant_owes"]), Decimal("0.000"))
        _kpi(
            _("ذمم المشاركين"),
            owed,
            _("مجموع أرصدة «عليه» في التسجيلات القائمة"),
            "operations:enrollments",
            lead=True,
            icon="wallet",
            tone="danger",
        )
        _kpi(
            _("أرصدة غير مسوّاة"),
            sum(1 for r in rows if not r["is_settled"]),
            _("تسجيل لم يُغلق حسابه بعد"),
            "operations:enrollments",
            icon="scale",
            tone="warn",
        )
        _kpi(
            _("التسجيلات"),
            len(rows),
            _("الإجمالي القائم"),
            "operations:enrollments",
            icon="list",
            tone="info",
        )
        _waiting(
            _("بانتظار الوصل"),
            sum(1 for r in rows if not r["voucher_received"]),
            _("لا يُعتمد التسجيل قبل تسجيل الوصل (BR-018)"),
            "operations:enrollments",
        )
        # Who owes, who is owed, who is square — the split behind the lead KPI.
        split = [
            (_("عليه"), sum(1 for r in rows if r["participant_owes"]), "danger"),
            (_("مسوّى"), sum(1 for r in rows if r["is_settled"]), "ok"),
            (_("له"), sum(1 for r in rows if r["centre_owes"]), "warn"),
        ]
        # A chart of nothing is not a chart — no rows, no card.
        if rows:
            charts["balances"] = {"parts": _donut(split, len(rows)), "total": len(rows)}
        live = [r for r in rows if not r["is_final"]]
        _queue(
            "voucher",
            _("بانتظار الوصل"),
            [r for r in live if not r["voucher_received"]],
            "operations:enrollments",
            "enrollment",
            icon="receipt",
            tone="warn",
        )
        _queue(
            "approve",
            _("بانتظار الاعتماد"),
            [r for r in live if r["voucher_received"] and not r["is_approved"]],
            "operations:enrollments",
            "enrollment",
            icon="stamp",
            tone="info",
        )
        _queue(
            "owing",
            _("عليه رصيد"),
            [r for r in live if r["is_approved"] and r["participant_owes"]],
            "operations:enrollments",
            "enrollment",
            icon="wallet",
            tone="danger",
        )
        # A grouping of rows already fetched — not a query, not a calculation,
        # and not a report. Every enrolment is counted once under the status
        # the service already resolved for it.
        tally = Counter(str(r["status_display"]) for r in rows)
        # One category at 100% is not a distribution, it is a restatement of
        # the counter above it. The chart earns its place from two upward.
        if len(tally) > 1:
            distribution = [
                {
                    "label": label,
                    "value": count,
                    "level": _level(count, len(rows)),
                    "pct": count * 100 // len(rows),
                }
                for label, count in tally.most_common()
            ]

    if _may(Screen.COHORTS):
        cohorts = cohort_service.list_cohorts(actor=actor, request=request)
        _kpi(
            _("الدفعات المُشغّلة"),
            len(cohorts),
            _("دفعة قائمة"),
            "operations:cohorts",
            icon="calendar",
            tone="violet",
        )

    if _may(Screen.PAYMENTS):
        from apps.cashbox.services import payment_service

        # One read covers today's counter and the week's chart.
        week_rows = [
            r
            for r in payment_service.list_receipts(
                actor=actor, since=today - timedelta(days=6), request=request
            )
            if r["status"] == "ISSUED"
        ]
        issued = [r for r in week_rows if r["received_on"] == today]
        _kpi(
            _("سندات اليوم"),
            len(issued),
            _("سند قبض صادر اليوم"),
            "cashbox:payments",
            icon="coins",
            tone="ok",
            query="range=today",
        )
        days = []
        for back in range(6, -1, -1):
            day = today - timedelta(days=back)
            day_rows = [r for r in week_rows if r["received_on"] == day]
            days.append(
                {
                    "label": day.strftime("%d/%m"),
                    "amount": sum((r["amount"] for r in day_rows), Decimal("0.000")),
                    "count": len(day_rows),
                }
            )
        peak = max((d["amount"] for d in days), default=Decimal("0"))
        for d in days:
            # One solid column per day, its height a class out of twenty.
            # Whole dinars into whole levels (A-01b).
            d["level"] = _level(int(d["amount"]), int(peak))
            d["is_today"] = d["label"] == today.strftime("%d/%m")
        week_total = sum((d["amount"] for d in days), Decimal("0.000"))
        if week_total:
            charts["week"] = {"days": days, "total": week_total}
        _waiting(
            _("سندات لم تدخل إقفالاً"),
            sum(1 for r in issued if not r["is_closed"]),
            _("من سندات اليوم، بانتظار إقفال الصندوق"),
            "cashbox:payments",
            query="range=today&status=unclosed",
        )

    alerts: list[dict[str, Any]] = []
    if _may(Screen.CLOSING):
        from apps.cashbox.services import closing_service

        closings = closing_service.list_closings(actor=actor, request=request)
        # Cashier-days with receipts and no closing: the queue, and — when
        # one is older than yesterday — a banner. A till left open is a
        # control failure, not a backlog.
        open_days = closing_service.open_days(actor=actor, request=request)
        for d in open_days:
            d["age"] = (today - d["closing_date"]).days
            d["url"] = (
                reverse("cashbox:closing")
                + f"?cashier={d['cashier_id']}&on={d['closing_date'].isoformat()}#close-day"
            )
        overdue = [d for d in open_days if d["age"] >= 1]
        if overdue:
            oldest = max(d["age"] for d in overdue)
            alerts.append(
                {
                    "tone": "danger" if oldest >= 3 else "warn",
                    "icon": "vault",
                    "title": _("%(n)s من أيام الصندوق بلا إقفال") % {"n": len(overdue)},
                    "body": _("أقدمها منذ %(d)s يوماً. يُقفل كل صندوق في نهاية يوم عمله (BR-026).")
                    % {"d": oldest},
                    "url": reverse("cashbox:closing") + "#open-days",
                    "action": _("افتح الإقفال اليومي"),
                }
            )
        _queue(
            "open_days",
            _("أيام صندوق بلا إقفال"),
            open_days,
            "cashbox:closing",
            "open_day",
            icon="clock",
            tone="amber",
        )
        _waiting(
            _("إقفالات لم تُعتمد"),
            sum(1 for c in closings if c["status"] in {"OPEN", "VARIANCE_PENDING"}),
            _("من قبض المال لا يوقّع على عدّه (BR-028)"),
            "cashbox:closing",
        )
        _queue(
            "closing",
            _("إقفالات معلّقة"),
            [c for c in closings if c["status"] in {"OPEN", "VARIANCE_PENDING"}],
            "cashbox:closing",
            "closing",
            icon="vault",
            tone="teal",
        )

    if _may(Screen.TRANSFERS):
        transfers = transfer_service.list_transfers(actor=actor, request=request)
        _waiting(
            _("طلبات نقل قائمة"),
            sum(
                1
                for t in transfers
                if t["status"] in {"DRAFT", "PENDING_MANAGER", "PENDING_FINANCE"}
            ),
            _("لم تُنفَّذ ولم تُرفض بعد"),
            "operations:transfers",
        )
        _queue(
            "transfer",
            _("طلبات النقل"),
            [
                t
                for t in transfers
                if t["status"] in {"DRAFT", "PENDING_MANAGER", "PENDING_FINANCE"}
            ],
            "operations:transfers",
            "transfer",
            icon="swap",
            tone="violet",
        )

    if _may(Screen.CLEARANCE):
        clearances = clearance_service.list_clearances(actor=actor, request=request)
        _waiting(
            _("براءات ذمة قائمة"),
            sum(1 for c in clearances if not c["is_completed"]),
            _("لا شهادة بلا براءة مكتملة (BR-075)"),
            "operations:clearances",
        )
        open_clearances = [c for c in clearances if not c["is_completed"]]
        _queue(
            "clearance",
            _("براءات الذمة"),
            open_clearances,
            "operations:clearances",
            "clearance",
            icon="shield-check",
            tone="ok",
        )
        # Where the open clearances stand: which step each waits on.
        by_step = Counter(
            str(c["pending_step_name"] or c["status_display"]) for c in open_clearances
        )
        if by_step:
            charts["clearances"] = [
                {
                    "label": label,
                    "value": count,
                    "level": _level(count, len(open_clearances)),
                    "pct": count * 100 // len(open_clearances),
                }
                for label, count in by_step.most_common()
            ]

    if _may(Screen.CLAIMS):
        from apps.settlements.services import claim_service

        claims = claim_service.list_claims(actor=actor, status="SUBMITTED", request=request)
        _waiting(
            _("مطالبات بانتظار الاعتماد"),
            len(claims),
            _("مرفوعة ولم يُبتّ فيها"),
            "settlements:claims",
        )
        _queue(
            "claim",
            _("مطالبات معلّقة"),
            claims,
            "settlements:claims",
            "claim",
            icon="building",
            tone="amber",
        )

    # Each queue against the longest one, so "which of these is the big one"
    # is answered by looking rather than by reading every number. A lone queue
    # has nothing to be longer than, so it keeps its number and drops its bar.
    if len(waiting) > 1:
        longest = max(row["value"] for row in waiting)
        for row in waiting:
            row["bar"] = _bar(row["value"], longest)

    # WORKFLOWS §1 … §7 in six stops. It is a map, not a progress bar: no step
    # is ever marked done, because the page has no participant in mind. It is
    # here because a centre with four enrolments still has a whole process, and
    # a dashboard that shows only volume has nothing to say on a quiet morning.
    #
    # A stop the reader may not open keeps its place and loses its link — the
    # rule the guided help follows, for the same reason: the shape of the work
    # belongs to everyone, the screens do not.
    #
    # Each stop carries a tone and an icon from the sidebar sprite: eight
    # stops in one colour read as one long bar, and the colour repeats on the
    # same screen's sidebar entry so the map and the menu agree.
    flow = [
        {
            "label": label,
            "who": who,
            "icon": icon,
            "tone": tone,
            "desc": desc,
            "url": reverse(route) if _may(screen) else "",
        }
        for screen, route, label, who, icon, tone, desc in (
            (
                Screen.MOHE,
                "operations:mohe",
                _("اعتماد الوزارة"),
                _("مدير المركز"),
                "stamp",
                "violet",
                _("فتح ملف الدفعة وإرساله للوزارة وتسجيل قرارها — لا تسجيل قبل الاعتماد (BR-013)."),
            ),
            (
                Screen.STUDENT_NEW,
                "people:participant-new",
                _("طلب التحاق"),
                _("موظف التسجيل"),
                "user-plus",
                "info",
                _("إدخال بيانات المشارك وفئته، ويُولَّد رقمه الجامعي."),
            ),
            (
                Screen.ENROLLMENTS,
                "operations:enrollments",
                _("التسجيل"),
                _("مدير المركز"),
                "list",
                "brand",
                _("تنسيب المشارك على دفعة معتمدة وتسعيره بقائمة الأسعار السارية (BR-012)."),
            ),
            (
                Screen.PAYMENT_NEW,
                "cashbox:payment-new",
                _("سند القبض"),
                _("الصندوق"),
                "coins",
                "ok",
                _("قبض الدفعة وتوزيعها على البنود، ثم إصدار سند القبض."),
            ),
            (
                Screen.ENROLLMENTS,
                "operations:enrollments",
                _("الاعتماد"),
                _("التسجيل ثم المدير"),
                "doc-check",
                "amber",
                _("تسجيل الوصل من موظف التسجيل، ثم اعتماد المدير للتسجيل (BR-018)."),
            ),
            (
                Screen.CLOSING,
                "cashbox:closing",
                _("الإقفال"),
                _("الصندوق"),
                "vault",
                "teal",
                _("عدّ صندوق اليوم ومطابقته بسندات النظام، ثم اعتماد الإقفال (BR-028)."),
            ),
            (
                Screen.CLEARANCE,
                "operations:clearances",
                _("براءة الذمة"),
                _("المركز والمالية"),
                "shield-check",
                "warn",
                _("إخلاء طرف المشارك على خطوات: العُهد، والمالية، والشهادة."),
            ),
            (
                Screen.CERTIFICATES,
                "operations:certificates",
                _("الشهادة"),
                _("مدير المركز"),
                "award",
                "danger",
                _("إصدار الشهادة للخرّيج بعد اكتمال براءة الذمة (BR-075)."),
            ),
        )
    ]

    # What this reader's permissions actually cover, said once at the top.
    # Derived from the matrix, never from the role name — so it cannot drift
    # from what the sidebar and the screens themselves allow.
    scope = [
        label
        for label, screens in (
            (_("التسجيل"), (Screen.STUDENTS, Screen.ENROLLMENTS)),
            (_("البرامج والدفعات"), (Screen.PROGRAMS, Screen.COHORTS)),
            (_("الصندوق"), (Screen.PAYMENTS, Screen.CLOSING)),
            (_("الشركاء"), (Screen.PARTNERS, Screen.CLAIMS)),
            (_("الإنهاء والشهادات"), (Screen.CLEARANCE, Screen.CERTIFICATES)),
            (_("التقارير"), (Screen.REPORTS,)),
        )
        if any(_may(screen) for screen in screens)
    ]

    return render(
        request,
        "operations/dashboard.html",
        {
            "title": _("لوحة المؤشرات"),
            "active_screen": Screen.DASHBOARD,
            "today": today,
            "kpis": kpis[:4],
            "waiting": waiting,
            "distribution": distribution,
            "charts": charts,
            "alerts": alerts,
            "queues": queues,
            "pending_total": sum(q["count"] for q in queues),
            "flow": flow,
            "scope": scope,
        },
    )


# ---------------------------------------------------------------------------
# Cohorts (Screen.COHORTS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def cohorts_view(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    can_create = policy.is_allowed(request.user, Screen.COHORTS, Action.CREATE)
    can_edit = policy.is_allowed(request.user, Screen.COHORTS, Action.EDIT)
    can_cancel = policy.is_allowed(request.user, Screen.COHORTS, Action.APPROVE)
    action = request.POST.get("action", "") if request.method == "POST" else ""
    posted_code = request.POST.get("cohort_code", "") if request.method == "POST" else ""

    form = None
    programs: list[dict[str, Any]] = []
    semesters: list[dict[str, Any]] = []
    suggestions: dict[str, list[str]] = {}
    if can_create:
        programs = cohort_service.program_options(actor=request.user, request=request)
        semesters = cohort_service.semester_options(actor=request.user, request=request)
        suggestions = cohort_service.field_suggestions(actor=request.user, request=request)
        form = CohortForm(
            # الطريقة هي ما يقرّر الربط، لا وجود المفتاح: في GET تكون
            # ``request.POST`` قاموساً فارغاً لا «لا شيء»، فيُبنى النموذج
            # مربوطاً بلا بيانات، وتمتلئ ``errors`` بكل حقل إلزامي — فيفتح
            # الحوار نفسه لحظة فتح الشاشة. نفس العلّة أُصلحت في نموذج التسجيل.
            request.POST if request.method == "POST" and action in ("", "open") else None,
            program_choices=[(p["code"], p["label"]) for p in programs],
            semester_choices=[(s["code"], s["label"]) for s in semesters],
            agreement_choices=_agreement_choices(request),
        )

    agreements = _agreement_choices(request)
    edit_form = CohortEditForm(
        request.POST if action == "edit" else None, agreement_choices=agreements
    )
    cancel_form = CohortCancelForm(request.POST if action == "cancel" else None)

    if request.method == "POST":
        response = _handle_cohort_action(
            request, action, form=form, edit_form=edit_form, cancel_form=cancel_form
        )
        if response is not None:
            return response

    rows = cohort_service.list_cohorts(
        actor=request.user, query=query, status=status, request=request
    )
    for row in rows:
        row["seat_level"] = _level(row["enrolled_count"], row["capacity"])
        # Drawn per row so a dialog opens on the cohort it belongs to and no
        # role sees an act it may not perform.
        row["offers_start"] = can_edit and row["can_start"]
        row["offers_complete"] = can_edit and row["can_complete"]
        row["offers_cancel"] = can_cancel and row["can_cancel"]
        row["offers_edit"] = can_edit and row["can_edit_row"]
    # The strip above the register answers «كم دفعة تحتاج مني؟» over the whole
    # register, not the filtered slice — a tile that shrank with the filter it
    # opens would count itself. Counts of rows only (no money, no forecast).
    everything = (
        rows
        if not (query or status)
        else cohort_service.list_cohorts(actor=request.user, request=request)
    )
    base = reverse("operations:cohorts")

    def _stage_tile(label: str, stage: str, icon: str, tone: str) -> dict[str, Any]:
        return {
            "label": label,
            "value": sum(1 for r in everything if r["stage"] == stage),
            "url": f"{base}?stage={stage}",
            "icon": icon,
            "tone": tone,
            "on": request.GET.get("stage", "") == stage,
        }

    tiles = [
        {
            "label": _("كل الدفعات"),
            "value": len(everything),
            "url": base,
            "icon": "calendar",
            "tone": "brand",
            "on": not (query or status or request.GET.get("stage")),
        },
        _stage_tile(_("بانتظار الملف الوزاري"), "NEEDS_FILE", "stamp", "warn"),
        _stage_tile(_("بانتظار قرار الوزارة"), "AT_MOHE", "clock", "info"),
        _stage_tile(_("جاهزة للتسجيل"), "ENROLLABLE", "checks", "ok"),
        _stage_tile(_("قيد التنفيذ"), "RUNNING", "route", "ok"),
        {
            "label": _("مقاعد متاحة"),
            "value": sum(
                r["seats_left"] for r in everything if r["stage"] in ("ENROLLABLE", "RUNNING")
            ),
            "url": base,
            "icon": "users",
            "tone": "info",
            "on": False,
        },
    ]

    # The stage is the reader's filter; the stored status stays reachable from a
    # URL for anyone who has one bookmarked.
    stage = request.GET.get("stage", "").strip()
    if stage:
        rows = [r for r in rows if r["stage"] == stage]

    return render(
        request,
        "operations/cohorts.html",
        {
            "title": _("الدفعات المُشغّلة"),
            "active_screen": Screen.COHORTS,
            "cohorts": rows,
            "form": form,
            "edit_form": edit_form,
            "cancel_form": cancel_form,
            "can_create": can_create,
            "can_edit": can_edit,
            "can_cancel": can_cancel,
            "query": query,
            # ``status`` has been reachable from the URL all along and was
            # drawn nowhere, so a narrowed list looked like the whole register.
            # Naming it adds no filter and opens no field: the column it names
            # is in the table for every role that may open this screen.
            "active_filters": _cohort_active_filters(rows, query, status, stage),
            "status_counts": _cohort_status_counts(rows),
            # The open-cohort dialog chooses, it does not type: programmes
            # grouped by kind with their levels, semesters with their dates,
            # trainers and places already on record.
            "tiles": tiles,
            "programs": programs,
            "semesters": semesters,
            "suggestions": suggestions,
            "stages": cohort_service.STAGES,
            "stage": stage,
            "status_choices": cohort_service.status_choices(),
            "statuses": status,
            "posted_action": action,
            "posted_code": posted_code,
            "today": timezone.localdate(),
            "can_submit_mohe": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.VIEW),
            # §9.7 — an expense is recorded on a cohort from its row.
            "can_record_expense": policy.is_allowed(request.user, Screen.EXPENSES, Action.CREATE),
            "can_view_enrollments": policy.is_allowed(
                request.user, Screen.ENROLLMENTS, Action.VIEW
            ),
        },
    )


#: The acts a row offers, and the cell of §3.3/12 each one asks for. Cancelling
#: is APPROVE because it opens a full refund (§5.3); the rest are corrections.
COHORT_ACTIONS = {
    "open": Action.CREATE,
    "edit": Action.EDIT,
    "start": Action.EDIT,
    "complete": Action.EDIT,
    "cancel": Action.APPROVE,
}


def _handle_cohort_action(
    request: HttpRequest,
    action: str,
    *,
    form: CohortForm | None,
    edit_form: CohortEditForm,
    cancel_form: CohortCancelForm,
) -> HttpResponse | None:
    """One POST target for the register; every act passes its own gate."""
    action = action or "open"
    if action not in COHORT_ACTIONS:
        raise Http404(_("إجراء غير معروف"))
    policy.require(request.user, Screen.COHORTS, COHORT_ACTIONS[action], request=request)

    if action == "open":
        if form is None:
            raise PermissionDenied
        if not form.is_valid():
            return None
        try:
            cohort_service.open_cohort(actor=request.user, request=request, **form.cleaned_data)
            messages.success(request, _("فُتحت الدفعة"))
            return redirect("operations:cohorts")
        except DjangoValidationError as exc:
            messages.error(request, _message_of(exc))
            return None

    code = request.POST.get("cohort_code", "").strip()
    try:
        cohort = cohort_service.get_cohort_instance(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist:
        raise Http404(_("لا توجد دفعة بهذا الرمز")) from None

    try:
        if action == "edit":
            if not edit_form.is_valid():
                return None
            cohort_service.update_cohort(
                actor=request.user, cohort=cohort, data=dict(edit_form.cleaned_data), request=request
            )
            messages.success(request, _("حُفظت بيانات الدفعة"))
        elif action == "start":
            cohort_service.start_cohort(actor=request.user, cohort=cohort, request=request)
            messages.success(request, _("بدأ تنفيذ الدفعة"))
        elif action == "complete":
            cohort_service.complete_cohort(actor=request.user, cohort=cohort, request=request)
            messages.success(request, _("أُنهيت الدفعة"))
        elif action == "cancel":
            if not cancel_form.is_valid():
                return None
            cohort_service.cancel_for_low_enrollment(
                actor=request.user,
                cohort=cohort,
                reason_ar=cancel_form.cleaned_data["reason_ar"],
                request=request,
            )
            messages.success(
                request,
                _("أُلغيت الدفعة لقلة التسجيل — المسجَّلون عليها يستحقون استرداداً كاملاً (§5.3)."),
            )
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    return redirect(f"{reverse('operations:cohorts')}?q={code}")


def _cohort_active_filters(
    rows: list[dict[str, Any]], query: str, status: str, stage: str = ""
) -> list[tuple[str, str]]:
    """The filters this request is narrowing by, named for the reader."""
    active: list[tuple[str, str]] = []
    if query:
        active.append((_("بحث"), query))
    if stage:
        label, _tone = cohort_service.STAGE_LOOK.get(stage, (stage, ""))
        active.append((_("المرحلة"), label))
    if status:
        # The label off the drawn rows, never the stored code: a filter that
        # matches nothing has no row to read it from, and printing the enum is
        # the defect the transfer register was fixed for.
        labels = {str(row["status"]): str(row["status_display"]) for row in rows}
        active.append((_("الحالة"), labels.get(status, status)))
    return active


def _cohort_status_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    The states of the cohorts ACTUALLY DRAWN, tallied off the same rows.

    Counted here rather than queried again so the chips cannot disagree with
    the table, and so they describe this request's result rather than the
    register. A state nobody is in gets no chip rather than a zero.
    """
    # Tallied by STAGE, which is what the rows themselves are labelled by: two
    # vocabularies over one table — «مخطَّطة» in the chips and «جاهزة للتسجيل»
    # in the rows — was the ambiguity this screen was fixed for.
    tally: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (str(row["stage"]), str(row["stage_display"]), str(row["stage_tone"]))
        tally[key] = tally.get(key, 0) + 1
    return [
        {"status": stage, "label": label, "tone": tone, "count": count}
        for (stage, label, tone), count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0][0]))
    ]


def _agreement_choices(request: HttpRequest) -> list[tuple[str, str]]:
    """Empty rather than absent when the role may not see agreements."""
    if not policy.is_allowed(request.user, Screen.AGREEMENTS, Action.VIEW):
        return []
    return partner_service.agreement_choices(actor=request.user, request=request)


# ---------------------------------------------------------------------------
# Enrolments (Screen.ENROLLMENTS)
# ---------------------------------------------------------------------------
def _enrollment_form(request: HttpRequest, data: Any = None) -> EnrollmentForm:
    """
    The creation form, with the only two lists it may offer.

    Both are guarded, though §3.2 gives STUDENTS VIEW to both roles that may
    create an enrolment — so a list is empty only for a role that could not
    have submitted anyway. Only ministry-approved cohorts are offered, because
    BR-013 refuses the rest and a dropdown leading to a refusal is a trap.
    """
    return EnrollmentForm(
        data,
        cohort_choices=cohort_service.cohort_choices(actor=request.user, request=request)
        if policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW)
        else [],
        participant_choices=participant_service.participant_choices(
            actor=request.user, request=request
        )
        if policy.is_allowed(request.user, Screen.STUDENTS, Action.VIEW)
        else [],
    )


@require_http_methods(["GET", "POST"])
def enrollment_new_view(request: HttpRequest) -> HttpResponse:
    """
    Creating an enrolment, on its own screen.

    The form used to live in the foot of the register, which put it 1289px
    down at 1440 and 1909px at 1024 on a page two and a half screens tall: a
    rejected field drew its message inside a card the reader could not see,
    and the register had to grow an error summary at its top to point at a
    form two screens below. A list screen lists and a form screen forms —
    which is how the till, this system's reference form screen, is built.

    The permission is the register's own CREATE: no new cell, no new matrix
    row, and a reader without it is refused here rather than handed a form
    whose save would be refused and written down as a DENIED_ATTEMPT (BR-085).
    """
    policy.require(request.user, Screen.ENROLLMENTS, Action.CREATE, request=request)

    # The method is what decides whether the form is bound. ``request.POST or
    # None`` read an EMPTY body as "not submitted", so a POST carrying nothing
    # came back as a pristine form with no error and no message at all.
    posted = request.method == "POST"
    form = _enrollment_form(request, request.POST if posted else None)

    if posted and form.is_valid():
        response = _create_enrollment(request, form)
        if response is not None:
            return response

    return render(
        request,
        "operations/enrollment_new.html",
        {
            "title": _("تسجيل جديد"),
            "active_screen": Screen.ENROLLMENTS,
            "form": form,
            # Named at the top so a refusal is read rather than hunted.
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound and form[name].errors
            ],
            "today": timezone.localdate(),
        },
    )


@require_http_methods(["GET"])
def enrollments_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.CREATE)

    query = request.GET.get("q", "").strip()
    cohort_code = request.GET.get("cohort", "").strip()
    status = request.GET.get("status", "").strip()
    participant_number = request.GET.get("participant", "").strip()
    rows = enrollment_service.list_enrollments(
        actor=request.user,
        query=query,
        cohort_code=cohort_code,
        status=status,
        participant_number=participant_number,
        request=request,
    )
    # Paginated in the VIEW, over the list the service already returned — the
    # register is one of four callers of ``list_enrollments`` and the other
    # three (the dashboard, a report and the participant file) want the whole
    # set, so a limit pushed down into the service would answer their question
    # wrongly. What this stops is the PAGE drawing thousands of rows, which is
    # the part the reader pays for.
    page = page_of(rows, request.GET.get("page", ""))
    rows = page["rows"]
    _annotate_clearance_step(request, rows)
    _annotate_transfer_step(request, rows)
    return render(
        request,
        "operations/enrollments.html",
        {
            "title": _("التسجيلات"),
            "active_screen": Screen.ENROLLMENTS,
            "enrollments": rows,
            "page": page,
            # Everything but ``page``, so a pager link keeps the filters.
            "params_qs": urlencode(
                {
                    key: value
                    for key, value in (
                        ("q", query),
                        ("cohort", cohort_code),
                        ("status", status),
                        ("participant", participant_number),
                    )
                    if value
                }
            ),
            # Counted over the page that is drawn, which is what the reader can
            # check against the table beneath them.
            "status_counts": _status_counts(rows),
            "active_filters": _enrollment_active_filters(
                rows, query, cohort_code, status, participant_number
            ),
            # Seven now: the expander, the code, the participant, the cohort,
            # the balance, the status and the actions. Counted here rather than
            # written into the template twice, because the empty row and the
            # detail row both have to span exactly what the header draws.
            "column_count": 7,
            "can_create": can_create,
            "can_edit": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.EDIT),
            "can_approve": policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.APPROVE),
            # Who may open the till: the "pay first" stop becomes a link for them.
            "can_take_payment": policy.is_allowed(request.user, Screen.PAYMENT_NEW, Action.CREATE),
            # Dismissal is filed as a special case (BR-067), so it wears that
            # screen's CREATE rather than this one's APPROVE.
            "can_dismiss": policy.is_allowed(request.user, Screen.SPECIAL_CASES, Action.CREATE),
            # The row's two quick ways out — the participant's file and the
            # cohort it belongs to — drawn only for a reader those screens
            # would actually let in. A link into a refusal is worse than no
            # link: it spends a click and files a DENIED_ATTEMPT (BR-085).
            "can_view_participants": policy.is_allowed(
                request.user, Screen.STUDENTS, Action.VIEW
            ),
            "can_view_cohorts": policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW),
            "can_request_transfer": policy.is_allowed(
                request.user, Screen.TRANSFER_NEW, Action.CREATE
            ),
            "query": request.GET.get("q", ""),
        },
    )


def _annotate_transfer_step(request: HttpRequest, rows: list[dict[str, Any]]) -> None:
    """
    Which rows the transfer screen would actually accept, marked on the row.

    The request starts from an enrolment, and the only way in was the transfer
    register — so the operator left this screen, opened that one, and hunted
    the same person again in a dropdown. The link is drawn here instead.

    The set comes from ``transferable_enrollment_choices``: the same function
    the target screen fills its own list from, so a row can never offer a link
    the next screen would refuse (BR-060 · a request already in flight). It is
    asked once per page rather than once per row, and only for a reader who
    may raise a request at all — the call itself requires VIEW there.
    """
    if not policy.is_allowed(request.user, Screen.TRANSFER_NEW, Action.CREATE):
        return
    transferable = {
        code
        for code, _label in transfer_service.transferable_enrollment_choices(
            actor=request.user, request=request
        )
    }
    for row in rows:
        row["transferable"] = row.get("code") in transferable


def _annotate_clearance_step(request: HttpRequest, rows: list[dict[str, Any]]) -> None:
    """
    What the clearance register already knows, carried onto the enrolment row.

    §6.4 — an enrolment that has reached an exit is followed by a clearance,
    and the operator was having to leave this screen, open the register and
    find the same person again to start it. Nothing here decides anything:
    which statuses may be cleared and which enrolments already carry a live
    clearance are both read from ``clearance_service`` exactly as the register
    reads them, so the case stays the service's to derive (BR-075, §6.4).

    Both calls are pure reads, and each is made only after ``is_allowed`` says
    the reader holds the permission — a reader who does not simply gets no
    button, and no ``DENIED_ATTEMPT`` is written for a screen they never asked
    for (BR-085).

    Order matters: ``clearable_enrollment_choices`` already excludes an
    enrolment carrying a live clearance, so "openable" is asked first. An
    enrolment whose only clearance was cancelled is openable again, and is
    offered the opening rather than a link to the cancelled one.
    """
    openable: set[str] = set()
    if policy.is_allowed(request.user, Screen.CLEARANCE, Action.CREATE):
        openable = {
            code
            for code, _ in clearance_service.clearable_enrollment_choices(
                actor=request.user, request=request
            )
        }

    latest: dict[str, str] = {}
    if policy.is_allowed(request.user, Screen.CLEARANCE, Action.VIEW):
        # Rows arrive newest first, so the first one seen for an enrolment is
        # the one worth linking to.
        for clearance in clearance_service.list_clearances(actor=request.user, request=request):
            latest.setdefault(clearance["enrollment_code"], clearance["code"])

    for row in rows:
        code = row["code"]
        row["clearance_openable"] = code in openable
        row["clearance_code"] = latest.get(code, "")


def _status_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    How the rows ON SCREEN divide by status — not a registry-wide total.

    Counted over the result set the request already produced, so the chips move
    with the filter and can be checked against the table beneath them. Nothing
    is queried, no key is read that the row did not already carry to the page,
    and a status nobody is in simply has no chip.

    Every list on this file's screens projects ``status`` beside
    ``status_display``, so this needs to know nothing about which list it is
    counting.
    """
    counts = Counter(str(row["status"]) for row in rows)
    labels = {str(row["status"]): row["status_display"] for row in rows}
    return [
        {"status": status, "label": labels[status], "count": count}
        for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _enrollment_active_filters(
    rows: list[dict[str, Any]],
    query: str,
    cohort_code: str,
    status: str,
    participant_number: str = "",
) -> list[tuple[str, str]]:
    """
    The filters this request is actually narrowing by, named for the reader.

    A filtered list that looks unfiltered is how «where did they go?» starts.
    ``cohort`` and ``status`` are reachable from the URL and have been all
    along; naming them adds no filter and opens no field — both are printed in
    the table for every role the matrix lets through this door.
    """
    active: list[tuple[str, str]] = []
    if query:
        active.append((_("بحث"), query))
    if cohort_code:
        active.append((_("الدفعة"), cohort_code))
    if status:
        labels = {str(row["status"]): row["status_display"] for row in rows}
        active.append((_("الحالة"), labels.get(status, status)))
    if participant_number:
        # The name when the rows carry it, else the number — both are on
        # every row the reader can already see.
        names = {row["participant_number"]: row["participant_name"] for row in rows}
        active.append((_("المشارك"), names.get(participant_number, participant_number)))
    return active


def _create_enrollment(
    request: HttpRequest, form: EnrollmentForm | QuickEnrollmentForm
) -> HttpResponse | None:
    data = form.cleaned_data
    try:
        participant = participant_service.participant_instance(
            actor=request.user, participant_number=data["participant_number"], request=request
        )
    except ObjectDoesNotExist:
        messages.error(request, _("لا يوجد مشارك بهذا الرقم"))
        return None

    try:
        cohort = cohort_service.get_cohort_instance(
            actor=request.user, code=data["cohort_code"], request=request
        )
        enrollment_service.enroll_with_charges(
            actor=request.user,
            participant=participant,
            cohort=cohort,
            enrolled_on=data["enrolled_on"],
            request=request,
        )
    except (DjangoValidationError, ObjectDoesNotExist) as exc:
        messages.error(request, _message_of(exc))
        return None
    # The pricing engine's refusals are not programming errors and they are the
    # ones a registrar meets most: a term with no approved list, a programme the
    # list never priced, a category with no fee rule (BR-008 · BR-009 · BR-012).
    # Uncaught, each arrived as a 500 — the registrar losing the typed form and
    # the screen saying nothing about what is missing or who fixes it.
    except (
        pricing_service.NoEffectivePriceListError,
        pricing_service.ProgramNotPricedError,
        pricing_service.LevelRequiredError,
    ) as exc:
        messages.error(
            request,
            _("تعذّر تحميل الرسوم: %(why)s — يُصلَح من قائمة الأسعار قبل التسجيل.")
            % {"why": exc},
        )
        return None

    messages.success(request, _("تم التسجيل وتحميل الرسوم"))
    return redirect("operations:enrollments")


@require_http_methods(["POST"])
def enrollment_quick_view(request: HttpRequest) -> HttpResponse:
    """
    The participants-screen dialog posts here: one participant, one cohort.

    Same gate, same service and same refusals as the enrolments screen — the
    dialog is a shorter road to the same door, not a second door. The cohort
    is checked against the approved set first (BR-013), so a stale dialog
    cannot enrol on a cohort whose approval was withdrawn meanwhile. On
    success or refusal the reader lands back where the dialog was opened.
    """
    policy.require(request.user, Screen.ENROLLMENTS, Action.CREATE, request=request)

    form = QuickEnrollmentForm(request.POST)
    back = reverse("people:participants")
    if form.is_valid():
        wanted = form.cleaned_data["next"]
        if wanted and url_has_allowed_host_and_scheme(wanted, allowed_hosts={request.get_host()}):
            back = wanted
        approved = {
            code
            for code, _label in cohort_service.cohort_choices(actor=request.user, request=request)
        }
        if form.cleaned_data["cohort_code"] not in approved:
            messages.error(request, _("الدفعة المختارة غير معتمدة من الوزارة (BR-013)"))
        elif _create_enrollment(request, form) is not None:
            return redirect(back)
    else:
        messages.error(request, _("اختر الدفعة وتاريخ التسجيل"))
    return redirect(back)


@require_http_methods(["POST"])
def enrollment_action_view(request: HttpRequest, code: str) -> HttpResponse:
    """The state changes the list offers: voucher, approval (BR-018), the three exits (§6.4)."""
    action = request.POST.get("action", "")
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    try:
        if action == "voucher":
            enrollment_service.record_voucher(
                actor=request.user, enrollment=enrollment, request=request
            )
            messages.success(request, _("سُجِّل استلام الوصل"))
        elif action == "approve":
            enrollment_service.approve_enrollment(
                actor=request.user, enrollment=enrollment, request=request
            )
            messages.success(request, _("اعتُمد التسجيل"))
        elif action == "complete":
            enrollment_service.complete_enrollment(
                actor=request.user, enrollment=enrollment, request=request
            )
            messages.success(request, _("سُجّل إكمال التسجيل، ويمكن الآن فتح براءة الذمة."))
        elif action == "withdraw":
            enrollment_service.withdraw_enrollment(
                actor=request.user,
                enrollment=enrollment,
                reason_ar=request.POST.get("reason_ar", ""),
                request=request,
            )
            messages.success(request, _("سُجّل الانسحاب، ويمكن الآن فتح براءة الذمة."))
        elif action == "dismiss":
            enrollment_service.dismiss_enrollment(
                actor=request.user,
                enrollment=enrollment,
                decision_reference=request.POST.get("decision_reference", ""),
                reason_ar=request.POST.get("reason_ar", ""),
                request=request,
            )
            messages.success(request, _("سُجّل الفصل، ويمكن الآن فتح براءة الذمة."))
        elif action == "cancel-registration":
            from apps.operations.services import special_case_service

            special_case_service.cancel_registration(
                actor=request.user,
                enrollment=enrollment,
                reason_ar=request.POST.get("reason_ar", ""),
                occurred_on=timezone.localdate(),
                request=request,
            )
            messages.success(
                request, _("أُلغي التسجيل قبل الاعتماد؛ المقبوض (إن وُجد) بقي رصيداً دائناً.")
            )
        else:
            messages.error(request, _("إجراء غير معروف"))
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))

    return redirect("operations:enrollments")


# ---------------------------------------------------------------------------
# The participant's account (Screen.ENROLLMENTS — §9 report 5 as a screen)
# ---------------------------------------------------------------------------
def account_view(request: HttpRequest, code: str) -> HttpResponse:
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=code, request=request
        )
        statement = account_service.account_statement(
            actor=request.user, enrollment=enrollment, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(
        request,
        "operations/account.html",
        {
            "title": _("كشف حساب المشارك"),
            "active_screen": Screen.ENROLLMENTS,
            "statement": statement,
            "can_take_payment": policy.is_allowed(request.user, Screen.PAYMENT_NEW, Action.CREATE),
            # §5.1 — the manager grants a discount from the account they are
            # reading; the discounts screen opens with this enrolment chosen.
            "can_grant_discount": policy.is_allowed(request.user, Screen.DISCOUNTS, Action.CREATE),
            # §5.3 / BR-071 — the finance officer raises a refund or returns a
            # credit from the account they are reading; the refunds screen
            # opens on the matching tab with this enrolment chosen.
            "can_refund": policy.is_allowed(request.user, Screen.REFUNDS, Action.CREATE),
            # §5.5 — an extra fee lands on the account being read.
            "can_charge_fee": policy.is_allowed(request.user, Screen.EXTRA_FEES, Action.CREATE),
        },
    )


# ---------------------------------------------------------------------------
# Clearance (Screen.CLEARANCE) — §6.4, form CS Fm 7.18 Rev A
# ---------------------------------------------------------------------------
#: Which permission each POST action needs. Two of the step-2 actions belong
#: to a DIFFERENT screen's permission: settling the deposit and returning a
#: credit are money movements gated by REFUNDS.CREATE, which the finance
#: officer holds and the finance manager does not. The finance manager
#: countersigns and nothing else — so the buttons are gated by what each one
#: actually needs, never by where it sits on the page.
CLEARANCE_ACTIONS = {
    "open": (Screen.CLEARANCE, Action.CREATE),
    "custody": (Screen.CLEARANCE, Action.APPROVE),
    "certify": (Screen.CLEARANCE, Action.APPROVE),
    "second-certify": (Screen.CLEARANCE, Action.APPROVE),
    "handover": (Screen.CLEARANCE, Action.APPROVE),
    "close": (Screen.CLEARANCE, Action.APPROVE),
    "cancel": (Screen.CLEARANCE, Action.APPROVE),
    "deposit": (Screen.REFUNDS, Action.CREATE),
    "forfeit": (Screen.REFUNDS, Action.CREATE),
    "return-credit": (Screen.REFUNDS, Action.CREATE),
}


@require_http_methods(["GET", "POST"])
def clearances_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CLEARANCE, Action.CREATE)
    form = None
    if can_create:
        form = ClearanceOpenForm(
            request.POST if request.POST.get("action") == "open" else None,
            enrollment_choices=clearance_service.clearable_enrollment_choices(
                actor=request.user, request=request
            ),
        )

    confirm = None
    if request.method == "POST":
        response = _handle_clearance_open(request, form)
        if isinstance(response, dict):
            confirm = response
        elif response is not None:
            return response

    return render(
        request,
        "operations/clearances.html",
        {
            "title": _("براءة الذمة"),
            "active_screen": Screen.CLEARANCE,
            "clearances": clearance_service.list_clearances(
                actor=request.user,
                status=request.GET.get("status", "").strip(),
                query=request.GET.get("q", "").strip(),
                request=request,
            ),
            "form": form,
            "can_create": can_create,
            # The confirmation step: what the first submit asked for, read back
            # from the service, awaiting a second submit that carries ``confirmed``.
            "confirm": confirm,
            "query": request.GET.get("q", ""),
        },
    )


def _handle_clearance_open(
    request: HttpRequest, form: ClearanceOpenForm | None
) -> HttpResponse | dict[str, Any] | None:
    """
    Opening is a formal act, so it takes two submits: the first shows what
    will happen (returned as a dict for the template), the second — carrying
    ``confirmed`` — does it. Both run the same form and the same service gate.
    """
    action = request.POST.get("action", "")
    if action in CLEARANCE_ACTIONS:
        screen, permission = CLEARANCE_ACTIONS[action]
        policy.require(request.user, screen, permission, request=request)
    if action != "open" or form is None or not form.is_valid():
        return None

    data = form.cleaned_data
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=data["enrollment_code"], request=request
        )
        if not request.POST.get("confirmed"):
            preview = clearance_service.opening_preview(
                actor=request.user, enrollment=enrollment, request=request
            )
            return {**preview, "opened_on": data["opened_on"]}
        clearance = clearance_service.open_clearance(
            actor=request.user,
            enrollment=enrollment,
            opened_on=data["opened_on"],
            request=request,
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("تسجيل غير معروف"))
        return None

    messages.success(request, _("فُتحت براءة الذمة برقم %(code)s") % {"code": clearance.code})
    return redirect("operations:clearance-detail", code=clearance.code)


@require_http_methods(["GET", "POST"])
def clearance_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    if request.method == "POST":
        response = _handle_clearance_step(request, code)
        if response is not None:
            return response

    try:
        clearance = clearance_service.get_clearance(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    approves = policy.is_allowed(request.user, Screen.CLEARANCE, Action.APPROVE)
    is_live = clearance["status"] not in {"COMPLETED", "CANCELLED"}

    return render(
        request,
        "operations/clearance_detail.html",
        {
            "title": _("براءة ذمة"),
            "active_screen": Screen.CLEARANCE,
            "clearance": clearance,
            "custody_form": CustodyForm(
                initial={
                    "items": "\n".join(clearance_service.standard_custody_items(as_of=date.today()))
                }
            ),
            "handover_form": HandoverForm(),
            "cancel_form": ClearanceCancelForm(),
            "deposit_form": DepositSettlementForm(),
            "credit_form": CreditReturnAtClearanceForm(),
            # §6.4 — each step offered to the department it belongs to, and
            # only while the clearance is still live. The DECISION is the
            # service's: comparing the role here as well gave the screen a
            # second copy of «whose step is this», and the two then had to be
            # kept in step by hand — which is exactly how the client's own
            # account came to be refused on screen after the service allowed it.
            "can_custody": approves
            and is_live
            and clearance_service.role_may_take_step(
                actor=request.user, required_role=clearance["custody_role"]
            ),
            "can_certify": approves and is_live,
            "can_second_certify": approves
            and is_live
            and clearance_service.role_may_take_step(
                actor=request.user, required_role=clearance["second_certifier_role"]
            ),
            "can_handover": approves
            and is_live
            and clearance_service.role_may_take_step(
                actor=request.user, required_role=clearance["handover_role"]
            ),
            "can_close": approves and is_live,
            "can_settle_money": policy.is_allowed(request.user, Screen.REFUNDS, Action.CREATE)
            and is_live,
        },
    )


def _handle_clearance_step(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLEARANCE_ACTIONS:
        screen, permission = CLEARANCE_ACTIONS[action]
        policy.require(request.user, screen, permission, request=request)
    try:
        clearance = clearance_service.clearance_instance(
            actor=request.user, code=code, request=request
        )
        handled = _dispatch_clearance(request, clearance, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return redirect("operations:clearance-detail", code=code)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return redirect("operations:clearance-detail", code=code) if handled else None


def _dispatch_clearance(request: HttpRequest, clearance: Any, action: str) -> bool:
    """Run one clearance action. Returns whether anything was done."""
    from apps.billing.services import deposit_service

    if action == "custody":
        custody_form = CustodyForm(request.POST)
        if not custody_form.is_valid():
            messages.error(request, _("يجب ذكر العُهد المُسترجَعة"))
            return True
        clearance_service.complete_custody_step(
            actor=request.user,
            clearance=clearance,
            custody_items=custody_form.cleaned_data["items"],
            request=request,
        )
        messages.success(request, _("أُتمّت الخطوة الأولى"))
    elif action == "certify":
        clearance_service.certify_finance_step(
            actor=request.user, clearance=clearance, request=request
        )
        messages.success(request, _("تمّت المصادقة المالية الأولى"))
    elif action == "second-certify":
        clearance_service.second_certify_finance_step(
            actor=request.user, clearance=clearance, request=request
        )
        messages.success(request, _("تمّت المصادقة الثانية وأُغلقت الخطوة المالية"))
    elif action == "handover":
        handover_form = HandoverForm(request.POST)
        if not handover_form.is_valid():
            messages.error(request, _("اسم مستلم الشهادة إلزامي"))
            return True
        clearance_service.complete_handover_step(
            actor=request.user,
            clearance=clearance,
            participant_ack_name=handover_form.cleaned_data["participant_ack_name"],
            request=request,
        )
        messages.success(request, _("سُجِّل تسليم الشهادة وإقرار المستلِم"))
    elif action == "close":
        clearance_service.close_clearance(actor=request.user, clearance=clearance, request=request)
        messages.success(request, _("أُغلقت براءة الذمة"))
    elif action == "cancel":
        cancel_form = ClearanceCancelForm(request.POST)
        if not cancel_form.is_valid():
            messages.error(request, _("سبب الإلغاء إلزامي"))
            return True
        clearance_service.cancel_clearance(
            actor=request.user,
            clearance=clearance,
            reason_ar=cancel_form.cleaned_data["reason_ar"],
            request=request,
        )
        messages.success(request, _("أُلغيت براءة الذمة"))
    elif action in {"deposit", "forfeit"}:
        _settle_deposit(request, clearance, action, deposit_service)
    elif action == "return-credit":
        credit_form = CreditReturnAtClearanceForm(request.POST)
        if not credit_form.is_valid():
            messages.error(request, _("بيانات ردّ الرصيد غير مكتملة"))
            return True
        clearance_service.return_credit_at_clearance(
            actor=request.user,
            clearance=clearance,
            returned_on=credit_form.cleaned_data["returned_on"],
            code=credit_form.cleaned_data["code"],
            request=request,
        )
        messages.success(request, _("رُدّ الرصيد الدائن"))
    else:
        return False
    return True


def _settle_deposit(
    request: HttpRequest, clearance: Any, action: str, deposit_service: Any
) -> None:
    form = DepositSettlementForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("بيانات تسوية التأمين غير مكتملة"))
        return
    data = form.cleaned_data
    if action == "deposit":
        deposit_service.return_deposit(
            actor=request.user,
            enrollment=clearance.enrollment,
            returned_on=data["returned_on"],
            deduction_amount=data["deduction_amount"] or ZERO,
            deduction_reason_ar=data["deduction_reason_ar"],
            request=request,
        )
        messages.success(request, _("أُعيد التأمين"))
    else:
        deposit_service.forfeit_deposit(
            actor=request.user,
            enrollment=clearance.enrollment,
            reason="CLEARANCE",
            justification_ar=data["deduction_reason_ar"],
            forfeited_on=data["returned_on"],
            request=request,
        )
        messages.success(request, _("صودر التأمين"))


# ---------------------------------------------------------------------------
# Certificates (Screen.CERTIFICATES) — §7
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def certificates_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CERTIFICATES, Action.CREATE)
    form = None
    if can_create:
        form = CertificateIssueForm(
            request.POST if request.POST.get("action") == "issue" else None,
            enrollment_choices=certificate_service.issuable_enrollment_choices(
                actor=request.user, request=request
            ),
            grade_choices=certificate_service.available_grades(as_of=timezone.now().date()),
        )

    if request.method == "POST":
        response = _handle_certificate(request, form)
        if response is not None:
            return response

    return render(
        request,
        "operations/certificates.html",
        {
            "title": _("الشهادات"),
            "active_screen": Screen.CERTIFICATES,
            "certificates": certificate_service.list_certificates(
                actor=request.user, query=request.GET.get("q", "").strip(), request=request
            ),
            "form": form,
            "date_form": CertificateDateForm(),
            "can_create": can_create,
            "can_print": policy.is_allowed(request.user, Screen.CERTIFICATES, Action.PRINT),
            "query": request.GET.get("q", ""),
        },
    )


def _handle_certificate(
    request: HttpRequest, form: CertificateIssueForm | None
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    permission = {
        "issue": Action.CREATE,
        "replace": Action.CREATE,
        "reprint": Action.PRINT,
    }.get(action)
    if permission is None:
        return None
    policy.require(request.user, Screen.CERTIFICATES, permission, request=request)

    try:
        if action == "issue":
            if form is None or not form.is_valid():
                return None
            _issue_certificate(request, form)
        else:
            _certificate_transition(request, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
    return redirect("operations:certificates")


def _issue_certificate(request: HttpRequest, form: CertificateIssueForm) -> None:
    data = form.cleaned_data
    enrollment = enrollment_service.get_enrollment(
        actor=request.user, code=data["enrollment_code"], request=request
    )
    certificate = certificate_service.issue_certificate(
        actor=request.user,
        enrollment=enrollment,
        grade=data["grade"],
        issued_on=data["issued_on"],
        duration_text=data["duration_text"],
        training_hours=data["training_hours"],
        request=request,
    )
    messages.success(request, _("صدرت الشهادة %(n)s") % {"n": certificate.certificate_number})


def _certificate_transition(request: HttpRequest, action: str) -> None:
    certificate = certificate_service.certificate_instance(
        actor=request.user, number=request.POST.get("number", ""), request=request
    )
    if action == "reprint":
        certificate_service.record_reprint(
            actor=request.user, certificate=certificate, request=request
        )
        messages.success(request, _("سُجّلت إعادة الطباعة — الرقم كما هو"))
        return

    date_form = CertificateDateForm(request.POST)
    if not date_form.is_valid():
        messages.error(request, _("تاريخ غير صالح"))
        return
    on_date = date_form.cleaned_data["on_date"]

    # Delivery is not an action here: §6.4 makes it clearance step 3, and
    # ``clearance_service.complete_handover_step`` is what marks a certificate
    # DELIVERED — so the two records cannot disagree.
    replacement = certificate_service.issue_replacement(
        actor=request.user, original=certificate, issued_on=on_date, request=request
    )
    messages.success(request, _("صدر بدل الفاقد %(n)s") % {"n": replacement.certificate_number})


# ---------------------------------------------------------------------------
# Printed documents (Sprint 8C-1)
# ---------------------------------------------------------------------------
# ⚠️ Requirements-based output. The centre's own blank forms were not in the
# client folder, so these follow §6.4 and §7 and carry a printed marker saying
# so until someone verifies them and flips ``document_mode``.
#
# The print route runs the SAME permission check as the screen it prints. A
# document route that read more loosely than its screen would be a way around
# the matrix wearing a printer icon.
def clearance_print_view(request: HttpRequest, code: str) -> HttpResponse:
    """
    The clearance form (§6.4).

    Printable at any stage, deliberately: the form is a checklist a
    participant is handed at the counter, and WORKFLOWS §6.3 wants what is
    still outstanding visible. Completion is shown, not required.
    """
    try:
        document = clearance_service.clearance_document(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(request, "print/clearance_form.html", {"clearance": document})


def certificate_print_view(request: HttpRequest, number: str) -> HttpResponse:
    """
    The certificate, or a replacement (§7).

    BR-075 is not re-checked here and does not need to be: a Certificate row
    only exists because ``issue_certificate`` found a COMPLETED clearance, and
    a replacement only because ``issue_replacement`` found an original whose
    fee had been collected. Printing reads what those refusals already
    permitted — it cannot conjure a certificate that was never issued.
    """
    try:
        document = certificate_service.certificate_document(
            actor=request.user, number=number, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(request, "print/certificate.html", {"certificate": document})


# ---------------------------------------------------------------------------
# The ministry file (Sprint 8G)
# ---------------------------------------------------------------------------
#: Which permission each POST on the MOHE screens needs, checked before the
#: service so a role violation is a 403 and a rule refusal is a message. The
#: split is §3.3's own: the registration officer drafts and attaches, the
#: centre manager sends and records the decision (footnote ⁹).
MOHE_ACTIONS: dict[str, tuple[str, str]] = {
    "attach": (Screen.MOHE_SUBMIT, Action.EDIT),
    "send": (Screen.MOHE_SUBMIT, Action.APPROVE),
    "approve": (Screen.MOHE, Action.APPROVE),
    "reject": (Screen.MOHE, Action.APPROVE),
    "resubmit": (Screen.MOHE_SUBMIT, Action.CREATE),
}


@require_http_methods(["GET"])
def mohe_view(request: HttpRequest) -> HttpResponse:
    """§3.3/14 — every ministry file, whatever its status."""
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    rows = mohe_service.list_submissions(
        actor=request.user, status=status, query=query, request=request
    )
    # The strip above the register counts the whole register, not the slice
    # — a tile that shrank with the filter it opens would count itself.
    everything = (
        rows
        if not (query or status)
        else mohe_service.list_submissions(actor=request.user, request=request)
    )
    base = reverse("operations:mohe")

    def _tile(label: Any, value: int, url: str, icon: str, tone: str, on: bool) -> dict[str, Any]:
        return {"label": label, "value": value, "url": url, "icon": icon, "tone": tone, "on": on}

    tiles = [
        _tile(_("كل الملفات"), len(everything), base, "stamp", "brand", not (query or status)),
        _tile(
            _("مسودات"),
            sum(1 for r in everything if r["status"] == "DRAFT"),
            f"{base}?status=DRAFT",
            "doc-plus",
            "warn",
            status == "DRAFT",
        ),
        _tile(
            _("بانتظار الوزارة"),
            sum(1 for r in everything if r["status"] == "SUBMITTED"),
            f"{base}?status=SUBMITTED",
            "globe",
            "info",
            status == "SUBMITTED",
        ),
        _tile(
            _("معتمدة"),
            sum(1 for r in everything if r["status"] == "APPROVED"),
            f"{base}?status=APPROVED",
            "shield-check",
            "ok",
            status == "APPROVED",
        ),
        # A fifth, because four tiles counted four of the five statuses and
        # «مرفوض» appeared only in a strip below them — the one status that
        # needs an act was the one the strip of tiles hid.
        _tile(
            _("مرفوضة"),
            sum(1 for r in everything if r["status"] == "REJECTED"),
            f"{base}?status=REJECTED",
            "undo",
            "danger",
            status == "REJECTED",
        ),
    ]
    # The next step on a row is drawn from the reader's permission, not from the
    # status alone: an auditor reads this register and holds nothing on
    # MOHE_SUBMIT, and «أكمل الملف» promised them an act that refuses — a
    # promise then a refusal is worse than no button, and it files a
    # DENIED_ATTEMPT the screen itself invited (BR-085).
    can_attach = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.EDIT)
    can_decide = policy.is_allowed(request.user, Screen.MOHE, Action.APPROVE)
    can_resubmit = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE)
    for row in rows:
        status_value = str(row["status"])
        row["may_act"] = (
            can_attach
            if status_value == "DRAFT"
            else can_decide
            if status_value == "SUBMITTED"
            else can_resubmit
            if status_value == "REJECTED"
            else False
        )

    # «المسجّلون» counts the standing; the names below are the APPROVED. The
    # column used to show the first and link to nothing, and the page had to
    # apologise for the gap in a sentence. Each row now carries both numbers,
    # read off the very sections drawn below — so no query of its own.
    name_sections = enrollment_service.list_mohe_name_uploads(
        actor=request.user, request=request
    )
    by_cohort = {s["cohort_code"]: s for s in name_sections}
    for row in rows:
        section = by_cohort.get(row["cohort_code"]) if row["status"] == "APPROVED" else None
        # A button is drawn only where pressing it would show something: an
        # approved file with no approved enrolment yet gets the sentence that
        # explains the gap instead of a control that opens an empty table.
        row["has_names"] = bool(section and section["rows"])
        row["approved_count"] = section["approved_count"] if section else 0
        row["pending_upload_count"] = section["pending_count"] if section else 0
        row["awaits_approved_enrolment"] = bool(section and not section["rows"])

    # The names open on the file's own page, and the link carries the filters
    # so its «رجوع» comes back to this same slice of the register.
    kept = urlencode({k: v for k, v in (("q", query), ("status", status)) if v})

    return render(
        request,
        "operations/mohe.html",
        {
            "title": _("اعتماد الوزارة"),
            "active_screen": Screen.MOHE,
            "submissions": rows,
            "tiles": tiles,
            "can_view_enrollments": policy.is_allowed(
                request.user, Screen.ENROLLMENTS, Action.VIEW
            ),
            "query": query,
            "status": status,
            #: The register's own filters, ready to carry into a link.
            "kept_filters": f"{kept}&" if kept else "",

            # Both filters were already read from the URL and neither was named
            # on screen, so a narrowed list read as the whole register.
            "active_filters": _mohe_active_filters(rows, query, status),
            "can_open_file": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE),
            "can_view_cohorts": policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW),
            "can_export_uploaded_names": policy.is_allowed(request.user, Screen.MOHE, Action.VIEW),
            "mohe_name_sections": name_sections,
        },
    )


@require_http_methods(["POST"])
def mohe_name_upload_view(request: HttpRequest, code: str) -> HttpResponse:
    try:
        enrollment = enrollment_service.get_enrollment(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد تسجيل بهذا الرمز")) from exc

    try:
        enrollment_service.mark_uploaded_to_mohe(
            actor=request.user,
            enrollment=enrollment,
            uploaded_on=timezone.localdate(),
            request=request,
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
    else:
        messages.success(request, _("سُجّل رفع اسم المتدرب للوزارة."))
    return redirect("operations:mohe")


#: CSV export of the names register, encoded so Excel reads Arabic correctly.
#: The BOM in ``utf-8-sig`` is what stops Excel from guessing a codepage.
MOHE_EXPORT_ENCODING = "utf-8-sig"


@require_http_methods(["GET"])
def mohe_uploaded_export_view(request: HttpRequest) -> HttpResponse:
    """
    "Export Uploaded Names" — the trainees already recorded as uploaded.

    The rows come from the service behind the SAME ``Screen.MOHE`` gate as
    the page, so whoever cannot open the register cannot download it either.
    """
    rows = enrollment_service.list_mohe_uploaded_names(actor=request.user, request=request)

    # Written to a buffer and encoded once: streaming rows into an
    # ``HttpResponse`` with this charset would put a BOM in front of every row.
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            _("المتدرب"),
            _("رمز التسجيل"),
            _("البرنامج"),
            _("الدفعة"),
            _("الرقم الوزاري"),
            _("تاريخ اعتماد التسجيل"),
            _("تاريخ رفع الاسم للوزارة"),
            _("مهلة التسجيل في الوزارة"),
        ]
    )
    for row in rows:
        approved_at = row["approved_at"]
        deadline = row["registration_deadline"]
        writer.writerow(
            [
                row["participant_name"],
                row["enrollment_code"],
                row["program_name_ar"],
                row["cohort_code"],
                row["mohe_course_number"],
                timezone.localtime(approved_at).date().isoformat() if approved_at else "",
                row["mohe_uploaded_on"].isoformat(),
                deadline.isoformat() if deadline else "",
            ]
        )

    response = HttpResponse(
        buffer.getvalue().encode(MOHE_EXPORT_ENCODING),
        content_type=f"text/csv; charset={MOHE_EXPORT_ENCODING}",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="mohe-uploaded-names-{timezone.localdate().isoformat()}.csv"'
    )
    return response


#: The three populations a registrar actually carries somewhere. ``PENDING``
#: is the list taken to the ministry's own system; ``UPLOADED`` is the receipt
#: of what was already carried; ``ALL`` is the file as a whole.
MOHE_NAME_SCOPES = ("pending", "uploaded", "all")


def _mohe_name_rows(
    *,
    actor: Any,
    request: HttpRequest,
    scope: str,
    cohort: str,
    query: str,
    status: str,
) -> tuple[list[dict[str, Any]], str]:
    """
    Flat trainee rows for the names export, and the label of what was exported.

    Built from the very sections the register draws, so the download and the
    screen can never disagree, and the ministry number comes from the register
    rows the page already read — no query of its own.
    """
    sections = enrollment_service.list_mohe_name_uploads(actor=actor, request=request)
    numbers = {
        row["cohort_code"]: row["mohe_course_number"]
        for row in mohe_service.list_submissions(
            actor=actor, status=status, query=query, request=request
        )
        if row["status"] == "APPROVED"
    }

    # A cohort named outright wins over the register's filters: the reader
    # pressed a button on one row, not on the strip above it.
    if cohort:
        sections = [s for s in sections if s["cohort_code"] == cohort]
        scope_of = cohort
    else:
        sections = [s for s in sections if s["cohort_code"] in numbers]
        scope_of = _("المعروض") if (query or status) else _("الكل")

    rows: list[dict[str, Any]] = []
    for section in sections:
        for row in section["rows"]:
            uploaded = row["mohe_uploaded_on"] is not None
            if scope == "pending" and uploaded:
                continue
            if scope == "uploaded" and not uploaded:
                continue
            rows.append(
                {
                    **row,
                    "cohort_code": section["cohort_code"],
                    "program_name_ar": section["program_name_ar"],
                    "registration_deadline": section["registration_deadline"],
                    "mohe_course_number": numbers.get(section["cohort_code"], ""),
                }
            )
    return rows, scope_of


def _mohe_names_csv(rows: list[dict[str, Any]], scope: str) -> str:
    """
    The columns each scope actually needs, and no others.

    A «بانتظار الرفع» sheet carried an empty «تاريخ الرفع» column and an
    «حالة الرفع» column that said the same thing on every line — so each
    scope now names only what varies within it.
    """
    header = [
        _("المتدرب"),
        _("رمز التسجيل"),
        _("البرنامج"),
        _("الدفعة"),
        _("الرقم الوزاري"),
        _("تاريخ اعتماد التسجيل"),
    ]
    if scope != "pending":
        header.append(_("تاريخ رفع الاسم للوزارة"))
    if scope == "all":
        header.append(_("حالة الرفع"))
    if scope != "uploaded":
        header.append(_("مهلة التسجيل في الوزارة"))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        approved_at = row["approved_at"]
        uploaded_on = row["mohe_uploaded_on"]
        deadline = row["registration_deadline"]
        line = [
            row["participant_name"],
            row["enrollment_code"],
            row["program_name_ar"],
            row["cohort_code"],
            row["mohe_course_number"],
            timezone.localtime(approved_at).date().isoformat() if approved_at else "",
        ]
        if scope != "pending":
            line.append(uploaded_on.isoformat() if uploaded_on else "")
        if scope == "all":
            line.append(str(_("مرفوع") if uploaded_on else _("بانتظار الرفع")))
        if scope != "uploaded":
            line.append(deadline.isoformat() if deadline else "")
        writer.writerow(line)
    return buffer.getvalue()


@require_http_methods(["GET"])
def mohe_names_export_view(request: HttpRequest) -> HttpResponse:
    """
    The trainees of a ministry file, by the population the reader asked for.

    ``scope`` is one of :data:`MOHE_NAME_SCOPES`; ``cohort`` narrows to one
    file, and without it the register's own ``q``/``status`` filters apply —
    so the download is what the screen was showing when it was pressed.

    The rows come from the service behind the SAME ``Screen.MOHE`` gate as the
    page, so whoever cannot open the register cannot download it either.
    """
    scope = request.GET.get("scope", "all").strip()
    if scope not in MOHE_NAME_SCOPES:
        scope = "all"
    cohort = request.GET.get("cohort", "").strip()

    rows, _scope_of = _mohe_name_rows(
        actor=request.user,
        request=request,
        scope=scope,
        cohort=cohort,
        query=request.GET.get("q", "").strip(),
        status=request.GET.get("status", "").strip(),
    )

    # Written to a buffer and encoded once: streaming rows into an
    # ``HttpResponse`` with this charset would put a BOM in front of every row.
    response = HttpResponse(
        _mohe_names_csv(rows, scope).encode(MOHE_EXPORT_ENCODING),
        content_type=f"text/csv; charset={MOHE_EXPORT_ENCODING}",
    )
    stem = "-".join(part for part in ("mohe-names", scope, cohort) if part)
    response["Content-Disposition"] = (
        f'attachment; filename="{stem}-{timezone.localdate().isoformat()}.csv"'
    )
    return response


def _mohe_active_filters(
    rows: list[dict[str, Any]], query: str, status: str
) -> list[tuple[str, str]]:
    """The filters this request is narrowing by, named for the reader."""
    active: list[tuple[str, str]] = []
    if query:
        active.append((_("بحث"), query))
    if status:
        # The label off the drawn rows, never the stored code — the defect the
        # transfer register was fixed for. A filter matching nothing has no row
        # to read from, and the empty state says «filtered», not «empty».
        labels = {str(row["status"]): str(row["status_display"]) for row in rows}
        active.append((_("الحالة"), labels.get(status, status)))
    return active


@require_http_methods(["GET", "POST"])
def mohe_submit_view(request: HttpRequest) -> HttpResponse:
    """
    §3.3/15 — open a ministry file for a cohort.

    Opens on VIEW and submits on CREATE. The row gives the audit account ``V``
    and withholds ``C``, so the editor is readable by the role that reads
    everything and fillable only by the two that draft.
    """
    policy.require(request.user, Screen.MOHE_SUBMIT, Action.VIEW, request=request)

    asked_for = request.GET.get("cohort", "")
    options = mohe_service.submittable_cohort_options(actor=request.user, request=request)
    form = MoheSubmissionForm(
        request.POST or None,
        # The cohorts register links here with the cohort already chosen.
        initial={"cohort_code": asked_for},
        cohort_choices=[(c["code"], c["label"]) for c in options],
    )

    # A cohort typed into the URL that no longer accepts a file: say which of
    # the two it is rather than answering an empty page to a precise question.
    existing_file = (
        mohe_service.live_file_for_code(
            actor=request.user, cohort_code=asked_for, request=request
        )
        if asked_for
        else None
    )
    unknown_cohort = bool(asked_for) and existing_file is None and not any(
        c["code"] == asked_for for c in options
    )

    if request.method == "POST":
        policy.require(request.user, Screen.MOHE_SUBMIT, Action.CREATE, request=request)
        if form.is_valid():
            try:
                cohort = cohort_service.get_cohort_instance(
                    actor=request.user, code=form.cleaned_data["cohort_code"], request=request
                )
                submission = mohe_service.create_submission(
                    actor=request.user, cohort=cohort, data=form.content(), request=request
                )
            except (DjangoValidationError, ObjectDoesNotExist) as exc:
                messages.error(request, _message_of(exc))
            else:
                messages.success(
                    request,
                    _("فُتح ملف وزاري للدفعة %(code)s — أرفق المستندين ثم أرسله")
                    % {"code": cohort.code},
                )
                return redirect("operations:mohe-detail", submission_id=submission.pk)
        else:
            # The refusal used to be silent: the choices are recomputed on every
            # request, so a cohort that gained a file while this page was open
            # left the list, the field became invalid, and the page came back
            # with no message — and with the seven typed fields gone.
            posted = request.POST.get("cohort_code", "").strip()
            taken = (
                mohe_service.live_file_for_code(
                    actor=request.user, cohort_code=posted, request=request
                )
                if posted
                else None
            )
            if taken:
                existing_file = taken
                messages.error(
                    request,
                    _("تعذّر الحفظ: للدفعة %(code)s ملف وزاري قائم — افتحه بدل فتح ملف ثانٍ.")
                    % {"code": posted},
                )
            else:
                messages.error(request, _("تعذّر الحفظ — راجع الحقول المعلَّمة أدناه."))

    return render(
        request,
        "operations/mohe_submit.html",
        {
            "title": _("نموذج الإرسال للوزارة"),
            "active_screen": Screen.MOHE_SUBMIT,
            "form": form,
            "can_create": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE),
            # The cohort picker draws a summary and pre-fills the trainer and
            # the place; the pick-lists offer what earlier files said.
            "cohorts": options,
            "suggestions": mohe_service.submission_suggestions(actor=request.user, request=request),
            "preselected": asked_for,
            # Arrived with a cohort that already holds a live file: say so and
            # open it, rather than an empty list with no explanation.
            "existing_file": existing_file,
            "unknown_cohort": unknown_cohort,
            # A refused POST keeps the form on screen with what was typed in it,
            # even where the list has meanwhile emptied — the alternative is
            # handing back a blank page for seven fields of work.
            "refused_post": request.method == "POST",
            # The empty state points somewhere, and only where this reader may go.
            "can_view_files": policy.is_allowed(request.user, Screen.MOHE, Action.VIEW),
            "can_view_cohorts": policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW),
        },
    )


@require_http_methods(["GET", "POST"])
def mohe_detail_view(request: HttpRequest, submission_id: int) -> HttpResponse:
    """
    One file: its contents, its documents, and whichever act comes next.

    Part of §3.3/14 rather than a screen of its own — the same permission row
    as the listing it opens from, the way the clearance detail sits under the
    clearance screen.
    """
    if request.method == "POST":
        response = _handle_mohe_action(request, submission_id)
        if response is not None:
            return response

    try:
        submission = mohe_service.get_submission(
            actor=request.user, submission_id=submission_id, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب وزاري بهذا الرقم")) from exc

    may_draft = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.EDIT)
    may_send = policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.APPROVE)
    may_decide = policy.is_allowed(request.user, Screen.MOHE, Action.APPROVE)

    # The trainees of THIS file, paged — fifty names belong on a page with a
    # pager and a print, not in a row of the register or inside a dialog.
    names = next(
        (
            section
            for section in enrollment_service.list_mohe_name_uploads(
                actor=request.user, request=request
            )
            if section["cohort_code"] == submission["cohort_code"]
        ),
        None,
    )
    name_page = page_of(names["rows"], request.GET.get("page", "")) if names else None

    # Back to exactly the slice of the register the reader left, filters and
    # all — ``from=register`` says they came from it rather than from a link.
    came_with = (("q", request.GET.get("q", "")), ("status", request.GET.get("status", "")))
    kept = urlencode({k: v for k, v in came_with if v})
    back_url = reverse("operations:mohe") + (f"?{kept}" if kept else "")
    #: Everything but ``page``, so the pager keeps the return journey intact.
    names_qs = urlencode(
        {
            k: v
            for k, v in (
                ("q", request.GET.get("q", "")),
                ("status", request.GET.get("status", "")),
                ("from", request.GET.get("from", "")),
            )
            if v
        }
    )

    return render(
        request,
        "operations/mohe_detail.html",
        {
            "title": _("الطلب الوزاري"),
            "active_screen": Screen.MOHE,
            "submission": submission,
            # The upload form offers what is still MISSING (BR-016), so the
            # second document cannot be filed under the first one's name by
            # a default that was never changed. Once nothing is missing, the
            # full list is offered again for a replacement.
            "attachment_form": MoheAttachmentForm(
                purpose_choices=[
                    (p["purpose"], p["label"])
                    for p in (submission["missing_attachments"] or submission["required_purposes"])
                ]
            ),
            "send_form": MoheSendForm(initial={"submitted_on": timezone.localdate()}),
            "decision_form": MoheDecisionForm(initial={"decided_on": timezone.localdate()}),
            "resubmission_form": MoheResubmissionForm(initial=submission["content"]),
            "names": names,
            "name_page": name_page,
            "back_url": back_url,
            "names_qs": names_qs,
            "came_from_register": request.GET.get("from", "") == "register",
            "can_view_enrollments": policy.is_allowed(
                request.user, Screen.ENROLLMENTS, Action.VIEW
            ),
            # BR-016 — the send button appears only when both documents are in.
            "can_attach": may_draft and submission["status"] == "DRAFT",
            "can_send": may_send and submission["is_sendable"],
            "send_blocked_by_documents": may_send
            and submission["status"] == "DRAFT"
            and bool(submission["missing_attachments"]),
            "can_decide": may_decide and submission["is_decidable"],
            "can_resubmit": policy.is_allowed(request.user, Screen.MOHE_SUBMIT, Action.CREATE)
            and submission["is_resubmittable"],
            # Where this file stands on the four-stop strip, read off the
            # service's own flags — never recomputed here.
            "stage": (
                4
                if submission["status"] in ("SUBMITTED", "APPROVED", "REJECTED")
                else 3
                if submission["is_sendable"]
                else 2
            ),
        },
    )


def _handle_mohe_action(request: HttpRequest, submission_id: int) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in MOHE_ACTIONS:
        return None

    screen, permission = MOHE_ACTIONS[action]
    policy.require(request.user, screen, permission, request=request)

    try:
        submission = mohe_service.submission_instance(
            actor=request.user, submission_id=submission_id, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب وزاري بهذا الرقم")) from exc

    try:
        created = _run_mohe_action(request, action, submission)
    except (DjangoValidationError, PermissionDenied) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        messages.error(request, _message_of(exc))
        return redirect("operations:mohe-detail", submission_id=submission_id)

    if created is not None:
        return redirect("operations:mohe-detail", submission_id=created)
    return redirect("operations:mohe-detail", submission_id=submission_id)


def _run_mohe_action(request: HttpRequest, action: str, submission: Any) -> int | None:
    """Perform one act and report the id to land on — a new one for a resubmission."""
    if action == "attach":
        attachment_form = MoheAttachmentForm(
            request.POST,
            request.FILES,
            purpose_choices=[(p, str(p)) for p in mohe_service.REQUIRED_ATTACHMENTS],
        )
        if not attachment_form.is_valid():
            messages.error(request, _("اختر نوع المستند وملفاً صالحاً."))
            return None
        mohe_service.attach_document(
            actor=request.user,
            submission=submission,
            purpose=attachment_form.cleaned_data["purpose"],
            upload=attachment_form.cleaned_data["upload"],
            request=request,
        )
        messages.success(request, _("أُرفق المستند."))
        return None

    if action == "send":
        send_form = MoheSendForm(request.POST)
        if not send_form.is_valid():
            messages.error(request, _("تاريخ الإرسال مطلوب."))
            return None
        mohe_service.submit_to_mohe(
            actor=request.user,
            submission=submission,
            submitted_on=send_form.cleaned_data["submitted_on"],
            request=request,
        )
        messages.success(request, _("أُرسل الطلب إلى الوزارة."))
        return None

    if action in {"approve", "reject"}:
        decision_form = MoheDecisionForm(request.POST)
        if not decision_form.is_valid():
            messages.error(request, _("تاريخ القرار مطلوب."))
            return None
        data = decision_form.cleaned_data
        mohe_service.record_decision(
            actor=request.user,
            submission=submission,
            approved=action == "approve",
            decided_on=data["decided_on"],
            mohe_course_number=data["mohe_course_number"],
            registration_deadline=data["registration_deadline"],
            rejection_reason_ar=data["rejection_reason_ar"],
            request=request,
        )
        messages.success(
            request,
            _("سُجّل الاعتماد الوزاري.") if action == "approve" else _("سُجّل الرفض الوزاري."),
        )
        return None

    # resubmit — a NEW file answering the rejection, which stays readable.
    resubmission_form = MoheResubmissionForm(request.POST)
    if not resubmission_form.is_valid():
        messages.error(request, _("راجع حقول النموذج."))
        return None
    fresh = mohe_service.resubmit(
        actor=request.user,
        rejected=submission,
        data=resubmission_form.content(),
        request=request,
    )
    messages.success(request, _("فُتح ملف جديد يردّ على الرفض — أرفق المستندين ثم أرسله."))
    return int(fresh.pk)


# ---------------------------------------------------------------------------
# Transfers (Sprint 8H)
# ---------------------------------------------------------------------------
#: §3.2/6's own split, which is why three different people appear on one
#: record: the registration officer opens the request (``C`` on §3.2/7), the
#: centre manager recommends or refuses it (``A``), and finance settles and
#: executes it (``E`` — footnote ⁶, the fee-difference settlement). The
#: manager holds no ``E`` here and finance holds no ``A``; neither can do the
#: other's step.
TRANSFER_ACTIONS: dict[str, tuple[str, str]] = {
    "recommend": (Screen.TRANSFERS, Action.APPROVE),
    "reject": (Screen.TRANSFERS, Action.APPROVE),
    "execute": (Screen.TRANSFERS, Action.EDIT),
}

#: The request itself, and the exception to it. They are split because the
#: second half belongs to one role: ``TransferRequestForm`` still declares all
#: seven fields and still validates them, so a waiver POSTed by a hand that
#: may not grant one is refused by the service exactly as before.
TRANSFER_REQUEST_FIELDS = (
    "from_enrollment_code",
    "to_cohort_code",
    "reason",
    "requested_on",
    "code",
)
#: And the exception's own two halves: the tick, and the reason that only
#: exists once it has been ticked.
TRANSFER_WAIVER_CHECK = ("grant_category_waiver",)
TRANSFER_WAIVER_WHY = ("category_waiver_reason_ar",)
TRANSFER_WAIVER_FIELDS = TRANSFER_WAIVER_CHECK + TRANSFER_WAIVER_WHY


@require_http_methods(["GET"])
def transfers_view(request: HttpRequest) -> HttpResponse:
    """§3.2/6 — every transfer, whatever stage it has reached."""
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    rows = transfer_service.list_transfers(
        actor=request.user, status=status, query=query, request=request
    )
    # Paginated in the VIEW over the list the service returned, exactly as the
    # enrolment register does: ``list_transfers`` is a read the detail screen
    # and the tests also call, and a limit pushed into it would answer their
    # question wrongly. What this stops is the PAGE drawing every request the
    # centre has ever raised.
    page = page_of(rows, request.GET.get("page", ""))
    rows = page["rows"]
    return render(
        request,
        "operations/transfers.html",
        {
            "title": _("النقل بين الدورات"),
            "active_screen": Screen.TRANSFERS,
            "transfers": rows,
            "page": page,
            # Everything but ``page``, so a pager link keeps the filters.
            "params_qs": urlencode(
                {key: value for key, value in (("q", query), ("status", status)) if value}
            ),
            # Nine: the expander, the code, the participant, from, to, the
            # field, the difference, the status and the actions. Counted here
            # so the empty row and the detail row span what the header draws.
            "column_count": 9,
            "status_counts": _status_counts(rows),
            "status_choices": transfer_service.filterable_status_choices(),
            "active_filters": _transfer_active_filters(query, status),
            "query": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "can_request": policy.is_allowed(request.user, Screen.TRANSFER_NEW, Action.CREATE),
        },
    )


def _transfer_active_filters(query: str, status: str) -> list[tuple[str, str]]:
    """
    The filters narrowing this list, named for the reader.

    Both were already on the screen as controls; what was missing is any sign,
    once they are applied, that the table is a subset. Neither names a field
    the table does not already print.

    The status is named from the vocabulary, not from the rows: a filter that
    matches nothing has no row to read a label off, and «الحالة: EXECUTED» in
    front of a client is the defect the participant registry was fixed for.
    Unknown stays unknown rather than being dressed up as a label.
    """
    active: list[tuple[str, str]] = []
    if query:
        active.append((_("بحث"), query))
    if status:
        labels = dict(transfer_service.filterable_status_choices())
        active.append((_("الحالة"), labels.get(status, status)))
    return active


@require_http_methods(["GET", "POST"])
def transfer_new_view(request: HttpRequest) -> HttpResponse:
    """
    §3.2/7 — raise a transfer request.

    Two POSTs, and the difference matters. ``preview`` runs the rule engine
    and the BR-064 arithmetic and writes nothing, so the operator sees the
    refusal — or the difference the participant will owe — before committing
    anybody to it. ``submit`` is the request itself.

    Opens on VIEW and submits on CREATE, so the audit account can read the
    form it may not file.
    """
    policy.require(request.user, Screen.TRANSFER_NEW, Action.VIEW, request=request)

    # §3.2/6's ``A``, asked here for the same reason ``_transfer_inputs`` asks
    # it on the way in: the waiver is the manager's to grant. It was DRAWN for
    # everybody, so a registrar who ticked it lost a filled form to a 403 page
    # and earned a DENIED_ATTEMPT for using a control the screen offered them
    # (BR-085 · قواعد التحسين §3.4). The check below is untouched; what changes
    # is that the control is no longer put in front of the hand that is refused.
    can_waive = policy.is_allowed(request.user, Screen.TRANSFERS, Action.APPROVE)

    enrollment_choices = transfer_service.transferable_enrollment_choices(
        actor=request.user, request=request
    )
    # The source the operator has already named — from the register's row link
    # on arrival, from their own pick afterwards. ``destination_cohort_choices``
    # has always taken it and nobody was passing it, so the list offered the
    # cohort the participant is already ON and the only answer available was
    # «الدفعة الهدف هي الدفعة نفسها».
    from_code = (
        request.POST.get("from_enrollment_code") or request.GET.get("enrollment", "")
    ).strip()
    cohort_choices = transfer_service.destination_cohort_choices(
        actor=request.user, from_code=from_code, request=request
    )

    initial: dict[str, Any] = {"requested_on": timezone.localdate()}
    prefill = request.GET.get("enrollment", "").strip()
    transferable = {code for code, _label in enrollment_choices}
    if prefill in transferable:
        initial["from_enrollment_code"] = prefill

    form = TransferRequestForm(
        request.POST or None,
        enrollment_choices=enrollment_choices,
        cohort_choices=cohort_choices,
        reason_choices=transfer_service.reason_choices(),
        initial=initial,
    )
    preview = None

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "submit":
            policy.require(request.user, Screen.TRANSFER_NEW, Action.CREATE, request=request)
        if form.is_valid():
            if action == "submit":
                created = _submit_transfer(request, form)
                if created is not None:
                    return redirect("operations:transfer-detail", code=created)
            else:
                preview = _preview_transfer(request, form)

    return render(
        request,
        "operations/transfer_new.html",
        {
            "title": _("طلب نقل جديد"),
            "active_screen": Screen.TRANSFER_NEW,
            "form": form,
            "preview": preview,
            "can_create": policy.is_allowed(request.user, Screen.TRANSFER_NEW, Action.CREATE),
            "can_waive": can_waive,
            # The two halves of the form, so the waiver can be drawn — or not
            # drawn — without the template listing field names it would then
            # own a second copy of.
            "request_fields": TRANSFER_REQUEST_FIELDS,
            "waiver_check": TRANSFER_WAIVER_CHECK,
            "waiver_why": TRANSFER_WAIVER_WHY,
            # Named at the top so a refusal is read rather than hunted for,
            # exactly as the enrolment form does.
            "error_fields": [
                {"label": form[name].label, "id": form[name].auto_id}
                for name in form.fields
                if form.is_bound
                and form[name].errors
                and (can_waive or name not in TRANSFER_WAIVER_FIELDS)
            ],
            # A link that arrives naming an enrolment nobody may transfer says
            # so, rather than silently opening an empty-handed form.
            "unknown_prefill": prefill if prefill and prefill not in transferable else "",
            "has_sources": bool(enrollment_choices),
            "has_destinations": bool(cohort_choices),
        },
    )


def _transfer_inputs(request: HttpRequest, form: TransferRequestForm) -> dict[str, Any]:
    """
    Resolve the two records and settle who granted any waiver.

    The waiver's approver is checked against §3.2/6's ``A`` rather than taken
    on trust from whoever filled the form. C-12 is precisely about this: the
    demo granted the waiver on a dropdown pick with nobody's name on it, and
    ``request_transfer`` still accepts whatever ``waiver_by`` it is handed —
    so the screen refuses a registrar's self-granted exception here, with a
    DENIED_ATTEMPT, instead of storing one.
    """
    data = form.cleaned_data
    waiver_by = None
    if data.get("grant_category_waiver"):
        policy.require(request.user, Screen.TRANSFERS, Action.APPROVE, request=request)
        waiver_by = request.user

    return {
        "from_enrollment": enrollment_service.get_enrollment(
            actor=request.user, code=data["from_enrollment_code"], request=request
        ),
        "to_cohort": cohort_service.get_cohort_instance(
            actor=request.user, code=data["to_cohort_code"], request=request
        ),
        "reason": data["reason"],
        "waiver_by": waiver_by,
        "waiver_reason_ar": data.get("category_waiver_reason_ar", ""),
    }


def _preview_transfer(request: HttpRequest, form: TransferRequestForm) -> dict[str, Any] | None:
    try:
        inputs = _transfer_inputs(request, form)
    except ObjectDoesNotExist as exc:
        messages.error(request, _message_of(exc))
        return None

    return transfer_service.preview_transfer(
        actor=request.user,
        as_of=form.cleaned_data["requested_on"],
        request=request,
        **inputs,
    )


def _submit_transfer(request: HttpRequest, form: TransferRequestForm) -> str | None:
    try:
        inputs = _transfer_inputs(request, form)
        transfer = transfer_service.request_transfer(
            actor=request.user,
            requested_on=form.cleaned_data["requested_on"],
            code=form.cleaned_data["code"],
            request=request,
            **inputs,
        )
    except ObjectDoesNotExist as exc:
        messages.error(request, _message_of(exc))
        return None
    except IntegrityError:
        # ``Transfer.code`` is unique and the operator types it, so a code
        # already used answered a filled form with a 500. The rule is the
        # database's and stays there; what the screen owes is the sentence
        # that says which field to change.
        messages.error(
            request,
            _("الرمز %(code)s مستعمل لطلب آخر — اختر رمزاً غيره.")
            % {"code": form.cleaned_data["code"]},
        )
        return None
    except (DjangoValidationError, *transfer_service.PRICING_ERRORS) as exc:
        # The rule engine's own words, with the reference the centre needs —
        # and the pricing refusals beside them, which are plain Exceptions
        # and answered with a 500 until Sprint 8I found it in the browser.
        messages.error(request, _message_of(exc))
        return None

    messages.success(
        request, _("سُجّل طلب النقل %(code)s — بانتظار تنسيب المدير") % {"code": transfer.code}
    )
    return str(transfer.code)


@require_http_methods(["GET", "POST"])
def transfer_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """§3.2/6 — one transfer, its frozen evidence, and the step it awaits."""
    if request.method == "POST":
        response = _handle_transfer_action(request, code)
        if response is not None:
            return response

    try:
        transfer = transfer_service.get_transfer(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب نقل بهذا الرمز")) from exc

    decides = policy.is_allowed(request.user, Screen.TRANSFERS, Action.APPROVE)
    settles = policy.is_allowed(request.user, Screen.TRANSFERS, Action.EDIT)

    return render(
        request,
        "operations/transfer_detail.html",
        {
            "title": _("طلب نقل"),
            "active_screen": Screen.TRANSFERS,
            "transfer": transfer,
            "reject_form": TransferRejectForm(),
            "execute_form": TransferExecuteForm(initial={"executed_on": timezone.localdate()}),
            "can_recommend": decides and transfer["awaits_manager"],
            "can_reject": decides and not transfer["is_closed"],
            "can_execute": settles and transfer["awaits_finance"],
        },
    )


def _handle_transfer_action(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in TRANSFER_ACTIONS:
        return None

    screen, permission = TRANSFER_ACTIONS[action]
    policy.require(request.user, screen, permission, request=request)

    try:
        transfer = transfer_service.transfer_instance(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404(_("لا يوجد طلب نقل بهذا الرمز")) from exc

    try:
        _run_transfer_action(request, action, transfer)
    except (DjangoValidationError, *transfer_service.PRICING_ERRORS) as exc:
        # Executing re-prices the target course, so the same pricing refusals
        # reach here. A settlement that cannot be priced is a message, not a
        # traceback.
        messages.error(request, _message_of(exc))

    return redirect("operations:transfer-detail", code=code)


def _run_transfer_action(request: HttpRequest, action: str, transfer: Any) -> None:
    if action == "recommend":
        transfer_service.manager_recommend(actor=request.user, transfer=transfer, request=request)
        messages.success(request, _("نُسّب الطلب — بانتظار التسوية المالية."))
        return

    if action == "reject":
        form = TransferRejectForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("سبب الرفض إلزامي."))
            return
        transfer_service.reject_transfer(
            actor=request.user,
            transfer=transfer,
            reason_ar=form.cleaned_data["reason_ar"],
            request=request,
        )
        messages.success(request, _("رُفض طلب النقل."))
        return

    execute_form = TransferExecuteForm(request.POST)
    if not execute_form.is_valid():
        messages.error(request, _("تاريخ التنفيذ ورمز التسجيل الجديد مطلوبان."))
        return
    transfer_service.execute_transfer(
        actor=request.user,
        transfer=transfer,
        executed_on=execute_form.cleaned_data["executed_on"],
        new_code=execute_form.cleaned_data["new_code"],
        request=request,
    )
    messages.success(request, _("نُفِّذ النقل وسُوّيت الرسوم."))


# ---------------------------------------------------------------------------
# Sprint 8K — demo-parity guided screens
# ---------------------------------------------------------------------------
def enroll_flow_view(request: HttpRequest) -> HttpResponse:
    """
    The eight steps of registration and payment — WORKFLOWS §1, read only.

    **The demo's eight, in the demo's order.** This screen was rebuilt once
    around a broader participant lifecycle, which was a different promise: the
    client approved a *registration and payment* map that runs from announcing
    an approved programme to filing the trainee's name with the ministry. What
    happens after approval is real and belongs on the page, but underneath, in
    its own section, not folded into the eight.

    **Nothing here is invented.** Each step names the rules that actually run
    beneath it — BR-013 · BR-016 on the ministry gate, BR-001 on the number,
    BR-020 · BR-022 on the money, BR-025 on the receipt, BR-018 on the approval
    and BR-015 on the ministry deadline — and each links only to the screen the
    reader may actually open.

    **Step 6 does not follow the demo's link.** The demo pointed «تقديم الوصل»
    at the payments screen; in this system ``voucher_received`` is recorded on
    the enrolment, so that is where the reader is sent. A map that points at
    the wrong door is worse than no map.
    """
    policy.require(request.user, Screen.ENROLL_FLOW, Action.VIEW, request=request)

    def _link(screen: str, route: str, label: Any) -> list[dict[str, Any]]:
        """The screen for this step, or nothing — never a door that refuses."""
        if not policy.is_allowed(request.user, screen, Action.VIEW):
            return []
        return [{"url": reverse(route), "label": label}]

    steps: list[dict[str, Any]] = [
        {
            "title": _("الإعلان عن البرنامج المعتمد"),
            "who": _("مدير المركز"),
            "what": _(
                "لا يُعلَن عن دفعة قبل أن تعتمدها الوزارة. الملف الوزاري هو ما يفتح "
                "الباب لكل ما بعده."
            ),
            "before": _("ملف وزاري مكتمل ومُرسَل."),
            "after": _("يصير التسجيل على الدفعة ممكناً."),
            "rules": [
                _("لا تسجيل على دفعة لم تعتمدها الوزارة (BR-013 · D-21)."),
                _("لا يُرسَل الملف بلا الوثيقتين الإلزاميتين: سيرة المدرّب ورخصة الجهة (BR-016)."),
            ],
            "links": _link(Screen.MOHE, "operations:mohe", _("افتح اعتماد الوزارة")),
        },
        {
            "title": _("الطالب يقدّم طلب التحاق"),
            "who": _("موظف التسجيل"),
            "what": _(
                "يُملأ نموذج طلب الالتحاق كاملاً، ويولّد النظام الرقم الجامعي: "
                "السنة (٤) + رمز النوع (١) + التسلسل (٤) — تسع خانات."
            ),
            "before": _("دفعة معتمدة معلنة."),
            "after": _("إنشاء التسجيل على الدفعة."),
            "rules": [
                _(
                    "الرقم دائم ولا يُصحَّح لاحقاً، ويرفض النظام توليده إن لم يكن هناك "
                    "فصل نشط بدل أن يخمّن السنة (BR-001 · BR-002)."
                ),
            ],
            "links": _link(Screen.STUDENT_NEW, "people:participant-new", _("افتح طلب التحاق جديد")),
        },
        {
            "title": _("تنسيب مدير المركز للدائرة المالية"),
            "who": _("مدير المركز"),
            "what": _(
                "يُنشأ التسجيل فيصير «بانتظار الدفع»، وتُحتسب رسومه فتظهر المواد "
                "والبنود التي سيدفع مقابلها."
            ),
            "before": _("طلب التحاق مُسجَّل ودفعة معتمدة."),
            "after": _("يذهب المشارك إلى الصندوق ومعه ما عليه."),
            "rules": [
                _("بوابة الوزارة تُفحص هنا أيضاً: لا تسجيل على دفعة غير معتمدة (BR-013)."),
            ],
            "links": _link(Screen.ENROLLMENTS, "operations:enrollments", _("افتح التسجيلات")),
        },
        {
            "title": _("الطالب يدفع في الدائرة المالية"),
            "who": _("أمين الصندوق، أو الموظف المالي"),
            "what": _(
                "يُقبض المبلغ ويوزّعه النظام على بنود الرسوم ويخزّن التوزيع، فيبقى "
                "مجموع التخصيصات مساوياً لقيمة السند بالفلس."
            ),
            "before": _("تسجيل قائم بحساب محتسب."),
            "after": _("صدور سند القبض."),
            "rules": [
                _(
                    "الدفعة الأولى على دبلوم لها حدّ أدنى تُرفض دونه (BR-020)، وأصله "
                    "«رسم التسجيل + أول مادة». الحدّ إعدادٌ مؤرّخ باسم "
                    "«diploma_minimum_first_payment»، ولبعض الدبلومات حدّ خاص يتجاوزه "
                    "(Q-15). القاعدة تخصّ الدبلومات وحدها، والدفعة الأولى وحدها."
                ),
                _(
                    "مدير المركز لا يستوفي دفعة ولا يُنشئ سند قبض (BR-081 · D-01)، "
                    "وموظف التسجيل لا يقبض نقداً — منعٌ صريح لا نقصٌ في الصلاحية."
                ),
                _("التوزيع على البنود مُخزَّن لا محسوباً عند كل قراءة (BR-022)."),
            ],
            "links": _link(Screen.PAYMENT_NEW, "cashbox:payment-new", _("افتح استيفاء دفعة")),
        },
        {
            "title": _("الطالب يستلم سند القبض"),
            "who": _("أمين الصندوق، أو الموظف المالي"),
            "what": _("يصدر سند القبض برقمه وقيمته وتخصيصاته، ويُسلَّم للمشارك."),
            "before": _("قبض المبلغ."),
            "after": _("يعود المشارك إلى المركز ومعه الوصل."),
            "rules": [
                _(
                    "لا يُعدَّل سند صادر (BR-025): الإلغاء يكتب تخصيصات عكسية ويُبقي "
                    "السند برقمه وقيمته، فيُقرأ الأمر تاريخاً لا رقماً تغيّر وحده."
                ),
            ],
            "links": _link(Screen.PAYMENTS, "cashbox:payments", _("افتح الدفعات وسندات القبض")),
        },
        {
            "title": _("يعود للمركز ويقدّم الوصل"),
            "who": _("موظف التسجيل"),
            "what": _(
                "يستقبل المركز الوصل ويسجّله على التسجيل نفسه إثباتاً للدفع، فيصير "
                "التسجيل «بانتظار اعتماد المركز»."
            ),
            "before": _("سند قبض بيد المشارك."),
            "after": _("يصير اعتماد التسجيل ممكناً."),
            "rules": [
                _("الوصل يُسجَّل على شاشة التسجيلات لا على شاشة الدفعات — هناك يعيش الحقل."),
            ],
            "links": _link(Screen.ENROLLMENTS, "operations:enrollments", _("افتح التسجيلات")),
        },
        {
            "title": _("المركز يعتمد التسجيل"),
            "who": _("مدير المركز"),
            "what": _("يُعتمد التسجيل فيصير «منتظماً» ونافذاً، ويظهر في كشوف الدفعة."),
            "before": _("وصل مُسجَّل على التسجيل."),
            "after": _("رفع الاسم إلى نظام الوزارة."),
            "rules": [
                _(
                    "لا اعتماد قبل تسجيل الوصل (BR-018) — وهذا قيد في قاعدة البيانات "
                    "أيضاً، فالرسالة على الشاشة شرحٌ لا آخر خطّ دفاع."
                ),
            ],
            "links": _link(Screen.ENROLLMENTS, "operations:enrollments", _("افتح التسجيلات")),
        },
        {
            "title": _("رفع اسم المتدرب لنظام الوزارة"),
            "who": _("موظف التسجيل، ومدير المركز يرسل"),
            "what": _(
                "يُرفع اسم المتدرب إلى نظام الوزارة خلال المهلة المسموحة. الإدخال "
                "يدوي مزدوج: لا تكامل تقني مباشر في هذا النطاق."
            ),
            "before": _("تسجيل معتمد."),
            "after": _("انتهاء مسار التسجيل والدفع."),
            "rules": [
                _(
                    "ينبّه النظام قبل انتهاء المهلة الوزارية بعدد أيام يحدده إعداد "
                    "«mohe_deadline_alert_days» (BR-015)."
                ),
            ],
            "links": _link(
                Screen.MOHE_SUBMIT, "operations:mohe-submit", _("افتح نموذج الإرسال للوزارة")
            ),
        },
    ]

    # Everything after the eight. Real, and deliberately kept out of them: the
    # client approved a registration-and-payment map, and a lifecycle wearing
    # its name is a different screen.
    later = [
        {
            "title": _("النقل بين الدورات"),
            "what": _(
                "نقل مشارك إلى دورة أخرى؛ المال ينتقل بقيد عكسي لا بتعديل (BR-060 … BR-066)."
            ),
            "links": _link(Screen.TRANSFERS, "operations:transfers", _("افتح النقل")),
        },
        {
            "title": _("الحالات الخاصة"),
            "what": _("الفصل والترحيل والإحلال والرصيد الدائن، ولكلٍّ قاعدته (BR-067 … BR-071)."),
            "links": _link(
                Screen.SPECIAL_CASES, "operations:special-cases", _("افتح الحالات الخاصة")
            ),
        },
        {
            "title": _("الإقفال اليومي"),
            "what": _("عدّ الصندوق في آخر اليوم، ومن قبض لا يوقّع على عدّه (BR-027 · BR-028)."),
            "links": _link(Screen.CLOSING, "cashbox:closing", _("افتح الإقفال اليومي")),
        },
        {
            "title": _("براءة الذمة"),
            "what": _("ثلاث خطوات لا يُعاد ترتيبها، والرصيد صفر في الاتجاهين (BR-073 · BR-074)."),
            "links": _link(Screen.CLEARANCE, "operations:clearances", _("افتح براءة الذمة")),
        },
        {
            "title": _("الشهادة"),
            "what": _("لا شهادة بلا براءة ذمة مكتملة (BR-075)."),
            "links": _link(Screen.CERTIFICATES, "operations:certificates", _("افتح الشهادات")),
        },
    ]

    return render(
        request,
        "operations/enroll_flow.html",
        {
            "title": _("مسار التسجيل والدفع"),
            "active_screen": Screen.ENROLL_FLOW,
            "steps": steps,
            "later": later,
        },
    )


def special_cases_view(request: HttpRequest) -> HttpResponse:
    """
    The six documented exceptions to the ordinary lifecycle (DATA_MODEL §7.6).

    **What this page had wrong.** It sorted the six types into «built» and
    «declared» and put CANCELLATION in the second bucket. That was false:
    ``special_case_service.cancel_registration`` files a CANCELLATION case,
    the enrolments screen calls it from a confirmation dialog, and cases of
    that type exist in the database today. A page that teaches the rules had
    to be corrected before anything else on it was worth polishing.

    **Two buckets could not tell the truth anyway.** «Built» covered both a
    dismissal — which has a service, a screen and a dialog — and a deferral,
    which has a service no screen calls. A reader who saw one green chip for
    both went looking for the deferral screen. So three buckets, and each
    says exactly how far the type has been carried:

    ``ON_SCREEN``   service + a screen that calls it (dismissal, cancellation)
    ``SERVICE_ONLY``service and tests, no caller in any view (deferral,
                    substitution, credit balance)
    ``TYPE_ONLY``   a ``SpecialCaseType`` value and a CheckConstraint, and no
                    service at all (credit transfer)

    Still a reading screen: no form, no POST, no button that would refuse
    whoever pressed it. What changed is that it now says WHERE the entry points are
    instead of stopping at «not here».
    """
    policy.require(request.user, Screen.SPECIAL_CASES, Action.VIEW, request=request)

    on_screen = _("مبني وله شاشة")
    service_only = _("مبني بلا شاشة")
    type_only = _("نوع مُعرَّف بلا خدمة")

    #: Where a reader who may open it can go and file one. Filtered like every
    #: other link on the page: §3.4 — never send a reader to a refusal.
    can_open_enrollments = policy.is_allowed(request.user, Screen.ENROLLMENTS, Action.VIEW)
    from_enrollments = _("من شاشة التسجيلات")

    cases = [
        {
            "label": _("إلغاء"),
            "reach": on_screen,
            "tone": "ok",
            "where": from_enrollments,
            "where_url": reverse("operations:enrollments") if can_open_enrollments else "",
            "what": _(
                "إلغاء الطلب قبل أن يعتمده المركز. ليس انسحاباً: المتدرّب لم يصر "
                "نشطاً، فلا يستحق الشريك عنه شيئاً ولا تتبعه براءة ذمة. الرسوم "
                "تُبطَل فيتوقف الحساب عن القراءة كدين، والمقبوض لا يُمسّ — يبقى "
                "رصيداً دائناً غير مخصَّص (§5.3)."
            ),
        },
        {
            "label": _("فصل"),
            "reach": on_screen,
            "tone": "ok",
            "where": from_enrollments,
            "where_url": reverse("operations:enrollments") if can_open_enrollments else "",
            "what": _(
                "قرار إداري لا يُتخذ على كلام: مرجع القرار إلزامي في الخدمة وفي "
                "قاعدة البيانات معاً (BR-067). لا استرداد يتبع الفصل، والشريك لا "
                "يستحق عنه شيئاً (BR-045)، وأي رصيد متبقٍ يبقى ديناً يمنع براءة "
                "الذمة (BR-068)."
            ),
        },
        {
            "label": _("ترحيل لدفعة لاحقة"),
            "reach": service_only,
            "tone": "warn",
            "where": _("الخدمة جاهزة، ولا شاشة تستدعيها بعد"),
            "where_url": "",
            "what": _(
                "ينتقل المشارك وماله معاً إلى دفعة لاحقة (BR-069). المال ينتقل كما "
                "ينتقل في النقل: تخصيص عكسي على التسجيل القديم ومثله على الجديد — "
                "لا يُعدَّل قيد ولا يُحذف."
            ),
        },
        {
            "label": _("إحلال"),
            "reach": service_only,
            "tone": "warn",
            "where": _("الخدمة جاهزة، ولا شاشة تستدعيها بعد"),
            "where_url": "",
            "what": _(
                "بديل يأخذ مقعداً شاغراً بانسحاب موثّق، وبلا رسم تسجيل ثانٍ "
                "(BR-070): المقعد دُفع عنه إدارياً مرة، وتحصيل الرسم مجدداً يُحاسب "
                "المركز على عمله مرتين."
            ),
        },
        {
            "label": _("نقل رصيد"),
            "reach": type_only,
            "tone": "",
            "where": _("لا خدمة تُنشئه"),
            "where_url": "",
            "what": _(
                "نقل رصيد بين تسجيلين. النوع مُعرَّف في النموذج ومحميّ بقيد في "
                "قاعدة البيانات، ولا توجد خدمة تُنشئ حالة من هذا النوع بعد."
            ),
        },
        {
            "label": _("رصيد دائن"),
            "reach": service_only,
            "tone": "warn",
            "where": _("الخدمة جاهزة، ولا شاشة تستدعيها بعد"),
            "where_url": "",
            "what": _(
                "يجعل الرصيد الدائن حالةً لها صاحب بدل أن يبقى رقماً سالباً "
                "(BR-071). يُنشأ عن ترحيل أو إحلال أو نقل أرخص، ويُردّ عند براءة "
                "الذمة — فالبراءة لا تُغلق ورصيد المشارك غير صفر (BR-073)."
            ),
        },
    ]

    # The three-way split, said once at the top of the table instead of being
    # left for the reader to derive by scanning six rows. Counted off `cases`
    # above, so the summary and the table can never disagree — and a bucket
    # nobody is in gets no chip rather than a zero.
    coverage = [
        row
        for row in (
            {
                "label": label,
                "tone": tone,
                "count": sum(1 for c in cases if c["reach"] == label),
            }
            for label, tone in ((on_screen, "ok"), (service_only, "warn"), (type_only, ""))
        )
        if row["count"]
    ]

    statuses = [
        (_("قائمة"), _("سُجِّلت ولم تُسوَّ بعد.")),
        (_("مسوّاة"), _("انتهى أثرها المالي والإداري.")),
        (_("ملغاة"), _("أُلغيت الحالة نفسها، ويبقى أثرها في سجل التدقيق.")),
    ]

    # Each neighbouring screen with the sentence that says WHY it is here. A
    # bare row of three buttons made the reader guess which one they wanted.
    links = [
        {"url": reverse(route), "label": label, "why": why}
        for screen, route, label, why in (
            (
                Screen.ENROLLMENTS,
                "operations:enrollments",
                _("التسجيلات"),
                _("منها يُسجَّل الفصل وإلغاء الطلب، من قائمة إجراءات السطر."),
            ),
            (
                Screen.TRANSFERS,
                "operations:transfers",
                _("النقل بين الدورات"),
                _("منه ينشأ الرصيد الدائن حين تكون الدورة الجديدة أرخص."),
            ),
            (
                Screen.CLEARANCE,
                "operations:clearances",
                _("براءة الذمة"),
                _("هناك يُردّ الرصيد الدائن، وهناك يمنعها الدين الذي يتركه الفصل."),
            ),
        )
        if policy.is_allowed(request.user, screen, Action.VIEW)
    ]

    return render(
        request,
        "operations/special_cases.html",
        {
            "title": _("الحالات الخاصة"),
            "active_screen": Screen.SPECIAL_CASES,
            "cases": cases,
            "coverage": coverage,
            "statuses": statuses,
            "links": links,
            "enrollments_url": reverse("operations:enrollments") if can_open_enrollments else "",
        },
    )
