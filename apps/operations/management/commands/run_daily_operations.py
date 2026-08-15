"""
The daily operations job (IMPLEMENTATION_PLAN Sprint 6).

Two things happen each night: enrolments cross the payment-overdue line in
either direction (Q-16), and cohorts approach or pass their ministry
registration deadline (BR-015).

Run it on a schedule. ``--as-of`` and ``--dry-run`` exist because the grace
period is an unsettled assumption: seeing what a different date or a changed
setting WOULD do, before it does it, is the difference between a reversible
decision and an overnight surprise.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.operations.services.daily_service import run_daily
from apps.people.models import Role, User


class Command(BaseCommand):
    help = "Sweep payment-overdue statuses (Q-16) and report ministry deadlines (BR-015)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--as-of",
            type=str,
            default=None,
            help="Run as if today were this date (YYYY-MM-DD). Defaults to today.",
        )
        parser.add_argument(
            "--actor",
            type=str,
            default=None,
            help="Username to attribute the status changes to. Defaults to a system admin.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and print, then roll everything back.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        as_of = self._parse_date(options["as_of"])
        actor = self._resolve_actor(options["actor"])

        if options["dry_run"]:
            # A dry run still has to WRITE to see what the writes would be —
            # the sweep reads a balance that its own changes do not affect,
            # but the history rows are the output worth previewing.
            try:
                with transaction.atomic():
                    report = run_daily(actor=actor, as_of=as_of)
                    self._print(report, dry_run=True)
                    raise _RollbackError
            except _RollbackError:
                return
        else:
            report = run_daily(actor=actor, as_of=as_of)
            self._print(report, dry_run=False)

    def _parse_date(self, raw: str | None) -> date:
        if not raw:
            return date.today()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError("صيغة التاريخ يجب أن تكون YYYY-MM-DD.") from exc

    def _resolve_actor(self, username: str | None) -> User:
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f"لا مستخدم باسم {username}.") from exc

        actor = (
            User.objects.filter(role=Role.SYSTEM_ADMINISTRATOR, is_active=True)
            .order_by("pk")
            .first()
        )
        if actor is None:
            # Every status change names who made it. A job with no attributable
            # actor would write history rows nobody can be asked about.
            raise CommandError(
                "لا مستخدم بدور مدير النظام لتُنسب إليه التغييرات — حدِّد --actor صراحةً."
            )
        return actor

    def _print(self, report: Any, *, dry_run: bool) -> None:
        prefix = "[تجريبي] " if dry_run else ""
        self.stdout.write(
            f"{prefix}المهمة اليومية {report.as_of} — "
            f"المهلة {report.grace_days} يوماً (Q-16 · افتراض)"
        )

        for code in report.marked_overdue:
            self.stdout.write(self.style.WARNING(f"  ⚠ {code} ← متأخر عن الدفع"))
        for code in report.restored_to_active:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {code} ← منتظم (سُوِّي الرصيد)"))
        for code in report.skipped_manual_override:
            self.stdout.write(f"  ⏸ {code} — تجاوز يدوي بمبرر، لم يُمسّ")

        for alert in report.deadline_alerts:
            style = self.style.ERROR if alert["severity"] == "EXPIRED" else self.style.WARNING
            wording = (
                f"انتهت المهلة الوزارية في {alert['deadline']}"
                if alert["severity"] == "EXPIRED"
                else f"تبقّى {alert['days_left']} يوماً على المهلة الوزارية"
            )
            self.stdout.write(style(f"  ⏳ {alert['cohort_code']} — {wording}"))

        if not report.changed and not report.deadline_alerts:
            self.stdout.write(self.style.SUCCESS("  لا تغييرات ولا تنبيهات."))
        else:
            self.stdout.write(
                f"{prefix}تم: {report.changed} تغيير حالة · "
                f"{len(report.deadline_alerts)} تنبيه مهلة."
            )


class _RollbackError(Exception):
    """Internal signal used to roll a dry run back."""
