"""
Financial periods behind a screen — D-23, DATA_MODEL §3.6, Q-18.

**Found by walking a fresh install.** ``core/services/period_service.py`` is
the guard every dated money movement calls: a payment, a refund payout, a
returned or forfeited deposit and an expense each ask ``require_open`` whether
their date falls in a period somebody has closed. Nine call sites, one rule —
and **no screen anywhere could create a period or close one.** The Django admin
was the only road, and it writes past the audit trail that a closed month is
later defended with.

The effect was quiet rather than loud, which is worse. ``period_for`` returns
``None`` for a date in no period at all and the movement is allowed — a
deliberate decision so the system is usable before the first period is defined.
So on a live database with no periods, **every month stays open forever**, a
backdated receipt is accepted into a month whose report was already issued and
approved, and nothing on any screen says so.

**This module writes periods; it does not decide them.** Whether a date is
refused stays entirely in ``core.services.period_service`` (A-03 keeps that
rule in infrastructure, where every app can reach it). What lives here is the
administrative act — open a period, close a period — and the permission and
audit row that act needs.

**The settings permission, and no new matrix cell.** PERMISSIONS.md §3.7/35
gives the centre manager ``V E P`` over settings. That grant is the reason this
screen hangs off SETTINGS rather than off the till's row (§3.4/18): closing a
month is not a cash act, and whoever counts the cash is not who signs the month
off — the same separation BR-027/BR-028 makes inside the daily closing.

**Overlapping periods are refused here.** Nothing in the schema forbids them —
``UniqueConstraint(starts_on)`` stops two periods STARTING on one day and
nothing stops one containing another. But ``period_for`` resolves a date with
``.filter(...).first()`` under ``ordering = ["-starts_on"]``, so a date inside
two periods is answered by whichever started later: one of them closed and the
other open would make the same receipt legal or illegal depending on a row
ordering nobody chose. The rule belongs to whoever writes the period, so it is
enforced on the way in.

**Re-opening a closed period is deliberately not offered.** It is the one act
that would undo the protection the rest of the module exists for, and who may
sign a month back open is a question for the centre and not for this file. A
close therefore asks for confirmation and says what it refuses, before it
happens rather than after.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.models import FinancialPeriod, FinancialPeriodStatus
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "core.FinancialPeriod"

#: Signing a month off is an administrative act over the system, not a till act
#: — see the module docstring. Named here so the screen and its tests agree.
SCREEN = Screen.SETTINGS

#: What a closed period turns away, in the reader's words. Taken from the call
#: sites listed in ``core.services.period_service``: the confirmation has to say
#: what it is about to refuse, because afterwards is too late to ask.
REFUSES: tuple[str, ...] = (
    "سندات القبض بتاريخٍ في الفترة",
    "إلغاء سند قبض قُبض فيها",
    "تنفيذ الاستردادات وردّ الفوائض",
    "ردّ التأمينات ومصادرتها",
    "تسجيل المصروفات",
)


def period_rows(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    Every period, newest first, with what a reader needs to judge it.

    ``covers_today`` and ``gap_before`` are computed rather than stored.
    A period that leaves a day uncovered between itself and the one before it
    is a day no close will ever reach — and a gap is invisible in a list of
    date pairs that each look perfectly reasonable on their own.
    """
    policy.require(actor, SCREEN, Action.VIEW, request=request)

    today = timezone.localdate()
    periods = list(
        FinancialPeriod.objects.select_related("closed_by").order_by("starts_on")
    )

    rows: list[dict[str, Any]] = []
    previous_end: date | None = None
    for period in periods:
        gap = bool(previous_end and period.starts_on > previous_end + timedelta(days=1))
        rows.append(
            {
                "id": period.pk,
                "starts_on": period.starts_on,
                "ends_on": period.ends_on,
                "status": period.status,
                "status_label": str(period.get_status_display()),
                "is_open": period.status == FinancialPeriodStatus.OPEN,
                "closed_by": getattr(period.closed_by, "username", ""),
                "closed_at": period.closed_at,
                "covers_today": period.starts_on <= today <= period.ends_on,
                "days": (period.ends_on - period.starts_on).days + 1,
                "gap_before": gap,
                "gap_from": previous_end + timedelta(days=1) if gap and previous_end else None,
                "gap_to": period.starts_on - timedelta(days=1) if gap else None,
            }
        )
        previous_end = max(previous_end, period.ends_on) if previous_end else period.ends_on

    rows.reverse()
    return rows


def today_is_covered(*, actor: Any, request: Any = None) -> bool:
    """
    Whether today falls inside any period at all.

    ``False`` is not a refusal — a date in no period is allowed on purpose — it
    is the state in which the guard governs nothing, which is the state a fresh
    install is in and the one nothing on any screen used to mention.
    """
    policy.require(actor, SCREEN, Action.VIEW, request=request)
    today = timezone.localdate()
    return FinancialPeriod.objects.filter(starts_on__lte=today, ends_on__gte=today).exists()


def suggested_range() -> dict[str, date]:
    """
    The dates the new-period form opens on: the calendar month after the last.

    Periods are kept monthly in practice, and the whole point of the suggestion
    is that the commonest act — «open next month» — needs no arithmetic done in
    the reader's head, which is where an off-by-one day becomes a day no period
    covers.
    """
    last = FinancialPeriod.objects.order_by("-ends_on").values_list("ends_on", flat=True).first()
    start = (last + timedelta(days=1)) if last else timezone.localdate().replace(day=1)
    return {"starts_on": start, "ends_on": _end_of_month(start)}


def _end_of_month(day: date) -> date:
    """The last day of ``day``'s month, without a calendar dependency."""
    first_of_next = (
        date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    )
    return first_of_next - timedelta(days=1)


def create_period(*, actor: Any, data: dict[str, Any], request: Any = None) -> FinancialPeriod:
    """A new period, audited, and refused if it crosses one that exists."""
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    starts_on: date = data["starts_on"]
    ends_on: date = data["ends_on"]
    _refuse_overlap(starts_on=starts_on, ends_on=ends_on)

    with transaction.atomic():
        period = FinancialPeriod(
            starts_on=starts_on, ends_on=ends_on, status=FinancialPeriodStatus.OPEN
        )
        period.full_clean()
        period.save()
        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(period.pk),
            reference=str(starts_on),
            summary_ar=f"فتح فترة مالية — {starts_on} إلى {ends_on}",
            actor=actor,
            request=request,
        )
    return period


def _refuse_overlap(*, starts_on: date, ends_on: date, exclude_pk: int | None = None) -> None:
    """See the module docstring — a date inside two periods has no answer."""
    clash = FinancialPeriod.objects.filter(starts_on__lte=ends_on, ends_on__gte=starts_on)
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    found = clash.order_by("starts_on").first()
    if found is not None:
        raise ValidationError(
            {
                "starts_on": ValidationError(
                    f"تتقاطع مع الفترة {found.starts_on} — {found.ends_on}. "
                    "ولا تقع تاريخٌ واحد في فترتين: الحارس يقرأ إحداهما ولا "
                    "يُعرَف أيّهما (D-23)."
                )
            }
        )


def close_period(*, actor: Any, period: FinancialPeriod, request: Any = None) -> FinancialPeriod:
    """
    Sign the period off — after which every dated movement in it is refused.

    ``closed_by`` and ``closed_at`` are written with the status because the
    check constraint ``core_period_closed_requires_closer`` will not have it
    any other way: a closed month with nobody's name on it is a month nobody
    signed.
    """
    policy.require(actor, SCREEN, Action.EDIT, request=request)

    if period.status == FinancialPeriodStatus.CLOSED:
        raise ValidationError(
            f"الفترة {period.starts_on} — {period.ends_on} مقفلة سلفاً."
        )

    with transaction.atomic():
        period.status = FinancialPeriodStatus.CLOSED
        period.closed_by = actor
        period.closed_at = timezone.now()
        period.full_clean()
        period.save()
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(period.pk),
            reference=str(period.starts_on),
            summary_ar=f"إقفال فترة مالية — {period.starts_on} إلى {period.ends_on}",
            actor=actor,
            changes={"status": {"from": "OPEN", "to": "CLOSED"}},
            request=request,
        )
    return period


def period_instance(*, actor: Any, period_id: str, request: Any = None) -> FinancialPeriod:
    """The row itself, for handing back into this module."""
    policy.require(actor, SCREEN, Action.VIEW, request=request)
    return FinancialPeriod.objects.select_related("closed_by").get(pk=int(period_id))


__all__ = [
    "ENTITY",
    "REFUSES",
    "SCREEN",
    "close_period",
    "create_period",
    "period_instance",
    "period_rows",
    "suggested_range",
    "today_is_covered",
]
