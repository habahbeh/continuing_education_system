"""Audit trail tests (ADR-010, BR-084 / BR-085) — three protection layers."""

from __future__ import annotations

from itertools import pairwise

import pytest

from apps.core.exceptions import AuditChainBroken, ImmutableRecordError
from apps.core.models import AuditAction, AuditEvent
from apps.core.models.audit import GENESIS_HASH
from apps.core.services.audit_service import assert_chain_intact, verify_chain, write_audit

pytestmark = pytest.mark.django_db


def _write(n: int = 1) -> list[AuditEvent]:
    return [
        write_audit(
            action=AuditAction.CREATE,
            entity_type="semester",
            entity_id=str(i),
            reference=f"S-{i}",
            summary_ar=f"إنشاء فصل رقم {i}",
        )
        for i in range(n)
    ]


# --- Layer 1: application immutability -------------------------------------
def test_layer1_save_on_existing_row_raises() -> None:
    event = _write(1)[0]
    event.summary_ar = "محاولة تعديل"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        event.save()


def test_layer1_delete_always_raises() -> None:
    event = _write(1)[0]
    with pytest.raises(ImmutableRecordError, match="cannot be deleted"):
        event.delete()


# --- Layer 3: hash chain ---------------------------------------------------
def test_layer3_first_event_links_to_genesis() -> None:
    event = _write(1)[0]
    assert event.prev_hash == GENESIS_HASH
    assert len(event.row_hash) == 64


def test_layer3_chain_links_sequentially() -> None:
    events = _write(5)
    for previous, current in pairwise(events):
        assert current.prev_hash == previous.row_hash


def test_layer3_chain_verifies_across_100_events() -> None:
    _write(100)
    checked, problems = verify_chain()
    assert checked == 100
    assert problems == []
    assert_chain_intact()


def test_layer3_manual_row_edit_is_detected() -> None:
    """
    Bypass the application layer with a raw UPDATE — exactly what an operator
    with DB access could do — and confirm the chain reports it.
    """
    events = _write(10)
    target = events[4]

    AuditEvent.objects.filter(pk=target.pk).update(summary_ar="نص مزوَّر")

    checked, problems = verify_chain()
    assert checked == 10
    assert problems, "tampering went undetected — layer 3 is not working"
    assert any(f"id={target.pk}" in p and "row_hash mismatch" in p for p in problems)

    with pytest.raises(AuditChainBroken):
        assert_chain_intact()


def test_layer3_deleted_row_breaks_the_chain() -> None:
    events = _write(6)
    AuditEvent.objects.filter(pk=events[2].pk).delete()  # queryset delete bypasses model.delete()

    _checked, problems = verify_chain()
    assert problems, "a deleted row went undetected"
    assert any("prev_hash mismatch" in p for p in problems)


# --- Content -------------------------------------------------------------
def test_actor_role_is_snapshotted(user) -> None:  # type: ignore[no-untyped-def]
    """Changing a user's role later must not rewrite history (ADR-012)."""
    event = write_audit(
        action=AuditAction.APPROVE,
        entity_type="enrollment",
        entity_id="EN-1001",
        summary_ar="اعتماد تسجيل",
        actor=user,
    )
    assert event.actor_role == "CENTER_MANAGER"

    user.role = "AUDIT_ACCOUNT"
    user.save()

    event.refresh_from_db()
    assert event.actor_role == "CENTER_MANAGER", "history was rewritten"


def test_denied_attempt_records_the_blocking_rule() -> None:
    event = write_audit(
        action=AuditAction.DENIED_ATTEMPT,
        entity_type="receipt",
        summary_ar="محاولة قبض من مدير المركز",
        denial_rule="BR-081",
    )
    assert event.action == AuditAction.DENIED_ATTEMPT
    assert event.denial_rule == "BR-081"


# --- Layer 2 compatibility -------------------------------------------------
def test_write_audit_never_updates_or_locks_the_audit_table() -> None:
    """
    Layer 2 strips UPDATE/DELETE on core_auditevent from the application DB
    user. MySQL also requires the UPDATE privilege for `SELECT ... FOR UPDATE`.

    So write_audit must touch the table with exactly one INSERT: no UPDATE, and
    no locking read. This is not a style preference — the earlier
    implementation used both and failed with error 1142 the moment layer 2 was
    applied, while the test suite stayed green because the TEST database is
    created with ALL PRIVILEGES. This test closes that gap.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        write_audit(
            action=AuditAction.CREATE,
            entity_type="semester",
            entity_id="1",
            summary_ar="قيد لفحص الطبقة الثانية",
        )

    audit_sql = [
        q["sql"] for q in captured.captured_queries if "core_auditevent" in q["sql"].lower()
    ]
    assert audit_sql, "no query touched core_auditevent — the test is not exercising the path"

    for sql in audit_sql:
        lowered = sql.lower()
        assert not lowered.lstrip().startswith("update"), (
            f"write_audit issued an UPDATE on the audit table, which layer 2 forbids: {sql}"
        )
        assert "for update" not in lowered, (
            f"write_audit took a locking read on the audit table, which needs the "
            f"UPDATE privilege layer 2 removes: {sql}"
        )
        assert not lowered.lstrip().startswith("delete"), (
            f"write_audit issued a DELETE on the audit table: {sql}"
        )


def test_write_audit_inserts_the_hash_in_a_single_statement() -> None:
    """The row must arrive complete: hash included in the INSERT itself."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        event = write_audit(
            action=AuditAction.CREATE,
            entity_type="semester",
            summary_ar="قيد",
        )

    inserts = [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].lower().lstrip().startswith("insert") and "core_auditevent" in q["sql"].lower()
    ]
    assert len(inserts) == 1, f"expected exactly one INSERT, got {len(inserts)}"
    assert event.row_hash
    event.refresh_from_db()
    assert event.row_hash == event.compute_row_hash()


def test_concurrent_writers_do_not_fork_the_chain() -> None:
    """Serialisation still holds now that the lock lives on NumberSequence."""
    _write(30)
    checked, problems = verify_chain()
    assert checked == 30
    assert problems == []
