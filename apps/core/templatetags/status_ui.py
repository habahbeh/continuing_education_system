"""
One presentation decision: which tone a status chip wears.

Every list screen in the system prints ``{{ row.status_display }}`` inside a
bare ``.chip``, so the most-scanned column on the busiest screens — the state
of a settlement, an agreement, a receipt, a migration batch — rendered in the
same neutral grey whether the record was signed, refused or still waiting for
somebody. The colour variants existed and were used for balances only.

The services already project the raw ``status`` beside ``status_display``
(``partner_service`` line 93, ``settlement_service`` line 348, and so on), so
the mapping needs no new query, no new context key and no service change.

**Unknown means grey.** Anything not listed returns the empty string, which is
exactly what every chip renders today. This table can only add signal; it
cannot invent one. Colour is never the only carrier either — the chip keeps
its Arabic label, so a status is still readable in monochrome and to anyone
who does not distinguish these hues.

A-03: imports Django and nothing else.
"""

from __future__ import annotations

from django import template

register = template.Library()

#: Reached its good end, or is validly in force.
_OK = frozenset(
    {
        "ACTIVE", "APPROVED", "COMPLETED", "SETTLED", "PAID", "SIGNED",
        "ISSUED", "DELIVERED", "POSTED", "RECONCILED", "VALIDATED", "VALID",
        "EXECUTED", "RECOVERED", "COMMITTED", "APPLIED", "REVIEWED",
    }
)

#: Waiting on a person. These are the rows a working day is spent clearing.
_WARN = frozenset(
    {
        "DRAFT", "OPEN", "PENDING_APPROVAL", "PENDING_FINANCE",
        "PENDING_MANAGER", "PENDING_MOHE", "SUBMITTED", "REQUESTED",
        "IN_PROGRESS", "PLANNED", "RECORDED", "RAW", "INCOMPLETE",
        "VARIANCE_PENDING", "REFUND_DUE", "PARTIALLY_RECOVERED", "CALCULATED",
    }
)

#: Refused, void, or overdue — none of these clear themselves.
_DANGER = frozenset(
    {
        "REJECTED", "MOHE_REJECTED", "CANCELLED", "CANCELLED_LOW_ENROLLMENT",
        "TERMINATED", "VOIDED", "EXPIRED", "BLOCKED", "DISMISSED",
        "WITHDRAWN", "REF_ERROR", "VALUE_ERROR", "UNIDENTIFIED",
        "PAYMENT_OVERDUE", "NOT_ATTENDED",
    }
)

#: Superseded or moved on: not a problem, not an achievement.
_INFO = frozenset(
    {
        "ARCHIVED", "SUPERSEDED", "REPLACED", "TRANSFERRED_OUT", "REFUNDED",
        "WAIVED", "DEFERRED", "RUNNING",
    }
)


@register.filter
def status_tone(status: object) -> str:
    """
    Map a raw status value to a `.chip` modifier: ok / warn / danger / info.

    Returns "" for anything unrecognised, which renders the neutral chip the
    templates already produce. Use on the RAW status, never on the display
    label — the label is translated prose and is not a stable key.
    """
    if not status:
        return ""
    return (
        "ok"
        if (key := str(status).upper()) in _OK
        else "warn"
        if key in _WARN
        else "danger"
        if key in _DANGER
        else "info"
        if key in _INFO
        else ""
    )
