"""
Partner entitlement, claims and settlements (DATA_MODEL §9).

An approved claim is EVIDENCE, not a record that can be tidied up afterwards.
Three layers hold it still (ADR-007, BR-051):

1. ``save()`` refuses any change once APPROVED or PAID, except the fields that
   legitimately move afterwards (settlement link, status, payment stamps);
2. ``content_hash`` seals the legal fields plus every line at approval;
3. ``verify_claim_hashes`` recomputes them and reports drift.

Two constraints correct defects the demo actually had:

* ``distribution_base`` must equal gross minus each exclusion minus the
  partner's discount burden. The demo displayed ``discountShare: 85`` as a
  deduction and did NOT subtract it, so the base was overstated and every
  share computed from it was too high.
* ``net_payable >= 0``. A claim that pays a negative amount is a demand on the
  partner, and demanding cash from a partner is exactly what BR-036 forbids —
  recovery happens by deducting from the NEXT claim, never by invoicing.

Q-08, Q-09 and Q-16 are assumptions, not settled client policy — see
``settlements.services`` for how each is kept switchable.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ImmutableRecordError
from apps.core.fields import Money, Rate, ShortCode


class ImmutableClaimError(ImmutableRecordError):
    """Raised when an approved claim is edited (BR-051, D-12)."""


#: Fields that may still move after approval — the settlement link, the
#: status, and the payment stamps. Everything else is sealed.
_MUTABLE_AFTER_APPROVAL = {
    "status",
    "settlement",
    "paid_by",
    "paid_at",
    "content_hash",
    "approved_by",
    "approved_at",
}


class IneligibilityReason(models.TextChoices):
    WITHDRAWN = "WITHDRAWN", _("منسحب")
    DISMISSED = "DISMISSED", _("مفصول")
    PAYMENT_OVERDUE = "PAYMENT_OVERDUE", _("متأخر عن الدفع")
    NOT_ATTENDED = "NOT_ATTENDED", _("غير حاضر")
    INCOMPLETE = "INCOMPLETE", _("غير مكمل")
    NO_AGREEMENT = "NO_AGREEMENT", _("بلا اتفاقية")


class ClaimStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسوّدة")
    SUBMITTED = "SUBMITTED", _("مرفوعة للاعتماد")
    APPROVED = "APPROVED", _("معتمدة")
    PAID = "PAID", _("مصروفة")
    CANCELLED = "CANCELLED", _("ملغاة")


class DeductionType(models.TextChoices):
    REFUND_RECOVERY = "REFUND_RECOVERY", _("استرجاع استرداد")
    FIELD_TRAINING = "FIELD_TRAINING", _("مصاريف تدريب عملي")
    TRAINER_ABSENCE = "TRAINER_ABSENCE", _("غرامة غياب مدرب")
    WITHDRAWAL_RETURN = "WITHDRAWAL_RETURN", _("إعادة عن انسحاب")
    ADVANCE_CLAWBACK = "ADVANCE_CLAWBACK", _("استرجاع صرف مقدّم")
    OTHER = "OTHER", _("أخرى")


class ObligationType(models.TextChoices):
    TRAINER_SALARIES = "TRAINER_SALARIES", _("رواتب مدربين")
    FIELD_TRAINING_EXPENSE = "FIELD_TRAINING_EXPENSE", _("مصاريف تدريب عملي")
    TRAINER_ABSENCE_PENALTY = "TRAINER_ABSENCE_PENALTY", _("غرامة غياب مدرب")
    WITHDRAWAL_RETURN = "WITHDRAWAL_RETURN", _("إعادة عن انسحاب")
    REFUND_RECOVERY = "REFUND_RECOVERY", _("استرجاع استرداد")
    ADVANCE_CLAWBACK = "ADVANCE_CLAWBACK", _("استرجاع صرف مقدّم")


class ObligationStatus(models.TextChoices):
    OPEN = "OPEN", _("قائم")
    PARTIALLY_RECOVERED = "PARTIALLY_RECOVERED", _("مُستردّ جزئياً")
    RECOVERED = "RECOVERED", _("مُستردّ")
    WAIVED = "WAIVED", _("متنازَل عنه")


class Entitlement(models.Model):
    """
    One enrolment's eligibility at one moment (DATA_MODEL §9.1, BR-044, BR-045).

    Kept as a record rather than recomputed on demand because eligibility
    depends on a status that changes: a participant eligible in October may be
    withdrawn in November, and the claim raised in October must still be
    explainable.
    """

    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="entitlements"
    )
    agreement = models.ForeignKey(
        "partners.Agreement", on_delete=models.PROTECT, related_name="entitlements"
    )
    evaluated_at = models.DateTimeField(auto_now_add=True)
    is_eligible = models.BooleanField(default=False)
    ineligibility_reason = ShortCode(blank=True)

    paid_amount = Money(default=0, verbose_name=_("المقبوض"))
    excluded_amount = Money(default=0, verbose_name=_("المستثنى"))
    distribution_base = Money(default=0, verbose_name=_("وعاء القسمة"))
    applied_rate = Rate(null=True, blank=True)
    partner_share = Money(default=0, verbose_name=_("حصة الشريك"))

    claim_line = models.ForeignKey(
        "settlements.PartnerClaimLine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entitlements",
    )

    class Meta:
        verbose_name = _("استحقاق")
        verbose_name_plural = _("الاستحقاقات")
        ordering = ["-evaluated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(distribution_base__gte=0),
                name="settlements_entitlement_base_not_negative",
            ),
            # BR-045 — an ineligible enrolment earns nothing. Without this a
            # withdrawn participant could still carry a share into a claim.
            models.CheckConstraint(
                condition=models.Q(is_eligible=True) | models.Q(partner_share=0),
                name="settlements_entitlement_ineligible_earns_nothing",
            ),
        ]

    def __str__(self) -> str:
        verdict = "مؤهل" if self.is_eligible else self.ineligibility_reason or "غير مؤهل"
        return f"{self.enrollment_id} — {verdict} — {self.partner_share}"


class PartnerClaim(models.Model):
    """
    A claim — frozen at approval (DATA_MODEL §9.2, BR-051, ADR-007).

    Everything under "snapshot" is copied from the agreement at approval, so a
    later amendment to the agreement cannot restate a claim that has already
    been signed off.
    """

    code = ShortCode(unique=True, verbose_name=_("رمز المطالبة"))
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="claims")
    agreement = models.ForeignKey(
        "partners.Agreement", on_delete=models.PROTECT, related_name="claims"
    )
    cohort = models.ForeignKey(
        "operations.Cohort",
        on_delete=models.PROTECT,
        related_name="claims",
        null=True,
        blank=True,
    )
    period_from = models.DateField()
    period_to = models.DateField()
    trigger_type = ShortCode(verbose_name=_("مُحفّز المطالبة"))
    trigger_reference_ar = models.CharField(max_length=255, blank=True)

    # --- terms as they stood at approval ---------------------------------
    model_snapshot = ShortCode(blank=True)
    rate_snapshot = Rate(null=True, blank=True)
    fixed_amount_snapshot = Money(null=True, blank=True)
    exclusions_snapshot = models.JSONField(default=dict, blank=True)
    consumables_cap_snapshot = Money(null=True, blank=True)
    #: ⚠️ ASSUMPTION (Q-28) — which base the share was computed on, recorded
    #: per claim so a later change of policy is visible rather than implied.
    base_mode_snapshot = ShortCode(blank=True)

    # --- the numbers -----------------------------------------------------
    gross_collected = Money(default=0)
    excluded_registration = Money(default=0)
    excluded_consumables = Money(default=0)
    excluded_deposits = Money(default=0)
    discount_partner_burden = Money(default=0)
    distribution_base = Money(default=0)
    student_count = models.PositiveIntegerField(null=True, blank=True)
    partner_share = Money(default=0)
    total_deductions = Money(default=0)
    net_payable = Money(default=0)

    # --- governance ------------------------------------------------------
    status = ShortCode(choices=ClaimStatus.choices, default=ClaimStatus.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="claims_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claims_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claims_paid",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    settlement = models.ForeignKey(
        "settlements.PartnerSettlement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claims",
    )

    class Meta:
        verbose_name = _("مطالبة شريك")
        verbose_name_plural = _("مطالبات الشركاء")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ClaimStatus.values),
                name="settlements_claim_status_valid",
            ),
            # C-02 — the demo showed an 85 discount share as a deduction and
            # did not subtract it, overstating the base and every share drawn
            # from it. The equation is now the constraint.
            models.CheckConstraint(
                condition=models.Q(
                    distribution_base=models.F("gross_collected")
                    - models.F("excluded_registration")
                    - models.F("excluded_consumables")
                    - models.F("excluded_deposits")
                    - models.F("discount_partner_burden")
                ),
                name="settlements_claim_base_equation",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    net_payable=models.F("partner_share") - models.F("total_deductions")
                ),
                name="settlements_claim_net_equation",
            ),
            # BR-036 — a negative payable would be a demand on the partner.
            # Recovery is by deduction from the next claim, never an invoice.
            models.CheckConstraint(
                condition=models.Q(net_payable__gte=0),
                name="settlements_claim_net_not_negative",
            ),
            # D-18 — nobody approves the claim they raised.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("created_by")),
                name="settlements_claim_approver_differs",
            ),
            # BR-051 — an approved claim without its seal is a claim nobody
            # can prove was not edited afterwards.
            models.CheckConstraint(
                condition=~models.Q(status=ClaimStatus.APPROVED)
                | (models.Q(approved_by__isnull=False) & ~models.Q(content_hash="")),
                name="settlements_claim_approved_is_sealed",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status"], name="stl_claim_partner_status_idx"),
            models.Index(fields=["agreement", "period_from"], name="stl_claim_agr_period_idx"),
            models.Index(fields=["status", "created_at"], name="stl_claim_status_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.net_payable}"

    def save(self, *args: object, **kwargs: object) -> None:
        """
        BR-051 layer 1 — an approved claim does not change.

        Only the fields that legitimately move afterwards are allowed through:
        the status, the settlement it joins, and the payment stamps. Anything
        else raises, because the printed claim is what the partner signed.
        """
        if self.pk is not None:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous is not None and previous.is_frozen:
                changed = [
                    field.name
                    for field in self._meta.concrete_fields
                    if field.name not in _MUTABLE_AFTER_APPROVAL
                    and getattr(previous, field.attname) != getattr(self, field.attname)
                ]
                if changed:
                    raise ImmutableClaimError(
                        f"المطالبة {self.code} معتمدة — لا تُعدَّل الحقول: "
                        f"{'، '.join(changed)} (BR-051 · D-12)."
                    )
        super().save(*args, **kwargs)  # type: ignore[arg-type]

    @property
    def is_frozen(self) -> bool:
        return self.status in {ClaimStatus.APPROVED, ClaimStatus.PAID}


class PartnerClaimLine(models.Model):
    """
    One participant on a claim (DATA_MODEL §9.3).

    The name, number and status are SNAPSHOTS. If a participant is renamed or
    an enrolment status changes after approval, the printed claim must still
    show what was true when it was signed — that document is the legal basis
    of the settlement.
    """

    claim = models.ForeignKey(PartnerClaim, on_delete=models.PROTECT, related_name="lines")
    enrollment = models.ForeignKey(
        "operations.Enrollment", on_delete=models.PROTECT, related_name="claim_lines"
    )
    participant_number_snapshot = models.CharField(max_length=9)
    participant_name_snapshot = models.CharField(max_length=150)
    enrollment_status_snapshot = ShortCode()

    paid_amount = Money(default=0)
    excluded_amount = Money(default=0)
    distribution_base = Money(default=0)
    partner_share = Money(default=0)
    is_included = models.BooleanField(default=True)
    exclusion_reason = ShortCode(blank=True)

    class Meta:
        verbose_name = _("بند مطالبة")
        verbose_name_plural = _("بنود المطالبات")
        ordering = ["claim", "participant_number_snapshot"]
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "enrollment"], name="settlements_claim_line_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.participant_number_snapshot} — {self.partner_share}"


class PartnerObligation(models.Model):
    """
    Money the partner owes back (DATA_MODEL §9.5, BR-055 … BR-059, Q-09).

    ``restricted_to_agreement`` is the Q-08 lever: offsets default to the
    PARTNER level, and an obligation may be pinned to one agreement when the
    contract requires it. Left NULL, the obligation may be recovered from any
    of that partner's claims.

    ``ADVANCE_CLAWBACK`` is the Q-09 answer: when an advance-payout agreement
    closes its name list, the ineligible participants become a recorded
    obligation rather than a silent adjustment nobody can trace.
    """

    code = ShortCode(unique=True)
    partner = models.ForeignKey(
        "partners.Partner", on_delete=models.PROTECT, related_name="obligations"
    )
    #: ⚠️ ASSUMPTION (Q-08) — NULL means partner-level, which is the default.
    restricted_to_agreement = models.ForeignKey(
        "partners.Agreement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="restricted_obligations",
        verbose_name=_("مقيَّد باتفاقية"),
        help_text=_("فارغ = يُحسم من أي مطالبة لهذا الشريك (Q-08)"),
    )
    obligation_type = ShortCode(choices=ObligationType.choices)
    cohort = models.ForeignKey(
        "operations.Cohort",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="partner_obligations",
    )
    amount = Money(verbose_name=_("المبلغ"))
    statement_reference = models.CharField(max_length=255, blank=True)
    occurred_on = models.DateField()

    absence_count = models.PositiveSmallIntegerField(null=True, blank=True)
    lecture_cost = Money(null=True, blank=True)
    multiplier = models.PositiveSmallIntegerField(null=True, blank=True)

    status = ShortCode(choices=ObligationStatus.choices, default=ObligationStatus.OPEN)
    recovered_amount = Money(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="obligations_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("التزام شريك")
        verbose_name_plural = _("التزامات الشركاء")
        ordering = ["-occurred_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(obligation_type__in=ObligationType.values),
                name="settlements_obligation_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ObligationStatus.values),
                name="settlements_obligation_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0)
                & models.Q(recovered_amount__gte=0)
                & models.Q(recovered_amount__lte=models.F("amount")),
                name="settlements_obligation_amounts_valid",
            ),
            # BR-057 — a trainer-absence penalty is a formula, and a penalty
            # without its inputs cannot be defended to the partner.
            models.CheckConstraint(
                condition=~models.Q(obligation_type=ObligationType.TRAINER_ABSENCE_PENALTY)
                | (
                    models.Q(absence_count__isnull=False)
                    & models.Q(lecture_cost__isnull=False)
                    & models.Q(multiplier__isnull=False)
                ),
                name="settlements_obligation_penalty_has_inputs",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status"], name="stl_obl_partner_status_idx"),
            models.Index(fields=["restricted_to_agreement"], name="stl_obl_restricted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.amount}"

    @property
    def outstanding(self) -> object:
        return self.amount - self.recovered_amount


class ClaimDeduction(models.Model):
    """
    One deduction on a claim (DATA_MODEL §9.4, BR-052).

    Every deduction names its source — the obligation or the refund it came
    from — because a partner presented with a reduced payment is entitled to
    see which event caused it. "Deduction: 150" is a dispute; "recovery of
    refund RF-001, agreement 2026/18" is a record.
    """

    claim = models.ForeignKey(PartnerClaim, on_delete=models.PROTECT, related_name="deductions")
    deduction_type = ShortCode(choices=DeductionType.choices)
    label_ar = models.CharField(max_length=255, verbose_name=_("البيان"))
    amount = Money(verbose_name=_("المبلغ"))
    source_obligation = models.ForeignKey(
        PartnerObligation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deductions",
    )
    source_refund = models.ForeignKey(
        "billing.Refund",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claim_deductions",
    )

    class Meta:
        verbose_name = _("بند حسم")
        verbose_name_plural = _("بنود الحسم")
        ordering = ["claim", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="settlements_deduction_amount_positive"
            ),
            models.CheckConstraint(
                condition=~models.Q(label_ar=""), name="settlements_deduction_has_label"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label_ar} — {self.amount}"


class SettlementStatus(models.TextChoices):
    OPEN = "OPEN", _("مفتوحة")
    SIGNED = "SIGNED", _("موقّعة")


class PartnerSettlement(models.Model):
    """A signed settlement closing a financial cycle (DATA_MODEL §9.6, BR-053)."""

    code = ShortCode(unique=True)
    partner = models.ForeignKey(
        "partners.Partner", on_delete=models.PROTECT, related_name="settlements"
    )
    agreement = models.ForeignKey(
        "partners.Agreement",
        on_delete=models.PROTECT,
        related_name="settlements",
        null=True,
        blank=True,
    )
    cycle_type = ShortCode(blank=True)
    period_from = models.DateField()
    period_to = models.DateField()
    total_due = Money(default=0)
    total_paid = Money(default=0)
    balance = Money(default=0)
    status = ShortCode(choices=SettlementStatus.choices, default=SettlementStatus.OPEN)
    signed_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="settlements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("مخالصة شريك")
        verbose_name_plural = _("مخالصات الشركاء")
        ordering = ["-period_to"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=SettlementStatus.values),
                name="settlements_settlement_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(balance=models.F("total_due") - models.F("total_paid")),
                name="settlements_settlement_balance_equation",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=SettlementStatus.SIGNED)
                | models.Q(signed_on__isnull=False),
                name="settlements_settlement_signed_has_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.balance}"
