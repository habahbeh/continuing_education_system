"""
Audit service (ADR-010, BR-084 / BR-085).

Services write audit events, never views. That way an event is recorded no
matter who invoked the operation — web UI, API, or a management command.

**Why the chain lock lives on NumberSequence, not on AuditEvent.**

Building a hash chain needs the writers serialised: two concurrent writers must
not both read the same ``prev_hash`` and fork the chain. The obvious way is
``SELECT ... FOR UPDATE`` on the last audit row — but MySQL requires the UPDATE
privilege to take a locking read, and layer 2 of the audit design deliberately
strips UPDATE and DELETE on ``core_auditevent``. The obvious way is therefore
impossible *by construction*, which is the point.

So the chain is serialised through a dedicated ``NumberSequence`` row instead.
That table does allow UPDATE, the lock is just as effective, and the audit table
itself receives exactly one statement per event: a single INSERT carrying its
hash already computed. No UPDATE ever touches it.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import AuditChainBroken
from apps.core.models import AuditEvent, NumberSequence
from apps.core.models.audit import GENESIS_HASH

#: The NumberSequence row used purely as a mutex for chain construction.
CHAIN_LOCK_SCOPE = "audit_chain"
CHAIN_LOCK_PARTITION = "global"


def _acquire_chain_lock() -> None:
    """
    Serialise audit writers.

    Uses get_or_create outside the lock first, then a locking read, for the same
    reason numbering_service does: racing to INSERT the same unique key inside
    the locking transaction deadlocks under load.
    """
    NumberSequence.objects.get_or_create(
        scope=CHAIN_LOCK_SCOPE,
        partition=CHAIN_LOCK_PARTITION,
        defaults={"next_value": 1, "padding": 1, "is_gapless": False},
    )
    # Consuming the lock row's queryset is what takes the row lock.
    list(
        NumberSequence.objects.select_for_update().filter(
            scope=CHAIN_LOCK_SCOPE, partition=CHAIN_LOCK_PARTITION
        )
    )


def write_audit(
    *,
    action: str,
    entity_type: str,
    summary_ar: str,
    actor: Any = None,
    entity_id: str = "",
    reference: str = "",
    changes: dict[str, Any] | None = None,
    denial_rule: str = "",
    request: Any = None,
) -> AuditEvent:
    """
    Append one audit event and link it into the hash chain.

    ``actor_role`` is snapshotted from the user at write time, so changing a
    user's role later does not rewrite history (ADR-012).
    """
    ip_address = None
    user_agent = ""
    if request is not None:
        ip_address = getattr(request, "audit_ip", None)
        user_agent = (getattr(request, "audit_user_agent", "") or "")[:255]
        if actor is None:
            candidate = getattr(request, "user", None)
            if candidate is not None and getattr(candidate, "is_authenticated", False):
                actor = candidate

    actor_role = ""
    if actor is not None:
        actor_role = getattr(actor, "role", "") or ""

    with transaction.atomic():
        _acquire_chain_lock()

        previous = AuditEvent.objects.order_by("-id").values("row_hash").first()
        prev_hash = previous["row_hash"] if previous else GENESIS_HASH

        event = AuditEvent(
            occurred_at=timezone.now(),
            actor=actor,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            reference=reference,
            summary_ar=summary_ar,
            changes=changes,
            denial_rule=denial_rule,
            ip_address=ip_address,
            user_agent=user_agent,
            prev_hash=prev_hash,
        )
        # The hash is computed before the write, so the row goes in complete.
        event.row_hash = event.compute_row_hash()
        event.save()

    return event


def verify_chain(*, limit: int | None = None) -> tuple[int, list[str]]:
    """
    Recompute the chain and report breaks.

    Returns ``(checked_count, problems)``. A non-empty ``problems`` list means
    a row was altered or deleted outside the application.
    """
    problems: list[str] = []
    expected_prev = GENESIS_HASH
    checked = 0

    queryset = AuditEvent.objects.order_by("id")
    if limit is not None:
        queryset = queryset[:limit]

    for event in queryset.iterator():
        checked += 1
        if event.prev_hash != expected_prev:
            problems.append(
                f"id={event.pk}: prev_hash mismatch "
                f"(stored={event.prev_hash[:12]}…, expected={expected_prev[:12]}…)"
            )
        recomputed = event.compute_row_hash()
        if recomputed != event.row_hash:
            problems.append(
                f"id={event.pk}: row_hash mismatch — the row was modified after it was written"
            )
        expected_prev = event.row_hash

    return checked, problems


def assert_chain_intact() -> None:
    """Raise AuditChainBroken if verification finds any problem."""
    checked, problems = verify_chain()
    if problems:
        raise AuditChainBroken(
            f"Audit chain verification failed over {checked} events: " + "; ".join(problems)
        )


__all__ = [
    "CHAIN_LOCK_PARTITION",
    "CHAIN_LOCK_SCOPE",
    "assert_chain_intact",
    "verify_chain",
    "write_audit",
]
