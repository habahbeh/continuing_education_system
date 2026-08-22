"""
The closed-period guard (D-23), in one place.

``FinancialPeriod``'s own docstring states the rule: "From Sprint 4 every
financial write validates that its date falls in an OPEN period". Until now
the only implementation of it lived privately inside ``expense_service``, so
every other money movement was on its honour — and Sprint 8D-4's refund payout
duly went in without one.

Promoting it here rather than copying it again is the point. A guard that
exists twice is a guard that will disagree with itself the first time somebody
changes one copy, and "which of our two closed-period rules applies to this
row?" is not a question anybody should have to answer about a closed month.

**Core may hold this** (A-03): it reads ``core.FinancialPeriod`` and nothing
else. The rule is infrastructure — what a closed period MEANS — and each
business service still decides for itself which of its dates the rule applies
to.

**A date with no period is allowed.** Periods are opened as the centre needs
them, and refusing every date outside one would make the system unusable
before the first period is defined. What is refused is a date inside a period
somebody has deliberately closed.

**Two entry points, and money uses the second.** ``period_for`` answers the
question; ``require_open`` answers it and records the refusal. Every dated
money movement calls ``require_open`` (Sprint 8D-6), because "who tried to
post into a month we had already signed off?" is a question the audit trail
has to be able to answer.

What calls it, and on which date
--------------------------------

    take_payment            received_on           cash in
    approve_void            receipt.received_on   the month it CHANGES
    execute_refund          executed_on           cash out
    return_credit           returned_on           cash out
    return_deposit          returned_on           cash out
    forfeit_deposit         forfeited_on          liability becomes revenue
    expense record          incurred_on           cash out
    pay_refund_due          paid_on               cash out
    reverse_refund_payout   reversed_on           the correction's own date

``approve_void`` is the one that does not use its own date, and the exception
is the rule working rather than an oversight: a void has no date: its
reversing allocations hang off the receipt being cancelled, so voiding an
October receipt in November takes October's money out of October's report. It
is the month being CHANGED that has to be open.

What deliberately does NOT call it
----------------------------------

**Charge lines.** A charge is an accrual, not a cash movement, and one path
creates them historically ON PURPOSE: ``opening_balance_service.post`` dates
its line ``as_of``, which is 2022 for a debt carried in from the workbooks.
Guarding ``charged_on`` would make posting an opening balance impossible the
moment the centre closes an old period — the opposite of what the archive
exists for. Whether ENROLMENT charges should be guarded is a genuine question
and a wider one: it would refuse enrolling anybody into a closed month, which
is a business decision about how the centre works and not a technical tidy-up.

**Discounts.** A ``Discount`` reduces ``total_due`` and has no posting date at
all. Its only date is ``president_approval_date`` — when BR-030's signature
happened, not when the reduction hit the account. Guarding that would refuse a
waiver because a signature fell in a closed month: the wrong field answering
the wrong question. Giving ``Discount`` a real posting date is a schema change
AND an accounting decision about which month a waiver belongs to.

Both omissions have tests that fail if somebody wires the guard in without
having had that conversation — see ``test_charge_creation_is_deliberately_not_
guarded`` and ``test_discounts_are_deliberately_not_guarded``. They are
recorded decisions, not gaps waiting to be found.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.exceptions import ValidationError


class ClosedPeriodError(ValidationError):
    """D-23 — a financial movement dated into a period that has been closed."""


def period_for(on_date: date, *, what_ar: str = "حركة مالية") -> Any:
    """
    The period covering this date, or None — and a refusal if it is closed.

    ``what_ar`` names the movement in the message, because "the period is
    closed" tells the user nothing about which of the three dates on their
    form is the problem.
    """
    from apps.core.models import FinancialPeriod, FinancialPeriodStatus

    period = FinancialPeriod.objects.filter(starts_on__lte=on_date, ends_on__gte=on_date).first()
    if period is not None and period.status == FinancialPeriodStatus.CLOSED:
        raise ClosedPeriodError(
            f"الفترة المالية {period} مقفلة — لا تُقيَّد فيها {what_ar} بتاريخ {on_date}."
        )
    return period


def require_open(
    on_date: date,
    *,
    actor: Any = None,
    what_ar: str = "حركة مالية",
    entity_type: str = "core.FinancialPeriod",
    reference: str = "",
    request: Any = None,
) -> Any:
    """
    The guard every dated money movement calls (Sprint 8D-6).

    Same refusal as ``period_for``, plus the audit row. A closed period turns
    an ordinary act away, and a turned-away attempt on a closed month is
    exactly the kind of thing somebody asks about six months later — "who
    tried to post into September after we signed it off?" ``period_for``
    alone could not answer that.

    **Called BEFORE any transaction opens.** The audit row is written and then
    the exception is raised; inside an atomic block the rollback would take
    the evidence with it, which is the defect BR-085 exists to prevent. Every
    caller in this codebase runs it among its guards, never among its writes.

    ``actor`` is optional so a read-only caller can ask the question without
    inventing a user, but a movement always passes one.
    """
    from apps.core.services.audit_service import write_audit

    try:
        return period_for(on_date, what_ar=what_ar)
    except ClosedPeriodError as closed:
        if actor is not None:
            write_audit(
                action="DENIED_ATTEMPT",
                entity_type=entity_type,
                entity_id="",
                reference=reference,
                summary_ar=f"محاولة {what_ar} بتاريخ {on_date} في فترة مالية مقفلة",
                actor=actor,
                denial_rule="D-23",
                changes={"movement_date": on_date.isoformat(), "movement": what_ar},
                request=request,
            )
        raise closed


def is_open(on_date: date) -> bool:
    """The same question without the exception, for a screen deciding a button."""
    try:
        period_for(on_date)
    except ClosedPeriodError:
        return False
    return True


__all__ = ["ClosedPeriodError", "is_open", "period_for", "require_open"]
