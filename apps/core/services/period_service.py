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


def is_open(on_date: date) -> bool:
    """The same question without the exception, for a screen deciding a button."""
    try:
        period_for(on_date)
    except ClosedPeriodError:
        return False
    return True


__all__ = ["ClosedPeriodError", "is_open", "period_for"]
