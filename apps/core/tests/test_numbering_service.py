"""Numbering tests (ADR-011) — gapless under concurrency and rollback."""

from __future__ import annotations

import threading

import pytest
from django.db import connection, transaction

from apps.core.models import NumberSequence
from apps.core.services.numbering_service import next_number, peek_number

pytestmark = pytest.mark.django_db


def test_sequence_starts_at_one_and_pads() -> None:
    assert next_number("participant", "2026-3") == "0001"
    assert next_number("participant", "2026-3") == "0002"


def test_partitions_are_independent() -> None:
    assert next_number("participant", "2026-3") == "0001"
    assert next_number("participant", "2026-5") == "0001"
    assert next_number("participant", "2026-3") == "0002"


def test_padding_is_configurable_per_sequence() -> None:
    NumberSequence.objects.create(scope="certificate", partition="2026", next_value=1, padding=6)
    assert next_number("certificate", "2026", prefix="2026") == "2026000001"


def test_ensure_sequence_is_idempotent() -> None:
    from apps.core.services.numbering_service import ensure_sequence

    ensure_sequence("receipt", "2026")
    ensure_sequence("receipt", "2026")
    assert NumberSequence.objects.filter(scope="receipt", partition="2026").count() == 1


def test_peek_does_not_consume() -> None:
    next_number("receipt", "2026")
    assert peek_number("receipt", "2026") == "0002"
    assert peek_number("receipt", "2026") == "0002"
    assert next_number("receipt", "2026") == "0002"


def test_rollback_leaves_no_gap() -> None:
    """
    A failed transaction must roll the counter back with it. A gap in a
    financial sequence is an audit finding, not a cosmetic issue.
    """
    assert next_number("receipt", "2026") == "0001"

    with pytest.raises(RuntimeError), transaction.atomic():
        next_number("receipt", "2026")  # would be 0002
        raise RuntimeError("simulated failure after consuming a number")

    assert next_number("receipt", "2026") == "0002", "the counter did not roll back"


@pytest.mark.django_db(transaction=True)
def test_concurrent_callers_get_unique_numbers_with_no_gaps() -> None:
    """
    100 concurrent calls must yield 100 distinct, contiguous numbers.
    This is the test that would have caught the demo's `array.length + 1`.
    """
    count = 100
    results: list[str] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            value = next_number("receipt", "2026")
            with lock:
                results.append(value)
        except BaseException as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker errors: {errors[:3]}"
    assert len(results) == count
    assert len(set(results)) == count, "duplicate numbers were issued"

    numbers = sorted(int(r) for r in results)
    assert numbers == list(range(1, count + 1)), f"gap detected: {numbers[:10]}…"
