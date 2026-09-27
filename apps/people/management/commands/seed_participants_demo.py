"""
A presentable demo registry — **seed data, never a business rule**.

The participants screen had no seeding command, so every demonstration ran on
whatever had been typed by hand into the development database: a participant
whose whole name was «احمد», another called «طالب انسحاب تجريبي», and a Latin
name sitting in the Arabic name column. The registry is the first screen under
«المشاركون والتسجيل», so that is the first thing a client sees.

What it writes is ordinary rows the client can edit or delete from the screens.
Names, documents and phone numbers are invented; nothing here is referenced by
name anywhere in the code, and no number is allocated by this file — the
permanent participant number comes from the numbering service exactly as it
does for a clerk filling the admission form (BR-001, BR-002).

Written through ``participant_service.create_participant``, not through the
ORM: the service is the only write path to people.Participant, and a seed that
went around it would be seeding rows no rule had ever seen.

    python manage.py seed_participants_demo            # idempotent
    python manage.py seed_participants_demo --count 8  # only the first eight
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.people.models import Gender, IdDocumentType, Participant, Role, User
from apps.people.services import participant_service
from apps.people.services.participant_numbering import NoActiveSemesterError

#: The demo registry: three categories, both document types, both genders, and
#: the mix of complete and partly-filled records a real registry carries — a
#: registry in which every row is complete teaches nobody what «—» means.
#: (name_ar, name_en, category, doc type, doc number, gender, birth, city,
#:  qualification, phone, email, employer)
PARTICIPANTS: list[tuple[str, ...]] = [
    (
        "رهف عمر خالد الزعبي",
        "Rahaf Omar Khaled Al-Zoubi",
        "UNIVERSITY",
        IdDocumentType.NATIONAL_ID,
        "9962014785",
        Gender.FEMALE,
        "1996-03-11",
        "AMMAN",
        "BACHELOR",
        "0791122334",
        "rahaf.z@example.edu.jo",
        "",
    ),
    (
        "ليلى محمد عبدالله الخطيب",
        "Laila Mohammad Abdullah Al-Khatib",
        "CENTER",
        IdDocumentType.NATIONAL_ID,
        "9932011456",
        Gender.FEMALE,
        "1993-07-24",
        "IRBID",
        "BACHELOR",
        "0790001111",
        "laila.k@example.com",
        "مدرسة اليرموك الثانوية",
    ),
    (
        "أحمد محمد سالم العدوان",
        "Ahmad Mohammad Salem Al-Adwan",
        "UNIVERSITY",
        IdDocumentType.NATIONAL_ID,
        "9901033221",
        Gender.MALE,
        "1990-01-09",
        "BALQA",
        "MASTER",
        "0790000000",
        "ahmad.adwan@example.edu.jo",
        "",
    ),
    (
        "سارة نبيل يوسف الحوراني",
        "Sara Nabil Yousef Al-Hourani",
        "EMPLOYEE",
        IdDocumentType.NATIONAL_ID,
        "9882044113",
        Gender.FEMALE,
        "1988-11-02",
        "AMMAN",
        "HIGHER_DIPLOMA",
        "0795566778",
        "sara.h@uop.edu.jo",
        "جامعة البترا — دائرة القبول والتسجيل",
    ),
    (
        "خالد سليمان عوض الرشيد",
        "Khaled Suleiman Awad Al-Rashid",
        "UNIVERSITY",
        IdDocumentType.NATIONAL_ID,
        "9951077889",
        Gender.MALE,
        "1995-05-30",
        "ZARQA",
        "BACHELOR",
        "0788899001",
        "",
        "",
    ),
    (
        "منار إبراهيم حسن الطراونة",
        "Manar Ibrahim Hasan Al-Tarawneh",
        "CENTER",
        IdDocumentType.NATIONAL_ID,
        "9992066554",
        Gender.FEMALE,
        "1999-09-17",
        "KARAK",
        "HIGH_SCHOOL",
        "0777712345",
        "manar.t@example.com",
        "",
    ),
    (
        "عمر فادي نصري الحداد",
        "Omar Fadi Nasri Al-Haddad",
        "UNIVERSITY",
        IdDocumentType.PASSPORT,
        "P4471226",
        Gender.MALE,
        "1997-12-05",
        "AMMAN",
        "BACHELOR",
        "0799001234",
        "omar.haddad@example.edu.jo",
        "",
    ),
    (
        "هبة زياد محمود القضاة",
        "Heba Ziad Mahmoud Al-Qudah",
        "EMPLOYEE",
        IdDocumentType.NATIONAL_ID,
        "9872099001",
        Gender.FEMALE,
        "1987-02-14",
        "IRBID",
        "PHD",
        "0796677889",
        "heba.q@uop.edu.jo",
        "جامعة البترا — كلية العلوم التربوية",
    ),
    (
        "يزن رامي عاطف الشوابكة",
        "Yazan Rami Atef Al-Shawabkeh",
        "UNIVERSITY",
        IdDocumentType.NATIONAL_ID,
        "9942055667",
        Gender.MALE,
        "1994-06-21",
        "MADABA",
        "DIPLOMA",
        "0782233445",
        "",
        "",
    ),
    (
        "دانا وليد سمير البيطار",
        "Dana Waleed Sameer Al-Bitar",
        "CENTER",
        IdDocumentType.NATIONAL_ID,
        "9922088776",
        Gender.FEMALE,
        "1992-10-08",
        "AMMAN",
        "MASTER",
        "0791234567",
        "dana.bitar@example.com",
        "شركة الأفق للاستشارات",
    ),
]


class Command(BaseCommand):
    help = "يزرع سجل مشاركين تجريبياً معقولاً للعرض — لا يمسّ سجلاً قائماً."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=len(PARTICIPANTS),
            help="كم مشاركاً يُزرع من القائمة (الافتراضي: كلهم).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # The seed writes as a real actor because the service audits every
        # creation against one; an unaudited participant is not a participant
        # this system recognises.
        actor = (
            User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).order_by("id").first()
            or User.objects.filter(role=Role.REGISTRATION_OFFICER, is_active=True)
            .order_by("id")
            .first()
        )
        if actor is None:
            raise CommandError("لا مستخدم فعّال بدور مدير النظام أو موظف التسجيل ليُنسب إليه الزرع.")

        # Spread over the past weeks rather than all landing on today: the
        # registry is ordered newest-first, and ten rows sharing one date say
        # nothing about what that order means.
        today = timezone.localdate()

        created = 0
        skipped = 0
        for offset, row in enumerate(PARTICIPANTS[: max(0, options["count"])]):
            (
                name_ar,
                name_en,
                category,
                doc_type,
                doc_number,
                gender,
                born,
                city,
                qualification,
                phone,
                email,
                employer,
            ) = row
            # Idempotent on the identity document, which is what identifies a
            # person here — re-running must not hand the same person a second
            # participant number.
            if Participant.objects.filter(
                id_document_type=doc_type, id_document_number=doc_number
            ).exists():
                skipped += 1
                continue

            try:
                with transaction.atomic():
                    participant = participant_service.create_participant(
                        actor=actor,
                        data={
                            "category": category,
                            "name_ar": name_ar,
                            "name_en": name_en,
                            "id_document_type": doc_type,
                            "id_document_number": doc_number,
                            "nationality": "الأردن",
                            "gender": gender,
                            "date_of_birth": date.fromisoformat(born),
                            "qualification": qualification,
                            "city": city,
                            "phone": phone,
                            "email": email,
                            "employer": employer,
                            "registered_on": today - timedelta(days=3 * offset),
                            # BR-003 — nothing is saved without it, and a seed
                            # that set it any other way would be seeding a row
                            # the form could not have produced.
                            "no_refund_pledge_accepted": True,
                        },
                    )
            except NoActiveSemesterError as exc:
                raise CommandError(
                    "لا فصل دراسي نشط، والرقم الجامعي يُولَّد منه (BR-001). شغّل seed_semester أولاً."
                ) from exc

            created += 1
            self.stdout.write(f"  + {participant.participant_number}  {name_ar}")

        self.stdout.write(
            self.style.SUCCESS(f"سجل المشاركين التجريبي: {created} مُنشأ · {skipped} قائم سلفاً")
        )
