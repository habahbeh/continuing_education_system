"""
زرّان للديمو: املأه، أو أعِده من الصفر — **بيانات عرض، لا قواعد عمل**.

**لماذا أمرٌ واحد.** في المشروع تسعة أوامر زرع، وترتيبها يهمّ: لا سعرَ بلا فصل
(§4 يربط القائمة بفصل)، ولا رقمَ مشارك بلا فصلٍ نشط (BR-001)، ولا تسجيلَ بلا
سعر (BR-012)، ولا مطالبةَ شريك بلا اتفاقية. فمن يشغّلها يدوياً يشغّلها بالترتيب
الخطأ مرّةً واحدة ويقضي ساعةً على رسالةٍ تقول «لا قائمة أسعار سارية».

وجردٌ على قاعدة الديمو في 2026-09-27 كشف ما هو أسوأ: ``seed_settlements_demo``
و``seed_reports_demo`` موجودان **ولم يُشغَّلا** — فالمطالبات والمخالصات
والتزامات الشركاء **صفرُ صفٍّ**، أي أن خمس شاشات تُعرَض على العميل فارغة. لم
يكن العطل في الأوامر بل في أن لا أحد يعرف أنها موجودة.

**ولماذا ``--reset`` يمحو الكلّ ولا ينتقي.** حذفٌ انتقائيّ بالبادئة يتعثّر في
``PROTECT`` المنثور في المخطّط، ويترك أثراً نصفه هنا ونصفه هناك. والمحوُ الكامل
ثم إعادة البناء بالمشي يُنتج قاعدةً متّسقة: سجلُّ تدقيقها مكتوبٌ من الأفعال
نفسها، فيمرّ من ``verify_audit_chain`` لا يتعارض معه.

**وليس هذا نقضاً لـ BR-084.** القاعدة تحمي سجلّ حركةٍ حقيقيّ من التعديل والحذف
الانتقائيّ. أمّا قاعدة عرضٍ تُعاد بناؤها بأمرٍ معلَن فهي ليست سجلّاً؛ ولذلك
``--reset`` **يرفض أن يعمل على قاعدةٍ ليست قاعدة عرض** (انظر ``DEMO_DATABASES``)
ويطلب تأكيداً مكتوباً. الرفض هو الحماية، لا حسن النيّة.

**كل حالة تُبلَغ بالمشي لا بالكتابة.** ``seed_active_enrollment_demo`` يفعل ذلك:
يسجّل ثم يقبض ثم يسجّل الوصل ثم يعتمد، لأن BR-018 يرفض الاعتماد بلا وصل. وصفٌّ
تُكتب حالته مباشرةً هو صفٌّ لا يستطيع أي مسار في النظام أن يُنتجه — وهو أسوأ ما
يُعرَض على عميل، لأنه يعرض نظاماً لا يعمل كما يقول.

    python manage.py seed_demo_all            # املأ الناقص، لا تمسّ القائم
    python manage.py seed_demo_all --reset    # امحُ الكلّ وابنِ من الصفر
    python manage.py seed_demo_all --reset --noinput   # بلا سؤال تأكيد
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

#: أسماء قواعد العرض وحدها. ``--reset`` يرفض ما ليس منها رفضاً صريحاً: أمرٌ
#: يمحو كل صفٍّ يجب أن يكون عاجزاً عن العمل على قاعدةٍ لم تُسمَّ له، لا أن
#: يعتمد على انتباه من يكتبه في الطرفية.
DEMO_DATABASES: frozenset[str] = frozenset({"continuing_education", "ce_walk", "ce_demo"})

#: كلمة مرور واحدة لكل مستخدمي العرض. عشرة محارف لتمرّ من مدقّق الطول، ومعلنةٌ
#: هنا لأن قاعدة العرض ليست فيها أسرار.
DEMO_PASSWORD = "audit-1234"

#: الأدوار السبعة التي يُفحَص بها النظام. BR-101 يُسقط حقولاً في الخدمة لا في
#: القالب، فالصندوق يتلقّى جدولاً مختلفاً لا جدولاً مخفيّ الأعمدة — ولا يُرى
#: ذلك إلا بالدخول بكل دور.
DEMO_USERS: tuple[tuple[str, str, str], ...] = (
    ("admin", "SUPER_ADMIN", "مدير النظام"),
    ("qa.manager", "CENTER_MANAGER", "أ. رامي الفاعوري — مدير المركز"),
    ("qa.registrar", "REGISTRATION_OFFICER", "أ. لمى الزعبي — موظفة التسجيل"),
    ("qa.cashier", "CASHIER", "أ. سامر الخطيب — الصندوق"),
    ("qa.finance", "FINANCE_OFFICER", "أ. هالة النابلسي — الموظفة المالية"),
    ("qa.finmanager", "FINANCE_MANAGER", "أ. عمر الدباس — المدير المالي"),
    ("qa.auditor", "AUDIT_ACCOUNT", "حساب التدقيق"),
)

#: الفصل الذي يُبنى عليه كل شيء. أوّل خطوة في النظام: بلا فصلٍ نشط لا يُولَّد
#: رقم مشارك واحد (BR-001)، فكل ما بعده يتعذّر.
SEMESTER: dict[str, Any] = {
    "code": "2026-1",
    "type": 1,
    "academic_year": "2026/2027",
    "starts": date(2026, 9, 1),
    "ends": date(2027, 1, 15),
    "name": "الفصل الأول 2026/2027",
}

#: تُفتَح أربع فترات مالية حول تواريخ العرض، وتُقفَل الأقدم — فالإقفال حالةٌ
#: تُعرض، و«فترة مقفلة» لا تُرى على شاشةٍ كل فتراتها مفتوحة (D-23).
PERIOD_MONTHS = 4

#: ستّ دفعات في ستّ حالات — وهي الحلقة التي لم يكن لها أمر زرع. ``reach`` هي
#: الحالة التي يُمشى بالدفعة إليها، و``mohe_number`` الرقم الوزاري الذي يشترطه
#: C-16 مع كل اعتماد.
#:
#: والرمزان ``CO-QA-1`` و``CO-DIP-ID-1`` مقصودان بأسمائهما: هما ما يطلبه
#: ``seed_active_enrollment_demo`` و``seed_settlements_demo``، وبغيابهما يتوقّف
#: الأمران عند «لا دفعة بهذا الرمز» — وذلك سبب كون المطالبات والمخالصات صفراً.
COHORTS: tuple[dict[str, Any], ...] = (
    {
        "code": "CO-DIP-ID-1", "program": "DIP-ID", "reach": "RUNNING",
        "name": "دبلوم التصميم الداخلي — الفصل الأول 2026/2027",
        "starts": date(2026, 9, 20), "ends": date(2027, 1, 10),
        "capacity": 25, "trainer": "م. ديما الشريف", "mohe_number": "M-2026-1401",
    },
    {
        "code": "CO-QA-1", "program": "SC-NET", "reach": "RUNNING",
        "name": "هندسة الشبكات — الفصل الأول 2026/2027",
        "starts": date(2026, 9, 20), "ends": date(2026, 12, 20),
        "capacity": 30, "trainer": "م. زياد القاسم", "mohe_number": "M-2026-1402",
    },
    {
        "code": "CO-ENG-GEN-1", "program": "SC-ENG-GEN", "reach": "COMPLETED",
        "name": "اللغة الإنجليزية العامة — المستوى الأول",
        "starts": date(2026, 9, 5), "ends": date(2026, 9, 30),
        "capacity": 20, "trainer": "أ. رنا العموش", "mohe_number": "M-2026-1403",
        # §4.2 — دورةٌ بمستويات (English 1–8)، ولا تُفتَح دفعتها بلا مستوى.
        "level": 1,
    },
    {
        "code": "CO-QA-2", "program": "SC-NET", "reach": "CANCELLED_LOW_ENROLLMENT",
        "name": "هندسة الشبكات — مجموعة مسائية",
        "starts": date(2026, 10, 1), "ends": date(2026, 12, 25),
        "capacity": 30, "trainer": "م. زياد القاسم", "mohe_number": "M-2026-1404",
    },
    {
        "code": "CO-QA-3", "program": "SC-CMA", "reach": "MOHE_REJECTED",
        "name": "CMA — مجموعة الخريف",
        "starts": date(2026, 10, 10), "ends": date(2027, 1, 20),
        "capacity": 18, "trainer": "أ. فراس الشوبكي", "mohe_number": "",
    },
    {
        "code": "CO-QA-4", "program": "SC-QA", "reach": "PENDING_MOHE",
        "name": "التدقيق الداخلي للجودة — مجموعة ١",
        "starts": date(2026, 11, 1), "ends": date(2027, 1, 30),
        "capacity": 22, "trainer": "أ. سهى المصري", "mohe_number": "",
    },
    {
        "code": "CO-ON-GENAI-1", "program": "ON-GENAI", "reach": "PLANNED",
        "name": "Generative AI — أونلاين",
        "starts": date(2026, 11, 15), "ends": date(2027, 1, 15),
        "capacity": 60, "trainer": "د. مهند العابد", "delivery": "ONLINE",
        "mohe_number": "",
    },
)


def _pdf(name: str) -> Any:
    """
    مرفقٌ صغير صحيح البنية — BR-016 يشترط مستندين قبل الإرسال.

    أصغر PDF سليم، لا ملفٌّ فارغ بامتداد: التحقّق من النوع يقرأ الترويسة، وملفٌ
    كاذب يمرّ اليوم ويفشل يوم يُشدَّد التحقّق.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    body = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )
    return SimpleUploadedFile(name, body, content_type="application/pdf")


class Command(BaseCommand):
    help = "يملأ قاعدة العرض ببيانات كل الحالات، أو يعيد بناءها من الصفر بـ --reset."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="امحُ كل صفٍّ في القاعدة قبل الزرع (قواعد العرض وحدها).",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="لا تسأل تأكيداً — للتشغيل من سكربت.",
        )
        parser.add_argument(
            "--password",
            default=DEMO_PASSWORD,
            help=f"كلمة مرور مستخدمي العرض (الافتراضي {DEMO_PASSWORD}).",
        )

    # -- المدخل -------------------------------------------------------------
    def handle(self, *args: Any, **options: Any) -> None:
        database = connection.settings_dict["NAME"]
        if options["reset"]:
            self._flush(database, noinput=options["noinput"])

        self.stdout.write(self.style.MIGRATE_HEADING(f"زرع قاعدة العرض: {database}"))
        self._users(options["password"])
        self._reference_data()
        self._calendar()
        self._catalogue()
        self._cohorts()
        self._people_and_money()
        self._partners()
        self._report_data()
        self._close_oldest_period()
        self._summary()

    # -- المحو، ومن يرفضه ---------------------------------------------------
    def _flush(self, database: str, *, noinput: bool) -> None:
        """
        كل صفٍّ يذهب — ولذلك يُرفض على قاعدةٍ لم تُسمَّ قاعدةَ عرض.

        الاسم يُفحَص أوّلاً لا أخيراً: أمرٌ يطلب تأكيداً ثم يمحو قاعدةَ إنتاج
        أطاع من كتبه؛ وأمرٌ يرفض اسمها لم يكن ليُطيعه.
        """
        if database not in DEMO_DATABASES:
            raise CommandError(
                f"«{database}» ليست قاعدة عرض — ولا يمحو هذا الأمر قاعدةً لم تُسمَّ له.\n"
                f"قواعد العرض المسمّاة: {', '.join(sorted(DEMO_DATABASES))}.\n"
                "إن كانت هذه قاعدة عرض فعلاً فأضف اسمها إلى DEMO_DATABASES بقرارٍ لا بالسهو."
            )
        if not noinput:
            self.stdout.write(
                self.style.WARNING(
                    f"سيُمحى كل صفٍّ في «{database}»: المشاركون والسندات والمطالبات "
                    "وسجل التدقيق — ولا رجعة."
                )
            )
            typed = input(f'اكتب اسم القاعدة للتأكيد [{database}]: ').strip()
            if typed != database:
                raise CommandError("لم يُطابق الاسم — لم يُمسّ شيء.")

        call_command("flush", "--noinput", verbosity=0)
        self.stdout.write(self.style.SUCCESS("✓ مُحيت القاعدة — تُبنى الآن من الصفر."))

    # -- الأدوار السبعة، قبل كل فعلٍ يحتاج فاعلاً ---------------------------
    def _users(self, password: str) -> None:
        from apps.people.models import User

        self.stdout.write("① المستخدمون والأدوار")
        for username, role, full_name in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "full_name_ar": full_name},
            )
            if created:
                user.set_password(password)
                if role == "SUPER_ADMIN":
                    user.is_staff = True
                    user.is_superuser = True
                user.save()
            self.stdout.write(f"   {'+' if created else '·'} {username:15} {role}")
        self.stdout.write(f"   كلمة المرور للجميع: {password}")

    def _reference_data(self) -> None:
        """الإعدادات وطرق الدفع — مفردات النظام قبل أي حركة."""
        self.stdout.write("② البيانات الأساسية")
        call_command("seed_settings", verbosity=0)
        self.stdout.write("   + الإعدادات المؤرّخة (ADR-009 · BR-086)")
        call_command("seed_payment_methods", verbosity=0)
        self.stdout.write("   + طرق الدفع: CASH · CHEQUE · VISA")

    # -- التقويم: الفصل ثم الفترات المالية ----------------------------------
    def _calendar(self) -> None:
        """
        الفصل أوّلاً لأن كل ما بعده يقرأه، ثم الفترات لأن كل حركةٍ تُؤرَّخ فيها.
        """
        self.stdout.write("③ تجهيز الفصل")
        call_command(
            "seed_semester",
            "--code", SEMESTER["code"],
            "--type", str(SEMESTER["type"]),
            "--academic-year", SEMESTER["academic_year"],
            "--starts", SEMESTER["starts"].isoformat(),
            "--ends", SEMESTER["ends"].isoformat(),
            "--name", SEMESTER["name"],
            verbosity=0,
        )
        self.stdout.write(f"   + الفصل {SEMESTER['code']} — وهو النشط، فأرقام المشاركين تُولَّد")
        self._financial_periods()

    def _financial_periods(self) -> None:
        """
        أربع فترات شهرية، أقدمُها مقفلة — والإقفال يمرّ بالخدمة لا بالجدول.

        الجرد وجد **صفر فترة** في الديمو، أي أن حارس D-23 لا يحرس شيئاً وكل
        شهرٍ مفتوح إلى الأبد. وفترةٌ مقفلة واحدة على الأقل ضرورية للعرض: «لا
        تُقيَّد حركة في شهرٍ أُقفل» قولٌ لا يُرى على شاشةٍ كل فتراتها مفتوحة.
        """
        from apps.core.models import FinancialPeriod
        from apps.people.models import Role, User
        from apps.people.services import fiscal_period_service

        manager = User.objects.filter(role=Role.CENTER_MANAGER, is_active=True).first()
        if manager is None:  # pragma: no cover - الأدوار تُنشأ قبل هذا
            raise CommandError("لا مدير مركز ليُنسب إليه فتح الفترات.")

        first = SEMESTER["starts"].replace(day=1)
        opened = 0
        for index in range(PERIOD_MONTHS):
            month = first.month + index
            year = first.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            starts_on = date(year, month, 1)
            ends_on = (
                date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            ) - timedelta(days=1)
            if FinancialPeriod.objects.filter(starts_on=starts_on).exists():
                continue
            period = fiscal_period_service.create_period(
                actor=manager, data={"starts_on": starts_on, "ends_on": ends_on}
            )
            opened += 1
            del period
        self.stdout.write(f"   + {opened} فترة مالية — كلّها مفتوحة، وتُقفَل أقدمُها في النهاية")

    def _close_oldest_period(self) -> None:
        """
        الإقفال **آخر خطوة**، لا مع فتح الفترات.

        أُقفلت أقدمُ فترة في المحاولة الأولى مع فتحها، فرفض D-23 كل مصروفٍ
        تاريخُه في أيلول — وهي بالضبط تواريخ ``seed_reports_demo``. والخطأ لم
        يكن في الحارس: الشهر يُقفَل بعد أن يُنجَز عملُه، لا قبله. فالترتيب هنا
        هو ترتيب الواقع.
        """
        from apps.core.models import FinancialPeriod, FinancialPeriodStatus
        from apps.people.models import Role, User
        from apps.people.services import fiscal_period_service

        manager = User.objects.filter(role=Role.CENTER_MANAGER, is_active=True).first()
        oldest = FinancialPeriod.objects.filter(
            status=FinancialPeriodStatus.OPEN
        ).order_by("starts_on").first()
        if manager is None or oldest is None:
            return
        fiscal_period_service.close_period(actor=manager, period=oldest)
        self.stdout.write(
            f"   + أُقفلت {oldest.starts_on} — {oldest.ends_on} (D-23)، "
            "فحالة «مقفلة» صارت قابلةً للعرض"
        )

    def _catalogue(self) -> None:
        self.stdout.write("④ الكتالوج والأسعار")
        call_command("seed_catalog_demo", verbosity=0)
        self.stdout.write("   + برامج وبنود أسعار وقواعد رسوم وسياسة تأمين")

    # -- الدفعات: الحلقة المفقودة التي أفرغت دورة الشركاء كلها --------------
    def _cohorts(self) -> None:
        """
        ستّ دفعات في ستّ حالات — وهذه هي الحلقة التي لم تكن موجودة.

        لا أمر زرعٍ في المشروع ينشئ دفعة. فـ``seed_active_enrollment_demo``
        يطلب ``CO-QA-1`` و``seed_settlements_demo`` يطلب ``CO-DIP-ID-1``،
        وكلاهما يتوقّف عند «لا دفعة بهذا الرمز». ودفعات الديمو الستّ أُنشئت
        يدوياً من الشاشات، فلم يكن لها طريقٌ يُعاد.

        وكل حالة تُبلَغ بالمشي: الدفعة تُفتَح ``PLANNED`` دائماً (BR-013 يجعل
        اعتماد الوزارة بوّابة التسجيل)، ثم يُفتَح ملفٌ وزاري ويُرفَق مستنداه
        الإلزاميان (BR-016) ويُرسَل ويُقرَّر — والقرار وحده هو ما يحرّك الحالة.
        """
        from apps.operations.models import Cohort
        from apps.operations.services import cohort_service

        self.stdout.write("⑤ الدفعات المُشغّلة")
        for plan in COHORTS:
            if Cohort.objects.filter(code=plan["code"]).exists():
                self.stdout.write(f"   · {plan['code']:16} قائمة — لم تُمسّ")
                continue
            try:
                cohort = cohort_service.open_cohort(
                    actor=self._actor("CENTER_MANAGER"),
                    program_code=plan["program"],
                    semester_code=SEMESTER["code"],
                    code=plan["code"],
                    name_ar=plan["name"],
                    starts_on=plan["starts"],
                    ends_on=plan["ends"],
                    capacity=plan["capacity"],
                    trainer_name=plan["trainer"],
                    location="مبنى المركز — قاعة 3",
                    delivery_method=plan.get("delivery", "IN_PERSON"),
                    level=plan.get("level"),
                )
                self._walk_cohort_to(cohort, plan)
                self.stdout.write(f"   + {plan['code']:16} {plan['reach']}")
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(f"   ⚠ {plan['code']}: {self._why(exc)}")
                )

    def _walk_cohort_to(self, cohort: Any, plan: dict[str, Any]) -> None:
        """يمشي بالدفعة إلى حالتها المطلوبة عبر الخدمات، لا بكتابة الحالة."""
        from apps.operations.services import cohort_service, mohe_service

        reach = plan["reach"]
        manager = self._actor("CENTER_MANAGER")
        if reach == "PLANNED":
            return

        submission = mohe_service.create_submission(
            actor=self._actor("REGISTRATION_OFFICER"),
            cohort=cohort,
            data={
                "trainer_name": plan["trainer"],
                "trainer_qualifications": "ماجستير في التخصص وخبرة عشر سنوات",
                "training_location": "مبنى المركز — قاعة 3",
                "responsible_entity": "مركز التعليم المستمر — جامعة البترا",
                "training_axes_ar": "محاور تدريبية حسب الخطة المعتمدة",
                "practical_aspects_ar": "تطبيقات عملية ومشروع ختامي",
                "target_audience_ar": "طلبة الجامعات والعاملون في القطاع",
            },
        )
        for purpose in ("TRAINER_CV", "ENTITY_LICENSE"):
            mohe_service.attach_document(
                actor=self._actor("REGISTRATION_OFFICER"),
                submission=submission,
                purpose=purpose,
                upload=_pdf(f"{cohort.code}-{purpose.lower()}.pdf"),
            )
        submission = mohe_service.submit_to_mohe(
            actor=manager, submission=submission, submitted_on=plan["starts"] - timedelta(days=30)
        )
        if reach == "PENDING_MOHE":
            return

        if reach == "MOHE_REJECTED":
            mohe_service.record_decision(
                actor=manager,
                submission=submission,
                approved=False,
                decided_on=plan["starts"] - timedelta(days=20),
                rejection_reason_ar="عدم تسجيل طلاب خلال المدة المسموحة",
            )
            return

        mohe_service.record_decision(
            actor=manager,
            submission=submission,
            approved=True,
            decided_on=plan["starts"] - timedelta(days=20),
            mohe_course_number=plan["mohe_number"],
            registration_deadline=plan["starts"] + timedelta(days=30),
        )
        cohort.refresh_from_db()
        if reach == "APPROVED":
            return

        if reach == "CANCELLED_LOW_ENROLLMENT":
            cohort_service.cancel_for_low_enrollment(
                actor=manager,
                cohort=cohort,
                reason_ar="لم يكتمل الحد الأدنى للعدد — أربعة مسجَّلين من عشرة (§5.3).",
            )
            return

        cohort_service.start_cohort(actor=manager, cohort=cohort)
        cohort.refresh_from_db()
        if reach == "COMPLETED":
            cohort_service.complete_cohort(actor=manager, cohort=cohort)

    def _actor(self, role: str) -> Any:
        """المستخدم صاحب الدور — والفعل يُنسب إليه لا إلى مديرٍ عام."""
        from apps.people.models import User

        user = User.objects.filter(role=role, is_active=True).order_by("id").first()
        if user is None:  # pragma: no cover - الأدوار تُنشأ في الخطوة ①
            raise CommandError(f"لا مستخدم فعّال بدور {role}.")
        return user

    @staticmethod
    def _why(exc: Exception) -> str:
        detail = getattr(exc, "messages", None)
        return " · ".join(str(m) for m in detail) if detail else str(exc)

    def _people_and_money(self) -> None:
        self.stdout.write("⑥ المشاركون والتسجيل والمال")
        call_command("seed_participants_demo", verbosity=0)
        self.stdout.write("   + سجل مشاركين بفئاتهم الثلاث")
        try:
            call_command("seed_active_enrollment_demo", verbosity=0)
            self.stdout.write("   + تسجيل نشط بالمشي: رسوم ← سند ← وصل ← اعتماد (BR-018)")
        except Exception as exc:
            # لا يُسكَت: صفٌّ نشطٌ مفقود يُخفي ثلاثة أفعال من شاشة التسجيلات.
            self.stdout.write(self.style.WARNING(f"   ⚠ التسجيل النشط لم يُزرع: {exc}"))

    def _partners(self) -> None:
        self.stdout.write("⑦ الشركاء والاتفاقيات")
        call_command("seed_partners_demo", verbosity=0)
        self.stdout.write("   + ثلاثة نماذج احتساب وثلاث حالات سريان (§3)")

    def _report_data(self) -> None:
        """
        المطالبات والمخالصات والالتزامات — وهي ما وجده الجرد **صفراً** في الديمو.
        """
        self.stdout.write("⑧ دورة الشركاء والتقارير")
        for command, what in (
            ("seed_settlements_demo", "مطالبة ومخالصة مرتبطتان باتفاقية (§5.4)"),
            ("seed_reports_demo", "الحركة التي تُنطق التقارير السبعة"),
        ):
            try:
                call_command(command, verbosity=0)
                self.stdout.write(f"   + {what}")
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"   ⚠ {command}: {exc}"))

    # -- ما بُلِغ فعلاً، وما لم يُبلَغ ---------------------------------------
    def _summary(self) -> None:
        """
        يُعدّ الصفوف ويُعلن ما بقي فارغاً — لا يدّعي النجاح.

        سجلٌّ فارغ هو شاشةٌ فارغة أمام العميل، وهو أهمّ ما يجب أن يعرفه من
        شغّل الأمر قبل أن يقف أمامه.
        """
        from django.apps import apps as django_apps

        self.stdout.write(self.style.MIGRATE_HEADING("الحصيلة"))
        watched = (
            ("مشاركون", "people.Participant"),
            ("تسجيلات", "operations.Enrollment"),
            ("دفعات", "operations.Cohort"),
            ("سندات قبض", "cashbox.Receipt"),
            ("إقفالات يومية", "cashbox.DailyClosing"),
            ("قوائم أسعار", "catalog.PriceList"),
            ("فترات مالية", "core.FinancialPeriod"),
            ("شركاء", "partners.Partner"),
            ("اتفاقيات", "partners.Agreement"),
            ("مطالبات", "settlements.PartnerClaim"),
            ("مخالصات", "settlements.PartnerSettlement"),
            ("التزامات", "settlements.PartnerObligation"),
            ("مصروفات", "expenses.Expense"),
            ("براءات ذمة", "operations.Clearance"),
            ("شهادات", "operations.Certificate"),
            ("نقل بين دورات", "operations.Transfer"),
            ("صفوف تدقيق", "core.AuditEvent"),
        )
        empty: list[str] = []
        for label, path in watched:
            app_label, model_name = path.split(".")
            count = django_apps.get_model(app_label, model_name).objects.count()
            mark = "✓" if count else "✗"
            self.stdout.write(f"   {mark} {label:16} {count}")
            if not count:
                empty.append(label)

        if empty:
            self.stdout.write(
                self.style.WARNING(
                    "\nسجلات فارغة — شاشاتها ستُعرَض فارغة: " + " · ".join(empty)
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nلا سجلّ فارغ."))
        self.stdout.write(
            "\nللإعادة من الصفر:  python manage.py seed_demo_all --reset"
        )
