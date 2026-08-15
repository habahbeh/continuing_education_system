"""
Operations — the participant lifecycle (DATA_MODEL §7).

Cohort and Enrollment arrived early, in Sprint 4: every billing and cashbox
entity has a foreign key to ``Enrollment``, so billing could not be built or
tested without one. Sprint 6 completes the app — the ministry gate, the status
history, transfers and special cases — and fills in the three Enrollment
fields that were deliberately left out rather than stubbed.

Three defects the demo demonstrably had are made unrepresentable here:

* the ministry approval gate was **displayed and not enforced**, so enrolments
  could be taken on an unapproved cohort (BR-013, D-21);
* the transfer category waiver was granted **silently** on a dropdown pick,
  with no approver and no reason (BR-065, C-12);
* an enrolment count was **stored** and drifted — 18 against 2 actual.

**Absent, permanently:** ``Cohort.enrolled_count`` and any ``paid`` /
``total`` / ``balance`` on Enrollment. Money and counts are computed from the
ledger (P1); a stored total is a number that can disagree with the rows that
produced it.

**Absent, deferred to Sprint 7:** Clearance, ClearanceStep and Certificate.
A negative transfer difference becomes a credit balance here, but RETURNING
it is BR-071 and belongs with clearance.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ImmutableRecordError
from apps.core.fields import DisplayRef, Money, ShortCode


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


class MoheStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسودة")
    SUBMITTED = "SUBMITTED", _("مُرسَل للوزارة")
    APPROVED = "APPROVED", _("معتمد")
    REJECTED = "REJECTED", _("مرفوض")


class TransferReason(models.TextChoices):
    PARTICIPANT_REQUEST = "PARTICIPANT_REQUEST", _("طلب المشارك")
    CENTER_CANCELLATION = "CENTER_CANCELLATION", _("إلغاء المركز للدورة")


class TransferStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسودة")
    PENDING_MANAGER = "PENDING_MANAGER", _("بانتظار تنسيب المدير")
    PENDING_FINANCE = "PENDING_FINANCE", _("بانتظار تسوية المالية")
    EXECUTED = "EXECUTED", _("منفَّذ")
    REJECTED = "REJECTED", _("مرفوض")


class SpecialCaseType(models.TextChoices):
    CANCELLATION = "CANCELLATION", _("إلغاء")
    DISMISSAL = "DISMISSAL", _("فصل")
    DEFERRAL = "DEFERRAL", _("ترحيل لدفعة لاحقة")
    SUBSTITUTION = "SUBSTITUTION", _("إحلال")
    CREDIT_TRANSFER = "CREDIT_TRANSFER", _("نقل رصيد")
    CREDIT_BALANCE = "CREDIT_BALANCE", _("رصيد دائن")


class SpecialCaseStatus(models.TextChoices):
    OPEN = "OPEN", _("قائمة")
    SETTLED = "SETTLED", _("مسوّاة")
    CANCELLED = "CANCELLED", _("ملغاة")


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

    # --- Sprint 6 — the lifecycle links Sprint 4 left out ------------------
    mohe_uploaded_on = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("رُفع للوزارة في"),
        help_text=_("BR-019 — ضمن المهلة الوزارية وبعد اعتماد التسجيل"),
    )
    transferred_to = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferred_from",
        verbose_name=_("نُقل إلى"),
        help_text=_("BR-064 — التسجيل الذي نُقل إليه هذا المشارك"),
    )
    deferred_to = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deferred_from",
        verbose_name=_("رُحِّل إلى"),
        help_text=_("BR-069 — التسجيل على الدفعة اللاحقة"),
    )

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
            # BR-019, structural half — a name cannot be reported to the
            # ministry for an enrolment nobody approved. The deadline half is
            # service-level, because the deadline lives on the submission.
            models.CheckConstraint(
                condition=models.Q(mohe_uploaded_on__isnull=True)
                | models.Q(approved_at__isnull=False),
                name="operations_enrollment_upload_needs_approval",
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

    @property
    def is_final(self) -> bool:
        """
        WORKFLOWS §1.2 — states nothing moves out of without a documented reversal.

        A transferred-out or dismissed enrolment that quietly returns to ACTIVE
        would restate what a partner earned and what a participant owes.
        """
        return self.status in {
            EnrollmentStatus.COMPLETED,
            EnrollmentStatus.WITHDRAWN,
            EnrollmentStatus.DISMISSED,
            EnrollmentStatus.CANCELLED,
            EnrollmentStatus.TRANSFERRED_OUT,
        }

    @property
    def attendance_is_documented(self) -> bool:
        """
        Q-06 / BR-095 — is the lecture counter an assertion or a guess?

        The transfer deadline (BR-062) is decided on this number, so a rule
        cannot be evaluated against an undocumented one. The database already
        refuses a half-documented count; this reports whether one exists at all.
        """
        return (
            self.lectures_attended is not None
            and bool(self.attendance_source)
            and bool(self.attendance_record_ref)
            and self.attendance_verified_by_id is not None
            and self.attendance_verified_at is not None
        )


class MoheSubmission(models.Model):
    """
    A cohort's file with the ministry (DATA_MODEL §7.2, BR-013 … BR-016).

    The demo displayed the approval requirement on screen and enforced it
    nowhere — enrolments could be taken on a cohort the ministry had not
    approved, which is not a UI defect but an illegal registration. The gate
    now lives in ``enrollment_service.create_enrollment()`` and reads this
    table (BR-013, D-21).

    Rejections keep their reason **verbatim as received** (BR-014). A summary
    written from memory is not what a resubmission has to answer.
    """

    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="mohe_submissions",
        verbose_name=_("الدفعة"),
    )
    status = ShortCode(
        choices=MoheStatus.choices, default=MoheStatus.DRAFT, verbose_name=_("الحالة")
    )
    mohe_course_number = DisplayRef(blank=True, verbose_name=_("الرقم الوزاري"))
    submitted_on = models.DateField(null=True, blank=True, verbose_name=_("أُرسل في"))
    decided_on = models.DateField(null=True, blank=True, verbose_name=_("تاريخ القرار"))
    registration_deadline = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("مهلة التسجيل"),
        help_text=_("BR-015 · BR-019 — بعدها يُمنع رفع أسماء جديدة"),
    )
    rejection_reason_ar = models.TextField(blank=True, verbose_name=_("سبب الرفض"))

    # --- the submission form's own content (BR-014) ------------------------
    training_axes_ar = models.TextField(blank=True, verbose_name=_("محاور التدريب"))
    practical_aspects_ar = models.TextField(blank=True, verbose_name=_("الجوانب العملية"))
    target_audience_ar = models.TextField(blank=True, verbose_name=_("الفئة المستهدفة"))
    trainer_name = models.CharField(max_length=150, blank=True, verbose_name=_("المدرب"))
    trainer_qualifications = models.TextField(blank=True, verbose_name=_("مؤهلات المدرب"))
    training_location = models.CharField(max_length=150, blank=True, verbose_name=_("مكان التدريب"))
    responsible_entity = models.CharField(
        max_length=150, blank=True, verbose_name=_("الجهة المسؤولة")
    )

    resubmission_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resubmissions",
        verbose_name=_("إعادة إرسال لـ"),
    )

    # MySQL does not collide NULLs, and here that is exactly what is wanted:
    # this column holds 1 only while the row is APPROVED, so drafts and
    # rejections never conflict — and a cohort cannot hold two approvals.
    approved_key = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mohe_submissions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("طلب وزاري")
        verbose_name_plural = _("الطلبات الوزارية")
        ordering = ["-submitted_on", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=MoheStatus.values),
                name="operations_mohe_status_valid",
            ),
            # C-16 — an approval with no ministry number and no decision date
            # is an approval nobody can produce evidence for.
            models.CheckConstraint(
                condition=~models.Q(status=MoheStatus.APPROVED)
                | (~models.Q(mohe_course_number="") & models.Q(decided_on__isnull=False)),
                name="operations_mohe_approved_has_number",
            ),
            # BR-014 — a rejection without its reason cannot be answered by a
            # resubmission.
            models.CheckConstraint(
                condition=~models.Q(status=MoheStatus.REJECTED) | ~models.Q(rejection_reason_ar=""),
                name="operations_mohe_rejected_has_reason",
            ),
            models.UniqueConstraint(
                fields=["cohort", "approved_key"],
                name="operations_mohe_one_approval_per_cohort",
            ),
        ]
        indexes = [
            models.Index(fields=["cohort", "status"], name="ops_mohe_cohort_status_idx"),
            models.Index(fields=["registration_deadline"], name="ops_mohe_deadline_idx"),
            models.Index(fields=["status"], name="ops_mohe_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.cohort.code} — {self.get_status_display()}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.approved_key = 1 if self.status == MoheStatus.APPROVED else None
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class EnrollmentStatusHistory(models.Model):
    """
    Every status change, append-only (DATA_MODEL §7.4).

    This is the evidence behind BR-045: a partner's entitlement turns on an
    enrolment's status, so "when did this become WITHDRAWN, and who said so"
    has to be answerable months after a claim was signed.
    """

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="status_history"
    )
    from_status = ShortCode(blank=True, verbose_name=_("من حالة"))
    to_status = ShortCode(verbose_name=_("إلى حالة"))
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="enrollment_status_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    reason_ar = models.CharField(max_length=255, blank=True, verbose_name=_("السبب"))
    reference = DisplayRef(blank=True, verbose_name=_("المرجع"))

    class Meta:
        verbose_name = _("سجل حالة تسجيل")
        verbose_name_plural = _("سجل حالات التسجيل")
        ordering = ["enrollment", "changed_at"]
        constraints = [
            # A "change" that changes nothing is a bug, and it would pad the
            # record BR-045 is audited against with rows that mean nothing.
            models.CheckConstraint(
                condition=~models.Q(from_status=models.F("to_status")),
                name="operations_status_history_actually_changes",
            ),
            models.CheckConstraint(
                condition=models.Q(to_status__in=EnrollmentStatus.values),
                name="operations_status_history_to_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "changed_at"], name="ops_esh_enr_time_idx"),
            models.Index(fields=["to_status", "changed_at"], name="ops_esh_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.enrollment_id}: {self.from_status or '—'} ← {self.to_status}"

    def save(self, *args: object, **kwargs: object) -> None:
        """Append-only — the same guarantee the audit trail carries."""
        if self.pk is not None:
            raise ImmutableRecordError(
                "سجل حالات التسجيل مضاف فقط — لا يُعدَّل ولا يُحذف (DATA_MODEL §7.4)."
            )
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class Transfer(models.Model):
    """
    A move between short courses (DATA_MODEL §7.5, BR-060 … BR-066).

    ``same_category`` and ``lectures_attended_at_request`` are COPIED at the
    moment of the request, not read live. They are the evidence the decision
    rested on, and the catalogue will move underneath them: a course
    recategorised next term must not retroactively justify — or condemn — a
    transfer that was already decided (WORKFLOWS §5.3).
    """

    code = ShortCode(unique=True, verbose_name=_("رمز النقل"))
    from_enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="transfers_out",
        verbose_name=_("من تسجيل"),
    )
    to_enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_in",
        verbose_name=_("إلى تسجيل"),
        help_text=_("فارغ حتى التنفيذ — يُنشأ التسجيل الجديد عندئذ"),
    )
    to_cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="incoming_transfers",
        verbose_name=_("إلى دفعة"),
    )
    requested_on = models.DateField(verbose_name=_("تاريخ الطلب"))
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transfers_requested"
    )
    reason = ShortCode(choices=TransferReason.choices, verbose_name=_("السبب"))

    # --- frozen evidence of the rule check (WORKFLOWS §5.3) ----------------
    lectures_attended_at_request = models.PositiveSmallIntegerField(
        default=0, verbose_name=_("المحاضرات وقت الطلب")
    )
    attendance_record_ref_at_request = models.CharField(
        max_length=255, blank=True, verbose_name=_("مرجع العدّاد وقت الطلب")
    )
    same_category = models.BooleanField(default=True, verbose_name=_("نفس المجال"))

    # --- BR-065 / C-12 — the demo's silent waiver --------------------------
    category_waiver_granted = models.BooleanField(
        default=False, verbose_name=_("مُنح استثناء المجال")
    )
    category_waiver_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="category_waivers_granted",
        verbose_name=_("مُجيز الاستثناء"),
    )
    category_waiver_reason_ar = models.TextField(blank=True, verbose_name=_("سبب الاستثناء"))

    registration_fee_transferred = Money(default=0, verbose_name=_("رسم التسجيل المُرحَّل"))
    fee_difference = Money(
        default=0,
        verbose_name=_("فرق الرسوم"),
        help_text=_("موجب = يدفعه المشارك · سالب = رصيد دائن له (BR-064)"),
    )

    status = ShortCode(
        choices=TransferStatus.choices, default=TransferStatus.DRAFT, verbose_name=_("الحالة")
    )
    manager_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_recommended",
    )
    manager_approved_at = models.DateTimeField(null=True, blank=True)
    finance_settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_settled",
    )
    finance_settled_at = models.DateTimeField(null=True, blank=True)
    rejection_reason_ar = models.TextField(blank=True, verbose_name=_("سبب الرفض"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("نقل")
        verbose_name_plural = _("عمليات النقل")
        ordering = ["-requested_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=TransferStatus.values),
                name="operations_transfer_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(reason__in=TransferReason.values),
                name="operations_transfer_reason_valid",
            ),
            # C-12 — the demo granted this waiver on a dropdown pick alone,
            # with no approver and no reason. That is a control gap, not a
            # convenience: the whole point of BR-061 is that someone owns the
            # decision to set it aside, and only when the CENTRE cancelled.
            models.CheckConstraint(
                condition=models.Q(category_waiver_granted=False)
                | (
                    models.Q(category_waiver_by__isnull=False)
                    & ~models.Q(category_waiver_reason_ar="")
                    & models.Q(reason=TransferReason.CENTER_CANCELLATION)
                ),
                name="operations_transfer_waiver_is_justified",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=TransferStatus.EXECUTED)
                | models.Q(to_enrollment__isnull=False),
                name="operations_transfer_executed_has_target",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=TransferStatus.REJECTED)
                | ~models.Q(rejection_reason_ar=""),
                name="operations_transfer_rejected_has_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "requested_on"], name="ops_tr_status_date_idx"),
            models.Index(fields=["from_enrollment"], name="ops_tr_from_idx"),
            models.Index(fields=["to_cohort"], name="ops_tr_to_cohort_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.get_status_display()}"


class SpecialCase(models.Model):
    """
    A documented exception to the ordinary lifecycle (DATA_MODEL §7.6).

    Every one of the six kinds has a financial consequence, so each carries
    its own statement of that consequence in words. ``financial_effect_ar``
    is not decoration: a deferral that moves 700 dinars and a substitution
    that moves a seat look identical in a status column.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز الحالة"))
    case_type = ShortCode(choices=SpecialCaseType.choices, verbose_name=_("نوع الحالة"))
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="special_cases",
        verbose_name=_("التسجيل"),
    )
    related_enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="related_special_cases",
        verbose_name=_("التسجيل المرتبط"),
        help_text=_("التسجيل الجديد في الترحيل أو الإحلال"),
    )
    occurred_on = models.DateField(verbose_name=_("تاريخ الحدث"))
    detail_ar = models.TextField(verbose_name=_("التفصيل"))
    financial_effect_ar = models.TextField(blank=True, verbose_name=_("الأثر المالي"))
    decision_reference = DisplayRef(blank=True, verbose_name=_("مرجع القرار"))
    status = ShortCode(
        choices=SpecialCaseStatus.choices,
        default=SpecialCaseStatus.OPEN,
        verbose_name=_("الحالة"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="special_cases_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("حالة خاصة")
        verbose_name_plural = _("الحالات الخاصة")
        ordering = ["-occurred_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(case_type__in=SpecialCaseType.values),
                name="operations_special_case_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SpecialCaseStatus.values),
                name="operations_special_case_status_valid",
            ),
            # BR-067 — a dismissal costs the participant their fees and their
            # clearance. It does not happen on somebody's say-so.
            models.CheckConstraint(
                condition=~models.Q(case_type=SpecialCaseType.DISMISSAL)
                | ~models.Q(decision_reference=""),
                name="operations_special_case_dismissal_has_reference",
            ),
        ]
        indexes = [
            models.Index(fields=["case_type", "status"], name="ops_sc_type_status_idx"),
            models.Index(fields=["enrollment"], name="ops_sc_enrollment_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.get_case_type_display()}"
