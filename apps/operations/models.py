"""
Operations — the MINIMAL slice pulled forward for Sprint 4 (DATA_MODEL §7).

**Why these two models are here early.** Every billing and cashbox entity has a
foreign key to ``Enrollment``: charge lines, discounts, refunds, deposits,
allocations. The plan put billing in Sprint 4 and enrolment in Sprint 6, but
the foreign keys run the other way, so billing could not be built — let alone
tested — without an enrolment to attach it to. Approved on 2026-08-15 as a
prerequisite slice, NOT as Sprint 6 arriving early.

**Deliberately absent, and deferred to Sprint 6 with the workflows that give
them meaning:** MoheSubmission, Transfer, SpecialCase, EnrollmentStatusHistory,
the daily overdue job, the operations screens, and the ``mohe_uploaded_on`` /
``transferred_to`` / ``deferred_to`` fields. Stubbing those columns now would
put half a workflow in the schema and invite someone to use it.

**Also absent, permanently:** ``Cohort.enrolled_count`` and any ``paid`` /
``total`` / ``balance`` on Enrollment. The demo stored an enrolment count and
it disagreed with reality — 18 against 2 actual. Money and counts are computed
from the ledger (P1); a stored total is a number that can drift from the rows
that produced it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import Money, ShortCode


class CohortStatus(models.TextChoices):
    PLANNED = "PLANNED", _("مخطَّطة")
    PENDING_MOHE = "PENDING_MOHE", _("بانتظار الوزارة")
    RUNNING = "RUNNING", _("قيد التنفيذ")
    COMPLETED = "COMPLETED", _("مكتملة")
    CANCELLED_LOW_ENROLLMENT = "CANCELLED_LOW_ENROLLMENT", _("ملغاة لقلة التسجيل")
    MOHE_REJECTED = "MOHE_REJECTED", _("مرفوضة من الوزارة")


class DeliveryMethod(models.TextChoices):
    IN_PERSON = "IN_PERSON", _("حضوري")
    ONLINE = "ONLINE", _("أونلاين")
    BLENDED = "BLENDED", _("مدمج")


class EnrollmentStatus(models.TextChoices):
    """GLOSSARY §7.1 — the twelve approved statuses."""

    PENDING_FINANCE = "PENDING_FINANCE", _("بانتظار الدفع")
    PENDING_APPROVAL = "PENDING_APPROVAL", _("بانتظار اعتماد المركز")
    ACTIVE = "ACTIVE", _("منتظم")
    PAYMENT_OVERDUE = "PAYMENT_OVERDUE", _("متأخر عن الدفع")
    NOT_ATTENDED = "NOT_ATTENDED", _("غير حاضر")
    INCOMPLETE = "INCOMPLETE", _("غير مكمل")
    WITHDRAWN = "WITHDRAWN", _("منسحب")
    DISMISSED = "DISMISSED", _("مفصول")
    TRANSFERRED_OUT = "TRANSFERRED_OUT", _("منقول منه")
    DEFERRED = "DEFERRED", _("مُرحَّل لدفعة لاحقة")
    CANCELLED = "CANCELLED", _("ملغى")
    COMPLETED = "COMPLETED", _("مكتمل")


class AttendanceSource(models.TextChoices):
    """Q-06 — MANUAL in v1; SYSTEM when an attendance module is built."""

    MANUAL = "MANUAL", _("إدخال يدوي")
    SYSTEM = "SYSTEM", _("من وحدة الحضور")


class Cohort(models.Model):
    """A running instance of a programme (DATA_MODEL §7.1)."""

    code = ShortCode(unique=True, verbose_name=_("رمز الدفعة"))
    program = models.ForeignKey(
        "catalog.Program",
        on_delete=models.PROTECT,
        related_name="cohorts",
        verbose_name=_("البرنامج"),
    )
    semester = models.ForeignKey(
        "core.Semester",
        on_delete=models.PROTECT,
        related_name="cohorts",
        verbose_name=_("الفصل"),
    )
    level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("المستوى"),
        help_text=_("إلزامي إن كان البرنامج ذا مستويات (BR-011)"),
    )
    name_ar = models.CharField(max_length=255, verbose_name=_("اسم الدفعة"))
    starts_on = models.DateField(verbose_name=_("تبدأ في"))
    ends_on = models.DateField(verbose_name=_("تنتهي في"))
    capacity = models.PositiveIntegerField(default=30, verbose_name=_("السعة"))
    trainer_name = models.CharField(max_length=150, blank=True, verbose_name=_("المدرب"))
    location = models.CharField(max_length=150, blank=True, verbose_name=_("المكان"))
    delivery_method = ShortCode(
        choices=DeliveryMethod.choices,
        default=DeliveryMethod.IN_PERSON,
        verbose_name=_("طريقة التقديم"),
    )
    status = ShortCode(
        choices=CohortStatus.choices,
        default=CohortStatus.PLANNED,
        verbose_name=_("الحالة"),
    )
    agreement = models.ForeignKey(
        "partners.Agreement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cohorts",
        verbose_name=_("الاتفاقية"),
        help_text=_("الاتفاقية التي تحكم قسمة إيراد هذه الدفعة (DATA_MODEL §7.1)"),
    )
    lecture_cost = Money(
        null=True,
        blank=True,
        verbose_name=_("كلفة المحاضرة"),
        help_text=_("أساس غرامة غياب المدرب — BR-057 (Sprint 5)"),
    )

    class Meta:
        verbose_name = _("دفعة")
        verbose_name_plural = _("الدفعات")
        ordering = ["-starts_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=CohortStatus.values),
                name="operations_cohort_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on__gt=models.F("starts_on")),
                name="operations_cohort_ends_after_starts",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0), name="operations_cohort_capacity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(lecture_cost__isnull=True) | models.Q(lecture_cost__gte=0),
                name="operations_cohort_lecture_cost_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["program", "semester"], name="ops_cohort_prog_sem_idx"),
            models.Index(fields=["status"], name="ops_cohort_status_idx"),
            models.Index(fields=["starts_on"], name="ops_cohort_starts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name_ar}"

    @property
    def enrolled_count(self) -> int:
        """
        Computed, never stored.

        The demo kept this as a column and it drifted: 18 stored against 2
        actual enrolments. A count that can disagree with the rows it counts
        is worse than a query.
        """
        return self.enrollments.exclude(
            status__in=[EnrollmentStatus.CANCELLED, EnrollmentStatus.TRANSFERRED_OUT]
        ).count()


class Enrollment(models.Model):
    """
    A participant registered on a cohort (DATA_MODEL §7.3).

    Carries no money. What is owed comes from ``billing.ChargeLine``, what was
    paid from ``cashbox.PaymentAllocation``, and the balance from
    ``billing.services.account_service.get_account_state()`` — the only place
    the equation lives (P1, DATA_MODEL §8.2).
    """

    code = ShortCode(unique=True, verbose_name=_("رمز التسجيل"))
    participant = models.ForeignKey(
        "people.Participant",
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("المشارك"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("الدفعة"),
    )
    enrolled_on = models.DateField(verbose_name=_("تاريخ التسجيل"))
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("قائمة الأسعار"),
        help_text=_("BR-012 — القائمة التي سُعِّر بها هذا التسجيل"),
    )

    status = ShortCode(
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.PENDING_FINANCE,
        verbose_name=_("الحالة"),
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_note_ar = models.CharField(max_length=255, blank=True)

    # --- Q-06 / BR-095 — the audited manual lecture counter ----------------
    lectures_attended = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("عدد المحاضرات"),
        help_text=_("فارغ = لم يُدخَل بعد — ويختلف عن صفر (Q-06)"),
    )
    attendance_source = ShortCode(
        choices=AttendanceSource.choices, blank=True, verbose_name=_("مصدر العدّاد")
    )
    attendance_record_ref = models.CharField(
        max_length=255, blank=True, verbose_name=_("مرجع المصدر")
    )
    attendance_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verified_attendance",
        verbose_name=_("تحقّق منه"),
    )
    attendance_verified_at = models.DateTimeField(null=True, blank=True)
    attendance_note = models.TextField(blank=True)

    # --- BR-018 — no approval without a recorded voucher -------------------
    voucher_received = models.BooleanField(default=False, verbose_name=_("استُلم الوصل"))
    voucher_received_at = models.DateTimeField(null=True, blank=True)
    voucher_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="received_vouchers",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_enrollments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("تسجيل")
        verbose_name_plural = _("التسجيلات")
        ordering = ["-enrolled_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=EnrollmentStatus.values),
                name="operations_enrollment_status_valid",
            ),
            # Q-19 — one enrolment per participant per cohort.
            models.UniqueConstraint(
                fields=["participant", "cohort"],
                name="operations_enrollment_unique_participant_cohort",
            ),
            models.CheckConstraint(
                condition=models.Q(voucher_received=False)
                | models.Q(voucher_received_at__isnull=False),
                name="operations_enrollment_voucher_timestamped",
            ),
            # BR-018 at the database level — approval without a voucher is a
            # registration approved with no evidence anything was paid.
            models.CheckConstraint(
                condition=models.Q(approved_at__isnull=True) | models.Q(voucher_received=True),
                name="operations_enrollment_no_approval_without_voucher",
            ),
            # BR-095 / Q-06 — a lecture count decides who pays a transfer
            # difference and what a partner earns. Recording one without its
            # source, verifier and timestamp is impossible here, which is what
            # turns a typed number into an auditable assertion.
            models.CheckConstraint(
                condition=models.Q(lectures_attended__isnull=True)
                | (
                    ~models.Q(attendance_source="")
                    & ~models.Q(attendance_record_ref="")
                    & models.Q(attendance_verified_by__isnull=False)
                    & models.Q(attendance_verified_at__isnull=False)
                ),
                name="operations_enrollment_attendance_documented",
            ),
        ]
        indexes = [
            models.Index(fields=["participant", "status"], name="ops_enr_part_status_idx"),
            models.Index(fields=["cohort", "status"], name="ops_enr_cohort_status_idx"),
            models.Index(fields=["status", "enrolled_on"], name="ops_enr_status_date_idx"),
            models.Index(fields=["price_list"], name="ops_enr_pricelist_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.participant.name_ar}"
