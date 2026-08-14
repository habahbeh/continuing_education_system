"""
Verify database-level audit protection (ADR-010, layer 2).

The application DB user must hold only INSERT and SELECT on core_auditevent.
If it can UPDATE or DELETE, layer 2 is absent and only the application layer
stands between an operator and a rewritten audit trail.

This reports rather than blocks boot: on a fresh development database the
grant has usually not been narrowed yet, and refusing to start would obstruct
setup. In production it belongs in the deployment checklist.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection

TABLE = "core_auditevent"
FORBIDDEN = {"UPDATE", "DELETE"}


class Command(BaseCommand):
    help = "Check that the app DB user cannot UPDATE or DELETE audit rows."

    def handle(self, *args: Any, **options: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            current_user = cursor.fetchone()[0]
            cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = [row[0] for row in cursor.fetchall()]

        self.stdout.write(f"المستخدم: {current_user}")

        risky = [
            g
            for g in grants
            if any(p in g.upper() for p in FORBIDDEN)
            and (TABLE in g or ".*" in g or "ON *.*" in g.upper())
        ]

        if not risky:
            self.stdout.write(self.style.SUCCESS(f"✓ لا صلاحية UPDATE/DELETE على {TABLE}."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"⚠ المستخدم يملك صلاحيات تعديل قد تشمل {TABLE} — الطبقة الثانية غير مفعّلة:"
            )
        )
        for g in risky:
            self.stdout.write(f"    {g}")
        self.stdout.write("")
        self.stdout.write("لتفعيلها بعد انتهاء الهجرات، نفّذ كـ root:")
        self.stdout.write(
            f"    REVOKE UPDATE, DELETE ON continuing_education.{TABLE} FROM 'ce_app'@'localhost';"
        )
