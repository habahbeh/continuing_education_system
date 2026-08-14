"""Tests for the audit verification management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.core.models import AuditAction, AuditEvent
from apps.core.services.audit_service import write_audit

pytestmark = pytest.mark.django_db


def _write(n: int) -> list[AuditEvent]:
    return [
        write_audit(
            action=AuditAction.CREATE,
            entity_type="semester",
            entity_id=str(i),
            summary_ar=f"قيد رقم {i}",
        )
        for i in range(n)
    ]


class TestVerifyAuditChain:
    def test_reports_success_on_an_intact_chain(self) -> None:
        _write(5)
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        assert "سليمة" in out.getvalue()
        assert "5" in out.getvalue()

    def test_succeeds_on_an_empty_chain(self) -> None:
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        assert "سليمة" in out.getvalue()

    def test_respects_the_limit_option(self) -> None:
        _write(10)
        out = StringIO()
        call_command("verify_audit_chain", limit=3, stdout=out)
        assert "3" in out.getvalue()

    def test_fails_loudly_when_a_row_was_tampered_with(self) -> None:
        """The command must exit non-zero, not merely print a note."""
        events = _write(6)
        AuditEvent.objects.filter(pk=events[3].pk).update(summary_ar="نص مزوَّر")

        out, err = StringIO(), StringIO()
        with pytest.raises(CommandError, match="مكسورة"):
            call_command("verify_audit_chain", stdout=out, stderr=err)
        assert "row_hash mismatch" in out.getvalue()


class TestVerifyAuditGrants:
    def test_runs_and_reports_the_current_user(self) -> None:
        out = StringIO()
        call_command("verify_audit_grants", stdout=out)
        output = out.getvalue()
        assert "المستخدم:" in output

    def test_reports_either_a_pass_or_an_actionable_revoke_hint(self) -> None:
        """
        On a fresh development database the grant has usually not been narrowed
        yet. Either outcome is acceptable, but the command must say which and,
        when the grant is too wide, print the exact REVOKE to run.
        """
        out = StringIO()
        call_command("verify_audit_grants", stdout=out)
        output = out.getvalue()

        passed = "لا صلاحية UPDATE/DELETE" in output
        warned = "الطبقة الثانية غير مفعّلة" in output
        assert passed or warned, f"unexpected output: {output}"

        if warned:
            assert "REVOKE UPDATE, DELETE" in output
            assert "core_auditevent" in output
