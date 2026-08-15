"""
Cashbox — what was RECEIVED, and against which line (DATA_MODEL §8.7 … §8.11).

``PaymentAllocation`` is the table the demo did not have, and its absence was
the root of three separate defects at once. With it:

* **Partner entitlement (BR-044)** is a sum over allocations to shareable
  lines, not a guess re-derived from a ``paid`` total and an assumed ordering.
* **Transfers (Q-11)** move allocations, which leaves the old enrolment at zero
  received automatically — no exception rule, and no double entitlement.
* **Voiding (BR-025)** writes REVERSING allocations instead of editing or
  deleting anything, so the original receipt survives intact.

Two controls are pushed down to the database because they are the ones people
are tempted to work around under pressure:

* ``ReceiptVoid.approved_by <> requested_by`` — the cashier requests, finance
  approves (D-18, Δ-06). The demo let a cashier void alone.
* ``DailyClosing.approved_by <> cashier`` — whoever counted the drawer does not
  sign off on it (BR-028).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, ShortCode


class ReceiptStatus(models.TextChoices):
    ISSUED = "ISSUED", _("صادر")
    VOIDED = "VOIDED", _("ملغى")


class AllocationType(models.TextChoices):
    AUTOMATIC = "AUTOMATIC", _("آلي")
    MANUAL = "MANUAL", _("يدوي")


class ClosingStatus(models.TextChoices):
    OPEN = "OPEN", _("مفتوح")
    VARIANCE_PENDING = "VARIANCE_PENDING", _("فرق قيد المطابقة")
    RECONCILED = "RECONCILED", _("مطابق مقفل")


class PaymentMethod(models.Model):
    """
    A reference table, not fixed choices (DATA_MODEL §8.7).

    Bank transfer and card are future scope; adding them must be a row, not a
    migration.
    """

    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = models.CharField(max_length=150, verbose_name=_("الطريقة"))
    is_active = models.BooleanField(default=True, verbose_name=_("نشطة"))

    class Meta:
        verbose_name = _("طريقة دفع")
        verbose_name_plural = _("طرق الدفع")
        ordering = ["name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class Receipt(models.Model):
    """
    A receipt (DATA_MODEL §8.8, Q-03).

    ``internal_receipt_number`` is the system's own gapless number and the ONLY
    reference the closing, the audit trail, allocations and reports use.
    ``external_receipt_ref`` records the finance department's or paper book's
    number when there is one, and no logic is ever built on it — so if that
    department later becomes the official source, nothing migrates.
    """

    internal_receipt_number = DisplayRef(
        unique=True,
        verbose_name=_("رقم السند الداخلي"),
        help_text=_("gapless سنوي من NumberSequence — المرجع النظامي (Q-03)"),
    )
    external_receipt_ref = DisplayRef(
        blank=True,
        verbose_name=_("رقم السند الخارجي"),
        help_text=_("من الدائرة المالية أو الدفتر الورقي — اختياري"),
    )
    participant = models.ForeignKey(
        "people.Participant",
        on_delete=models.PROTECT,
        related_name="receipts",
        verbose_name=_("المشارك"),
    )
    received_on = models.DateField(verbose_name=_("تاريخ القبض"))
    amount = Money(verbose_name=_("المبلغ المقبوض"))
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        related_name="receipts",
        verbose_name=_("طريقة الدفع"),
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="receipts_taken",
        verbose_name=_("القابض"),
    )
    breakdown_text_ar = models.CharField(max_length=255, blank=True)

    daily_closing = models.ForeignKey(
        "cashbox.DailyClosing",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipts",
    )
    financial_period = models.ForeignKey(
        "core.FinancialPeriod",
        on_delete=models.PROTECT,
        related_name="receipts",
        null=True,
        blank=True,
        verbose_name=_("الفترة المالية"),
    )

    voucher_received = models.BooleanField(default=False)
    voucher_received_at = models.DateTimeField(null=True, blank=True)
    voucher_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipt_vouchers",
    )

    status = ShortCode(choices=ReceiptStatus.choices, default=ReceiptStatus.ISSUED)
    created_at = models.DateTimeField(auto_now_add=True)

    # MySQL lets NULLs repeat in a unique key, so a plain UNIQUE on the
    # optional external reference would not stop the same finance-department
    # number being entered twice. This copy is NULL-free for present values
    # and holds the constraint (§11.3, same device as Semester.active_flag).

    # must constrain only references that exist; NULLs are what let the
    # optional field stay optional.
    external_ref_key = models.CharField(  # noqa: DJ001
        max_length=64, null=True, editable=False, default=None
    )

    class Meta:
        verbose_name = _("سند قبض")
        verbose_name_plural = _("سندات القبض")
        ordering = ["-received_on", "-internal_receipt_number"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ReceiptStatus.values),
                name="cashbox_receipt_status_valid",
            ),
            # The demo issued receipt R-20260722-018 for zero.
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="cashbox_receipt_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(voucher_received=False)
                | models.Q(voucher_received_at__isnull=False),
                name="cashbox_receipt_voucher_timestamped",
            ),
            models.UniqueConstraint(
                fields=["external_ref_key"], name="cashbox_receipt_external_ref_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["received_on", "cashier"], name="cash_rcpt_date_cashier_idx"),
            models.Index(fields=["participant"], name="cash_rcpt_participant_idx"),
            models.Index(fields=["daily_closing"], name="cash_rcpt_closing_idx"),
            models.Index(fields=["status", "received_on"], name="cash_rcpt_status_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.internal_receipt_number} — {self.amount}"

    def save(self, *args: object, **kwargs: object) -> None:
        self.external_ref_key = self.external_receipt_ref or None
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class PaymentAllocation(models.Model):
    """
    Which charge line a payment went to (DATA_MODEL §8.9).

    ``charge_line = NULL`` is a credit balance: money received that no line
    was owed for (BR-023). It is a real allocation, not an absence, so the
    invariant ``Σ allocations == receipt.amount`` holds exactly for every
    receipt — to the fils.
    """

    receipt = models.ForeignKey(
        Receipt,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("السند"),
    )
    charge_line = models.ForeignKey(
        "billing.ChargeLine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocations",
        verbose_name=_("بند الرسم"),
        help_text=_("فارغ = رصيد دائن غير مخصَّص (BR-023)"),
    )
    enrollment = models.ForeignKey(
        "operations.Enrollment",
        on_delete=models.PROTECT,
        related_name="allocations",
        null=True,
        blank=True,
        verbose_name=_("التسجيل"),
    )
    amount = Money(verbose_name=_("المبلغ المخصَّص"))
    allocation_type = ShortCode(choices=AllocationType.choices, default=AllocationType.AUTOMATIC)
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocations_made",
    )
    allocated_at = models.DateTimeField(auto_now_add=True)
    manual_reason_ar = models.CharField(max_length=255, blank=True)

    # BR-025 — voiding writes a reversing allocation pointing back at the
    # original. Nothing is deleted, and the pair reads as a history.
    reversed_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reverses",
        verbose_name=_("التخصيص العكسي"),
    )

    class Meta:
        verbose_name = _("تخصيص دفعة")
        verbose_name_plural = _("تخصيصات الدفعات")
        ordering = ["receipt", "id"]
        constraints = [
            # Zero is meaningless here; negative is a reversal and legitimate.
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="cashbox_allocation_amount_not_zero"
            ),
            models.CheckConstraint(
                condition=~models.Q(allocation_type=AllocationType.MANUAL)
                | (models.Q(allocated_by__isnull=False) & ~models.Q(manual_reason_ar="")),
                name="cashbox_allocation_manual_is_explained",
            ),
        ]
        indexes = [
            models.Index(fields=["receipt"], name="cash_alloc_receipt_idx"),
            models.Index(fields=["charge_line"], name="cash_alloc_chargeline_idx"),
            models.Index(fields=["enrollment", "allocated_at"], name="cash_alloc_enr_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.amount} → {self.charge_line or _('رصيد دائن')}"


class ReceiptVoid(models.Model):
    """
    Voiding a receipt (DATA_MODEL §8.10, BR-025, Δ-06).

    The cashier requests and finance approves. The demo let a cashier void a
    receipt alone, which puts the person who took the cash in sole control of
    unmaking the record of it.
    """

    receipt = models.OneToOneField(
        Receipt,
        on_delete=models.PROTECT,
        related_name="void_record",
        verbose_name=_("السند"),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="voids_requested"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voids_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    reason_ar = models.TextField(verbose_name=_("سبب الإلغاء"))

    class Meta:
        verbose_name = _("إلغاء سند")
        verbose_name_plural = _("إلغاءات السندات")
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason_ar=""), name="cashbox_void_has_reason"
            ),
            # D-18 / Δ-06 — separation of duties, in the database.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("requested_by")),
                name="cashbox_void_approver_differs",
            ),
        ]

    def __str__(self) -> str:
        return f"إلغاء {self.receipt.internal_receipt_number}"


class DailyClosing(models.Model):
    """The daily till reconciliation (DATA_MODEL §8.11, BR-026 … BR-028)."""

    code = ShortCode(unique=True, verbose_name=_("رمز الإقفال"))
    closing_date = models.DateField(verbose_name=_("تاريخ الإقفال"))
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="closings",
        verbose_name=_("القابض"),
    )
    system_total = Money(default=0, verbose_name=_("إجمالي النظام"))
    counted_total = Money(default=0, verbose_name=_("العدّ الفعلي"))
    variance = Money(default=0, verbose_name=_("الفرق"))
    receipt_count = models.PositiveIntegerField(default=0)
    status = ShortCode(choices=ClosingStatus.choices, default=ClosingStatus.OPEN)
    variance_resolution_ar = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closings_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("إقفال يومي")
        verbose_name_plural = _("الإقفالات اليومية")
        ordering = ["-closing_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ClosingStatus.values),
                name="cashbox_closing_status_valid",
            ),
            models.UniqueConstraint(
                fields=["closing_date", "cashier"], name="cashbox_closing_unique_day_cashier"
            ),
            models.CheckConstraint(
                condition=models.Q(variance=models.F("counted_total") - models.F("system_total")),
                name="cashbox_closing_variance_is_difference",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=ClosingStatus.RECONCILED)
                | models.Q(approved_by__isnull=False),
                name="cashbox_closing_reconciled_is_approved",
            ),
            # BR-027 — a till may close with a difference, but not silently.
            models.CheckConstraint(
                condition=~models.Q(status=ClosingStatus.RECONCILED)
                | models.Q(variance=0)
                | ~models.Q(variance_resolution_ar=""),
                name="cashbox_closing_variance_is_explained",
            ),
            # BR-028 — whoever held the cash does not sign off on the count.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("cashier")),
                name="cashbox_closing_approver_is_not_cashier",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.closing_date}"
