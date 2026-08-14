"""
Verify the audit hash chain (ADR-010, layer 3).

Run on a schedule. A break means a row was altered or deleted outside the
application — which the application layer alone cannot detect.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.services.audit_service import verify_chain


class Command(BaseCommand):
    help = "Recompute and verify the AuditEvent hash chain."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit", type=int, default=None, help="Check the first N events only."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        checked, problems = verify_chain(limit=options["limit"])

        if not problems:
            self.stdout.write(self.style.SUCCESS(f"✓ سلسلة التدقيق سليمة عبر {checked} قيداً."))
            return

        for problem in problems:
            self.stdout.write(self.style.ERROR(f"  ✗ {problem}"))
        raise CommandError(f"سلسلة التدقيق مكسورة: {len(problems)} مشكلة عبر {checked} قيداً.")
