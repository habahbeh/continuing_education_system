"""
Partners and agreements (DATA_MODEL §6).

The agreement is the contract the money is split by, so its terms are frozen
at signing rather than referenced live: ``AgreementProgramSnapshot`` copies the
fees and the subject list as they stood, because the catalogue will move and a
signed agreement must not move with it (BR-042, D-15).

One constraint here fixes a defect the demo demonstrably had. It applied
``BY_RATIO`` discount splitting to a FIXED_PER_STUDENT agreement, which meant
treating 195 dinars-per-student as a percentage — and produced a NEGATIVE
university share. The database now makes that combination unrepresentable
(BR-031), rather than trusting every future caller to check.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, NameAr, NameEn, Rate, ShortCode


class PartnerType(models.TextChoices):
    COMPANY = "COMPANY", _("شركة")
    FREELANCE_TRAINER = "FREELANCE_TRAINER", _("مدرب مستقل")


class PartnerStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("نشط")
    FORMER = "FORMER", _("سابق")


class CalculationModel(models.TextChoices):
    PERCENT = "PERCENT", _("نسبة مئوية")
    FIXED_PER_STUDENT = "FIXED_PER_STUDENT", _("مبلغ ثابت لكل طالب")
    SERVICE_COMMISSION = "SERVICE_COMMISSION", _("عمولة خدمة")


class DiscountSplitMode(models.TextChoices):
    HALF = "HALF", _("مناصفة")
    BY_RATIO = "BY_RATIO", _("بنسبة القسمة")
    UNIVERSITY_ONLY = "UNIVERSITY_ONLY", _("على الجامعة وحدها")


class PayoutTiming(models.TextChoices):
    END_OF_SUBJECT = "END_OF_SUBJECT", _("نهاية المادة")
    END_OF_COURSE = "END_OF_COURSE", _("نهاية الدورة")
    ADVANCE = "ADVANCE", _("صرف مقدّم")


class SettlementCycle(models.TextChoices):
    EVERY_4_MONTHS = "EVERY_4_MONTHS", _("كل أربعة أشهر")
    END_OF_COURSE = "END_OF_COURSE", _("نهاية الدورة")


class AgreementStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسودة")
    ACTIVE = "ACTIVE", _("سارية")
    EXPIRED = "EXPIRED", _("منتهية")
    TERMINATED = "TERMINATED", _("منهاة")


class Partner(models.Model):
    """A training partner (DATA_MODEL §6.1)."""

    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = NameAr(verbose_name=_("اسم الشريك"))
    name_en = NameEn(blank=True)
    partner_type = ShortCode(choices=PartnerType.choices, verbose_name=_("النوع"))
    registry_number = DisplayRef(blank=True, verbose_name=_("رقم التسجيل"))
    registry_date = models.DateField(null=True, blank=True)
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    status = ShortCode(
        choices=PartnerStatus.choices,
        default=PartnerStatus.ACTIVE,
        verbose_name=_("الحالة"),
    )

    class Meta:
        verbose_name = _("شريك")
        verbose_name_plural = _("الشركاء")
        ordering = ["name_ar"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(partner_type__in=PartnerType.values),
                name="partners_partner_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PartnerStatus.values),
                name="partners_partner_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name_ar


class Agreement(models.Model):
    """The contract that decides how money is split (DATA_MODEL §6.2, BR-041)."""

    agreement_number = DisplayRef(unique=True, verbose_name=_("رقم الاتفاقية"))
    partner = models.ForeignKey(
        Partner,
        on_delete=models.PROTECT,
        related_name="agreements",
        verbose_name=_("الشريك"),
    )
    title_ar = models.CharField(max_length=255, verbose_name=_("عنوان الاتفاقية"))
    signed_on = models.DateField(verbose_name=_("تاريخ التوقيع"))
    valid_from = models.DateField(verbose_name=_("سارية من"))
    valid_to = models.DateField(verbose_name=_("سارية حتى"))

    calculation_model = ShortCode(
        choices=CalculationModel.choices, verbose_name=_("نموذج الاحتساب")
    )
    percent_rate = Rate(null=True, blank=True, verbose_name=_("النسبة"))
    fixed_amount_per_student = Money(null=True, blank=True, verbose_name=_("المبلغ لكل طالب"))
    sell_price = Money(null=True, blank=True, verbose_name=_("سعر البيع"))
    service_name_ar = models.CharField(max_length=255, blank=True)
    service_price = Money(null=True, blank=True)
    commission_amount = Money(null=True, blank=True)

    exclude_registration_fee = models.BooleanField(
        default=True, verbose_name=_("استثناء رسم التسجيل")
    )
    exclude_deposits = models.BooleanField(
        default=True,
        verbose_name=_("استثناء التأمينات"),
        help_text=_(
            "Q-01 — الافتراض استثناء التأمين من وعاء القسمة. "
            "تعطيله يتطلب نصاً صريحاً في الاتفاقية ويُسجَّل تحذيراً (BR-092)"
        ),
    )
    exclude_consumables = models.BooleanField(default=False, verbose_name=_("استثناء المستهلكات"))
    consumables_cap_per_student = Money(
        null=True, blank=True, verbose_name=_("سقف المستهلكات لكل طالب")
    )

    discount_split_mode = ShortCode(
        choices=DiscountSplitMode.choices,
        default=DiscountSplitMode.HALF,
        verbose_name=_("توزيع عبء الخصم"),
    )
    payout_timing = ShortCode(
        choices=PayoutTiming.choices,
        default=PayoutTiming.END_OF_COURSE,
        verbose_name=_("توقيت الصرف"),
    )
    settlement_cycle = ShortCode(
        choices=SettlementCycle.choices,
        default=SettlementCycle.END_OF_COURSE,
        verbose_name=_("دورة المخالصة"),
    )
    name_list_due_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("مهلة كشف الأسماء بالأيام"),
        help_text=_("BR-054 — بعدها يُقفل الكشف ويُحتسب استرجاع الصرف المقدّم"),
    )
    entitlement_rule_ar = models.TextField(blank=True, verbose_name=_("نص قاعدة الاستحقاق"))

    status = ShortCode(
        choices=AgreementStatus.choices,
        default=AgreementStatus.DRAFT,
        verbose_name=_("الحالة"),
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        verbose_name=_("ملحق لاتفاقية"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("اتفاقية")
        verbose_name_plural = _("الاتفاقيات")
        ordering = ["-signed_on", "agreement_number"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(calculation_model__in=CalculationModel.values),
                name="partners_agreement_model_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=AgreementStatus.values),
                name="partners_agreement_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__gt=models.F("valid_from")),
                name="partners_agreement_valid_period",
            ),
            # Each model must carry the numbers it needs. An incomplete
            # agreement is one that computes a share from a missing field.
            models.CheckConstraint(
                condition=~models.Q(calculation_model=CalculationModel.PERCENT)
                | (
                    models.Q(percent_rate__isnull=False)
                    & models.Q(percent_rate__gte=0)
                    & models.Q(percent_rate__lte=100)
                ),
                name="partners_agreement_percent_complete",
            ),
            models.CheckConstraint(
                condition=~models.Q(calculation_model=CalculationModel.FIXED_PER_STUDENT)
                | (
                    models.Q(fixed_amount_per_student__isnull=False)
                    & models.Q(sell_price__isnull=False)
                    & models.Q(sell_price__gte=models.F("fixed_amount_per_student"))
                ),
                name="partners_agreement_fixed_complete",
            ),
            models.CheckConstraint(
                condition=~models.Q(calculation_model=CalculationModel.SERVICE_COMMISSION)
                | (
                    models.Q(service_price__isnull=False)
                    & models.Q(commission_amount__isnull=False)
                    & models.Q(commission_amount__lte=models.F("service_price"))
                ),
                name="partners_agreement_commission_complete",
            ),
            # C-01 — the demo's live defect. BY_RATIO on a fixed-per-student
            # agreement treated 195 dinars as a percentage and produced a
            # negative university share. Unrepresentable from here on.
            models.CheckConstraint(
                condition=~models.Q(discount_split_mode=DiscountSplitMode.BY_RATIO)
                | models.Q(calculation_model=CalculationModel.PERCENT),
                name="partners_agreement_by_ratio_needs_percent",
            ),
            models.CheckConstraint(
                condition=models.Q(consumables_cap_per_student__isnull=True)
                | models.Q(consumables_cap_per_student__gte=0),
                name="partners_agreement_cap_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status"], name="prt_agr_partner_status_idx"),
            models.Index(fields=["valid_from", "valid_to"], name="prt_agr_validity_idx"),
            models.Index(fields=["status"], name="prt_agr_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.agreement_number} — {self.partner.name_ar}"

    @property
    def is_frozen(self) -> bool:
        """D-15 — once active, the snapshot cannot move (BR-042)."""
        return self.status in {
            AgreementStatus.ACTIVE,
            AgreementStatus.EXPIRED,
            AgreementStatus.TERMINATED,
        }


class AgreementProgramSnapshot(models.Model):
    """
    The programme and its prices AS SIGNED (DATA_MODEL §6.3, BR-042).

    ``subjects_snapshot`` is JSON rather than a frozen relation on purpose: a
    diploma's subject list will be edited in the catalogue afterwards, and the
    agreement must keep showing what both parties signed. Flat text is a more
    honest freeze than a relation someone can follow to current data.
    """

    agreement = models.ForeignKey(
        Agreement, on_delete=models.PROTECT, related_name="program_snapshots"
    )
    program = models.ForeignKey(
        "catalog.Program", on_delete=models.PROTECT, related_name="agreement_snapshots"
    )
    level = models.PositiveSmallIntegerField(null=True, blank=True)
    course_fee_at_signing = Money(verbose_name=_("رسوم الدورة وقت التوقيع"))
    registration_fee_at_signing = Money(null=True, blank=True)
    subjects_snapshot = models.JSONField(default=list, blank=True)

    # Same NULL-in-a-unique-key issue as the price list: `level` is NULL for
    # unlevelled programmes, so without a normalised copy one programme could
    # be snapshotted twice on the same agreement.
    level_key = models.PositiveSmallIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = _("لقطة برنامج اتفاقية")
        verbose_name_plural = _("لقطات برامج الاتفاقيات")
        ordering = ["agreement", "program"]
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "program", "level_key"],
                name="partners_snapshot_unique_program_level",
            ),
            models.CheckConstraint(
                condition=models.Q(course_fee_at_signing__gte=0),
                name="partners_snapshot_fee_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.agreement.agreement_number} — {self.program.name_ar}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.level_key = self.level or 0
        super().save(*args, **kwargs)  # type: ignore[arg-type]
