"""
Gapless numbering service (ADR-011, BR-001 / BR-076 / Q-03).

The counter row is locked with SELECT ... FOR UPDATE inside the SAME
transaction that creates the record it numbers. Two consequences:

* Two concurrent callers cannot receive the same number.
* If the surrounding transaction fails, the counter rolls back with it, so no
  gap appears. A gap in a financial sequence is an audit finding.

Because the lock is held until the transaction commits, the transaction must
stay SHORT: no file uploads, no PDF generation, no network calls inside it.

**Concurrency design.** Two phases, deliberately separated:

1. *Ensure the row exists.* Doing this inside the locking transaction makes
   N callers race to INSERT the same unique key, and the resulting mix of
   insert-intention locks and FOR UPDATE locks deadlocks under load — measured
   at 100 concurrent callers, not theorised.
2. *Lock and increment.* Once the row exists, contention is an ordinary
   row-lock queue, which InnoDB serialises cleanly.

Even then a deadlock remains possible under heavy contention; MySQL's own
guidance is that applications must be prepared to retry error 1213. The retry
below is safe precisely because each attempt is atomic: a failed attempt
increments nothing.
"""

from __future__ import annotations

import random
import time

from django.db import OperationalError, connection, transaction

from apps.core.exceptions import SequenceExhausted
from apps.core.models import NumberSequence

#: Guard against a runaway counter producing a number wider than its padding.
MAX_SEQUENCE_VALUE = 10**15

#: MySQL error codes worth retrying: deadlock, and lock-wait timeout.
_RETRYABLE_MYSQL_ERRORS = {1213, 1205}

MAX_RETRIES = 8
_BASE_BACKOFF_SECONDS = 0.005


def _is_retryable(exc: OperationalError) -> bool:
    code = exc.args[0] if exc.args else None
    return code in _RETRYABLE_MYSQL_ERRORS


def ensure_sequence(scope: str, partition: str, *, padding: int = 4) -> None:
    """
    Make sure the counter row exists, tolerating a concurrent creator.

    Losing the insert race is expected and harmless: the winner's row is the
    one everybody then locks.

    **Why INSERT IGNORE rather than catch-and-recover.**

    The original implementation created the row and caught the IntegrityError
    a concurrent winner produces. That works when this runs on its own — but
    callers reach it through ``next_number`` INSIDE their own transaction, and
    there a failed statement poisons the connection: every later query raises
    TransactionManagementError until the block unwinds, including the recovery
    query meant to confirm the winner's row. The recovery could not run in the
    exact situation it existed for.

    It surfaces only when several callers hit a BRAND-NEW partition at once —
    the first participants of a new semester, several clerks at once, which is
    precisely the busiest moment of the term.

    ``bulk_create(ignore_conflicts=True)`` emits ``INSERT IGNORE``: the
    duplicate is discarded by MySQL without raising, so nothing is poisoned and
    the row is guaranteed to exist afterwards either way.

    Callers should still invoke this BEFORE opening their transaction when the
    partition may be new: the row is then committed before anyone locks it,
    which keeps concurrent callers out of the insert-intention / FOR UPDATE
    contention entirely. Inside a transaction it is safe but not free.
    """
    if NumberSequence.objects.filter(scope=scope, partition=partition).exists():
        return

    NumberSequence.objects.bulk_create(
        [
            NumberSequence(
                scope=scope,
                partition=partition,
                next_value=1,
                padding=padding,
                is_gapless=True,
            )
        ],
        ignore_conflicts=True,
    )

    if not NumberSequence.objects.filter(scope=scope, partition=partition).exists():
        # Neither we nor anyone else created it — a real fault, not a race.
        raise SequenceExhausted(f"Could not create the counter row for {scope}/{partition}.")


def peek_number(scope: str, partition: str) -> str | None:
    """Return the next number WITHOUT consuming it (for previews)."""
    seq = NumberSequence.objects.filter(scope=scope, partition=partition).first()
    if seq is None:
        return None
    return _format(seq, seq.next_value)


def next_number(scope: str, partition: str, *, prefix: str = "", padding: int = 4) -> str:
    """
    Consume and return the next number for ``(scope, partition)``.

    MUST be called inside the caller's transaction so that a rollback undoes
    the increment. ``transaction.atomic`` here nests as a savepoint when the
    caller already opened one.

    **Retrying is only ours to do when we own the outermost transaction.**

    A MySQL deadlock (1213) or lock-wait timeout (1205) aborts the ENTIRE
    transaction, not the savepoint. If a caller opened it, retrying here runs
    the next attempt inside a transaction MySQL has already rolled back: every
    statement then raises TransactionManagementError, and the caller sees that
    instead of the deadlock — the same failure, wearing a disguise that leads
    the reader away from the cause.

    So when the caller owns the transaction the error propagates untouched, and
    the caller retries its whole unit of work. That is also what MySQL's own
    guidance says to do.
    """
    ensure_sequence(scope, partition, padding=padding)

    caller_owns_transaction = connection.in_atomic_block
    last_error: OperationalError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with transaction.atomic():
                seq = NumberSequence.objects.select_for_update().get(
                    scope=scope, partition=partition
                )
                if seq.next_value >= MAX_SEQUENCE_VALUE:
                    raise SequenceExhausted(f"Sequence {scope}/{partition} is exhausted.")

                value = seq.next_value
                seq.next_value = value + 1
                seq.save(update_fields=["next_value"])

            return f"{prefix}{_format(seq, value)}"

        except OperationalError as exc:
            if not _is_retryable(exc) or caller_owns_transaction:
                raise
            last_error = exc
            # Exponential backoff with jitter so retries do not resynchronise.
            time.sleep(_BASE_BACKOFF_SECONDS * (2**attempt) * (0.5 + random.random()))

    raise OperationalError(
        f"Could not obtain a number for {scope}/{partition} after {MAX_RETRIES} attempts."
    ) from last_error


def _format(seq: NumberSequence, value: int) -> str:
    return str(value).zfill(seq.padding)


__all__ = ["MAX_RETRIES", "MAX_SEQUENCE_VALUE", "ensure_sequence", "next_number", "peek_number"]
