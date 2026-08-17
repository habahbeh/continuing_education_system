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


class ClearanceCaseType(models.TextChoices):
    GRADUATION = "GRADUATION", _("تخرج")
    WITHDRAWAL = "WITHDRAWAL", _("انسحاب")
    DISMISSAL = "DISMISSAL", _("فصل")


class ClearanceStatus(models.TextChoices):
    OPEN = "OPEN", _("مفتوحة")
    IN_PROGRESS = "IN_PROGRESS", _("قيد التنفيذ")
    BLOCKED = "BLOCKED", _("موقوفة")
    COMPLETED = "COMPLETED", _("مكتملة")
    CANCELLED = "CANCELLED", _("ملغاة")


class CertificateStatus(models.TextChoices):
    ISSUED = "ISSUED", _("صادرة")
    DELIVERED = "DELIVERED", _("مُسلَّمة")
    REPLACED = "REPLACED", _("استُبدلت")


class GradeSource(models.TextChoices):
    """BR-078 — MANUAL in v1; CALCULATED when a grades module exists."""

    MANUAL = "MANUAL", _("إدخال يدوي")
    CALCULATED = "CALCULATED", _("محتسب")


class Clearance(models.Model):
    """
    Clearing a participant out (DATA_MODEL §7.7, BR-072 … BR-074).

    Three sequential steps on form ``CS Fm 7.18 Rev A``: the centre recovers
    its property, finance verifies the account is EXACTLY zero, the centre
    hands over the certificate. Nothing about this is a formality — BR-075
    makes a completed clearance the precondition for a certificate existing at
    all, so this is the gate the whole ending runs through.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز البراءة"))
    participant = models.ForeignKey(
        "people.Participant", on_delete=models.PROTECT, related_name="clearances"
    )
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="clearances")
    case_type = ShortCode(choices=ClearanceCaseType.choices, verbose_name=_("الحالة"))
    opened_on = models.DateField(verbose_name=_("فُتحت في"))
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="clearances_opened"
    )
    status = ShortCode(
        choices=ClearanceStatus.choices,
        default=ClearanceStatus.OPEN,
        verbose_name=_("حالة البراءة"),
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason_ar = models.TextField(blank=True, verbose_name=_("سبب الإلغاء"))

    # Same device as MoheSubmission: holds 1 while the clearance is live, NULL
    # once cancelled. MySQL does not collide NULLs, so a cancelled clearance
    # never blocks a fresh one — but two live ones cannot exist.
    active_key = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("براءة ذمة")
        verbose_name_plural = _("براءات الذمة")
        ordering = ["-opened_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(case_type__in=ClearanceCaseType.values),
                name="operations_clearance_case_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ClearanceStatus.values),
                name="operations_clearance_status_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=ClearanceStatus.COMPLETED)
                | models.Q(completed_at__isnull=False),
                name="operations_clearance_completed_has_timestamp",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=ClearanceStatus.CANCELLED)
                | ~models.Q(cancellation_reason_ar=""),
                name="operations_clearance_cancelled_has_reason",
            ),
            # Two completed clearances on one enrolment would let two
            # certificates be issued for one course.
            models.UniqueConstraint(
                fields=["enrollment", "active_key"],
                name="operations_clearance_one_live_per_enrollment",
            ),
        ]
        indexes = [
            models.Index(fields=["participant", "status"], name="ops_clr_part_status_idx"),
            models.Index(fields=["status", "opened_on"], name="ops_clr_status_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.get_status_display()}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.active_key = None if self.status == ClearanceStatus.CANCELLED else 1
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class ClearanceStep(models.Model):
    """
    One of the three steps (DATA_MODEL §7.7).

    Step 2 carries the weight. Its constraints make two things impossible in
    the database rather than merely discouraged in a service:

    * closing it while the balance is anything other than exactly zero —
      **in either direction** (C-09, BR-073). A credit balance stops a
      clearance just as firmly as a debt: the centre owing the participant is
      not "close enough to settled".
    * closing it on one signature, or on two signatures from one person
      (C-29, C-30, BR-074, D-30).

    That ``second_certified_by`` must hold the FINANCE_MANAGER role is
    enforced in the POLICY layer, not here: a role can be changed on a user
    afterwards, and a constraint has to stay true for rows written years ago.
    """

    clearance = models.ForeignKey(Clearance, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveSmallIntegerField(verbose_name=_("رقم الخطوة"))
    name_ar = models.CharField(max_length=255, verbose_name=_("اسم الخطوة"))

    #: Step 1 — what the centre lent out and wants back.
    custody_items = models.JSONField(default=list, blank=True, verbose_name=_("العُهد"))

    #: Step 2 — positive = the participant owes us · negative = we owe them.
    balance_at_check = Money(null=True, blank=True, verbose_name=_("الرصيد عند الفحص"))

    deposit_return_amount = Money(null=True, blank=True, verbose_name=_("مبلغ إعادة التأمين"))
    deposit_return = models.ForeignKey(
        "billing.DepositReturn",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clearance_steps",
    )
    deposit_forfeiture = models.ForeignKey(
        "billing.DepositForfeiture",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clearance_steps",
    )
    #: BR-071 — the credit handed back so the balance could reach zero.
    credit_return = models.ForeignKey(
        "billing.CreditReturn",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clearance_steps",
    )

    certified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clearance_steps_certified",
    )
    certified_at = models.DateTimeField(null=True, blank=True)
    second_certified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clearance_steps_second_certified",
    )
    second_certified_at = models.DateTimeField(null=True, blank=True)
    is_done = models.BooleanField(default=False, verbose_name=_("مكتملة"))

    class Meta:
        verbose_name = _("خطوة براءة ذمة")
        verbose_name_plural = _("خطوات براءة الذمة")
        ordering = ["clearance", "step_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["clearance", "step_number"], name="operations_clearance_step_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(step_number__gte=1) & models.Q(step_number__lte=3),
                name="operations_clearance_step_number_range",
            ),
            # C-09 · BR-073 — zero, in either direction, or the step does not
            # close. This is the constraint the demo had no equivalent of.
            models.CheckConstraint(
                condition=~models.Q(step_number=2)
                | models.Q(is_done=False)
                | models.Q(balance_at_check=0),
                name="operations_clearance_step_finance_needs_zero_balance",
            ),
            models.CheckConstraint(
                condition=models.Q(is_done=False) | models.Q(certified_by__isnull=False),
                name="operations_clearance_step_done_has_certifier",
            ),
            # C-29 · BR-074 — one signature never closes the financial step.
            models.CheckConstraint(
                condition=~models.Q(step_number=2)
                | models.Q(is_done=False)
                | models.Q(second_certified_by__isnull=False),
                name="operations_clearance_step_finance_needs_second_cert",
            ),
            # C-30 · D-30 — two signatures from one person is a single control
            # wearing a costume.
            models.CheckConstraint(
                condition=models.Q(second_certified_by__isnull=True)
                | ~models.Q(second_certified_by=models.F("certified_by")),
                name="operations_clearance_step_second_certifier_differs",
            ),
            # Q-01 — deposit settlement belongs to the financial step alone.
            models.CheckConstraint(
                condition=models.Q(deposit_return_amount__isnull=True) | models.Q(step_number=2),
                name="operations_clearance_step_deposit_fields_on_step_2",
            ),
            # BR-097 — a deposit is returned or forfeited, never both.
            models.CheckConstraint(
                condition=models.Q(deposit_return__isnull=True)
                | models.Q(deposit_forfeiture__isnull=True),
                name="operations_clearance_step_not_return_and_forfeit",
            ),
            models.CheckConstraint(
                condition=models.Q(credit_return__isnull=True) | models.Q(step_number=2),
                name="operations_clearance_step_credit_return_on_step_2",
            ),
        ]
        indexes = [
            models.Index(fields=["clearance", "is_done"], name="ops_clrstep_done_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.clearance_id}/{self.step_number} — {self.name_ar}"


class Certificate(models.Model):
    """
    The certificate (DATA_MODEL §7.8, BR-075 … BR-079).

    🐞 The demo issued certificate ``2026000002`` as a REPLACEMENT with
    neither a clearance nor an original certificate to replace — a complete
    way around BR-075. ``operations_certificate_clearance_or_original`` makes
    that unrepresentable: either a completed clearance, or an original this
    one replaces.

    Programme name, duration and hours are SNAPSHOTS (ADR-012). A certificate
    is a document that leaves the building and is read years later; the
    catalogue behind it will have moved on.

    ``grade`` carries NO check constraint. BR-078 lists four grades today, but
    the vocabulary belongs to the centre — it lives in the ``certificate_grades``
    setting, following the same decision taken for qualifications and cities
    (Q-31, client instruction 2026-08-15).
    """

    certificate_number = models.CharField(max_length=10, unique=True, verbose_name=_("رقم الشهادة"))
    participant = models.ForeignKey(
        "people.Participant", on_delete=models.PROTECT, related_name="certificates"
    )
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="certificates"
    )
    clearance = models.ForeignKey(
        Clearance,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
        help_text=_("إلزامية إلا لبدل الفاقد (BR-075 · C-08)"),
    )

    program_name_snapshot = models.CharField(max_length=255, verbose_name=_("اسم البرنامج"))
    duration_text = models.CharField(max_length=150, blank=True, verbose_name=_("المدة"))
    training_hours = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name=_("عدد الساعات")
    )

    grade = ShortCode(verbose_name=_("التقدير"))
    grade_source = ShortCode(
        choices=GradeSource.choices,
        default=GradeSource.MANUAL,
        verbose_name=_("مصدر التقدير"),
    )

    issued_on = models.DateField(verbose_name=_("تاريخ الإصدار"))
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="certificates_issued"
    )

    is_replacement = models.BooleanField(default=False, verbose_name=_("بدل فاقد"))
    replaces = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="replaced_by"
    )
    #: BR-038 — the replacement fee, so "was it collected" is answered by a
    #: link rather than by guessing which extra fee on the account meant this.
    replacement_fee_line = models.ForeignKey(
        "billing.ChargeLine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_certificates",
    )

    status = ShortCode(
        choices=CertificateStatus.choices,
        default=CertificateStatus.ISSUED,
        verbose_name=_("الحالة"),
    )
    delivered_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("شهادة")
        verbose_name_plural = _("الشهادات")
        ordering = ["-issued_on", "certificate_number"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=CertificateStatus.values),
                name="operations_certificate_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(grade_source__in=GradeSource.values),
                name="operations_certificate_grade_source_valid",
            ),
            # C-20 · BR-076 — ten digits, year plus sequence.
            models.CheckConstraint(
                condition=models.Q(certificate_number__regex=r"^[0-9]{10}$"),
                name="operations_certificate_number_ten_digits",
            ),
            # C-08 — the demo's replacement loophole, closed.
            models.CheckConstraint(
                condition=(
                    models.Q(is_replacement=False, clearance__isnull=False)
                    | models.Q(is_replacement=True, replaces__isnull=False)
                ),
                name="operations_certificate_clearance_or_original",
            ),
        ]
        indexes = [
            models.Index(fields=["participant"], name="ops_cert_participant_idx"),
            models.Index(fields=["enrollment"], name="ops_cert_enrollment_idx"),
            models.Index(fields=["status", "issued_on"], name="ops_cert_status_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.certificate_number} — {self.participant_id}"
