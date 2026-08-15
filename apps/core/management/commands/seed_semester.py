"""
Create or activate an academic semester (BR-001).

Sprint 2B needs one because the participant number takes its year and type
digit from the ACTIVE semester, and a database with no active semester cannot
issue numbers at all — deliberately, since the alternative is a permanent
number built on a guessed year (T-287).

Idempotent: re-running with the same code activates that semester and
deactivates the others, without creating duplicates.

    python manage.py seed_semester --code 2026-1 --type 1 \
        --academic-year 2026/2027 --starts 2026-09-01 --ends 2027-01-15
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Semester
from apps.core.models.semester import SemesterType

TYPE_NAMES = {1: "الفصل الأول", 2: "الفصل الثاني", 3: "الفصل الصيفي"}


class Command(BaseCommand):
    help = "Create or activate an academic semester (BR-001)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--code", required=True, help="e.g. 2026-1")
        parser.add_argument("--type", type=int, required=True, choices=[1, 2, 3])
        parser.add_argument("--academic-year", required=True, help="e.g. 2026/2027")
        parser.add_argument("--starts", type=date.fromisoformat, required=True)
        parser.add_argument("--ends", type=date.fromisoformat, required=True)
        parser.add_argument("--name", default="", help="Arabic name; derived if omitted")
        parser.add_argument(
            "--inactive",
            action="store_true",
            help="Create without activating (does not touch the current active semester).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        year: str = options["academic_year"].strip()
        if len(year) < 4 or not year[:4].isdigit():
            raise CommandError(
                f"--academic-year must start with four digits (got {year!r}). "
                "The first four become the leading digits of every participant "
                "number issued this semester (BR-001)."
            )

        type_code: int = options["type"]
        if options["ends"] <= options["starts"]:
            raise CommandError("--ends must fall after --starts.")

        name = options["name"] or f"{TYPE_NAMES[type_code]} {year}"
        activate = not options["inactive"]

        semester, created = Semester.objects.get_or_create(
            code=options["code"],
            defaults={
                "name_ar": name,
                "type_code": SemesterType(type_code),
                "academic_year": year,
                "starts_on": options["starts"],
                "ends_on": options["ends"],
            },
        )

        if activate:
            # Only one semester may be active (core_semester_single_active), so
            # stand the others down first.
            Semester.objects.exclude(pk=semester.pk).filter(is_active=True).update(
                is_active=True, active_flag=None
            )
            Semester.objects.exclude(pk=semester.pk).update(is_active=False, active_flag=None)
            semester.is_active = True
            semester.save()

        verb = "أُنشئ" if created else "موجود"
        state = "نشط" if semester.is_active else "غير نشط"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: {semester.code} — {semester.name_ar} "
                f"(النوع {semester.type_code} · {semester.academic_year}) — {state}"
            )
        )
        self.stdout.write(
            f"أرقام المشاركين ستبدأ بـ {year[:4]}{type_code}… (وطلاب المركز بـ {year[:4]}5…)"
        )
