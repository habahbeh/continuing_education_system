"""
The daily sweep — Q-16's status job and BR-015's deadline alert.

Sprint 5 built the ``is_payment_overdue()`` PREDICATE and deliberately stopped
there, because the job that acts on it belongs to this sprint. It is imported
rather than reimplemented: two copies of "what counts as overdue" would drift,
and the one that drifts is the one that decides what a partner earns (BR-045).

⚠️ **Q-16 is still an assumption, not a client decision.** The grace period is
``payment_overdue_days``, and everything this job does follows from it. The
sweep is deliberately reversible in both directions — an enrolment that pays
up returns to ACTIVE — so a wrong grace period costs a setting change and a
re-run, not a data repair.

A MANUAL override with a stated reason is never touched. The finance officer
who flagged an enrolment with "bounced cheque" outranks the arithmetic, and a
nightly job silently clearing that flag would be the job overruling a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db import transaction

from apps.core.services.audit_service import write_audit
from apps.operations.models import Enrollment, EnrollmentStatus, EnrollmentStatusHistory
from apps.operations.services import mohe_service
from apps.settlements.services import assumptions, entitlement_service

ENTITY = "operations.Enrollment"

#: The statuses the sweep may move OUT of. Anything final, withdrawn or
#: dismissed is somebody's documented decision and not the job's business.
SWEEPABLE = frozenset({EnrollmentStatus.ACTIVE, EnrollmentStatus.PAYMENT_OVERDUE})


@dataclass
class DailyRunReport:
    """What the run did, for the command to print and a test to assert on."""

    as_of: date
    grace_days: int
    marked_overdue: list[str] = field(default_factory=list)
    restored_to_active: list[str] = field(default_factory=list)
    skipped_manual_override: list[str] = field(default_factory=list)
    deadline_alerts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.marked_overdue) + len(self.restored_to_active)


def _has_manual_override(enrollment: Enrollment) -> bool:
    """Q-16 — a human flag with a stated reason, which the job must not undo."""
    return enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE and bool(enrollment.status_note_ar)


@transaction.atomic
def run_daily(*, actor: Any, as_of: date, request: Any = None) -> DailyRunReport:
    """
    WORKFLOWS §1.2 T4 and T5, plus the BR-015 alert.

    T4 moves ACTIVE → PAYMENT_OVERDUE; T5 moves it back when the balance is
    settled. Both write status history, because a partner's entitlement turns
    on the status and "the system did it overnight" still has to be a row
    someone can read.
    """
    report = DailyRunReport(as_of=as_of, grace_days=assumptions.overdue_days(as_of=as_of))

    candidates = (
        Enrollment.objects.filter(status__in=SWEEPABLE)
        .select_related("cohort", "participant")
        .order_by("code")
    )

    for enrollment in candidates:
        # A human decision is respected without even asking the arithmetic.
        # Consulting the predicate first would be pointless: it honours the
        # override too, so the job would simply agree with itself.
        if _has_manual_override(enrollment):
            report.skipped_manual_override.append(enrollment.code)
            continue

        overdue = entitlement_service.is_payment_overdue(enrollment, as_of=as_of)

        if overdue and enrollment.status == EnrollmentStatus.ACTIVE:
            _move(actor, enrollment, EnrollmentStatus.PAYMENT_OVERDUE, as_of, request)
            report.marked_overdue.append(enrollment.code)

        elif not overdue and enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE:
            _move(actor, enrollment, EnrollmentStatus.ACTIVE, as_of, request)
            report.restored_to_active.append(enrollment.code)

    report.deadline_alerts = mohe_service.deadline_alerts(as_of=as_of)
    return report


def _move(actor: Any, enrollment: Enrollment, to_status: str, as_of: date, request: Any) -> None:
    from django.utils import timezone

    from_status = enrollment.status
    reason = (
        f"مهمة يومية {as_of} — تجاوز مهلة {assumptions.overdue_days(as_of=as_of)} يوماً (Q-16)"
        if to_status == EnrollmentStatus.PAYMENT_OVERDUE
        else f"مهمة يومية {as_of} — سُوِّي الرصيد"
    )

    enrollment.status = to_status
    enrollment.status_changed_at = timezone.now()
    # ``status_note_ar`` describes the CURRENT status and is written only by a
    # human, so the job CLEARS it rather than writing its own reason there.
    #
    # Both halves matter. Writing a job reason would be indistinguishable from
    # an override (the predicate reads any note on a PAYMENT_OVERDUE enrolment
    # as "a human decided this") and the job could never undo its own flag.
    # Leaving a stale note behind is just as bad: the note left over from
    # "اعتماد التسجيل" would be read as an override the moment the job flagged
    # the enrolment. The job's own reason lives in the history row and the
    # audit event, which is where reasons belong.
    enrollment.status_note_ar = ""
    enrollment.save(update_fields=["status", "status_changed_at", "status_note_ar"])

    EnrollmentStatusHistory.objects.create(
        enrollment=enrollment,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor,
        reason_ar=reason[:255],
        reference=f"DAILY-{as_of:%Y%m%d}",
    )
    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(enrollment.pk),
        reference=enrollment.code,
        summary_ar=f"مهمة يومية: {from_status} ← {to_status}",
        actor=actor,
        changes={"from": from_status, "to": to_status, "as_of": as_of.isoformat()},
        request=request,
    )


__all__ = ["SWEEPABLE", "DailyRunReport", "run_daily"]
