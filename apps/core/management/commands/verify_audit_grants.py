"""
Verify database-level audit protection (ADR-010, layer 2) — BOTH halves.

Layer 2 works by revoking UPDATE/DELETE across the schema and re-granting them
on every table EXCEPT the audit table. That makes it two claims, and this
command checks both: the audit table is protected, AND no other table was left
behind by a migration that ran after the grants were last applied.

The application DB user must hold only INSERT and SELECT on core_auditevent.
If it can UPDATE or DELETE, layer 2 is absent and only the application layer
stands between an operator and a rewritten audit trail.

This reports rather than blocks boot: on a fresh development database the
grant has usually not been narrowed yet, and refusing to start would obstruct
setup. In production it belongs in the deployment checklist.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
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
            self._check_other_tables_are_writable(grants)
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

    def _check_other_tables_are_writable(self, grants: list[str]) -> None:
        """
        The other half of layer 2 — and the half that used to fail silently.

        Protection works by revoking UPDATE/DELETE for the whole schema and
        re-granting them table by table, skipping the audit table. A table
        created by a LATER migration therefore has no grant at all and is
        INSERT-only to the application: rows go in, edits raise error 1142 at
        runtime, and no test catches it because the test database is created
        with ALL PRIVILEGES.

        Nine tables sat in that state across Sprints 2B and 3 while this
        command still reported success, because it only ever looked at the
        audit table. A green check that inspects one table and implies the
        rest is worse than no check.
        """
        granted = set()
        for grant in grants:
            if "UPDATE" not in grant.upper():
                continue
            # ... ON `continuing_education`.`table_name` TO ...
            if "`.`" in grant:
                granted.add(grant.split("`.`")[1].split("`")[0])

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
            )
            all_tables = {row[0] for row in cursor.fetchall()}

        missing = sorted(all_tables - granted - {TABLE})
        if not missing:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ كل الجداول الأخرى ({len(all_tables) - 1}) تملك UPDATE/DELETE."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.ERROR(
                f"✗ {len(missing)} جدولاً بلا صلاحية UPDATE/DELETE — "
                "الكتابة تنجح والتعديل يفشل وقت التشغيل (خطأ 1142):"
            )
        )
        for table in missing:
            self.stdout.write(f"    {table}")
        self.stdout.write("")
        self.stdout.write("السبب المعتاد: هجرة أنشأت جدولاً ولم يُعَد تشغيل سكربت الصلاحيات.")
        self.stdout.write("العلاج — كـ root بعد كل هجرة تُنشئ جدولاً:")
        self.stdout.write("    mysql --socket=/tmp/mysql_ce84.sock -u root continuing_education \\")
        self.stdout.write("      < scripts/apply_audit_grants.sql")
        raise CommandError(f"{len(missing)} جدولاً بلا صلاحيات كتابة كاملة.")
