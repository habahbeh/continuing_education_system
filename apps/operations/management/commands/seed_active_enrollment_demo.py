"""
One enrolment that is actually RUNNING — **seed data, never a business rule**.

A survey of the demo found the register holding seven enrolments in five
states, and **not one of them ACTIVE**: two COMPLETED, two PENDING_FINANCE,
one CANCELLED, one DISMISSED, one WITHDRAWN. Three of the screen's own actions
— «تسجيل التخرج» و«تسجيل انسحاب» و«تسجيل فصل» — are offered on an ACTIVE row
and on no other, so a third of what that screen does could not be shown to the
client at all. Not because it was broken: because nothing was in the state
that reveals it.

**The whole road, not a row dropped into the state.** ACTIVE is the end of a
chain the rules guard at every link: an enrolment raises its charges from the
price list in force (BR-012), the participant pays at the finance department
and a receipt is issued, the centre records the voucher, and only then may the
manager approve — BR-018 refuses an approval without a recorded voucher, and
the service will say so if this command ever gets the order wrong. Writing the
status straight onto the row would produce an enrolment no workflow could have
produced, which is the opposite of what a demonstration is for.

**Four actors, because the matrix says one cannot do this.** The registrar
enrols and records the voucher, the cashier issues the receipt, and the centre
manager approves. A seed run under a single super-administrator would show a
row approved by the hand that created it.

    python manage.py seed_active_enrollment_demo   # idempotent
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.operations.models import Cohort, Enrollment, EnrollmentStatus
from apps.people.models import Participant, Role, User

#: The cohort the enrolment is placed on: RUNNING, ministry-approved, and the
#: one whose other rows already show the exits. Its neighbours being finished
#: and withdrawn is what makes one live row worth looking at.
COHORT_CODE = "CO-QA-1"

#: The code is the register's to mint everywhere else; here it is fixed so the
#: command can recognise its own work and run twice without doubling it.
ENROLLMENT_CODE = "EN-DEMO-ACTIVE"


class Command(BaseCommand):
    help = "تسجيل واحد في حالة «نشط» ليصير التخرج والانسحاب والفصل قابلةً للعرض."

    def handle(self, *args: Any, **options: Any) -> None:
        registrar = self._actor(Role.REGISTRATION_OFFICER, "موظف التسجيل")
        cashier = self._actor(Role.CASHIER, "الصندوق")
        manager = self._actor(Role.CENTER_MANAGER, "مدير المركز")

        existing = Enrollment.objects.filter(code=ENROLLMENT_CODE).first()
        if existing is not None:
            self.stdout.write(f"قائم: {existing.code} في الحالة {existing.status} — لم يُمسّ.")
            return

        cohort = Cohort.objects.filter(code=COHORT_CODE).select_related("program").first()
        if cohort is None:
            raise CommandError(f"لا دفعة بالرمز {COHORT_CODE} — شغّل زرع الكتالوج والدفعات أولاً.")

        participant = self._free_participant(cohort)
        if participant is None:
            raise CommandError(
                "كل المشاركين مسجَّلون على هذه الدفعة سلفاً — لا أحد يُسجَّل بلا تكرار."
            )

        today = timezone.localdate()
        enrollment = self._enrol(registrar, participant, cohort, today)
        self._pay(cashier, enrollment, today)
        self._approve(registrar, manager, enrollment)

        enrollment.refresh_from_db()
        self.stdout.write(
            f"تم: {enrollment.code} — {participant.name_ar} على {cohort.code} "
            f"في الحالة {enrollment.status}."
        )
        if enrollment.status != EnrollmentStatus.ACTIVE:
            self.stdout.write(
                self.style.WARNING(
                    f"تنبيه: الحالة {enrollment.status} لا ACTIVE — "
                    "راجع الرسوم والدفع قبل العرض."
                )
            )

    # -- the four hands ----------------------------------------------------
    def _actor(self, role: str, label: str) -> User:
        found = User.objects.filter(role=role, is_active=True).order_by("id").first()
        if found is None:
            raise CommandError(f"لا مستخدم فعّال بدور {label} ليُنسب إليه الزرع.")
        return found

    def _free_participant(self, cohort: Cohort) -> Participant | None:
        """Anyone not already on this cohort — a second enrolment would be refused."""
        taken = Enrollment.objects.filter(cohort=cohort).values_list("participant_id", flat=True)
        return (
            Participant.objects.exclude(pk__in=list(taken))
            .order_by("participant_number")
            .first()
        )

    # -- the road ----------------------------------------------------------
    def _enrol(self, registrar: User, participant: Participant, cohort: Cohort, today: Any) -> Any:
        from apps.operations.services import enrollment_service

        enrollment = enrollment_service.enroll_with_charges(
            actor=registrar,
            participant=participant,
            cohort=cohort,
            enrolled_on=today,
            code=ENROLLMENT_CODE,
        )
        self.stdout.write(f"  ١) تسجيل {enrollment.code} ورسومه من قائمة الأسعار السارية.")
        return enrollment

    def _pay(self, cashier: User, enrollment: Any, today: Any) -> None:
        """
        The whole balance, at the finance department, against a receipt.

        BR-018 asks for a voucher before approval and the screen refuses an
        approval on an untouched balance, so paying the lot is the shortest
        honest road to a live row.
        """
        from apps.billing.services import account_service
        from apps.cashbox.models import PaymentMethod
        from apps.cashbox.services import payment_service

        amount = Decimal(str(account_service.get_account_state(enrollment).balance))
        if amount <= 0:
            self.stdout.write("  ٢) لا رصيد مستحق — لا سند.")
            return
        method = PaymentMethod.objects.filter(code="CASH").first() or PaymentMethod.objects.first()
        if method is None:
            raise CommandError("لا طريقة دفع معرَّفة — شغّل seed_payment_methods أولاً.")
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=amount,
            payment_method=method,
            received_on=today,
            external_receipt_ref="DEMO-ACTIVE-1",
            breakdown_text_ar="سداد كامل للعرض",
        )
        self.stdout.write(f"  ٢) سند قبض بـ{amount} من الصندوق.")

    def _approve(self, registrar: User, manager: User, enrollment: Any) -> None:
        from apps.operations.services import enrollment_service

        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        self.stdout.write("  ٣) المركز يسجّل استلام الوصل (BR-018).")
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
        self.stdout.write("  ٤) مدير المركز يعتمد التسجيل.")
