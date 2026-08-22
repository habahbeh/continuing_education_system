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

from apps.core.fields import DisplayRef, Money, NameAr, Rate, ShortCode


class ChargeType(models.TextChoices):
    REGISTRATION = "REGISTRATION", _("رسم تسجيل")
    TUITION = "TUITION", _("رسوم دراسية")
    CONSUMABLES = "CONSUMABLES", _("مستهلكات")
    DEPOSIT = "DEPOSIT", _("تأمين مسترد")
    EXTRA_FEE = "EXTRA_FEE", _("رسم إضافي")
    TRANSFER_DIFFERENCE = "TRANSFER_DIFFERENCE", _("فرق نقل")

    #: Sprint 8D-2 — a debt carried in from before the system existed, posted
    #: by an APPROVED ``OpeningBalance`` and by nothing else. It is a real
    #: obligation, so it is a real charge line; it is not current business, so
    #: it never enters a partner's base.
    OPENING_BALANCE = "OPENING_BALANCE", _("رصيد افتتاحي")


#: BR-022 — the order a payment is consumed in. Deposit sits between
#: consumables and tuition and appears only when the programme has a policy.
#:
#: **The opening balance is FIRST, by the client's decision (Sprint 8D-3).**
#:
#: Sprint 8D-2 put it last and argued the case: money handed over for this
#: term is for this term, and clearing an old arrear from it takes a payment
#: the participant may think is buying something else. The centre decided the
#: other way, and their reasoning governs — an old debt is the debt most at
#: risk of never being collected, and a participant paying anything at all is
#: the moment to recover it. It is recorded here as a decision and not as a
#: default so that the next person to read this order knows it was chosen.
ALLOCATION_ORDER: tuple[str, ...] = (
    ChargeType.OPENING_BALANCE,
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


class CreditReturn(models.Model):
    """
    Money handed back that was never a refund (BR-071, DATA_MODEL gap).

    A participant who overpaid — most often because a transfer moved them to a
    cheaper course — is owed the difference. BR-071 requires this to be
    recorded as an INDEPENDENT financial movement, and neither existing model
    can carry it:

    * ``Refund`` reverses REVENUE and demands an official letter plus the
      president's approval (BR-034, ``billing_refund_has_external_approvals``).
      Requiring a presidential decree to hand back a participant's own fifty
      dinars would make the rule unusable and is not what BR-034 is for.
    * ``ChargeLine`` cannot represent it either: everything that is not a
      deposit must be revenue (C-21, ``billing_charge_non_deposit_is_revenue``),
      and returned money is not income.

    The money itself moves by a REVERSING allocation against the credit row —
    the same pattern as a void or a transfer — so no original row is edited or
    deleted. ``reversal_allocation`` points at the row that moved it, which is
    what makes the payout traceable from either direction.

    ⚠️ **ASSUMPTION — who authorises it.** The documents do not say. It is
    executed inside clearance step 2, which already requires the finance
    officer's certification followed by the finance manager's (BR-074, D-30),
    so the existing dual control is the control. That is a professional
    reading, NOT a settled client decision.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز ردّ الرصيد"))
    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="credit_returns"
    )
    amount = Money(verbose_name=_("المبلغ المُعاد"))
    reason_ar = models.TextField(verbose_name=_("سبب الرصيد الدائن"))
    returned_on = models.DateField(verbose_name=_("تاريخ الإعادة"))
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credit_returns"
    )
    #: The reversing allocation that actually moved the money out. Nullable
    #: only so the row can be written before the allocation inside one
    #: transaction; the service always links it.
    reversal_allocation = models.ForeignKey(
        "cashbox.PaymentAllocation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_returns",
        verbose_name=_("التخصيص العكسي"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("ردّ رصيد دائن")
        verbose_name_plural = _("ردود الأرصدة الدائنة")
        ordering = ["-returned_on", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_credit_return_amount_positive"
            ),
            # Money leaving the centre always says why it left.
            models.CheckConstraint(
                condition=~models.Q(reason_ar=""), name="billing_credit_return_has_reason"
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment"], name="bil_credret_enr_idx"),
            models.Index(fields=["returned_on"], name="bil_credret_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.amount}"


class OpeningBalanceStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسودة")
    REVIEWED = "REVIEWED", _("مُراجَع")
    APPROVED = "APPROVED", _("معتمَد")
    #: A RECEIVABLE reached the ledger as a charge line.
    POSTED = "POSTED", _("مُرحَّل إلى الدفتر")
    #: Sprint 8D-3 · client decision 1 — a CREDIT carried forward onto a later
    #: registration. It reduces what that enrolment must pay, and it does so
    #: through the balance equation rather than through an invented receipt.
    APPLIED = "APPLIED", _("مُرحَّل لتسجيل لاحق")
    #: Sprint 8D-3 · client decision 1 — a CREDIT with no later registration.
    #: The centre owes cash back; the obligation stands until it is paid, and
    #: paying it is a cash movement the cashbox records when it happens.
    REFUND_DUE = "REFUND_DUE", _("مستحق الردّ نقداً")
    #: Sprint 8D-4 — the cash actually left, against a real voucher. Terminal:
    #: the obligation is closed and no second payout is possible.
    REFUNDED = "REFUNDED", _("رُدّ نقداً")
    REJECTED = "REJECTED", _("مرفوض")


class OpeningBalanceDirection(models.TextChoices):
    #: The participant owes the centre. Posts a charge line.
    RECEIVABLE = "RECEIVABLE", _("ذمة على المشارك")
    #: The centre owes the participant. Never posted as a charge line; Sprint
    #: 8D-3 resolves it either onto a later registration (APPLIED) or as cash
    #: the centre must hand back (REFUND_DUE).
    CREDIT = "CREDIT", _("رصيد دائن للمشارك")


class OpeningBalance(models.Model):
    """
    The one gateway from the historical archive to the ledger (BR-094).

    **Why this lives in ``billing`` and not in ``datamigration``.** The archive
    is forbidden by A-04 from importing any financial app, and Sprint 8D-1 was
    built so that a workbook can be read, validated and committed without a
    single ledger row moving. Putting ``OpeningBalance`` in ``datamigration``
    would have handed that app a way to create money and undone the boundary.

    So the arrow points the other way. This model sits on the LEDGER side and
    reaches INTO the archive through ``source_enrollment``. The archive cannot
    reach back — it does not know this model exists — and the gate can only be
    opened from the money side, by people, one row at a time.

    **Four hands, and the database counts them** (D-24 · BR-094). One person
    proposes, a second reviews the figure against the source, a third
    approves, and only then may it be posted. The constraints below make each
    of those distinct, because a review the same person performed on their own
    entry is not a review.

    **One row at a time** (D-25). There is no bulk proposal service, and the
    absence is the control: ``propose_from_archive`` takes a single archived
    enrolment. A list of two hundred balances nobody read individually is
    exactly what BR-094 exists to prevent.

    **Provenance survives supersession.** ``source_enrollment`` is nullable and
    PROTECTed, but the workbook, sheet and row are ALSO copied here as text.
    An archive batch can be superseded by a better reading of the same file;
    the balance somebody approved must still be able to say where it came
    from even then.

    **CREDIT is recorded but not postable, and that is deliberate.** A
    participant the centre owes money to is a real fact and belongs on the
    record. Posting it, though, would mean creating a ``Receipt`` for money
    this system never received — inventing the very document Sprint 8D-1
    refused to invent. ``post()`` refuses it by name. Returning historical
    credit is a decision the centre has to make on paper first.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز الرصيد الافتتاحي"))

    #: Nullable: a balance may be entered by hand from a paper file the
    #: workbooks never contained. When it IS derived, this is the row.
    source_enrollment = models.ForeignKey(
        "datamigration.HistoricalEnrollment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances",
        verbose_name=_("الصف التاريخي المصدر"),
    )
    source_workbook = models.CharField(max_length=255, blank=True, verbose_name=_("الملف المصدر"))
    source_sheet = models.CharField(max_length=120, blank=True, verbose_name=_("الورقة المصدر"))
    source_row = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("الصف المصدر"))
    source_legacy_number = models.CharField(
        max_length=32, blank=True, verbose_name=_("الرقم الجامعي القديم")
    )

    direction = ShortCode(choices=OpeningBalanceDirection.choices, verbose_name=_("اتجاه الرصيد"))
    amount = Money(verbose_name=_("المبلغ"))
    as_of = models.DateField(verbose_name=_("الرصيد كما في تاريخ"))
    description_ar = models.CharField(max_length=255, verbose_name=_("البيان"))

    #: Where the money will land. Required before APPROVAL, not before a
    #: draft: the reviewer is often the person who works out which live
    #: enrolment an old debt belongs to. A balance with nowhere to go is a
    #: balance nobody can collect, so it may be proposed and never approved.
    enrollment = models.ForeignKey(
        "operations.Enrollment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances",
        verbose_name=_("التسجيل"),
    )

    #: Sprint 8D-3 · client decision 2 — an unpaid old debt must stop the
    #: PARTICIPANT getting a clearance or a certificate, and a participant is
    #: not an enrolment. A debt whose owner never registered again has no
    #: enrolment to hang on, yet still has to block them if they ever come
    #: back; and a debt attached to one enrolment must block a clearance on
    #: another. So the person is recorded separately from the account.
    #:
    #: Set by the reviewer, by hand, exactly like ``enrollment``. Nothing here
    #: matches a participant automatically: eight legacy numbers in the
    #: delivered workbooks carry two different names, and a matcher that
    #: guessed would attach one person's debt to another's file.
    participant = models.ForeignKey(
        "people.Participant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances",
        verbose_name=_("المشارك"),
    )

    status = ShortCode(
        choices=OpeningBalanceStatus.choices,
        default=OpeningBalanceStatus.DRAFT,
        verbose_name=_("الحالة"),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="opening_balances_created",
        verbose_name=_("اقترحه"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances_reviewed",
        verbose_name=_("راجعه"),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة المراجعة"))

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances_approved",
        verbose_name=_("اعتمده"),
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة القرار"))

    #: Set once, by ``post()``. A OneToOne rather than a flag: idempotency is
    #: then a database fact rather than a check somebody has to remember to
    #: write. A second post collides here even if every guard above it fails.
    posted_charge_line = models.OneToOneField(
        ChargeLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balance",
        verbose_name=_("بند الرسم الناتج"),
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances_posted",
        verbose_name=_("رحّله"),
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    # --- Sprint 8D-3 · how a CREDIT ended -----------------------------------
    #: Stamped when a credit is carried forward or declared refundable. Kept
    #: apart from the posting stamps because they are different acts with
    #: different consequences, and flattening them would make the audit read
    #: as though a credit had reached the ledger.
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opening_balances_resolved",
        verbose_name=_("سوّاه"),
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note_ar = models.CharField(
        max_length=255, blank=True, verbose_name=_("مسوّغ التسوية")
    )

    class Meta:
        verbose_name = _("رصيد افتتاحي")
        verbose_name_plural = _("الأرصدة الافتتاحية")
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=OpeningBalanceStatus.values),
                name="billing_opening_balance_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(direction__in=OpeningBalanceDirection.values),
                name="billing_opening_balance_direction_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="billing_opening_balance_amount_positive",
            ),
            # D-24 · BR-094 — the reviewer is not the person who proposed it.
            models.CheckConstraint(
                condition=models.Q(reviewed_by__isnull=True)
                | ~models.Q(reviewed_by=models.F("created_by")),
                name="billing_opening_balance_reviewer_differs",
            ),
            # D-24 · BR-094 — nor is the approver either of them.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | (
                    ~models.Q(approved_by=models.F("created_by"))
                    & ~models.Q(approved_by=models.F("reviewed_by"))
                ),
                name="billing_opening_balance_approver_differs",
            ),
            # A status is a claim; these make it a signed one.
            models.CheckConstraint(
                condition=~models.Q(status=OpeningBalanceStatus.REVIEWED)
                | (models.Q(reviewed_by__isnull=False) & models.Q(reviewed_at__isnull=False)),
                name="billing_opening_balance_reviewed_is_stamped",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=OpeningBalanceStatus.APPROVED)
                | (
                    models.Q(approved_by__isnull=False)
                    & models.Q(approved_at__isnull=False)
                    & models.Q(reviewed_by__isnull=False)
                    & models.Q(enrollment__isnull=False)
                ),
                name="billing_opening_balance_approved_is_stamped",
            ),
            # POSTED means the ledger row exists. Not "was requested".
            models.CheckConstraint(
                condition=~models.Q(status=OpeningBalanceStatus.POSTED)
                | (
                    models.Q(posted_charge_line__isnull=False)
                    & models.Q(posted_by__isnull=False)
                    & models.Q(posted_at__isnull=False)
                ),
                name="billing_opening_balance_posted_has_line",
            ),
            # And the converse: a charge line exists only for a POSTED row, so
            # a rolled-back post cannot leave a line orphaned to a draft.
            models.CheckConstraint(
                condition=models.Q(posted_charge_line__isnull=True)
                | models.Q(status=OpeningBalanceStatus.POSTED),
                name="billing_opening_balance_line_implies_posted",
            ),
            # A credit never reaches the ledger as a charge line, in the
            # database as well as in the service: posting one would mean
            # inventing a receipt for money never received.
            models.CheckConstraint(
                condition=~models.Q(direction=OpeningBalanceDirection.CREDIT)
                | models.Q(posted_charge_line__isnull=True),
                name="billing_opening_balance_credit_is_not_posted",
            ),
            # Sprint 8D-3 — and the two credit outcomes belong to credits
            # alone. A receivable that somehow reached APPLIED would silently
            # reduce an enrolment's balance by a debt.
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=[
                        OpeningBalanceStatus.APPLIED,
                        OpeningBalanceStatus.REFUND_DUE,
                        OpeningBalanceStatus.REFUNDED,
                    ]
                )
                | models.Q(direction=OpeningBalanceDirection.CREDIT),
                name="billing_opening_balance_credit_outcomes_are_credits",
            ),
            # Both outcomes name who decided and when — the same rule the
            # posting stamps carry, for the same reason.
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=[
                        OpeningBalanceStatus.APPLIED,
                        OpeningBalanceStatus.REFUND_DUE,
                        OpeningBalanceStatus.REFUNDED,
                    ]
                )
                | (models.Q(resolved_by__isnull=False) & models.Q(resolved_at__isnull=False)),
                name="billing_opening_balance_resolution_is_stamped",
            ),
            # A carried-forward credit lands on a named enrolment. Without one
            # it reduces nothing and the status is a claim about nowhere.
            models.CheckConstraint(
                condition=~models.Q(status=OpeningBalanceStatus.APPLIED)
                | models.Q(enrollment__isnull=False),
                name="billing_opening_balance_applied_has_enrollment",
            ),
            # One balance per archived row. A second proposal from the same
            # source is a duplicate, and duplicates are how a debt gets
            # collected twice.
            models.UniqueConstraint(
                fields=["source_enrollment"],
                condition=models.Q(source_enrollment__isnull=False),
                name="billing_opening_balance_one_per_source_row",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="bil_ob_status_idx"),
            models.Index(fields=["participant", "status"], name="bil_ob_part_status_idx"),
            models.Index(fields=["direction", "status"], name="bil_ob_dir_status_idx"),
            models.Index(fields=["enrollment"], name="bil_ob_enrollment_idx"),
            models.Index(fields=["source_legacy_number"], name="bil_ob_legacy_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.get_direction_display()} {self.amount}"

    @property
    def is_postable(self) -> bool:
        """Approved, receivable, attached to an enrolment, and not yet posted."""
        return (
            self.status == OpeningBalanceStatus.APPROVED
            and self.direction == OpeningBalanceDirection.RECEIVABLE
            and self.enrollment_id is not None
            and self.posted_charge_line_id is None
        )

    @property
    def is_refund_payable(self) -> bool:
        """Declared refundable, and the cash has not gone out yet."""
        return (
            self.status == OpeningBalanceStatus.REFUND_DUE
            and self.direction == OpeningBalanceDirection.CREDIT
        )

    @property
    def is_resolvable_credit(self) -> bool:
        """An approved credit still waiting to be carried forward or refunded."""
        return (
            self.status == OpeningBalanceStatus.APPROVED
            and self.direction == OpeningBalanceDirection.CREDIT
        )

    @property
    def blocks_clearance(self) -> bool:
        """
        Sprint 8D-3 · client decision 2 — an old debt that is still owed.

        REJECTED does not block: somebody looked and said no. POSTED does not
        block HERE either, because a posted line is already in the balance
        equation and BR-073 catches it there; double-blocking would report the
        same debt twice and under the wrong rule. What blocks is the debt that
        the balance cannot see — proposed, reviewed or approved and not yet
        posted, and therefore invisible to every existing guard.
        """
        return self.direction == OpeningBalanceDirection.RECEIVABLE and self.status in {
            OpeningBalanceStatus.DRAFT,
            OpeningBalanceStatus.REVIEWED,
            OpeningBalanceStatus.APPROVED,
        }


class OpeningBalanceRefund(models.Model):
    """
    The cash actually handed back on a historical credit (Sprint 8D-4).

    **Why this is a new model and not one of the four that already return
    money.** Each existing one is defined by something this case does not
    have:

    * ``Refund`` (§5.3) reverses REVENUE and demands the president's approval
      plus two external documents (BR-034). A credit inherited from before the
      system existed was never this system's revenue, so there is nothing to
      reverse — and ``CreditReturn``'s own docstring already warns that
      demanding a presidential decree to hand back a participant's own money
      is not what BR-034 is for.
    * ``CreditReturn`` (BR-071) is the right shape and the wrong mechanism. It
      is defined by ``reversal_allocation`` — "the reversing allocation that
      actually moved the money out", which is what makes it traceable from
      both ends. Here there is no incoming allocation to reverse, because no
      money ever came in through this system. Reusing it would leave its one
      distinguishing field empty.
    * ``DepositReturn`` returns a deposit; ``PartnerSettlement`` pays a
      partner. Neither is a participant's historical credit.
    * The cashbox has no outgoing model at all. Every model there —
      ``Receipt``, ``PaymentAllocation``, ``DailyClosing`` — is money coming
      IN, and ``system_total_for`` reconciles issued receipts only. That is
      the established design, so a payout belongs beside the other payouts in
      billing rather than inside the till count.

    **No receipt, ever.** A ``Receipt`` asserts that cash arrived. This is cash
    leaving, on an obligation the centre inherited; issuing one would state
    the opposite of what happened and would inflate the day's takings by the
    amount handed back.

    **One payout, guaranteed by shape.** ``opening_balance`` is a OneToOne, so
    a second payout collides at the database even if every service guard were
    removed.

    **The voucher is required.** Cash leaving the centre carries a document
    number — ``external_reference`` is what an auditor follows from this row
    to the paper, and a payout without one cannot be verified by anybody.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز الصرف"))

    opening_balance = models.OneToOneField(
        OpeningBalance,
        on_delete=models.PROTECT,
        related_name="refund_payout",
        verbose_name=_("الرصيد الافتتاحي"),
    )

    amount = Money(verbose_name=_("المبلغ المصروف"))
    paid_on = models.DateField(verbose_name=_("تاريخ الصرف"))

    #: How it left — the same reference table the till uses for money coming
    #: in, because "cash" and "cheque" mean the same thing in both directions.
    payment_method = models.ForeignKey(
        "cashbox.PaymentMethod",
        on_delete=models.PROTECT,
        related_name="opening_balance_refunds",
        verbose_name=_("طريقة الصرف"),
    )
    external_reference = DisplayRef(verbose_name=_("رقم سند الصرف"))
    payee_name_ar = NameAr(verbose_name=_("اسم المستلم"))
    note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة"))

    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="opening_balance_refunds_paid",
        verbose_name=_("صرفه"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("صرف رصيد افتتاحي دائن")
        verbose_name_plural = _("صرف الأرصدة الافتتاحية الدائنة")
        ordering = ["-paid_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="billing_ob_refund_amount_positive",
            ),
            # Money leaving the centre always carries the document it left on.
            models.CheckConstraint(
                condition=~models.Q(external_reference=""),
                name="billing_ob_refund_has_voucher",
            ),
            # And names who took it. "Paid out" with no payee is unverifiable.
            models.CheckConstraint(
                condition=~models.Q(payee_name_ar=""),
                name="billing_ob_refund_has_payee",
            ),
        ]
        indexes = [
            models.Index(fields=["paid_on"], name="bil_obref_paid_idx"),
            models.Index(fields=["payment_method"], name="bil_obref_method_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.amount}"
