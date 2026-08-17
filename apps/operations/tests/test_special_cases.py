"""
Special cases — T-130 … T-134, BR-067 … BR-071.

⏳ **Sprint 7 boundary, stated once here and honoured throughout.** Several of
these rules end at clearance: a dismissed participant's clearance is suspended
(T-131), and a credit balance blocks clearance until it is returned (T-134,
BR-071). ``Clearance`` does not exist yet. What Sprint 6 can prove — the debt
survives, the credit is recorded, the case names its financial effect — is
proved; the clearance half is marked DEFERRED and is NOT counted as passing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.billing.models import ChargeLine, ChargeType
from apps.billing.services.account_service import get_account_state
from apps.cashbox.models import PaymentAllocation, Receipt, ReceiptStatus
from apps.operations.models import (
    EnrollmentStatus,
    SpecialCase,
    SpecialCaseType,
)
from apps.operations.services import enrollment_service, special_case_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


@pytest.fixture
def enrolled(make_cohort, approve_cohort, make_enrollment, charge_and_pay):
    """A participant on SC-NET: 250 tuition + 20 registration."""

    def _make(paid: str = "270.000", index: int = 1, code: str = "CO-SC"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount=paid)
        return enrollment

    return _make


# ---------------------------------------------------------------------------
# T-130 · T-131 — BR-067 · BR-068, dismissal
# ---------------------------------------------------------------------------
def test_a_dismissal_without_a_decision_reference_is_refused(enrolled, manager) -> None:
    """
    T-130 / BR-067 — dismissal costs the participant their fees and clearance.

    Refused in the service for the readable message and in the database for
    everything that bypasses it.
    """
    enrollment = enrolled()
    with pytest.raises(special_case_service.DecisionReferenceRequiredError):
        special_case_service.dismiss(
            actor=manager,
            enrollment=enrollment,
            decision_reference="  ",
            detail_ar="مشكلة سلوكية",
            occurred_on=TERM_START,
            code="SC-1",
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        SpecialCase.objects.create(
            code="SC-BAD",
            case_type=SpecialCaseType.DISMISSAL,
            enrollment=enrollment,
            occurred_on=TERM_START,
            detail_ar="بلا مرجع",
            created_by=manager,
        )


def test_a_dismissal_records_the_debt_it_leaves_behind(enrolled, manager) -> None:
    """
    T-131 (the part Sprint 6 owns) / BR-068 — no refund, and the debt stands.

    ⏳ DEFERRED to Sprint 7: that this debt SUSPENDS clearance. Clearance does
    not exist, so the rule is recorded on the case and not asserted here.
    """
    enrollment = enrolled(paid="100.000")  # 270 charged, 170 still owed
    case = special_case_service.dismiss(
        actor=manager,
        enrollment=enrollment,
        decision_reference="قرار مجلس المركز 2026/7",
        detail_ar="رسوب في أربع مواد",
        occurred_on=TERM_START,
        code="SC-DIS",
    )

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.DISMISSED
    assert get_account_state(enrollment).balance == Decimal("170.000")
    assert "170.000" in case.financial_effect_ar
    assert "BR-068" in case.financial_effect_ar


def test_a_dismissed_participant_earns_the_partner_nothing(enrolled, manager) -> None:
    """
    BR-068 · BR-045 — the entitlement rule already covers the status.

    Given a partner cohort, so the verdict is about the DISMISSAL and not
    about there being no agreement to earn under.
    """
    from apps.partners.models import (
        Agreement,
        AgreementStatus,
        CalculationModel,
        Partner,
        PartnerType,
    )
    from apps.settlements.services import entitlement_service

    partner = Partner.objects.create(
        code="PRT-SC", name_ar="شريك", partner_type=PartnerType.COMPANY
    )
    agreement = Agreement.objects.create(
        agreement_number="2026/SC",
        partner=partner,
        title_ar="اتفاقية",
        signed_on=date(2026, 8, 1),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 8, 31),
        calculation_model=CalculationModel.PERCENT,
        percent_rate=Decimal("50.0000"),
        status=AgreementStatus.ACTIVE,
    )
    enrollment = enrolled()
    enrollment.cohort.agreement = agreement
    enrollment.cohort.save(update_fields=["agreement"])

    special_case_service.dismiss(
        actor=manager,
        enrollment=enrollment,
        decision_reference="قرار 9",
        detail_ar="سلوك",
        occurred_on=TERM_START,
        code="SC-DIS2",
    )
    enrollment.refresh_from_db()

    verdict = entitlement_service.evaluate_eligibility(enrollment, as_of=TERM_START)
    assert verdict.is_eligible is False
    assert verdict.reason == "DISMISSED"


# ---------------------------------------------------------------------------
# T-132 — BR-069, deferral
# ---------------------------------------------------------------------------
def test_a_deferral_carries_the_money_to_the_new_enrolment(
    enrolled, make_cohort, approve_cohort, manager
) -> None:
    """
    T-132 / BR-069 — a participant who paid 270 starts the next cohort at 270.

    The money moves the way every other move works: a reversing allocation on
    the old enrolment and a matching one on the new. Nothing is edited.
    """
    enrollment = enrolled()
    later = make_cohort("SC-NET", code="CO-LATER")
    approve_cohort(later, course_number="M-LATER")

    case, new = special_case_service.defer_to_cohort(
        actor=manager,
        enrollment=enrollment,
        to_cohort=later,
        occurred_on=TERM_START,
        case_code="SC-DEF",
        new_enrollment_code="EN-DEF",
    )

    assert case.case_type == SpecialCaseType.DEFERRAL
    assert get_account_state(new).total_paid == Decimal("270.000")
    assert get_account_state(new).unallocated_credit == Decimal("270.000")

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.DEFERRED
    assert enrollment.deferred_to_id == new.pk
    assert get_account_state(enrollment).total_paid == Decimal("0.000")
    assert get_account_state(enrollment).balance == Decimal("0.000")


def test_a_deferral_moves_money_without_deleting_anything(
    enrolled, make_cohort, approve_cohort, manager
) -> None:
    """The receipt still sums to itself, to the fils."""
    enrollment = enrolled()
    later = make_cohort("SC-NET", code="CO-LATER2")
    approve_cohort(later, course_number="M-LATER2")
    before = set(PaymentAllocation.objects.values_list("pk", flat=True))

    special_case_service.defer_to_cohort(
        actor=manager,
        enrollment=enrollment,
        to_cohort=later,
        occurred_on=TERM_START,
        case_code="SC-DEF2",
        new_enrollment_code="EN-DEF2",
    )

    assert before <= set(PaymentAllocation.objects.values_list("pk", flat=True))
    for receipt in Receipt.objects.filter(status=ReceiptStatus.ISSUED):
        assert (
            sum((a.amount for a in receipt.allocations.all()), Decimal("0.000")) == receipt.amount
        )


# ---------------------------------------------------------------------------
# T-133 — BR-070, substitution
# ---------------------------------------------------------------------------
def test_a_substitution_charges_no_new_registration_fee(
    enrolled, make_participant, manager
) -> None:
    """
    T-133 / BR-070 — the seat's admin work was paid for once.

    The incoming participant's enrolment is raised with no registration line;
    charging one would bill the centre's own work twice for one seat.
    """
    withdrawn = enrolled()
    enrollment_service.change_status(
        actor=manager,
        enrollment=withdrawn,
        to_status=EnrollmentStatus.WITHDRAWN,
        reason_ar="انسحاب موثّق",
    )
    incoming = make_participant(9)

    case, new = special_case_service.substitute(
        actor=manager,
        withdrawn_enrollment=withdrawn,
        incoming_participant=incoming,
        occurred_on=TERM_START,
        case_code="SC-SUB",
        new_enrollment_code="EN-SUB",
    )

    assert case.case_type == SpecialCaseType.SUBSTITUTION
    assert new.cohort_id == withdrawn.cohort_id
    assert not ChargeLine.objects.filter(
        enrollment=new, charge_type=ChargeType.REGISTRATION
    ).exists()
    assert "BR-070" in case.financial_effect_ar


def test_a_substitution_needs_an_actually_vacated_seat(enrolled, make_participant, manager) -> None:
    """An active participant's seat is not free for someone else to take."""
    active = enrolled()
    with pytest.raises(ValidationError):
        special_case_service.substitute(
            actor=manager,
            withdrawn_enrollment=active,
            incoming_participant=make_participant(8),
            occurred_on=TERM_START,
            case_code="SC-SUB2",
            new_enrollment_code="EN-SUB2",
        )


# ---------------------------------------------------------------------------
# T-134 — BR-071, the credit balance
# ---------------------------------------------------------------------------
def test_a_credit_balance_becomes_a_case_someone_owns(enrolled, manager) -> None:
    """
    T-134 (the part Sprint 6 owns) — a negative number nobody is watching is
    how money owed back gets forgotten.

    ⏳ DEFERRED to Sprint 7: that clearance REFUSES to close while the balance
    is non-zero (BR-071 · BR-073), and the return itself. Neither is asserted
    here, because neither is built.
    """
    enrollment = enrolled(paid="320.000")  # 270 owed, 50 overpaid
    state = get_account_state(enrollment)
    assert state.balance == Decimal("-50.000")
    assert state.centre_owes is True

    case = special_case_service.record_credit_balance(
        actor=manager, enrollment=enrollment, occurred_on=TERM_START, code="SC-CR"
    )
    assert case is not None
    assert case.case_type == SpecialCaseType.CREDIT_BALANCE
    assert "50.000" in case.detail_ar
    assert "BR-071" in case.financial_effect_ar
    assert "Sprint 7" in case.financial_effect_ar


def test_no_credit_case_is_invented_when_nothing_is_owed(enrolled, manager) -> None:
    """A settled account produces no row at all, rather than a zero one."""
    enrollment = enrolled(paid="270.000")
    assert (
        special_case_service.record_credit_balance(
            actor=manager, enrollment=enrollment, occurred_on=TERM_START, code="SC-NONE"
        )
        is None
    )
    assert not SpecialCase.objects.exists()


def test_the_sign_convention_is_the_one_the_demo_contradicted(enrolled) -> None:
    """
    🐞 BR-071 — the demo used the same negative number for both meanings.

    Fixed once in ``get_account_state`` and asserted here so the convention
    cannot drift: POSITIVE = the participant owes us; NEGATIVE = we owe them.
    """
    owing = enrolled(paid="100.000", index=1, code="CO-OWE")
    credited = enrolled(paid="320.000", index=2, code="CO-CRED")

    assert get_account_state(owing).participant_owes is True
    assert get_account_state(owing).balance > 0
    assert get_account_state(credited).centre_owes is True
    assert get_account_state(credited).balance < 0


# ---------------------------------------------------------------------------
# Scope — Sprint 7 is not here
# ---------------------------------------------------------------------------
def test_the_clearance_half_of_these_rules_now_exists() -> None:
    """
    ✅ **Sprint 7 arrived**, so this guard inverted rather than being deleted.

    Sprint 6 could only record that a dismissal leaves a debt and that a
    credit balance is owed; the rules SAY those block clearance, and there was
    no clearance to block. There is now — and `test_clearance.py` asserts the
    blocking directly, in both directions.
    """
    from django.apps import apps as django_apps

    names = {model.__name__ for model in django_apps.get_models()}
    for arrived in ("Clearance", "ClearanceStep", "Certificate"):
        assert arrived in names, f"{arrived} is Sprint 7 scope and should exist"
