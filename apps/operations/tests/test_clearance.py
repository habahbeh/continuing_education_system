"""
Clearance — T-135 … T-140, BR-072 … BR-074, C-09 · C-29 · C-30.

The financial step carries the sprint. Three separate controls sit on it, and
each was absent from the demo in a different way:

* the balance must be **exactly zero, in either direction** (C-09, BR-073) —
  the demo used one negative number for "owes us" and "we owe them", so
  CLR-002 and CLR-003 were literally indistinguishable;
* it takes **two signatures** (C-29, BR-074) — the demo had a free-text
  "المدير المالي" string in `CLR-001.step2.by`;
* from **two different people** (C-30, D-30) — the demo had no such notion.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.services.account_service import get_account_state
from apps.core.models import AuditEvent
from apps.operations.models import (
    Clearance,
    ClearanceCaseType,
    ClearanceStatus,
    ClearanceStep,
)
from apps.operations.services import clearance_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fim.clr", password="probe-password-1234", role=Role.FINANCE_MANAGER
    )


@pytest.fixture
def settled(make_cohort, approve_cohort, make_enrollment, charge_and_pay):
    """
    An enrolment on SC-NET, paid to whatever the test wants.

    270 due: 20 registration + 250 tuition. Paying exactly that leaves a zero
    balance, which is the only state step 2 will close on.
    """

    def _make(paid: str = "270.000", index: int = 1, code: str = "CO-CLR"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount=paid)
        return enrollment

    return _make


def _open(actor, enrollment, code="CLR-001"):
    return clearance_service.open_clearance(
        actor=actor,
        enrollment=enrollment,
        case_type=ClearanceCaseType.GRADUATION,
        opened_on=TERM_START,
        code=code,
    )


def _through_step_1(manager, clearance):
    return clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------
def test_opening_lays_out_all_three_steps(settled, manager) -> None:
    """
    The form is a printed checklist (CS Fm 7.18 Rev A).

    All three rows exist from the start so a participant at the counter can be
    told what is still outstanding, not just what has been done.
    """
    clearance = _open(manager, settled())

    steps = list(clearance.steps.order_by("step_number"))
    assert [s.step_number for s in steps] == [1, 2, 3]
    assert steps[1].name_ar == "المالية — التحقق المالي"
    assert clearance.status == ClearanceStatus.OPEN


def test_one_live_clearance_per_enrolment(settled, manager) -> None:
    """Two completed clearances would authorise two certificates for one course."""
    enrollment = settled()
    _open(manager, enrollment)

    with pytest.raises(ValidationError, match="قائمة سلفاً"):
        _open(manager, enrollment, code="CLR-DUP")

    with pytest.raises(IntegrityError), transaction.atomic():
        Clearance.objects.create(
            code="CLR-RAW",
            participant=enrollment.participant,
            enrollment=enrollment,
            case_type=ClearanceCaseType.WITHDRAWAL,
            opened_on=TERM_START,
            opened_by=manager,
            active_key=1,
        )


def test_a_cancelled_clearance_frees_the_enrolment(settled, manager) -> None:
    """
    MySQL's non-colliding NULLs again — deliberately.

    A clearance opened in error must not block the real one forever.
    """
    enrollment = settled()
    first = _open(manager, enrollment)
    clearance_service.cancel_clearance(
        actor=manager, clearance=first, reason_ar="فُتحت على تسجيل خاطئ"
    )

    second = _open(manager, enrollment, code="CLR-002")
    assert second.pk is not None
    assert Clearance.objects.filter(enrollment=enrollment).count() == 2


def test_a_cancellation_without_a_reason_is_refused(settled, manager) -> None:
    clearance = _open(manager, settled())
    with pytest.raises(ValidationError):
        clearance_service.cancel_clearance(actor=manager, clearance=clearance, reason_ar=" ")

    with pytest.raises(IntegrityError), transaction.atomic():
        Clearance.objects.filter(pk=clearance.pk).update(
            status=ClearanceStatus.CANCELLED, active_key=None
        )


# ---------------------------------------------------------------------------
# T-135 — BR-072, the sequence
# ---------------------------------------------------------------------------
def test_steps_cannot_be_taken_out_of_order(settled, manager, finance) -> None:
    """T-135 — step 3 before 2, and step 2 before 1, both refused."""
    clearance = _open(manager, settled())

    with pytest.raises(clearance_service.StepOutOfOrderError, match="الخطوة 3"):
        clearance_service.complete_handover_step(actor=manager, clearance=clearance)

    with pytest.raises(clearance_service.StepOutOfOrderError, match="الخطوة 2"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)


def test_an_outstanding_custody_item_keeps_step_1_open(settled, manager) -> None:
    """WORKFLOWS §6.7 — this is the correct outcome, not a nuisance."""
    clearance = _open(manager, settled())

    with pytest.raises(ValidationError, match="بطاقة المواصلات"):
        clearance_service.complete_custody_step(
            actor=manager,
            clearance=clearance,
            custody_items=[
                {"name_ar": "هوية المركز", "returned": True},
                {"name_ar": "بطاقة المواصلات", "returned": False},
            ],
        )
    assert clearance.steps.get(step_number=1).is_done is False


# ---------------------------------------------------------------------------
# T-136 · T-137 · T-138 — C-09, zero in either direction
# ---------------------------------------------------------------------------
def test_a_debt_blocks_the_financial_step(settled, manager, finance) -> None:
    """T-136 — 75 owed, and the clearance is parked with its reason."""
    enrollment = settled(paid="195.000")  # 270 due
    clearance = _open(manager, enrollment)
    _through_step_1(manager, clearance)

    with pytest.raises(clearance_service.ClearanceBlockedError, match="عليه ذمة"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    clearance.refresh_from_db()
    assert clearance.status == ClearanceStatus.BLOCKED
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="BR-073").exists()


def test_a_credit_blocks_the_financial_step_just_as_firmly(settled, manager, finance) -> None:
    """
    T-137 — the centre owing the participant is not "close enough to settled".

    🐞 The demo could not tell this state from a debt: CLR-002 (−40, the
    university owes) and CLR-003 (−75, described as a debt) carried the same
    sign for opposite meanings.
    """
    enrollment = settled(paid="310.000")  # 40 overpaid
    clearance = _open(manager, enrollment)
    _through_step_1(manager, clearance)

    with pytest.raises(clearance_service.ClearanceBlockedError, match="رصيد دائن"):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)


def test_the_two_directions_are_worded_differently(settled) -> None:
    """T-138 — the message tells the counter clerk what to actually do."""
    owed = clearance_service._balance_message(Decimal("75.000"))
    credited = clearance_service._balance_message(Decimal("-50.000"))

    assert "عليه ذمة" in owed and "75.000" in owed
    assert "رصيد دائن" in credited and "50.000" in credited
    assert owed != credited


def test_the_database_refuses_a_closed_financial_step_with_a_balance(
    settled, manager, finance, finance_manager
) -> None:
    """
    T-136 / C-09 as a CONSTRAINT — what holds when a service is bypassed.

    Both directions, because a credit balance is exactly as blocking.
    """
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    step = clearance.steps.get(step_number=2)

    for balance in (Decimal("75.000"), Decimal("-40.000")):
        with pytest.raises(IntegrityError), transaction.atomic():
            ClearanceStep.objects.filter(pk=step.pk).update(
                balance_at_check=balance,
                is_done=True,
                certified_by=finance,
                second_certified_by=finance_manager,
            )


# ---------------------------------------------------------------------------
# T-139 — BR-074 · C-29 · C-30, two signatures from two people
# ---------------------------------------------------------------------------
def test_one_signature_does_not_close_the_financial_step(settled, manager, finance) -> None:
    """T-139 — the accountant certifies; the step stays open."""
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)

    step = clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    assert step.certified_by_id == finance.pk
    assert step.is_done is False

    with pytest.raises(clearance_service.StepOutOfOrderError):
        clearance_service.complete_handover_step(actor=manager, clearance=clearance)


def test_the_database_refuses_a_single_signature_close(settled, manager, finance) -> None:
    """C-29 — the service check is the message; this is the guarantee."""
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    step = clearance.steps.get(step_number=2)

    with pytest.raises(IntegrityError), transaction.atomic():
        ClearanceStep.objects.filter(pk=step.pk).update(is_done=True)


def test_the_second_signature_belongs_to_the_finance_manager_alone(
    settled, manager, finance
) -> None:
    """
    BR-074 / Q-14 — enforced in the POLICY layer, and audited when refused.

    Not a constraint on purpose: a role can be changed on a user afterwards,
    and a database constraint has to stay true for rows written years ago.
    """
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    with pytest.raises(clearance_service.SecondCertifierRoleError):
        clearance_service.second_certify_finance_step(actor=manager, clearance=clearance)

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="BR-074").exists()


def test_the_first_certifier_cannot_also_be_the_second(settled, manager, finance_manager) -> None:
    """
    D-30 — two signatures from one person is a single control in a costume.

    The finance manager certifies first here, which passes the ROLE check, so
    only the person check can catch it. That is why both exist.
    """
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance_manager, clearance=clearance)

    # Sprint 2A's shared `assert_different_actor` raises ValidationError: the
    # role was right, the record is wrong. The role refusal above is the one
    # that raises PermissionDenied.
    with pytest.raises(ValidationError, match="D-30"):
        clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)


def test_the_database_refuses_the_same_person_twice(settled, manager, finance) -> None:
    """C-30 — held at the last line too."""
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    step = clearance.steps.get(step_number=2)

    with pytest.raises(IntegrityError), transaction.atomic():
        ClearanceStep.objects.filter(pk=step.pk).update(
            second_certified_by=finance, second_certified_at=timezone.now()
        )


def test_the_second_signature_cannot_come_first(settled, manager, finance_manager) -> None:
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)

    with pytest.raises(clearance_service.StepOutOfOrderError):
        clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)


def test_both_certifications_are_audited_separately(
    settled, manager, finance, finance_manager
) -> None:
    """WORKFLOWS §6.6 — one audit row per certification, not one per step."""
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)

    events = AuditEvent.objects.filter(
        entity_type="operations.ClearanceStep", reference=clearance.code
    ).order_by("id")
    orders = [e.changes.get("order") for e in events if e.changes]
    assert "first" in orders and "second" in orders
    assert events.filter(actor=finance).exists()
    assert events.filter(actor=finance_manager).exists()


# ---------------------------------------------------------------------------
# T-140 — the re-check WORKFLOWS §6.7 demands
# ---------------------------------------------------------------------------
def test_a_balance_that_moves_after_certification_stops_the_close(
    settled, manager, finance, finance_manager, registrar
) -> None:
    """
    T-140 — the failure named in WORKFLOWS §6.7, and the reason for the recheck.

    Step 2 was certified honestly on a zero balance. A charge raised
    afterwards makes it non-zero, and closing on the number captured earlier
    would complete a clearance over a live debt — after which BR-075 would let
    a certificate out to someone who owes money.
    """
    from apps.billing.services import charge_service

    enrollment = settled()
    clearance = _open(manager, enrollment)
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
    clearance_service.complete_handover_step(actor=manager, clearance=clearance)

    charge_service.create_charge_line(
        actor=registrar,
        enrollment=enrollment,
        charge_type="EXTRA_FEE",
        description_ar="رسم إعادة مادة",
        net_amount=Decimal("75.000"),
        charged_on=TERM_START,
    )

    with pytest.raises(clearance_service.ClearanceBlockedError, match="تغيّر الرصيد"):
        clearance_service.close_clearance(actor=manager, clearance=clearance)

    clearance.refresh_from_db()
    assert clearance.status == ClearanceStatus.BLOCKED


def test_a_clean_clearance_closes(settled, manager, finance, finance_manager) -> None:
    enrollment = settled()
    clearance = _open(manager, enrollment)
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
    clearance_service.complete_handover_step(actor=manager, clearance=clearance)
    clearance_service.close_clearance(actor=manager, clearance=clearance)

    clearance.refresh_from_db()
    assert clearance.status == ClearanceStatus.COMPLETED
    assert clearance.completed_at is not None
    assert get_account_state(enrollment).balance == Decimal("0.000")


def test_a_completed_clearance_needs_all_three_steps(
    settled, manager, finance, finance_manager
) -> None:
    clearance = _open(manager, settled())
    _through_step_1(manager, clearance)
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)

    with pytest.raises(clearance_service.StepOutOfOrderError, match=r"\[3\]"):
        clearance_service.close_clearance(actor=manager, clearance=clearance)


def test_the_database_refuses_a_completed_clearance_with_no_timestamp(settled, manager) -> None:
    clearance = _open(manager, settled())
    with pytest.raises(IntegrityError), transaction.atomic():
        Clearance.objects.filter(pk=clearance.pk).update(status=ClearanceStatus.COMPLETED)
