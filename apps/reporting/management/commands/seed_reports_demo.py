"""
الحركة التي تُنطق التقارير السبعة — **بيانات زرع، لا قاعدة عمل**.

الشاشات كانت مزروعة والدفاتر لم تكن: `seed_partners_demo` يكتب الشركاء
والاتفاقيات، ولا أحد يكتب **مطالبةً** عليها. فالتقرير الثالث — «كشف مستحقات
كل شريك ومخالصاته» — يُفتح على لا شيء، وهو التقرير الذي يشرح نموذج العمل كلّه
لمن يُعرض عليه النظام. قِيس على قاعدة الديمو في 8L-12: صفر مطالبات، صفر
مخالصات، صفر التزامات، وصفر صفوف شركاء في التقرير الثاني.

**يُكتب عبر الخدمات لا عبر الـ ORM.** المطالبة تُبنى بـ `build_claim` من
المحصَّل الفعلي في الفترة، وتُعتمد باعتماد، وتُربط بمخالصة تُفتح وتُدفع
وتُوقَّع — كما يجري في الشاشات سطراً بسطر. زرعٌ يدور حول الخدمات يزرع صفوفاً
لم ترَها قاعدةٌ قطّ، وأول ما ينكشف ذلك يكون أمام العميل.

**متكرّر الأثر.** ما وُجد يُترك؛ التشغيل مرّتين لا يضاعف مطالبةً ولا مصروفاً.

    python manage.py seed_reports_demo
    python manage.py seed_reports_demo --dry-run    # يقول ماذا سيفعل ولا يفعل

⚠️ لا يُشغَّل على قاعدة الديمو إلا بإذن صاحبها: يكتب صفوفاً مالية حقيقية في
الدفاتر. والقراءة منها لا تحتاج هذا الأمر.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "يزرع الحركة التي تُنطق التقارير السبعة: مطالبات ومخالصات والتزامات ومصروفات."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="يعرض ما سيُكتب ولا يكتب شيئاً.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self.dry_run: bool = options["dry_run"]
        if self.dry_run:
            self.stdout.write(self.style.WARNING("تشغيل تجريبي — لا يُكتب شيء."))

        actor = self._actor()
        if actor is None:
            self.stdout.write(
                self.style.ERROR(
                    "لا مستخدم بدور «الموظف المالي» ولا «مدير المركز» — "
                    "شغّل أوامر زرع المستخدمين أولاً."
                )
            )
            return

        self._seed_expenses(actor)
        self._seed_claims_and_settlements(actor)

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING("⚠️ بيانات تجريبية بالكامل. لا رقم منها مذكور بالاسم في أي شيفرة.")
        )

    # -- الفاعلون ----------------------------------------------------------
    def _actor(self) -> Any:
        """
        الموظف المالي يبني ويدفع، ومدير المركز يعتمد ويوقّع (فصل الأدوار).

        وإن لم يوجد أحدهما، لا يُزرع شيء: زرعٌ بفاعلٍ خاطئ يكتب في سجل
        التدقيق أن المدير فعل ما لا يفعله المدير.
        """
        from apps.people.models import Role, User

        self.finance = User.objects.filter(role=Role.FINANCE_OFFICER).first()
        self.approver = User.objects.filter(role=Role.CENTER_MANAGER).first()
        return self.finance if (self.finance and self.approver) else None

    # -- المصروفات (§9.7) --------------------------------------------------
    #: أربعة تصنيفات لأن §9.7 يسمّيها: «مستهلكات، تسويق، شهادات، غيرها».
    #: التقرير بتصنيف واحد يُظهر شريطاً واحداً بنسبة ١٠٠٪، وهو رسمٌ لا معلومة.
    EXPENSES: tuple[tuple[str, str, str, int], ...] = (
        ("CONSUMABLES", "أوراق وأحبار طباعة للفصل الثاني", "INV-2026-0141", 180),
        ("CONSUMABLES", "قرطاسية قاعات التدريب", "INV-2026-0152", 95),
        ("MARKETING", "حملة إعلانية للدورات القصيرة", "INV-2026-0163", 420),
        ("CERTIFICATES", "طباعة شهادات الفصل الأول", "INV-2026-0170", 260),
        ("OTHER", "صيانة جهاز عرض القاعة الثالثة", "INV-2026-0182", 140),
    )

    def _seed_expenses(self, actor: Any) -> None:
        from apps.expenses.models import Expense
        from apps.expenses.services import expense_service

        known = {code for code, _label in expense_service.category_choices(as_of=date.today())}
        if not known:
            self.stdout.write(self.style.ERROR("لا تصنيفات مصروفات مُعدَّة — شغّل seed_settings."))
            return

        created = skipped = 0
        for category, description, reference, amount in self.EXPENSES:
            if category not in known:
                # تصنيفٌ ليس في الإعداد لا يُخترع هنا: الإعداد هو المرجع،
                # والزرع لا يوسّعه من تحته.
                skipped += 1
                continue
            if Expense.objects.filter(reference=reference).exists():
                skipped += 1
                continue
            if self.dry_run:
                created += 1
                continue
            with transaction.atomic():
                expense = expense_service.record(
                    actor=actor,
                    category=category,
                    amount=Decimal(amount),
                    incurred_on=date.today() - timedelta(days=21),
                    description_ar=description,
                    reference=reference,
                )
                # واحدٌ يبقى بانتظار الاعتماد قصداً: التقرير السابع يفصل
                # المعتمَد عن المنتظر، وشاشةٌ كل قيودها معتمدة لا تُظهر الفرق.
                if category != "OTHER":
                    expense_service.approve(actor=self.approver, expense=expense)
            created += 1

        self.stdout.write(f"المصروفات: أُنشئ {created}، تُرك {skipped}.")

    # -- المطالبات والمخالصات (§9.2 · §9.3) --------------------------------
    def _seed_claims_and_settlements(self, actor: Any) -> None:
        from apps.operations.models import Cohort
        from apps.settlements.models import PartnerClaim, PartnerSettlement
        from apps.settlements.services import claim_service, settlement_service

        cohorts = list(
            Cohort.objects.filter(agreement__isnull=False)
            .select_related("agreement__partner")
            .order_by("code")
        )
        if not cohorts:
            self.stdout.write(
                self.style.ERROR(
                    "لا دفعة مرتبطة باتفاقية — شغّل seed_partners_demo ثم اربط دفعةً باتفاقية."
                )
            )
            return

        claims = settlements = paid = skipped = 0
        for index, cohort in enumerate(cohorts, start=1):
            agreement = cohort.agreement
            if PartnerClaim.objects.filter(cohort=cohort).exists():
                skipped += 1
                continue
            if self.dry_run:
                claims += 1
                continue

            period_from = cohort.starts_on
            period_to = min(cohort.ends_on or date.today(), date.today())
            try:
                with transaction.atomic():
                    claim = claim_service.build_claim(
                        actor=actor,
                        agreement=agreement,
                        cohort=cohort,
                        period_from=period_from,
                        period_to=period_to,
                        trigger_type="END_OF_COURSE",
                        trigger_reference_ar="نهاية الدورة — زرع تجريبي",
                    )
                    claim_service.approve_claim(actor=self.approver, claim=claim)
                claims += 1
            except Exception as failure:  # يُبلَّغ عنه ويُكمَل الزرع
                self.stdout.write(self.style.WARNING(f"  تعذّرت مطالبة {cohort.code}: {failure}"))
                continue

            # مخالصة لكل دفعة ثانية: بعضها مصروف وموقَّع وبعضها مفتوح، فيظهر
            # في التقرير الثالث عمودا «المصروف» و«الرصيد» بقيمتين مختلفتين.
            code = f"STL-DEMO-{index:03d}"
            if PartnerSettlement.objects.filter(code=code).exists():
                continue
            try:
                with transaction.atomic():
                    settlement = settlement_service.open_settlement(
                        actor=actor,
                        agreement=agreement,
                        opens_on=period_from,
                        cohort=cohort,
                        code=code,
                    )
                    settlement_service.attach_claims(actor=actor, settlement=settlement)
                    settlement.refresh_from_db()
                    settlements += 1
                    if index % 2 == 1 and settlement.total_due > Decimal("0.000"):
                        settlement_service.record_payment(
                            actor=actor, settlement=settlement, amount=settlement.total_due
                        )
                        settlement_service.sign_settlement(
                            actor=self.approver, settlement=settlement, signed_on=period_to
                        )
                        paid += 1
            except Exception as failure:  # يُبلَّغ عنه ويُكمَل الزرع
                self.stdout.write(self.style.WARNING(f"  تعذّرت مخالصة {cohort.code}: {failure}"))

        self.stdout.write(
            f"المطالبات: أُنشئ {claims}، تُرك {skipped}. "
            f"المخالصات: أُنشئ {settlements} (منها {paid} مدفوعة وموقَّعة)."
        )
