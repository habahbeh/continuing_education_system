"""
What the centre SPENT (§9.7, §5.5) — a different ledger from what a partner owes.

**The distinction this module exists to hold.** Three things in this system
carry the word «مستهلكات» and only one of them is an expense:

* an ``Expense`` here — money the centre paid out, deducted from its net income;
* ``ChargeType.CONSUMABLES`` — money charged TO a participant, which is
  REVENUE and reaches the ledger through ``ChargeLine``;
* ``Agreement.exclude_consumables`` — whether that revenue enters the
  partner's distribution base at all.

Confusing the first with the second makes "net centre income" subtract a
figure that was income. Confusing it with the third makes an expense entry
move a partner's share. Neither may happen, which is why this is its own app
with its own table and its own screen, and why the first test written against
it asserts that recording an expense changes no partner share by one dinar.

**Two people, from the matrix.** §3.4 of the permission matrix gives the
finance officer ``C E`` here and the centre manager ``A`` — the officer
records and the manager approves. That split was already encoded before this
app existed; it is read from there rather than invented.

Only APPROVED expenses reduce net income. A recorded-but-unapproved entry is a
claim about money, not yet an agreed fact about it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, ShortCode


class ExpenseStatus(models.TextChoices):
    RECORDED = "RECORDED", _("مقيَّد")
    APPROVED = "APPROVED", _("معتمَد")
    REJECTED = "REJECTED", _("مرفوض")


class Expense(models.Model):
    """One outgoing payment by the centre (§9.7)."""

    code = ShortCode(unique=True, verbose_name=_("رمز القيد"))

    #: §9.7 names «مستهلكات · تسويق · شهادات · غيرها». The vocabulary is a
    #: SETTING rather than choices, on the ``certificate_grades`` precedent: a
    #: centre that starts tracking a new category should not need a migration,
    #: and there is no CHECK constraint on it for the same reason.
    category = ShortCode(verbose_name=_("التصنيف"))

    amount = Money(verbose_name=_("المبلغ"))
    incurred_on = models.DateField(verbose_name=_("تاريخ الصرف"))
    description_ar = models.CharField(max_length=255, verbose_name=_("البيان"))

    #: §5.5 — consumables bought for a particular cohort are tied to it so the
    #: per-student figure can be read beside the agreement's cap. Marketing has
    #: no cohort, so this is optional rather than absent.
    cohort = models.ForeignKey(
        "operations.Cohort",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name=_("الدفعة"),
    )

    reference = DisplayRef(verbose_name=_("رقم الفاتورة أو أمر الصرف"))

    #: Same pattern as ``Receipt.financial_period`` — an entry dated into a
    #: closed period is refused, so a closed month cannot quietly move.
    financial_period = models.ForeignKey(
        "core.FinancialPeriod",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name=_("الفترة المالية"),
    )

    status = ShortCode(
        choices=ExpenseStatus.choices,
        default=ExpenseStatus.RECORDED,
        verbose_name=_("الحالة"),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="expenses_created",
        verbose_name=_("قيّده"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses_approved",
        verbose_name=_("اعتمده"),
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة القرار"))

    class Meta:
        verbose_name = _("مصروف")
        verbose_name_plural = _("المصروفات")
        ordering = ["-incurred_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ExpenseStatus.values),
                name="expenses_expense_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expenses_expense_amount_positive"
            ),
            # A payment with no statement of what it bought cannot be defended
            # in report 7, which is the whole reason the row exists.
            models.CheckConstraint(
                condition=~models.Q(description_ar=""),
                name="expenses_expense_has_description",
            ),
            # D-18 — the same hand does not record and approve. The precedent
            # is Discount and Refund, and money leaving deserves it at least
            # as much as money being waived.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("created_by")),
                name="expenses_expense_approver_differs",
            ),
            # An APPROVED row carries who approved it and when. Without that
            # the status is an assertion nobody signed.
            models.CheckConstraint(
                condition=~models.Q(status=ExpenseStatus.APPROVED)
                | (models.Q(approved_by__isnull=False) & models.Q(approved_at__isnull=False)),
                name="expenses_expense_approved_is_stamped",
            ),
        ]
        indexes = [
            models.Index(fields=["incurred_on"], name="exp_incurred_idx"),
            models.Index(fields=["category", "status"], name="exp_cat_status_idx"),
            models.Index(fields=["cohort"], name="exp_cohort_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.amount}"

    @property
    def counts_toward_net_income(self) -> bool:
        """§9.2 — only an approved expense reduces the centre's net income."""
        return self.status == ExpenseStatus.APPROVED


__all__ = ["Expense", "ExpenseStatus"]
