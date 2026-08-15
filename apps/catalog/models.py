"""
Catalogue and pricing (DATA_MODEL §5).

Three ideas carry this module, and each one is a correction of the demo:

**Prices are dated, approved, and frozen — never edited.** A price list moves
DRAFT → APPROVED → ARCHIVED and becomes immutable the moment it is approved
(BR-008, D-14). Correcting a price means issuing a new list, so an enrolment
made last term keeps the amounts it was made with.

**Fee exceptions are rows, not code.** The demo carried two columns,
`regFeeCenter` and `regFeeUni`, with nowhere to put the third participant
category its own UI offered. ``RegistrationFeeRule`` is keyed by
``(program, participant_category)`` so a fourth category — or the client's
answer to Q-10 — is a row, not a migration (client feedback 2026-08-15).

**A deposit exists only where a policy says so.** No programme carries a
deposit by default (BR-096). The amount lives on the price list item, not the
programme, because it IS a price: dated, frozen at enrolment, and changing it
later must not touch an existing registration.

CHECK constraints are used for structural invariants and for closed system
vocabularies only. Values the client owns — deposit amounts, fee exceptions,
refund triggers, the course-category list — carry no CHECK, so changing them
is data entry rather than a migration (client feedback 2026-08-15).
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, NameAr, NameEn, ShortCode


class ProgramType(models.TextChoices):
    """SPEC §2 — the three programme kinds. A closed system vocabulary."""

    DIPLOMA = "DIPLOMA", _("دبلوم تدريبي")
    SHORT_COURSE = "SHORT_COURSE", _("دورة قصيرة")
    ONLINE_COURSE = "ONLINE_COURSE", _("دورة أونلاين")


class PriceListStatus(models.TextChoices):
    """A state machine owned by the system, not a client-editable list."""

    DRAFT = "DRAFT", _("مسودة")
    APPROVED = "APPROVED", _("معتمدة")
    ARCHIVED = "ARCHIVED", _("مؤرشفة")


class CourseCategory(models.Model):
    """
    Course field (BR-061) — the eight areas that bound a permitted transfer.

    Rows, not choices: the centre may add or retire an area, and BR-061 only
    cares that two programmes share one.
    """

    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = NameAr(verbose_name=_("المجال"))
    is_active = models.BooleanField(default=True, verbose_name=_("نشط"))

    class Meta:
        verbose_name = _("مجال الدورة")
        verbose_name_plural = _("مجالات الدورات")
        ordering = ["name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class KnowledgeField(models.Model):
    """Ministry knowledge field (DATA_MODEL §5.2)."""

    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = NameAr(verbose_name=_("المجال المعرفي"))
    is_active = models.BooleanField(default=True, verbose_name=_("نشط"))

    class Meta:
        verbose_name = _("مجال معرفي")
        verbose_name_plural = _("المجالات المعرفية")
        ordering = ["name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class Program(models.Model):
    """A diploma, short course or online course (DATA_MODEL §5.3)."""

    code = ShortCode(unique=True, verbose_name=_("رمز البرنامج"))
    program_type = ShortCode(choices=ProgramType.choices, verbose_name=_("النوع"))
    name_ar = NameAr(verbose_name=_("الاسم"))
    name_en = NameEn(blank=True, verbose_name=_("الاسم بالإنجليزية"))
    training_hours = models.PositiveIntegerField(default=0, verbose_name=_("الساعات التدريبية"))

    knowledge_field = models.ForeignKey(
        KnowledgeField,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="programs",
        verbose_name=_("المجال المعرفي"),
    )
    specialization = models.CharField(max_length=150, blank=True, verbose_name=_("التخصص"))
    course_category = models.ForeignKey(
        CourseCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="programs",
        verbose_name=_("مجال الدورة"),
        help_text=_("إلزامي للدورات القصيرة — يحدد نطاق النقل المسموح (BR-061)"),
    )

    is_leveled = models.BooleanField(default=False, verbose_name=_("ذات مستويات"))
    levels_count = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name=_("عدد المستويات")
    )

    consumables_per_student = Money(default=0, verbose_name=_("مستهلكات لكل مشارك"))
    minimum_first_payment_override = Money(
        null=True,
        blank=True,
        verbose_name=_("حد أدنى خاص للدفعة الأولى"),
        help_text=_("Q-15 — يتجاوز الإعداد العام عند الحاجة"),
    )

    is_active = models.BooleanField(default=True, verbose_name=_("نشط"))

    class Meta:
        verbose_name = _("برنامج")
        verbose_name_plural = _("البرامج")
        ordering = ["program_type", "name_ar"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(program_type__in=ProgramType.values),
                name="catalog_program_type_valid",
            ),
            # BR-061 — a short course without a field cannot be transferred
            # against, so the rule would silently never apply.
            models.CheckConstraint(
                condition=~models.Q(program_type=ProgramType.SHORT_COURSE)
                | models.Q(course_category__isnull=False),
                name="catalog_program_short_course_has_category",
            ),
            # BR-011 — "levelled" with one level is a contradiction.
            models.CheckConstraint(
                condition=models.Q(is_leveled=False) | models.Q(levels_count__gte=2),
                name="catalog_program_levels_at_least_two",
            ),
            models.CheckConstraint(
                condition=models.Q(consumables_per_student__gte=0),
                name="catalog_program_consumables_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["program_type"], name="catalog_program_type_idx"),
            models.Index(fields=["is_active"], name="catalog_program_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name_ar}"


class Subject(models.Model):
    """
    A diploma subject (DATA_MODEL §5.4, BR-006, BR-007).

    A price of zero is legal (BR-007) and is NOT the same as "no price": it
    means the subject is taught at no charge and generates no partner
    entitlement, which is why the column is not nullable.
    """

    program = models.ForeignKey(
        Program,
        on_delete=models.CASCADE,
        related_name="subjects",
        verbose_name=_("البرنامج"),
    )
    sequence = models.PositiveSmallIntegerField(verbose_name=_("التسلسل"))
    name_ar = NameAr(verbose_name=_("اسم المادة"))
    training_hours = models.PositiveIntegerField(default=0, verbose_name=_("الساعات"))
    price = Money(default=0, verbose_name=_("سعر المادة"))

    class Meta:
        verbose_name = _("مادة")
        verbose_name_plural = _("المواد")
        ordering = ["program", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "sequence"], name="catalog_subject_unique_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="catalog_subject_price_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sequence}. {self.name_ar}"


class DepositPolicy(models.Model):
    """
    Refundable-deposit policy (DATA_MODEL §5.6.1, BR-096, BR-097).

    A programme with no policy carries no deposit line at all — the feature is
    absent rather than zero (BR-096).

    ``refund_trigger`` carries NO CHECK constraint deliberately. Q-30 is still
    open, the client owns the answer, and the client's instruction of
    2026-08-15 was to keep these values configurable. A constraint here would
    make a fifth trigger a migration instead of a row.
    """

    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = NameAr(verbose_name=_("اسم السياسة"))

    is_required = models.BooleanField(
        default=True,
        verbose_name=_("إلزامي"),
        help_text=_("إلزامي على المشارك أم اختياري"),
    )
    refund_trigger = ShortCode(
        verbose_name=_("مُحفّز الاسترداد"),
        help_text=_("🔴 Q-30 — قيمة قابلة للإعداد بلا قيد قاعدة بيانات"),
    )
    forfeit_on = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("حالات المصادرة"),
        help_text=_('مثال ["DISMISSED", "NOT_ATTENDED"] — 🔴 Q-30'),
    )
    is_taxable = models.BooleanField(
        default=False,
        verbose_name=_("خاضع للضريبة"),
        help_text=_("🔴 Q-27 — الافتراض: التأمين التزام لا إيراد فلا يخضع"),
    )
    allows_partial_deduction = models.BooleanField(default=True, verbose_name=_("يسمح بحسم جزئي"))
    claim_deadline_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("مهلة المطالبة بالأيام"),
        help_text=_("فارغ = بلا مهلة سقوط"),
    )
    notes_ar = models.TextField(blank=True, verbose_name=_("ملاحظات"))
    is_active = models.BooleanField(default=True, verbose_name=_("نشطة"))

    class Meta:
        verbose_name = _("سياسة تأمين")
        verbose_name_plural = _("سياسات التأمين")
        ordering = ["name_ar"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(claim_deadline_days__isnull=True)
                | models.Q(claim_deadline_days__gt=0),
                name="catalog_deposit_policy_deadline_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name_ar


class PriceList(models.Model):
    """
    A dated, approved price list (DATA_MODEL §5.5, BR-008).

    Approval freezes it (D-14). The approving authority is the university
    president, who has no account in this system (Q-14, D-31), so the approval
    is recorded as a name plus a decision reference — evidence, not a workflow
    waiting for a login that will never come.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز القائمة"))
    name_ar = NameAr(verbose_name=_("اسم القائمة"))
    semester = models.ForeignKey(
        "core.Semester",
        on_delete=models.PROTECT,
        related_name="price_lists",
        verbose_name=_("الفصل"),
    )
    issued_on = models.DateField(verbose_name=_("تاريخ الإصدار"))
    effective_from = models.DateField(verbose_name=_("تاريخ السريان"))

    proposed_by_text = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("جهة التنسيب"),
        help_text=_("مدير المركز — BR-008"),
    )
    approved_by_text = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("جهة الاعتماد"),
        help_text=_("رئيس الجامعة — خارج النظام (D-31)"),
    )
    decision_reference = DisplayRef(blank=True, verbose_name=_("مرجع القرار"))

    status = ShortCode(
        choices=PriceListStatus.choices,
        default=PriceListStatus.DRAFT,
        verbose_name=_("الحالة"),
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("وقت الاعتماد"))
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name=_("وقت الأرشفة"))

    # MySQL has no partial index, and NULLs do not collide in a unique key.
    # Carrying the semester here ONLY while approved gives "one approved list
    # per semester" as a real constraint — the same device Semester uses for
    # its single active row (DATA_MODEL §9.3, §11.3).
    approved_semester_key = models.BigIntegerField(null=True, editable=False, default=None)

    class Meta:
        verbose_name = _("قائمة أسعار")
        verbose_name_plural = _("قوائم الأسعار")
        ordering = ["-effective_from", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=PriceListStatus.values),
                name="catalog_price_list_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_from__gte=models.F("issued_on")),
                name="catalog_price_list_effective_after_issued",
            ),
            models.UniqueConstraint(
                fields=["approved_semester_key"],
                name="catalog_price_list_one_approved_per_semester",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name_ar}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.approved_semester_key = (
            self.semester_id if self.status == PriceListStatus.APPROVED else None
        )
        super().save(*args, **kwargs)  # type: ignore[arg-type]

    @property
    def is_frozen(self) -> bool:
        """D-14 — approved and archived lists are read-only for everyone."""
        return self.status in {PriceListStatus.APPROVED, PriceListStatus.ARCHIVED}


class PriceListItem(models.Model):
    """
    One programme's price on one list (DATA_MODEL §5.6).

    ``deposit_amount`` is NULL when the programme has no deposit — which is a
    different statement from zero, and the reason the column is nullable.
    """

    price_list = models.ForeignKey(
        PriceList,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name=_("قائمة الأسعار"),
    )
    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        related_name="price_items",
        verbose_name=_("البرنامج"),
    )
    level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("المستوى"),
        help_text=_("للدورات ذات المستويات فقط (BR-011)"),
    )
    course_fee = Money(verbose_name=_("رسوم الدورة"))

    deposit_amount = Money(
        null=True,
        blank=True,
        verbose_name=_("مبلغ التأمين"),
        help_text=_("فارغ = لا تأمين على هذا البرنامج — ويختلف عن صفر (BR-096)"),
    )
    deposit_policy = models.ForeignKey(
        DepositPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_items",
        verbose_name=_("سياسة التأمين"),
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظات"))

    # `level` is NULL for programmes without levels, and MySQL lets NULLs
    # repeat in a unique key — so the documented unique(price_list, program,
    # level) would silently permit duplicate rows for every non-levelled
    # programme. This normalised copy (0 when absent) is what the key uses.
    level_key = models.PositiveSmallIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = _("بند سعر")
        verbose_name_plural = _("بنود الأسعار")
        ordering = ["price_list", "program", "level_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "program", "level_key"],
                name="catalog_price_item_unique_program_level",
            ),
            models.CheckConstraint(
                condition=models.Q(course_fee__gte=0),
                name="catalog_price_item_fee_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(deposit_amount__isnull=True) | models.Q(deposit_amount__gt=0),
                name="catalog_price_item_deposit_positive",
            ),
            # C-26 · BR-096 — an amount with no policy governing it, or a
            # policy with no amount, is a half-defined deposit.
            models.CheckConstraint(
                condition=(
                    models.Q(deposit_amount__isnull=True, deposit_policy__isnull=True)
                    | models.Q(deposit_amount__isnull=False, deposit_policy__isnull=False)
                ),
                name="catalog_price_item_deposit_paired",
            ),
        ]

    def __str__(self) -> str:
        level = f" — المستوى {self.level}" if self.level else ""
        return f"{self.program.name_ar}{level}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.level_key = self.level or 0
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class RegistrationFeeRule(models.Model):
    """
    Registration fee by programme and participant category (DATA_MODEL §5.7,
    BR-009, Q-10).

    ``fee = NULL`` means NO registration fee, which is not the same as a fee of
    zero: several documented courses (JCPA, PMP, drug registration) charge
    none at all, and a stored zero would read as "we charged nothing" rather
    than "no fee applies".

    Resolution is most-specific-wins: a rule for ``(program, category)`` beats
    the general ``(NULL, category)``.
    """

    price_list = models.ForeignKey(
        PriceList,
        on_delete=models.PROTECT,
        related_name="registration_fee_rules",
        verbose_name=_("قائمة الأسعار"),
    )
    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="registration_fee_rules",
        verbose_name=_("البرنامج"),
        help_text=_("فارغ = القاعدة العامة لكل البرامج"),
    )
    participant_category = ShortCode(verbose_name=_("فئة المشارك"))
    fee = Money(
        null=True,
        blank=True,
        verbose_name=_("الرسم"),
        help_text=_("فارغ = بلا رسوم تسجيل — ويختلف عن صفر"),
    )
    exception_note_ar = models.CharField(
        max_length=255, blank=True, verbose_name=_("سبب الاستثناء")
    )

    # Same NULL-in-a-unique-key problem as PriceListItem.level: `program` is
    # NULL for the general rule, so without this the general rule could be
    # entered twice per category and the resolver would pick one arbitrarily.
    program_key = models.BigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = _("قاعدة رسم تسجيل")
        verbose_name_plural = _("قواعد رسوم التسجيل")
        ordering = ["price_list", "program_key", "participant_category"]
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "program_key", "participant_category"],
                name="catalog_reg_fee_unique_program_category",
            ),
            models.CheckConstraint(
                condition=models.Q(fee__isnull=True) | models.Q(fee__gte=0),
                name="catalog_reg_fee_not_negative",
            ),
        ]

    def __str__(self) -> str:
        scope = self.program.name_ar if self.program else _("كل البرامج")
        return f"{scope} · {self.participant_category}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.program_key = self.program_id or 0
        super().save(*args, **kwargs)  # type: ignore[arg-type]
