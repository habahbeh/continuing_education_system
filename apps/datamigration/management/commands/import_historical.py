"""
``import_historical`` — read a workbook, report what is in it, archive it.

Dry run is the DEFAULT. Committing takes ``--commit`` and an explicit actor,
because archiving is the decision that a reading of the file is the centre's
history, and a decision needs somebody's name on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.datamigration.services import archive_service, batch_service, validation_service


class Command(BaseCommand):
    help = "استيراد سجلات Excel التاريخية إلى الأرشيف المعزول (Sprint 8D-1)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--file", required=True, help="مسار ملف xlsx")
        parser.add_argument("--code", required=True, help="رمز الدفعة")
        parser.add_argument("--actor", required=True, help="اسم المستخدم المنفِّذ")
        parser.add_argument("--note", default="", help="ملاحظة على الدفعة")
        parser.add_argument(
            "--commit",
            action="store_true",
            help="أرشفة فعلية. بدونه تشغيل تجريبي لا يُنشئ سجلات أرشيف.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.people.models import User

        try:
            actor = User.objects.get(username=options["actor"])
        except User.DoesNotExist as exc:
            raise CommandError(f"مستخدم غير معروف: {options['actor']}") from exc

        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"الملف غير موجود: {path}")

        batch = batch_service.import_workbook(
            actor=actor, path=path, code=options["code"], note_ar=options["note"]
        )
        self.stdout.write(
            f"قُرئ {batch.row_count} صفاً من {batch.sheet_count} ورقة — الدفعة {batch.code}"
        )

        report = validation_service.validate(actor=actor, batch=batch)
        self._print_report(report)

        if not options["commit"]:
            self.stdout.write(
                self.style.WARNING("تشغيل تجريبي — لم يُنشأ أي سجل أرشيف. أضف --commit للأرشفة.")
            )
            return

        summary = archive_service.commit(actor=actor, batch=batch)
        self.stdout.write(
            self.style.SUCCESS(
                f"أُرشف {summary['archived']} صفاً · "
                f"{summary['participants']} مشاركاً · "
                f"{summary['enrollments']} تسجيلاً · "
                f"{summary['payments']} مقبوضاً"
            )
        )
        self.stdout.write("لا أثر مالي — الأرشيف معزول عن الدفتر (A-04 · D-26).")

    def _print_report(self, report: dict[str, Any]) -> None:
        self.stdout.write("")
        self.stdout.write(f"  صالح للأرشفة   {report['archivable']:5}")
        self.stdout.write(f"  بلا رقم صالح   {report['unidentified']:5}")
        self.stdout.write(f"  مرفوض          {report['rejected']:5}")
        mark = "✓" if report["reconciles"] else "✗"
        self.stdout.write(f"  {mark} المطابقة مع عدد الصفوف المقروءة ({report['rows_read']})")
        self.stdout.write("")
        for finding in report["findings"]:
            self.stdout.write(f"  {finding['count']:5}  {finding['label']}")
