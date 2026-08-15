"""
Read-only browsing of the audit trail (PERMISSIONS.md row 34, D-11).

This module has no write path and must never grow one. BR-084 makes the audit
log append-only for every role without exception, and Sprint 1 backs that with
database privileges — the application user holds no UPDATE or DELETE on
``core_auditevent``. A "correction" feature here would be refused by MySQL
anyway; the point is that it must not exist to be attempted.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from apps.core.models import AuditEvent
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: A single page of history. The audit table grows without bound, so an
#: unbounded query here becomes a slow scan the day it matters most.
PAGE_SIZE = 200


def browse_audit(
    *,
    actor: Any,
    action: str = "",
    actor_username: str = "",
    entity_type: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    request: Any = None,
) -> list[AuditEvent]:
    policy.require(actor, Screen.AUDIT, Action.VIEW, request=request)

    rows = AuditEvent.objects.select_related("actor").order_by("-occurred_at", "-id")

    if action:
        rows = rows.filter(action=action)
    if actor_username:
        rows = rows.filter(actor__username__icontains=actor_username)
    if entity_type:
        rows = rows.filter(entity_type__icontains=entity_type)
    if date_from:
        rows = rows.filter(occurred_at__date__gte=date_from)
    if date_to:
        rows = rows.filter(occurred_at__date__lte=date_to)

    return list(rows[:PAGE_SIZE])


def distinct_actions() -> list[str]:
    """Action codes actually present, for the filter dropdown."""
    return sorted(AuditEvent.objects.values_list("action", flat=True).distinct())


__all__ = ["PAGE_SIZE", "browse_audit", "distinct_actions"]
