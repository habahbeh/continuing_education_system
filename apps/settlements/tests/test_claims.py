"""
Building, sealing and offsetting a partner claim (BR-045 … BR-052, ADR-007).

Two of these tests exist because the demo got the arithmetic wrong, not
because the rule is subtle:

* it displayed a discount share as a deduction and did NOT subtract it from
  the base, so every share drawn from that base was too high;
* it had no freeze at all, so an approved claim could be edited afterwards and
  nothing recorded that it had been.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.core.exceptions import ImmutableRecordError
from apps.settlements.models import (
    ClaimStatus,
    ObligationStatus,
    ObligationType,
    PartnerClaim,
    PartnerObligation,
)
from apps.settlements.services import claim_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
TERM_END = date(2026, 12, 20)


def _build(finance, agreement, cohort, period_to: date = TERM_END) -> PartnerClaim:
    return claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=cohort,
        period_from=TERM_START,
        period_to=period_to,
        trigger_type="END_OF_COURSE",
    )


# ---------------------------------------------------------------------------
# What the claim is built from
# ---------------------------------------------------------------------------
def test_the_base_excludes_the_registration_fee(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-009 — registration is the centre's own administrative charge.

    270 collected: 20 registration, excluded by the agreement, and 250
    tuition, which is the only shareable part.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    assert claim.gross_collected == Decimal("270.000")
    assert claim.excluded_registration == Decimal("20.000")
    assert claim.distribution_base == Decimal("250.000")
    assert claim.partner_share == Decimal("125.000")


def test_the_base_equation_holds_as_a_database_constraint(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    C-02 — the demo's overstated base is now unrepresentable.

    A base that does not equal gross minus the exclusions minus the partner's
    discount burden cannot be stored at all, so the error the demo made
    silently is impossible rather than merely discouraged.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerClaim.objects.filter(pk=claim.pk).update(
            discount_partner_burden=Decimal("85.000")  # not subtracted from the base
        )


def test_entitlement_follows_cash_not_invoices(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-044 — an invoice earns the partner nothing.

    Charged 270 and paid 120: after the 20 registration fee is covered, only
    100 of tuition has actually been collected, so that is the whole base.

    Claimed ten days in, deliberately: past the 30-day grace the same
    part-paid enrolment becomes overdue and earns the partner nothing at all
    (BR-045 · Q-16), which the next test asserts.
    """
    make_paid_enrollment(cohort_with_agreement, index=2, amount="120.000")
    claim = _build(
        finance,
        percent_agreement,
        cohort_with_agreement,
        period_to=TERM_START + timedelta(days=10),
    )

    assert claim.distribution_base == Decimal("100.000")
    assert claim.partner_share == Decimal("50.000")


def test_a_participant_who_falls_behind_stops_earning_the_partner_anything(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-045 meets Q-16 — the same 100 collected, three months later.

    The partner earned a share while the participant was merely part-paid;
    once the grace period lapses they are overdue and the share goes away
    until the balance is settled. That is the assumption at its most
    consequential, which is why the grace period is a setting.
    """
    make_paid_enrollment(cohort_with_agreement, index=2, amount="120.000")
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    assert claim.distribution_base == Decimal("0.000")
    assert claim.partner_share == Decimal("0.000")
    line = claim.lines.get()
    assert line.is_included is False
    assert line.exclusion_reason == "PAYMENT_OVERDUE"


def test_an_ineligible_participant_appears_as_an_excluded_line(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-045 — a withdrawn participant earns nothing, but is still SHOWN.

    Dropping the row would leave the partner unable to tell a participant who
    was considered and rejected from one the centre simply forgot.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    make_paid_enrollment(cohort_with_agreement, index=2, status="WITHDRAWN")
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    assert claim.lines.count() == 2
    excluded = claim.lines.get(is_included=False)
    assert excluded.exclusion_reason == "WITHDRAWN"
    assert excluded.partner_share == Decimal("0.000")
    assert claim.student_count == 1
    assert claim.distribution_base == Decimal("250.000")


def test_an_ineligible_entitlement_cannot_carry_a_share(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """BR-045 as a constraint, not only as service logic."""
    from apps.settlements.models import Entitlement

    make_paid_enrollment(cohort_with_agreement, index=2, status="WITHDRAWN")
    _build(finance, percent_agreement, cohort_with_agreement)
    entitlement = Entitlement.objects.get(is_eligible=False)

    with pytest.raises(IntegrityError), transaction.atomic():
        Entitlement.objects.filter(pk=entitlement.pk).update(partner_share=Decimal("125.000"))


def test_the_claim_line_snapshots_the_participant(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    The printed claim is the legal basis of the settlement (ADR-012).

    A participant renamed next term must not silently restate a document both
    parties already signed.
    """
    enrollment = make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    line = claim.lines.get(is_included=True)
    original = line.participant_name_snapshot

    enrollment.participant.name_ar = "اسم آخر تماماً"
    enrollment.participant.save()

    line.refresh_from_db()
    assert line.participant_name_snapshot == original


# ---------------------------------------------------------------------------
# Approval seals it (BR-051)
# ---------------------------------------------------------------------------
def test_approval_stamps_a_hash_that_verifies(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    assert claim.status == ClaimStatus.APPROVED
    assert len(claim.content_hash) == 64
    assert claim_service.verify_hash(claim) is True


def test_an_approved_claim_refuses_to_be_edited(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """BR-051 layer 1 — the model itself refuses, before any view or form."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    claim.partner_share = Decimal("999.000")
    with pytest.raises(ImmutableRecordError):
        claim.save()


def test_the_payment_stamps_may_still_move_after_approval(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    The freeze is not a wall around the whole row.

    An approved claim still has to be PAID, so status and the payment stamps
    stay writable — everything that decides the amount does not.
    """
    from django.utils import timezone

    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    claim.status = ClaimStatus.PAID
    claim.paid_by = manager
    claim.paid_at = timezone.now()
    claim.save()

    claim.refresh_from_db()
    assert claim.status == ClaimStatus.PAID


def test_tampering_behind_the_orm_is_caught_by_the_hash(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-051 layer 3 — the layer that survives a raw UPDATE.

    ``save()`` cannot see a queryset update, which is exactly the case the
    hash exists for.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    claim.lines.filter(is_included=True).update(participant_name_snapshot="اسم مزوَّر")
    claim.refresh_from_db()
    assert claim_service.verify_hash(claim) is False


def test_the_verify_command_reports_a_tampered_claim(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    The scheduled sweep, not just the per-claim check.

    A broken seal must be findable without knowing which claim to look at —
    otherwise layer 3 only works for someone already suspicious.
    """
    from django.core.management import CommandError, call_command

    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    call_command("verify_claim_hashes", verbosity=0)  # clean

    claim.lines.filter(is_included=True).update(partner_share=Decimal("999.000"))
    with pytest.raises(CommandError):
        call_command("verify_claim_hashes", verbosity=0)


def test_nobody_approves_the_claim_they_raised(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """D-18 — separation of duties on the document that authorises a payment."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(PermissionDenied):
        claim_service.approve_claim(actor=finance, claim=claim)


def test_the_database_also_refuses_a_self_approved_claim(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """D-18 held at the last line too, not only in the service."""
    from django.utils import timezone

    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerClaim.objects.filter(pk=claim.pk).update(
            status=ClaimStatus.APPROVED,
            approved_by=finance,
            approved_at=timezone.now(),
            content_hash="x" * 64,
        )


def test_an_approved_claim_cannot_lack_its_seal(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """An approved claim with no hash is one nobody can prove was not edited."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerClaim.objects.filter(pk=claim.pk).update(status=ClaimStatus.APPROVED)


def test_a_refused_approval_leaves_an_audit_row(
    seeded_settings,
    finance,
    cashier,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-100 — the refusal must outlive the raise.

    Approval is transactional, so a permission check inside the transaction
    would have its denied-attempt row rolled back with the exception, and the
    attempt to approve a partner payment would leave no trace at all.
    """
    from apps.core.models import AuditEvent

    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(PermissionDenied):
        claim_service.approve_claim(actor=cashier, claim=claim)

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=cashier).exists()


def test_a_cashier_cannot_approve_a_claim(
    seeded_settings,
    finance,
    cashier,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(PermissionDenied):
        claim_service.approve_claim(actor=cashier, claim=claim)


# ---------------------------------------------------------------------------
# Deductions (BR-036, BR-052, Q-08)
# ---------------------------------------------------------------------------
def test_a_deduction_names_the_event_that_caused_it(
    seeded_settings,
    finance,
    partner,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    The user's condition on Q-08: every deduction shows its source.

    "Deduction: 100" is a dispute waiting to happen; the obligation code and
    its type are a record the partner can check against their own file.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    obligation = PartnerObligation.objects.create(
        code="OBL-RF-1",
        partner=partner,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("40.000"),
        occurred_on=TERM_START,
        created_by=finance,
        statement_reference="استرداد RF-001",
    )
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    deductions = claim_service.apply_offsets(actor=finance, claim=claim)

    assert len(deductions) == 1
    assert deductions[0].source_obligation_id == obligation.pk
    assert "OBL-RF-1" in deductions[0].label_ar
    assert claim.net_payable == Decimal("85.000")  # 125 − 40


def test_a_pinned_obligation_says_so_on_the_deduction_line(
    seeded_settings,
    finance,
    partner,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """A restricted recovery reads differently, so the restriction is visible."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    PartnerObligation.objects.create(
        code="OBL-PIN-1",
        partner=partner,
        restricted_to_agreement=percent_agreement,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("40.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    deduction = claim_service.apply_offsets(actor=finance, claim=claim)[0]

    assert percent_agreement.agreement_number in deduction.label_ar


def test_recovery_never_exceeds_what_the_claim_can_bear(
    seeded_settings,
    finance,
    partner,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    BR-036 — the centre deducts from the next claim; it never invoices.

    An obligation larger than the claim is recovered as far as the claim
    reaches and stays OPEN for the remainder.
    """
    make_paid_enrollment(cohort_with_agreement, index=1)
    obligation = PartnerObligation.objects.create(
        code="OBL-BIG",
        partner=partner,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("500.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.apply_offsets(actor=finance, claim=claim)

    obligation.refresh_from_db()
    assert claim.net_payable == Decimal("0.000")
    assert obligation.recovered_amount == Decimal("125.000")
    assert obligation.status == ObligationStatus.PARTIALLY_RECOVERED


def test_a_negative_payable_cannot_be_stored(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """BR-036 as a constraint — a negative payable is a demand on the partner."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)

    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerClaim.objects.filter(pk=claim.pk).update(
            total_deductions=Decimal("200.000"), net_payable=Decimal("-75.000")
        )


def test_offsets_cannot_be_applied_to_an_approved_claim(
    seeded_settings,
    finance,
    manager,
    partner,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """Deducting after approval would change the amount someone signed off."""
    make_paid_enrollment(cohort_with_agreement, index=1)
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim_service.approve_claim(actor=manager, claim=claim)

    PartnerObligation.objects.create(
        code="OBL-LATE",
        partner=partner,
        obligation_type=ObligationType.REFUND_RECOVERY,
        amount=Decimal("10.000"),
        occurred_on=TERM_START,
        created_by=finance,
    )
    with pytest.raises(ImmutableRecordError):
        claim_service.apply_offsets(actor=finance, claim=claim)


def test_a_claim_with_a_negative_net_is_refused_at_approval(
    seeded_settings,
    finance,
    manager,
    percent_agreement,
    cohort_with_agreement,
) -> None:
    """
    An empty cohort earns nothing; approving a negative claim is refused.

    Built with no enrolments so the share is zero, then forced negative to
    reach the service check rather than the constraint.
    """
    claim = _build(finance, percent_agreement, cohort_with_agreement)
    claim.net_payable = Decimal("-1.000")

    with pytest.raises(ValidationError):
        claim_service.approve_claim(actor=manager, claim=claim)


def test_a_deduction_must_carry_a_label(
    seeded_settings,
    finance,
    percent_agreement,
    cohort_with_agreement,
) -> None:
    """An unlabelled deduction is the thing the source-naming rule forbids."""
    from apps.settlements.models import ClaimDeduction, DeductionType

    claim = _build(finance, percent_agreement, cohort_with_agreement)
    with pytest.raises(IntegrityError), transaction.atomic():
        ClaimDeduction.objects.create(
            claim=claim,
            deduction_type=DeductionType.OTHER,
            label_ar="",
            amount=Decimal("10.000"),
        )
