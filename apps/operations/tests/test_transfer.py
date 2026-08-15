"""
Transfers — T-119 … T-129, T-241 … T-243, BR-060 … BR-066.

Two demo defects are covered here, and one open question:

🐞 **C-12** — the demo granted the category waiver SILENTLY on a dropdown
pick, with no approver and no reason. That is a control gap, not a
convenience.

🐞 **Double entitlement** — the demo left a transferred-out enrolment carrying
its collected amount, so a partner could earn on the same money twice. Moving
the allocations makes the old enrolment collect nothing, which fixes it
structurally rather than by an exception rule.

⚠️ **Q-11 is NOT client-settled.** "Entitlement follows the allocations" is
the documented assumption; if the target cohort belongs to another partner,
that partner now earns the money.
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
from apps.core.models import AuditEvent
from apps.operations.models import (
    EnrollmentStatus,
    Transfer,
    TransferReason,
    TransferStatus,
)
from apps.operations.services import enrollment_service, transfer_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


@pytest.fixture
def ready_transfer(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    documented_attendance,
    registrar,
):
    """
    A paid-up participant on CMA, ready to move to another business course.

    CMA is 800 + 15 registration, paid in full; JCPA is 600 and charges no
    registration fee. Both sit in CAT-BUS, so the category rule passes and the
    target is deliberately CHEAPER — the credit path gets exercised unless a
    test asks otherwise.
    """

    def _setup(
        to_program: str = "SC-JCPA",
        lectures: int = 2,
        paid: str = "815.000",
        suffix: str = "1",
    ):
        source = make_cohort("SC-CMA", code=f"CO-FROM-{suffix}")
        target = make_cohort(to_program, code=f"CO-TO-{suffix}")
        approve_cohort(source, course_number=f"MOHE-FROM-{suffix}")
        approve_cohort(target, course_number=f"MOHE-TO-{suffix}")

        enrollment = make_enrollment(source, index=int(suffix))
        charge_and_pay(enrollment, amount=paid)
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        documented_attendance(enrollment, lectures)
        enrollment.refresh_from_db()
        return enrollment, target

    return _setup


def _request(registrar, enrollment, target, **kwargs):
    return transfer_service.request_transfer(
        actor=registrar,
        from_enrollment=enrollment,
        to_cohort=target,
        reason=kwargs.pop("reason", TransferReason.PARTICIPANT_REQUEST),
        requested_on=TERM_START,
        code=kwargs.pop("code", "TR-001"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# T-119 … T-123 — the rule engine (WORKFLOWS §5.3)
# ---------------------------------------------------------------------------
def test_a_diploma_on_either_side_is_refused(
    make_cohort, approve_cohort, make_enrollment, documented_attendance, registrar
) -> None:
    """T-119 / BR-060 — transfers are a short-course facility."""
    source = make_cohort("SC-NET", code="CO-S")
    target = make_cohort("DIP-ID", code="CO-D")
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")

    enrollment = make_enrollment(source)
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    with pytest.raises(transfer_service.TransferRuleError, match="القصيرة فقط"):
        _request(registrar, enrollment, target)


def test_a_different_category_is_refused_on_a_participant_request(
    make_cohort, approve_cohort, make_enrollment, documented_attendance, registrar
) -> None:
    """
    T-120 / BR-061 — IT to business is not the course they signed up for.

    Only a centre cancellation opens the waiver, and only with an approver.
    """
    source = make_cohort("SC-NET", code="CO-IT")  # CAT-IT
    target = make_cohort("SC-CMA", code="CO-BUS")  # CAT-BUS
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")

    enrollment = make_enrollment(source)
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    with pytest.raises(transfer_service.TransferRuleError, match="مجال"):
        _request(registrar, enrollment, target)


def test_the_lecture_deadline_has_no_waiver(ready_transfer, registrar) -> None:
    """
    T-121 / BR-062 — 3 passes, 4 fails, 5 fails, and no exception exists.

    Unlike the category rule, nobody can set this one aside: past the third
    lecture the participant has consumed a real part of the course.
    """
    for lectures, allowed in ((3, True), (4, False), (5, False)):
        enrollment, target = ready_transfer(lectures=lectures, suffix=str(lectures))
        if allowed:
            transfer = _request(registrar, enrollment, target, code=f"TR-{lectures}")
            assert transfer.status == TransferStatus.PENDING_MANAGER
        else:
            with pytest.raises(transfer_service.TransferRuleError, match="مهلة"):
                _request(registrar, enrollment, target, code=f"TR-{lectures}")


def test_the_deadline_cannot_be_judged_on_an_undocumented_counter(
    make_cohort, approve_cohort, make_enrollment, registrar
) -> None:
    """
    T-241 / Q-06 — the answer is "cannot evaluate", not "refused".

    A refusal would be a decision made on a number nobody stands behind.
    """
    source = make_cohort("SC-CMA", code="CO-U1")
    target = make_cohort("SC-JCPA", code="CO-U2")
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")
    enrollment = make_enrollment(source)

    with pytest.raises(transfer_service.AttendanceNotDocumentedError, match="غير موثَّق"):
        _request(registrar, enrollment, target)


def test_the_lecture_limit_is_a_setting(ready_transfer, registrar) -> None:
    """BR-062 is configurable — the number belongs to the centre."""
    from apps.core.models import EffectiveSetting, SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    enrollment, target = ready_transfer(lectures=5)
    with pytest.raises(transfer_service.TransferRuleError):
        _request(registrar, enrollment, target)

    close_setting(transfer_service.LECTURE_LIMIT_KEY, effective_to=date(2026, 1, 1))
    set_setting(
        transfer_service.LECTURE_LIMIT_KEY,
        6,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2026, 1, 2),
        note="اختبار",
    )
    assert EffectiveSetting.objects.filter(key=transfer_service.LECTURE_LIMIT_KEY).count() == 2

    transfer = _request(registrar, enrollment, target, code="TR-RAISED")
    assert transfer.lectures_attended_at_request == 5


def test_a_silent_category_waiver_is_refused_by_the_database(ready_transfer, registrar) -> None:
    """
    T-122 / C-12 — the demo's control gap, now unrepresentable.

    Asserted against the DATABASE: the service refuses it too, but the demo's
    failure was precisely that the service did not.
    """
    enrollment, target = ready_transfer()
    with pytest.raises(IntegrityError), transaction.atomic():
        Transfer.objects.create(
            code="TR-SILENT",
            from_enrollment=enrollment,
            to_cohort=target,
            requested_on=TERM_START,
            requested_by=registrar,
            reason=TransferReason.CENTER_CANCELLATION,
            category_waiver_granted=True,  # no approver, no reason
        )


def test_a_waiver_on_a_participant_request_is_refused_even_when_documented(
    ready_transfer, registrar, manager
) -> None:
    """
    C-12's third clause — the waiver exists for centre cancellations only.

    A participant who simply changed their mind does not get a different
    course category because a manager signed something.
    """
    enrollment, target = ready_transfer()
    with pytest.raises(IntegrityError), transaction.atomic():
        Transfer.objects.create(
            code="TR-WRONGREASON",
            from_enrollment=enrollment,
            to_cohort=target,
            requested_on=TERM_START,
            requested_by=registrar,
            reason=TransferReason.PARTICIPANT_REQUEST,
            category_waiver_granted=True,
            category_waiver_by=manager,
            category_waiver_reason_ar="لأنه طلب",
        )


def test_a_documented_waiver_on_a_centre_cancellation_is_allowed_and_audited(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    documented_attendance,
    registrar,
    manager,
) -> None:
    """T-123 / BR-065 — allowed, with the approver and the reason recorded."""
    source = make_cohort("SC-NET", code="CO-CX1")
    target = make_cohort("SC-CMA", code="CO-CX2")
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")
    enrollment = make_enrollment(source)
    charge_and_pay(enrollment)
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    transfer = _request(
        registrar,
        enrollment,
        target,
        reason=TransferReason.CENTER_CANCELLATION,
        waiver_by=manager,
        waiver_reason_ar="أُلغيت الدورة لعدم اكتمال العدد",
    )

    assert transfer.category_waiver_granted is True
    assert transfer.same_category is False
    assert transfer.category_waiver_by_id == manager.pk

    event = AuditEvent.objects.filter(summary_ar__contains="استثناء قيد المجال").first()
    assert event is not None and event.changes is not None
    assert event.changes["event"] == "WAIVE"
    assert event.changes["rule"] == "BR-065"


def test_the_rule_check_is_frozen_as_evidence(ready_transfer, registrar) -> None:
    """
    T-243 — the catalogue moves; the justification must not move with it.

    A course recategorised next term must not retroactively justify — or
    condemn — a transfer that was already decided.
    """
    enrollment, target = ready_transfer(lectures=2)
    transfer = _request(registrar, enrollment, target)

    assert transfer.lectures_attended_at_request == 2
    assert transfer.attendance_record_ref_at_request == "كشف المدرب 2026/09"
    assert transfer.same_category is True

    # The counter later changes; the frozen evidence does not.
    enrollment.lectures_attended = 9
    enrollment.save(update_fields=["lectures_attended"])
    transfer.refresh_from_db()
    assert transfer.lectures_attended_at_request == 2


def test_an_unapproved_target_cohort_is_refused(
    make_cohort, approve_cohort, make_enrollment, documented_attendance, registrar
) -> None:
    """BR-013 applies to where they are going, not only where they came from."""
    source = make_cohort("SC-CMA", code="CO-OK")
    target = make_cohort("SC-JCPA", code="CO-NOPE")  # never submitted
    approve_cohort(source, course_number="M1")
    enrollment = make_enrollment(source)
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    with pytest.raises(transfer_service.TransferRuleError, match="الوزارة"):
        _request(registrar, enrollment, target)


# ---------------------------------------------------------------------------
# T-129 — BR-066, two-step approval
# ---------------------------------------------------------------------------
def test_execution_before_the_managers_recommendation_is_refused(
    ready_transfer, registrar, finance
) -> None:
    """T-129 / BR-066 — finance settles what the manager recommended."""
    enrollment, target = ready_transfer()
    transfer = _request(registrar, enrollment, target)

    with pytest.raises(ValidationError):
        transfer_service.execute_transfer(
            actor=finance, transfer=transfer, executed_on=TERM_START, new_code="EN-X"
        )


def test_the_two_step_path_runs_registrar_manager_finance(
    ready_transfer, registrar, manager, finance
) -> None:
    enrollment, target = ready_transfer()
    transfer = _request(registrar, enrollment, target)
    assert transfer.status == TransferStatus.PENDING_MANAGER

    transfer_service.manager_recommend(actor=manager, transfer=transfer)
    assert transfer.status == TransferStatus.PENDING_FINANCE

    transfer_service.execute_transfer(
        actor=finance, transfer=transfer, executed_on=TERM_START, new_code="EN-NEW"
    )
    assert transfer.status == TransferStatus.EXECUTED
    assert transfer.manager_approved_by_id == manager.pk
    assert transfer.finance_settled_by_id == finance.pk


def test_a_rejection_carries_its_reason(ready_transfer, registrar, manager) -> None:
    enrollment, target = ready_transfer()
    transfer = _request(registrar, enrollment, target)

    with pytest.raises(ValidationError):
        transfer_service.reject_transfer(actor=manager, transfer=transfer, reason_ar="   ")

    transfer_service.reject_transfer(
        actor=manager, transfer=transfer, reason_ar="الدفعة الهدف ممتلئة"
    )
    assert transfer.status == TransferStatus.REJECTED


def test_an_executed_transfer_without_a_target_is_unrepresentable(
    ready_transfer, registrar
) -> None:
    enrollment, target = ready_transfer()
    transfer = _request(registrar, enrollment, target)

    with pytest.raises(IntegrityError), transaction.atomic():
        Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.EXECUTED)


# ---------------------------------------------------------------------------
# T-124 … T-128 — execution (WORKFLOWS §5.4)
# ---------------------------------------------------------------------------
def _execute(registrar, manager, finance, enrollment, target, code="TR-EX"):
    transfer = _request(registrar, enrollment, target, code=code)
    transfer_service.manager_recommend(actor=manager, transfer=transfer)
    transfer_service.execute_transfer(
        actor=finance, transfer=transfer, executed_on=TERM_START, new_code="EN-NEW"
    )
    return transfer


def test_the_registration_fee_moves_and_is_not_charged_again(
    ready_transfer, registrar, manager, finance
) -> None:
    """T-124 / BR-063 — the centre's admin work was already paid for once."""
    enrollment, target = ready_transfer()
    transfer = _execute(registrar, manager, finance, enrollment, target)
    new = transfer.to_enrollment

    assert transfer.registration_fee_transferred == Decimal("15.000")
    registration_lines = ChargeLine.objects.filter(
        enrollment=new, charge_type=ChargeType.REGISTRATION, voided=False
    )
    assert registration_lines.count() == 1
    carried = registration_lines.first()
    assert carried is not None
    assert carried.net_amount == Decimal("15.000")


def test_a_dearer_course_raises_an_explicit_difference_line(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    documented_attendance,
    registrar,
    manager,
    finance,
) -> None:
    """
    T-125 / BR-064 — quality auditing 200 → CMA 800 leaves 600 to pay.

    The gap is its own line rather than an unexplained balance: "you paid 200
    for quality auditing, CMA is 800, here is the 600" is a statement somebody
    can act on.
    """
    source = make_cohort("SC-QA", code="CO-CHEAP")
    target = make_cohort("SC-CMA", code="CO-DEAR")
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")
    enrollment = make_enrollment(source)
    charge_and_pay(enrollment, amount="200.000")  # no registration fee on SC-QA
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    transfer = _execute(registrar, manager, finance, enrollment, target, code="TR-UP")
    new = transfer.to_enrollment

    assert transfer.fee_difference == Decimal("600.000")
    difference = ChargeLine.objects.get(enrollment=new, charge_type=ChargeType.TRANSFER_DIFFERENCE)
    assert difference.net_amount == Decimal("600.000")

    state = get_account_state(new)
    assert state.total_due == Decimal("800.000")  # 200 carried + 600 difference
    assert state.total_paid == Decimal("200.000")
    assert state.balance == Decimal("600.000")  # the participant owes it


def test_a_cheaper_course_leaves_a_credit_rather_than_a_charge(
    ready_transfer, registrar, manager, finance
) -> None:
    """
    T-126 / BR-064 — CMA 800 → JCPA 600 leaves 200 owed to the participant.

    A charge line cannot be negative (``billing_charge_amounts_not_negative``),
    so the surplus surfaces as a credit balance instead. ⏳ RETURNING it is
    BR-071 at clearance — **Sprint 7**, and deliberately not built here.
    """
    enrollment, target = ready_transfer()
    transfer = _execute(registrar, manager, finance, enrollment, target)
    new = transfer.to_enrollment

    assert transfer.fee_difference == Decimal("-200.000")
    assert not ChargeLine.objects.filter(
        enrollment=new, charge_type=ChargeType.TRANSFER_DIFFERENCE
    ).exists()

    state = get_account_state(new)
    assert state.total_due == Decimal("615.000")  # 15 registration + 600 tuition
    assert state.total_paid == Decimal("815.000")
    assert state.balance == Decimal("-200.000")
    assert state.centre_owes is True


def test_the_old_enrolment_ends_with_nothing_collected(
    ready_transfer, registrar, manager, finance
) -> None:
    """
    T-127 / ADR-006 — this is the structural answer to Q-11.

    The demo left the money on the old enrolment, so a partner could earn on
    it AND on the new one — the same dinar shared twice. After the move the
    old enrolment has collected nothing, so it earns nothing with no exception
    rule anywhere.
    """
    enrollment, target = ready_transfer()
    _execute(registrar, manager, finance, enrollment, target)

    enrollment.refresh_from_db()
    state = get_account_state(enrollment)
    assert state.total_paid == Decimal("0.000")
    assert state.total_due == Decimal("0.000")
    assert state.balance == Decimal("0.000")
    assert enrollment.status == EnrollmentStatus.TRANSFERRED_OUT


def test_the_money_moves_by_reversal_and_nothing_is_deleted(
    ready_transfer, registrar, manager, finance
) -> None:
    """
    Every allocation row that existed still exists.

    The receipt's own invariant survives too: its parts still sum to its
    whole, to the fils, which is what makes the pair a history rather than a
    number that changed by itself.
    """
    enrollment, target = ready_transfer()
    before = set(PaymentAllocation.objects.values_list("pk", flat=True))

    _execute(registrar, manager, finance, enrollment, target)

    after = set(PaymentAllocation.objects.values_list("pk", flat=True))
    assert before <= after, "an allocation was deleted"

    for receipt in Receipt.objects.filter(status=ReceiptStatus.ISSUED):
        total = sum((a.amount for a in receipt.allocations.all()), Decimal("0.000"))
        assert total == receipt.amount

    assert PaymentAllocation.objects.filter(enrollment=enrollment, amount__lt=0).exists(), (
        "the reversal rows are missing"
    )


def test_the_old_charge_lines_are_voided_with_a_reason(
    ready_transfer, registrar, manager, finance
) -> None:
    """A void names who did it and why — never a delete (D-19)."""
    enrollment, target = ready_transfer()
    transfer = _execute(registrar, manager, finance, enrollment, target)

    for line in ChargeLine.objects.filter(enrollment=enrollment):
        assert line.voided is True
        assert line.voided_by_id == finance.pk
        assert transfer.code in line.void_reason_ar


def test_execution_is_atomic(ready_transfer, registrar, manager, finance, monkeypatch) -> None:
    """
    T-128 — money on one enrolment and a seat on another is the worst outcome.

    The failure is injected at the very last step, after the new enrolment,
    the lines and the allocations have all been written.
    """
    enrollment, target = ready_transfer()
    transfer = _request(registrar, enrollment, target)
    transfer_service.manager_recommend(actor=manager, transfer=transfer)

    def _boom(**kwargs):
        raise RuntimeError("قاعدة البيانات سقطت في منتصف التنفيذ")

    monkeypatch.setattr(transfer_service, "_recover_partner_share_if_claimed", _boom)

    with pytest.raises(RuntimeError):
        transfer_service.execute_transfer(
            actor=finance, transfer=transfer, executed_on=TERM_START, new_code="EN-HALF"
        )

    from apps.operations.models import Enrollment

    assert not Enrollment.objects.filter(code="EN-HALF").exists()
    enrollment.refresh_from_db()
    transfer.refresh_from_db()
    assert enrollment.status != EnrollmentStatus.TRANSFERRED_OUT
    assert transfer.status == TransferStatus.PENDING_FINANCE
    assert get_account_state(enrollment).total_paid == Decimal("815.000")


def test_the_execution_is_audited_with_what_moved(
    ready_transfer, registrar, manager, finance
) -> None:
    enrollment, target = ready_transfer()
    transfer = _execute(registrar, manager, finance, enrollment, target)

    event = AuditEvent.objects.filter(
        entity_type="operations.Transfer",
        reference=transfer.code,
        summary_ar__contains="تنفيذ النقل",
    ).first()
    assert event is not None and event.changes is not None
    assert event.changes["event"] == "EXECUTE"
    assert event.changes["allocations_moved"] == "815.000"
    assert event.changes["registration_transferred"] == "15.000"


# ---------------------------------------------------------------------------
# WORKFLOWS §5.4 F3 — a transfer after the partner was already paid
# ---------------------------------------------------------------------------
def test_a_transfer_after_an_approved_claim_raises_an_obligation(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    documented_attendance,
    registrar,
    manager,
    finance,
) -> None:
    """
    WORKFLOWS §5.4 F3 — the partner was paid for money that has now left.

    The approved claim is FROZEN (BR-051) and is not recomputed. The
    correction is an obligation recovered from the next claim, which is the
    mechanism every other partner recovery already uses (BR-036).
    """
    from apps.partners.models import (
        Agreement,
        AgreementStatus,
        CalculationModel,
        Partner,
        PartnerType,
    )
    from apps.settlements.models import ObligationType, PartnerObligation
    from apps.settlements.services import claim_service

    partner = Partner.objects.create(
        code="PRT-TR", name_ar="شريك النقل", partner_type=PartnerType.COMPANY
    )
    agreement = Agreement.objects.create(
        agreement_number="2026/TR",
        partner=partner,
        title_ar="اتفاقية",
        signed_on=date(2026, 8, 1),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 8, 31),
        calculation_model=CalculationModel.PERCENT,
        percent_rate=Decimal("50.0000"),
        status=AgreementStatus.ACTIVE,
    )
    source = make_cohort("SC-CMA", code="CO-PAID", agreement=agreement)
    target = make_cohort("SC-JCPA", code="CO-AFTER")
    approve_cohort(source, course_number="M1")
    approve_cohort(target, course_number="M2")

    enrollment = make_enrollment(source)
    charge_and_pay(enrollment, amount="815.000")
    documented_attendance(enrollment, 1)
    enrollment.refresh_from_db()

    claim = claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=source,
        period_from=TERM_START,
        period_to=date(2026, 9, 30),
        trigger_type="END_OF_COURSE",
    )
    claim_service.approve_claim(actor=manager, claim=claim)
    frozen_share = claim.partner_share
    frozen_hash = claim.content_hash

    _execute(registrar, manager, finance, enrollment, target, code="TR-AFTER")

    obligation = PartnerObligation.objects.get(obligation_type=ObligationType.WITHDRAWAL_RETURN)
    assert obligation.amount == Decimal("400.000")  # 50% of the 800 collected
    assert obligation.restricted_to_agreement_id == agreement.pk
    assert "TR-AFTER" in obligation.code

    # The approved claim is untouched — no recomputation, seal intact.
    claim.refresh_from_db()
    assert claim.partner_share == frozen_share
    assert claim.content_hash == frozen_hash
    assert claim_service.verify_hash(claim) is True


def test_no_obligation_when_nothing_was_claimed_yet(
    ready_transfer, registrar, manager, finance
) -> None:
    """The ordinary case: no claim, nothing to recover, no row invented."""
    from apps.settlements.models import PartnerObligation

    enrollment, target = ready_transfer()
    _execute(registrar, manager, finance, enrollment, target)
    assert not PartnerObligation.objects.exists()
