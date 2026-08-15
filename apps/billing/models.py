"""
Billing — what is OWED (DATA_MODEL §8.3 … §8.6).

The separation from ``cashbox`` is the point of the whole design: this app
answers "how much does the participant owe?", cashbox answers "how much did we
receive, and against which line?". The demo collapsed both into a single
``paid`` figure and re-derived everything from it, which produced correct
answers by luck for as long as the ordering never changed.

Three rules are expressed as database constraints here because each one, if
broken, is silently wrong rather than loudly broken:

* **C-22** ``gross = net + tax``. The participant pays gross; the partner
  shares net; revenue reports read net where ``is_revenue``.
* **C-28** a taxable line MUST carry the rate it was computed with. This is
  what makes Q-25 safe: with ``default_tax_rate`` unset, a taxable line cannot
  be stored at all, so "treat tax as zero for now" is not available as a
  silent shortcut. The service raises TaxRateNotConfigured first.
* **C-21** a deposit is never revenue, and everything else always is. A
  refundable deposit is a liability the university owes back; counting it as
  income inflates both the revenue reports and every partner's share.

``DepositReturn.trigger`` and ``DepositForfeiture.reason`` carry NO CHECK
constraint: Q-30 is open, the values are copied from DepositPolicy rows the
client owns, and the client's instruction of 2026-08-15 was to keep them
configurable (BR-096, BR-097).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, Rate, ShortCode


class ChargeType(models.TextChoices):
    REGISTRATION = "REGISTRATION", _("رسم تسجيل")
    TUITION = "TUITION", _("رسوم دراسية")
    CONSUMABLES = "CONSUMABLES", _("مستهلكات")
    DEPOSIT = "DEPOSIT", _("تأمين مسترد")
    EXTRA_FEE = "EXTRA_FEE", _("رسم إضافي")
    TRANSFER_DIFFERENCE = "TRANSFER_DIFFERENCE", _("فرق نقل")


#: BR-022 — the order a payment is consumed in. Deposit sits between
#: consumables and tuition and appears only when the programme has a policy.
ALLOCATION_ORDER: tuple[str, ...] = (
    ChargeType.REGISTRATION,
    ChargeType.CONSUMABLES,
    ChargeType.DEPOSIT,
    ChargeType.TUITION,
    ChargeType.EXTRA_FEE,
    ChargeType.TRANSFER_DIFFERENCE,
)


class ChargeLine(models.Model):
    """One amount owed on one enrolment (DATA_MODEL §8.3)."""

    enrollment = models.ForeignKey(
        "operations.Enrollment",
        on_delete=models.PROTECT,
        related_name="charge_lines",
        verbose_name=_("التسجيل"),
    )
    charge_type = ShortCode(choices=ChargeType.choices, verbose_name=_("نوع البند"))
    description_ar = models.CharField(max_length=255, verbose_name=_("البيان"))

    net_amount = Money(verbose_name=_("المبلغ الصافي"))
    is_taxable = models.BooleanField(default=False, verbose_name=_("خاضع للضريبة"))
    tax_rate_snapshot = Rate(
        null=True,
        blank=True,
        verbose_name=_("نسبة الضريبة المطبَّقة"),
        help_text=_("لقطة وقت الإنشاء — البند القديم يحتفظ بنسبته (BR-098)"),
    )
    tax_amount = Money(default=0, verbose_name=_("مبلغ الضريبة"))
    gross_amount = Money(verbose_name=_("الإجمالي المطلوب"))

    charged_on = models.DateField(verbose_name=_("تاريخ الاستحقاق"))
    subject = models.ForeignKey(
        "catalog.Subject",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charge_lines",
        verbose_name=_("المادة"),
    )

    is_partner_shareable = models.BooleanField(
        default=True,
        verbose_name=_("يدخل وعاء الشريك"),
        help_text=_("يُحسم وقت الإنشاء من الاتفاقية — BR-046"),
    )
    is_revenue = models.BooleanField(
        default=True,
        verbose_name=_("يُحتسب إيراداً"),
        help_text=_("False للتأمينات — التزام لا إيراد (BR-092)"),
    )
    deposit_policy_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("لقطة سياسة التأمين"),
        help_text=_("للتأمينات فقط — تغيير السياسة لاحقاً لا يمسّ بنداً قائماً"),
    )

    voided = models.BooleanField(default=False, verbose_name=_("ملغى"))
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voided_charge_lines",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason_ar = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("بند رسم")
        verbose_name_plural = _("بنود الرسوم")
        ordering = ["enrollment", "charged_on", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(charge_type__in=ChargeType.values),
                name="billing_charge_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(net_amount__gte=0) & models.Q(tax_amount__gte=0),
                name="billing_charge_amounts_not_negative",
            ),
            # C-22 — the triple must add up. Everything downstream (what the
            # participant pays, what the partner shares, what counts as
            # revenue) reads one of these three numbers.
            models.CheckConstraint(
                condition=models.Q(gross_amount=models.F("net_amount") + models.F("tax_amount")),
                name="billing_charge_gross_equals_net_plus_tax",
            ),
            # C-28 — no tax without the rate that produced it, and no rate on
            # a line that is not taxable. With default_tax_rate still unset
            # (Q-25) this makes a taxable line unstorable rather than
            # quietly zero-rated.
            models.CheckConstraint(
                condition=(
                    models.Q(is_taxable=True, tax_rate_snapshot__isnull=False)
                    | models.Q(is_taxable=False, tax_rate_snapshot__isnull=True, tax_amount=0)
                ),
                name="billing_charge_tax_rate_captured",
            ),
            # C-21 — a deposit is a liability, never income; everything else
            # is income. Hard, both ways.
            models.CheckConstraint(
                condition=~models.Q(charge_type=ChargeType.DEPOSIT) | models.Q(is_revenue=False),
                name="billing_charge_deposit_is_not_revenue",
            ),
            models.CheckConstraint(
                condition=models.Q(charge_type=ChargeType.DEPOSIT) | models.Q(is_revenue=True),
                name="billing_charge_non_deposit_is_revenue",
            ),
            # C-26 — a deposit line carries the policy that governs it, so the
            # refund terms are knowable years later even if the policy changed.
            models.CheckConstraint(
                condition=~models.Q(charge_type=ChargeType.DEPOSIT)
                | models.Q(deposit_policy_snapshot__isnull=False),
                name="billing_charge_deposit_has_policy_snapshot",
            ),
            models.CheckConstraint(
                condition=models.Q(voided=False)
                | (models.Q(voided_by__isnull=False) & ~models.Q(void_reason_ar="")),
                name="billing_charge_void_is_explained",
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "charge_type"], name="bil_cl_enr_type_idx"),
            models.Index(fields=["enrollment", "voided"], name="bil_cl_enr_voided_idx"),
            models.Index(fields=["charged_on"], name="bil_cl_charged_idx"),
            models.Index(
                fields=["charge_type", "is_partner_shareable"], name="bil_cl_type_share_idx"
            ),
            models.Index(fields=["is_revenue"], name="bil_cl_revenue_idx"),
            models.Index(fields=["is_taxable"], name="bil_cl_taxable_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_charge_type_display()} — {self.gross_amount}"


class DepositReturn(models.Model):
    """
    Returning a deposit (DATA_MODEL §8.3.1, BR-097).

    A path entirely separate from ``Refund``: returning a deposit is the
    participant's RIGHT, not an exceptional refund, so it needs no official
    letter and no presidential approval (C-07 does not apply).
    """

    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="deposit_returns"
    )
    deposit_charge_line = models.ForeignKey(
        ChargeLine,
        on_delete=models.PROTECT,
        related_name="returns",
        verbose_name=_("بند التأمين"),
    )
    amount = Money(verbose_name=_("المبلغ المُعاد"))
    # No CHECK — copied from DepositPolicy.refund_trigger, which the client
    # owns and Q-30 has not settled (client instruction 2026-08-15).
    trigger = ShortCode(verbose_name=_("مُحفّز الاسترداد"))
    returned_on = models.DateField(verbose_name=_("تاريخ الإعادة"))
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="deposit_returns"
    )
    deduction_amount = Money(default=0, verbose_name=_("الحسم الجزئي"))
    deduction_reason_ar = models.TextField(blank=True, verbose_name=_("سبب الحسم"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("إعادة تأمين")
        verbose_name_plural = _("إعادات التأمين")
        ordering = ["-returned_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0) & models.Q(deduction_amount__gte=0),
                name="billing_deposit_return_amounts_valid",
            ),
            # BR-097 — money withheld from a refundable deposit needs a stated
            # reason. Without one it is indistinguishable from a shortfall.
            models.CheckConstraint(
                condition=models.Q(deduction_amount=0) | ~models.Q(deduction_reason_ar=""),
                name="billing_deposit_deduction_is_explained",
            ),
        ]

    def __str__(self) -> str:
        return f"إعادة تأمين {self.amount}"


class DepositForfeiture(models.Model):
    """
    Forfeiting a deposit (DATA_MODEL §8.3.2, BR-097).

    Forfeiture turns a liability into income — so it creates a NEW revenue
    charge line and leaves the deposit line untouched. Editing the original to
    flip ``is_revenue`` would breach C-21 and erase the fact that the money
    arrived as a deposit. Two rows tell the truth; one row edited tells a
    tidier story that is no longer auditable.
    """

    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="deposit_forfeitures"
    )
    deposit_charge_line = models.ForeignKey(
        ChargeLine,
        on_delete=models.PROTECT,
        related_name="forfeitures",
        verbose_name=_("بند التأمين الأصلي"),
    )
    amount = Money(verbose_name=_("المبلغ المُصادَر"))
    # No CHECK — drawn from DepositPolicy.forfeit_on, which is client data
    # and still open under Q-30.
    reason = ShortCode(verbose_name=_("سبب المصادرة"))
    justification_ar = models.TextField(verbose_name=_("المبرر"))
    revenue_charge_line = models.ForeignKey(
        ChargeLine,
        on_delete=models.PROTECT,
        related_name="forfeiture_source",
        null=True,
        blank=True,
        verbose_name=_("بند الإيراد الناتج"),
    )
    forfeited_on = models.DateField(verbose_name=_("تاريخ المصادرة"))
    forfeited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="forfeitures_made"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="forfeitures_approved",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("مصادرة تأمين")
        verbose_name_plural = _("مصادرات التأمين")
        ordering = ["-forfeited_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_forfeiture_amount_positive"
            ),
            models.CheckConstraint(
                condition=~models.Q(justification_ar=""),
                name="billing_forfeiture_has_justification",
            ),
            # D-18 — taking a participant's money and approving it yourself.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("forfeited_by")),
                name="billing_forfeiture_approver_differs",
            ),
        ]

    def __str__(self) -> str:
        return f"مصادرة تأمين {self.amount}"


class DiscountType(models.TextChoices):
    PERCENT = "PERCENT", _("نسبة")
    AMOUNT = "AMOUNT", _("مبلغ")


class Discount(models.Model):
    """A discount on an enrolment (DATA_MODEL §8.4, BR-029 … BR-032)."""

    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="discounts"
    )
    discount_type = ShortCode(choices=DiscountType.choices, verbose_name=_("نوع الخصم"))
    rate = Rate(null=True, blank=True, verbose_name=_("النسبة"))
    amount = Money(verbose_name=_("مبلغ الخصم"))
    base_amount = Money(
        verbose_name=_("الوعاء وقت الاحتساب"),
        help_text=_("منسوخ — لا يُعاد حسابه إن تغيّرت الرسوم لاحقاً"),
    )
    reason_ar = models.CharField(max_length=255, verbose_name=_("السبب"))

    # BR-030 — the president approves every discount, from outside the system
    # (D-31), so the reference and date ARE the evidence.
    president_approval_ref = DisplayRef(verbose_name=_("رقم موافقة رئيس الجامعة"))
    president_approval_date = models.DateField(verbose_name=_("تاريخ الموافقة"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="discounts_created"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="discounts_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    university_burden = Money(default=0, verbose_name=_("حصة الجامعة من الخصم"))
    partner_burden = Money(default=0, verbose_name=_("حصة الشريك من الخصم"))
    discount_split_mode_snapshot = ShortCode(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("خصم")
        verbose_name_plural = _("الخصومات")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_discount_amount_positive"
            ),
            # BR-030 at the database level — a discount with no approval
            # reference is a waiver of revenue nobody signed for.
            models.CheckConstraint(
                condition=~models.Q(president_approval_ref=""),
                name="billing_discount_has_approval_ref",
            ),
            # C-04 — the two burdens must reconstitute the discount exactly.
            models.CheckConstraint(
                condition=models.Q(
                    amount=models.F("university_burden") + models.F("partner_burden")
                ),
                name="billing_discount_burdens_sum_to_amount",
            ),
            # D-18 — nobody approves the discount they raised.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("created_by")),
                name="billing_discount_approver_differs",
            ),
        ]

    def __str__(self) -> str:
        return f"خصم {self.amount}"


class ExtraFeeType(models.TextChoices):
    SUBJECT_REPEAT = "SUBJECT_REPEAT", _("إعادة مادة")
    CERTIFICATE_REPLACEMENT = "CERTIFICATE_REPLACEMENT", _("بدل فاقد شهادة")
    INTERNATIONAL_EXAM = "INTERNATIONAL_EXAM", _("امتحان دولي")
    OTHER = "OTHER", _("أخرى")


class ExtraFee(models.Model):
    """An additional fee (DATA_MODEL §8.5, BR-037 … BR-040)."""

    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="extra_fees"
    )
    fee_type = ShortCode(choices=ExtraFeeType.choices, verbose_name=_("نوع الرسم"))
    subject_name = models.CharField(max_length=150, blank=True)
    amount = Money(verbose_name=_("المبلغ"))
    is_partner_shareable = models.BooleanField(default=False)
    prior_agreement_with_participant = models.BooleanField(
        default=False,
        verbose_name=_("اتفاق مسبق مع المشارك"),
        help_text=_("BR-040 — لا رسم إضافي بلا علم المشارك"),
    )
    charge_line = models.ForeignKey(
        ChargeLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extra_fee_source",
    )
    charged_on = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="extra_fees_created"
    )

    class Meta:
        verbose_name = _("رسم إضافي")
        verbose_name_plural = _("الرسوم الإضافية")
        ordering = ["-charged_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_extra_fee_amount_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_fee_type_display()} — {self.amount}"


class RefundStatus(models.TextChoices):
    REQUESTED = "REQUESTED", _("مطلوب")
    APPROVED = "APPROVED", _("معتمد")
    EXECUTED = "EXECUTED", _("منفَّذ")
    REJECTED = "REJECTED", _("مرفوض")


class Refund(models.Model):
    """
    An exceptional refund (DATA_MODEL §8.6, BR-033 … BR-036).

    Not to be confused with DepositReturn: a refund reverses revenue and needs
    an official letter plus the president's approval; returning a deposit
    hands back money that was never income.
    """

    code = ShortCode(unique=True)
    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="refunds"
    )
    refund_type = ShortCode(verbose_name=_("نوع الاسترداد"))
    amount = Money(verbose_name=_("المبلغ"))
    reason_ar = models.TextField(verbose_name=_("السبب"))

    official_letter_ref = DisplayRef(verbose_name=_("مرجع الكتاب الرسمي"))
    official_letter_date = models.DateField(null=True, blank=True)
    president_approval_ref = DisplayRef(verbose_name=_("رقم موافقة رئيس الجامعة"))
    president_approval_date = models.DateField(null=True, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="refunds_requested"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="refunds_approved",
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="refunds_executed",
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    status = ShortCode(choices=RefundStatus.choices, default=RefundStatus.REQUESTED)

    partner_recovery_amount = Money(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("استرداد")
        verbose_name_plural = _("الاستردادات")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=RefundStatus.values),
                name="billing_refund_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_refund_amount_positive"
            ),
            # BR-034 at the database level — both external documents, or no
            # refund. The demo left these as optional text.
            models.CheckConstraint(
                condition=~models.Q(official_letter_ref="") & ~models.Q(president_approval_ref=""),
                name="billing_refund_has_external_approvals",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=RefundStatus.EXECUTED)
                | models.Q(executed_at__isnull=False),
                name="billing_refund_executed_has_timestamp",
            ),
            # D-18 — the finance officer executes, but never approves his own.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("requested_by")),
                name="billing_refund_approver_differs",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.amount}"
