"""
Demo catalogue and pricing data — **seed data, never a business rule**.

Everything this command writes is an ordinary row the client can add to, edit,
disable or delete from the screens, without a migration and without a
developer. Nothing here is referenced by name anywhere in the code.

**What it seeds and why that set:**

* The deposit policy the client supplied on 2026-08-15 to unblock Sprint 3
  (Q-29): General English, 25 JOD, non-refundable after confirmation and
  refundable only if the centre cancels. ONE real policy, which is what
  end-to-end testing of the deposit path needs.
* The fee exceptions named in QA_CHECKLIST T-096…T-099 — network engineering
  at 20, CMA at 170, JCPA/PMP/drug-registration at no fee, internal quality
  audit free for university students. These are the documented ACCEPTANCE
  CRITERIA, so seeding them is what makes those tests meaningful rather than
  self-referential.
* The BR-006 worked example (interior design: 1700 − 50 = 1650 across four
  subjects) and the BR-011 levelled courses (English 90/level).

**What it does NOT seed:** the full catalogue — 5 diplomas, 28 short courses,
13 online courses and every fee exception. That data lives with the client and
has not been supplied. Seeding invented rows would put fictional prices in
front of users who cannot tell them from real ones.

    python manage.py seed_catalog_demo            # idempotent
    python manage.py seed_catalog_demo --approve  # also approve the list
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import (
    CourseCategory,
    DepositPolicy,
    KnowledgeField,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
    RegistrationFeeRule,
    Subject,
)
from apps.core.models import Semester

#: The client's demo policy (2026-08-15). A row, not a rule.
DEMO_DEPOSIT: dict[str, Any] = {
    "code": "DEP-ENG-GEN",
    "name_ar": "تأمين دورة اللغة الإنجليزية العامة",
    "is_required": True,
    # Free text by design: Q-30 is open and refund_trigger carries no CHECK,
    # so the client can introduce a trigger the code has never heard of.
    "refund_trigger": "ON_CENTRE_CANCELLATION",
    "forfeit_on": ["CONFIRMED"],
    "is_taxable": False,
    "allows_partial_deduction": False,
    "claim_deadline_days": None,
    "notes_ar": (
        "غير مسترد بعد تأكيد التسجيل · ويُسترد كاملاً إذا ألغى المركز الدورة. "
        "بيانات تجريبية من العميل 2026-08-15 لفتح Sprint 3 — قابلة للتعديل والحذف."
    ),
}

CATEGORIES = [
    ("CAT-LANG", "اللغات"),
    ("CAT-IT", "تكنولوجيا المعلومات"),
    ("CAT-BUS", "الأعمال والإدارة"),
    ("CAT-ART", "الفنون والتصميم"),
    ("CAT-HLTH", "العلوم الصحية"),
]

KNOWLEDGE_FIELDS = [
    ("KF-IT", "تكنولوجيا المعلومات"),
    ("KF-HLTH", "العلوم الصحية"),
    ("KF-BUS", "الأعمال والإدارة"),
    ("KF-ART", "الفنون والتصميم"),
    ("KF-LANG", "اللغات"),
]

#: (code, type, name, category, leveled, levels, consumables)
PROGRAMS: list[tuple[str, str, str, str | None, bool, int | None, str]] = [
    ("DIP-ID", ProgramType.DIPLOMA, "دبلوم التصميم الداخلي", None, False, None, "50.000"),
    (
        "SC-ENG-GEN",
        ProgramType.SHORT_COURSE,
        "دورة اللغة الإنجليزية العامة",
        "CAT-LANG",
        True,
        8,
        "0.000",
    ),
    ("SC-NET", ProgramType.SHORT_COURSE, "هندسة الشبكات", "CAT-IT", False, None, "0.000"),
    ("SC-CMA", ProgramType.SHORT_COURSE, "CMA", "CAT-BUS", False, None, "0.000"),
    ("SC-JCPA", ProgramType.SHORT_COURSE, "JCPA", "CAT-BUS", False, None, "0.000"),
    ("SC-PMP", ProgramType.SHORT_COURSE, "PMP", "CAT-BUS", False, None, "0.000"),
    ("SC-DRUG", ProgramType.SHORT_COURSE, "تسجيل الدواء", "CAT-HLTH", False, None, "0.000"),
    ("SC-QA", ProgramType.SHORT_COURSE, "التدقيق الداخلي للجودة", "CAT-BUS", False, None, "0.000"),
    ("ON-GENAI", ProgramType.ONLINE_COURSE, "Generative AI", None, False, None, "0.000"),
]

#: BR-006 worked example: 1700 − 50 consumables = 1650 across four subjects.
INTERIOR_DESIGN_SUBJECTS = [
    (1, "أساسيات التصميم الداخلي", 300, "300.000"),
    (2, "الرسم بالحاسوب", 300, "450.000"),
    (3, "المواد والتشطيبات", 300, "450.000"),
    (4, "مشروع التخرج", 300, "450.000"),
]

#: (program_code, level, course_fee, deposit_code|None)
PRICE_ITEMS: list[tuple[str, int | None, str, str | None]] = [
    ("DIP-ID", None, "1700.000", None),
    *[("SC-ENG-GEN", level, "90.000", "DEP-ENG-GEN") for level in range(1, 9)],
    ("SC-NET", None, "250.000", None),
    ("SC-CMA", None, "800.000", None),
    ("SC-JCPA", None, "600.000", None),
    ("SC-PMP", None, "450.000", None),
    ("SC-DRUG", None, "300.000", None),
    ("SC-QA", None, "200.000", None),
    ("ON-GENAI", None, "200.000", None),
]

#: (program_code|None, category, fee|None, note) — QA T-096…T-099.
FEE_RULES: list[tuple[str | None, str, str | None, str]] = [
    (None, "CENTER", "50.000", "القاعدة العامة — BR-009"),
    (None, "UNIVERSITY", "15.000", "القاعدة العامة — BR-009"),
    (None, "EMPLOYEE", "15.000", "Q-10 — قيمة ابتدائية قابلة للتغيير من البيانات"),
    ("SC-NET", "UNIVERSITY", "20.000", "استثناء موثّق — T-096"),
    ("SC-CMA", "CENTER", "170.000", "استثناء موثّق — T-097"),
    ("SC-JCPA", "CENTER", None, "بلا رسوم تسجيل — T-098"),
    ("SC-JCPA", "UNIVERSITY", None, "بلا رسوم تسجيل — T-098"),
    ("SC-PMP", "CENTER", None, "بلا رسوم تسجيل — T-098"),
    ("SC-PMP", "UNIVERSITY", None, "بلا رسوم تسجيل — T-098"),
    ("SC-DRUG", "CENTER", None, "بلا رسوم تسجيل — T-098"),
    ("SC-DRUG", "UNIVERSITY", None, "بلا رسوم تسجيل — T-098"),
    ("SC-QA", "UNIVERSITY", None, "بلا رسوم للجامعيين — T-099"),
]


class Command(BaseCommand):
    help = "Seed DEMO catalogue and pricing data. Idempotent. Not business rules."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--approve",
            action="store_true",
            help="Approve the seeded price list so pricing can be resolved.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        semester = Semester.objects.filter(is_active=True).first()
        if semester is None:
            raise CommandError(
                "لا يوجد فصل دراسي نشط. شغّل seed_semester أولاً — "
                "قائمة الأسعار مرتبطة بفصل (DATA_MODEL §5.5)."
            )

        categories = {
            code: CourseCategory.objects.get_or_create(code=code, defaults={"name_ar": name})[0]
            for code, name in CATEGORIES
        }
        for code, name in KNOWLEDGE_FIELDS:
            KnowledgeField.objects.get_or_create(code=code, defaults={"name_ar": name})

        policy, created = DepositPolicy.objects.get_or_create(
            code=str(DEMO_DEPOSIT["code"]), defaults=DEMO_DEPOSIT
        )
        self.stdout.write(
            self.style.SUCCESS(f"  {'+' if created else '='} سياسة تأمين تجريبية: {policy.name_ar}")
        )

        programs: dict[str, Program] = {}
        for code, ptype, name, cat, leveled, levels, consumables in PROGRAMS:
            programs[code] = Program.objects.get_or_create(
                code=code,
                defaults={
                    "program_type": ptype,
                    "name_ar": name,
                    "course_category": categories.get(cat) if cat else None,
                    "is_leveled": leveled,
                    "levels_count": levels,
                    "consumables_per_student": Decimal(consumables),
                    "training_hours": 60,
                },
            )[0]

        diploma = programs["DIP-ID"]
        for seq, name, hours, price in INTERIOR_DESIGN_SUBJECTS:
            Subject.objects.get_or_create(
                program=diploma,
                sequence=seq,
                defaults={"name_ar": name, "training_hours": hours, "price": Decimal(price)},
            )

        price_list, _ = PriceList.objects.get_or_create(
            code=f"PL-{semester.code}",
            defaults={
                "name_ar": f"قائمة أسعار {semester.name_ar}",
                "semester": semester,
                "issued_on": date(2026, 8, 1),
                "effective_from": date(2026, 9, 1),
                "proposed_by_text": "مدير المركز",
            },
        )

        if price_list.is_frozen:
            self.stdout.write(
                self.style.WARNING(
                    f"  ! القائمة {price_list.code} معتمدة — لا تُعدَّل (D-14). البنود أدناه لم تُمسّ."
                )
            )
        else:
            for code, level, fee, deposit_code in PRICE_ITEMS:
                deposit = policy if deposit_code else None
                PriceListItem.objects.get_or_create(
                    price_list=price_list,
                    program=programs[code],
                    level_key=level or 0,
                    defaults={
                        "level": level,
                        "course_fee": Decimal(fee),
                        "deposit_amount": Decimal("25.000") if deposit_code else None,
                        "deposit_policy": deposit,
                    },
                )
            for rule_code, category, rule_fee, rule_note in FEE_RULES:
                RegistrationFeeRule.objects.get_or_create(
                    price_list=price_list,
                    program_key=programs[rule_code].pk if rule_code else 0,
                    participant_category=category,
                    defaults={
                        "program": programs[rule_code] if rule_code else None,
                        "fee": Decimal(rule_fee) if rule_fee else None,
                        "exception_note_ar": rule_note,
                    },
                )

        if options["approve"] and price_list.status == PriceListStatus.DRAFT:
            price_list.status = PriceListStatus.APPROVED
            price_list.approved_by_text = "رئيس الجامعة (بيانات تجريبية)"
            price_list.decision_reference = "DEMO/2026/001"
            price_list.approved_at = timezone.now()
            price_list.save()
            self.stdout.write(self.style.SUCCESS(f"  + اعتُمدت القائمة {price_list.code}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"تم: {Program.objects.count()} برنامج · "
                f"{PriceListItem.objects.count()} بند سعر · "
                f"{RegistrationFeeRule.objects.count()} قاعدة رسم · "
                f"{DepositPolicy.objects.count()} سياسة تأمين"
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "⚠️  بيانات تجريبية بالكامل — تُعدَّل وتُحذف من الشاشات بلا هجرة. "
                "الكتالوج الكامل (5 دبلومات · 28 دورة · 13 أونلاين) ينتظر بيانات العميل."
            )
        )
