"""
Taking a payment, and voiding one (BR-020 … BR-025).

The allocation algorithm (BR-022) is the piece the demo lacked entirely: it
kept a single ``paid`` figure and re-derived which fee it covered on every
read, from an assumed ordering. That produced right answers for as long as the
ordering never changed, and made a partner's entitlement unverifiable.

Here the split is STORED. ``Σ allocations == receipt.amount`` exactly, to the
fils, for every receipt — asserted as a property test, because a rounding drift
of one fils across thousands of receipts is a reconciliation nobody can close.

Voiding writes REVERSING allocations rather than editing or deleting (BR-025).
The original receipt keeps its amount and its number forever; the pair reads as
a history. The demo zeroed the original, which erased the fact that money had
once been taken.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import ALLOCATION_ORDER, ChargeLine
from apps.billing.services.account_service import ZERO, outstanding_for_line
from apps.core.display import person_name, text_of
from apps.core.services import period_service
from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.core.services.settings_service import get_setting
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

RECEIPT_ENTITY = "cashbox.Receipt"
RECEIPT_SCOPE = "receipt"

MIN_FIRST_PAYMENT_KEY = "diploma_minimum_first_payment"
EXTERNAL_REF_REQUIRED_KEY = "external_receipt_required"


def _receipt_partition(received_on: date) -> str:
    """Gapless per YEAR (Q-03) — a new year restarts at 1."""
    return str(received_on.year)


def is_first_payment(enrollment: Any) -> bool:
    """No issued receipt has yet been allocated to this enrolment."""
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    return not PaymentAllocation.objects.filter(
        enrollment=enrollment, receipt__status=ReceiptStatus.ISSUED
    ).exists()


def first_payment_minimum(*, enrollment: Any, as_of: date) -> Decimal | None:
    """
    BR-020's floor for THIS enrolment on THIS date, or ``None`` when the rule
    does not apply — a course, or a diploma that already took a payment.

    The one place the rule is read: :func:`check_minimum_first_payment`
    enforces it and the till repeats it, both through this function, so the
    figure the screen quotes is the figure the save refuses below (Q-15:
    programme override before the effective-dated setting).
    """
    from apps.catalog.models import ProgramType

    program = enrollment.cohort.program
    if program.program_type != ProgramType.DIPLOMA:
        return None
    if not is_first_payment(enrollment):
        return None  # The rule governs the FIRST payment only.

    minimum = program.minimum_first_payment_override
    if minimum is None:
        configured = get_setting(MIN_FIRST_PAYMENT_KEY, as_of=as_of, default="400")
        minimum = Decimal(str(configured))
    return minimum


def default_breakdown_text(*, enrollment: Any) -> str:
    """
    The statement a receipt carries when the cashier types none.

    Named from the enrolment so the printed receipt reads on its own: what
    was paid for, on which cohort, and whether it opened the account.
    """
    cohort = enrollment.cohort
    program = cohort.program
    what = "رسوم تسجيل ودفعة أولى" if is_first_payment(enrollment) else "دفعة على حساب"
    return f"{what} — {program.name_ar} / {cohort.name_ar or cohort.code}"


def check_minimum_first_payment(*, enrollment: Any, amount: Decimal, as_of: date) -> None:
    """
    BR-020 — the first payment on a diploma must clear a minimum.

    Read from an effective-dated setting, with a per-programme override
    (Q-15), because the rule is "registration plus the first subject" and the
    centre may price that differently per diploma.
    """
    minimum = first_payment_minimum(enrollment=enrollment, as_of=as_of)
    if minimum is None:
        return

    if amount < minimum:
        raise ValidationError(
            f"الدفعة الأولى للدبلوم يجب ألّا تقل عن {minimum} ديناراً "
            f"(المبلغ المُدخَل {amount}) — BR-020."
        )


def allocate(*, receipt: Any, enrollment: Any, actor: Any, request: Any = None) -> list[Any]:
    """
    BR-022 — spread a receipt across the outstanding lines, in order.

    Registration, consumables, deposit, tuition, extra fees, transfer
    difference. Whatever is left over becomes an unallocated credit rather
    than being forced onto a line (BR-023) — over-allocating a line would
    quietly manufacture a debt that was already settled.
    """
    from apps.cashbox.models import AllocationType, PaymentAllocation

    remaining = receipt.amount
    allocations: list[PaymentAllocation] = []

    lines = list(
        ChargeLine.objects.filter(enrollment=enrollment, voided=False).order_by("charged_on", "id")
    )
    lines.sort(key=lambda line: ALLOCATION_ORDER.index(line.charge_type))

    for line in lines:
        if remaining <= ZERO:
            break
        outstanding = outstanding_for_line(line)
        if outstanding <= ZERO:
            continue
        portion = min(remaining, outstanding)
        allocations.append(
            PaymentAllocation.objects.create(
                receipt=receipt,
                charge_line=line,
                enrollment=enrollment,
                amount=portion,
                allocation_type=AllocationType.AUTOMATIC,
            )
        )
        remaining -= portion

    if remaining > ZERO:
        # BR-023 — a credit balance is a real allocation with no line, so the
        # invariant Σ allocations == receipt.amount still holds exactly.
        allocations.append(
            PaymentAllocation.objects.create(
                receipt=receipt,
                charge_line=None,
                enrollment=enrollment,
                amount=remaining,
                allocation_type=AllocationType.AUTOMATIC,
            )
        )

    total = sum((a.amount for a in allocations), ZERO)
    if total != receipt.amount:
        # Never expected; raised rather than logged because a receipt whose
        # parts do not equal its whole must not reach the database.
        raise ValidationError(
            f"خلل في التخصيص: مجموع التخصيصات {total} لا يساوي مبلغ السند {receipt.amount}."
        )
    return allocations


@transaction.atomic
def _issue(
    *,
    actor: Any,
    participant: Any,
    enrollment: Any,
    amount: Decimal,
    payment_method: Any,
    received_on: date,
    external_receipt_ref: str,
    breakdown_text_ar: str,
    request: Any,
) -> Any:
    from apps.cashbox.models import Receipt

    number = next_number(
        RECEIPT_SCOPE,
        _receipt_partition(received_on),
        prefix=f"R-{received_on.year}-",
        padding=5,
    )
    receipt = Receipt(
        internal_receipt_number=number,
        external_receipt_ref=external_receipt_ref,
        participant=participant,
        received_on=received_on,
        amount=amount,
        payment_method=payment_method,
        cashier=actor,
        breakdown_text_ar=breakdown_text_ar,
    )
    receipt.full_clean(exclude=["participant", "payment_method", "cashier"])
    receipt.save()

    allocations = allocate(receipt=receipt, enrollment=enrollment, actor=actor, request=request)

    write_audit(
        action="RECEIVE_CASH",
        entity_type=RECEIPT_ENTITY,
        entity_id=str(receipt.pk),
        reference=number,
        summary_ar=f"قبض {amount} — {participant.name_ar}",
        actor=actor,
        changes={
            "amount": str(amount),
            "method": payment_method.code,
            "allocations": [
                {
                    "charge_line": a.charge_line_id,
                    "type": a.charge_line.charge_type if a.charge_line else "CREDIT",
                    "amount": str(a.amount),
                }
                for a in allocations
            ],
        },
        request=request,
    )
    return receipt


def take_payment(
    *,
    actor: Any,
    enrollment: Any,
    amount: Decimal,
    payment_method: Any,
    received_on: date,
    external_receipt_ref: str = "",
    breakdown_text_ar: str = "",
    request: Any = None,
) -> Any:
    """
    Issue a receipt and allocate it.

    D-01 lives on the permission check: the centre manager approves and
    recommends but never takes cash, on any path (BR-081). The check runs
    BEFORE the transaction so a refusal survives it (BR-100).
    """
    policy.require(actor, Screen.PAYMENT_NEW, Action.CREATE, request=request)

    # D-23 (Sprint 8D-6) — a receipt dated into a closed month would change a
    # day's takings and a revenue report that have both been signed off.
    # Checked here among the guards, before any transaction, so the refusal's
    # own audit row survives the raise (BR-085).
    period_service.require_open(
        received_on,
        actor=actor,
        what_ar="قبض",
        entity_type="cashbox.Receipt",
        reference=getattr(enrollment, "code", ""),
        request=request,
    )

    if amount <= ZERO:
        raise ValidationError("مبلغ السند يجب أن يكون أكبر من صفر.")

    check_minimum_first_payment(enrollment=enrollment, amount=amount, as_of=received_on)

    if external_ref_required(as_of=received_on) and not external_receipt_ref.strip():
        raise ValidationError(
            "لا يصدر سند بلا رقم سند الدائرة المالية: القبض هناك والمركز يسجّل وصله "
            "(§5.2 · Q-03 — external_receipt_required)."
        )

    ensure_sequence(RECEIPT_SCOPE, _receipt_partition(received_on), padding=5)

    return _issue(
        actor=actor,
        participant=enrollment.participant,
        enrollment=enrollment,
        amount=amount,
        payment_method=payment_method,
        received_on=received_on,
        external_receipt_ref=external_receipt_ref.strip(),
        breakdown_text_ar=breakdown_text_ar,
        request=request,
    )


def external_ref_required(*, as_of: date) -> bool:
    """§5.2 / Q-03 — must a receipt carry the finance department's voucher number?"""
    return bool(get_setting(EXTERNAL_REF_REQUIRED_KEY, as_of=as_of, default=False))


def voucher_addable(receipt: Any) -> bool:
    """
    May the finance-department voucher number still be written on this receipt?

    Only while it is EMPTY (a number once written is never changed — the
    correction is a void and a new receipt, BR-025), on an issued receipt,
    and before the day it belongs to is approved (BR-026).
    """
    from apps.cashbox.models import ClosingStatus, DailyClosing, ReceiptStatus

    if receipt.external_receipt_ref or receipt.status != ReceiptStatus.ISSUED:
        return False
    return not DailyClosing.objects.filter(
        cashier_id=receipt.cashier_id,
        closing_date=receipt.received_on,
        status=ClosingStatus.RECONCILED,
    ).exists()


def record_external_ref(
    *, actor: Any, receipt: Any, external_receipt_ref: str, request: Any = None
) -> Any:
    """
    Complete a receipt with the finance department's voucher number (§5.2).

    The till often issues the receipt before the paper voucher is in hand;
    the closing then lists the receipt as «بلا وصل» until this is done. It is
    a completion, not an edit: refused once a number exists, once the receipt
    is voided, or once its day is approved. A day still open or pending is
    re-counted so the closing's «بلا وصل» figure follows the receipt, and a
    pending day that is now clean is promoted to OPEN.
    """
    from apps.cashbox.models import ClosingStatus, DailyClosing, Receipt

    # Whoever may record a payment (§3.4/17: the finance officer and the
    # cashier) may complete its voucher number; nobody else.
    policy.require(actor, Screen.PAYMENT_NEW, Action.CREATE, request=request)

    ref = external_receipt_ref.strip()
    if not ref:
        raise ValidationError("اكتب رقم سند الدائرة المالية.")
    if receipt.external_receipt_ref:
        raise ValidationError(
            "هذا السند يحمل رقم سند من الدائرة المالية بالفعل ولا يُغيَّر؛ "
            "التصحيح بإلغاء السند وإصدار سند جديد (BR-025)."
        )
    if receipt.status != "ISSUED":
        raise ValidationError("السند ملغى؛ لا يُستكمل رقم وصل على سند ملغى.")
    if Receipt.objects.filter(external_ref_key=ref).exclude(pk=receipt.pk).exists():
        other = Receipt.objects.filter(external_ref_key=ref).values_list(
            "internal_receipt_number", flat=True
        )[0]
        raise ValidationError(f"رقم سند الدائرة المالية {ref} مسجَّل على السند {other}.")

    with transaction.atomic():
        closing = (
            DailyClosing.objects.select_for_update()
            .filter(cashier_id=receipt.cashier_id, closing_date=receipt.received_on)
            .first()
        )
        if closing is not None and closing.status == ClosingStatus.RECONCILED:
            raise ValidationError(
                f"يوم هذا السند مُقفل ومعتمد ({closing.code}) ولا يُعدَّل بعده (BR-026)."
            )

        receipt.external_receipt_ref = ref
        receipt.save(update_fields=["external_receipt_ref", "external_ref_key"])

        write_audit(
            action="UPDATE",
            entity_type=RECEIPT_ENTITY,
            entity_id=str(receipt.pk),
            reference=receipt.internal_receipt_number,
            summary_ar=f"استكمال رقم سند الدائرة المالية — {ref}",
            actor=actor,
            changes={"external_receipt_ref": ref},
            request=request,
        )

        if closing is not None:
            from apps.cashbox.services import closing_service

            summary = closing_service.day_summary(
                cashier=receipt.cashier, closing_date=receipt.received_on
            )
            closing.unvouched_count = summary["unvouched_count"]
            if closing.unvouched_count == 0 and closing.variance == ZERO:
                closing.status = ClosingStatus.OPEN
            closing.save(update_fields=["unvouched_count", "status"])
    return receipt


def request_void(*, actor: Any, receipt: Any, reason_ar: str, request: Any = None) -> Any:
    """
    Δ-06 — the cashier asks; finance decides. Not the same person.

    The permission split falls straight out of the matrix (§3.4 row 16) rather
    than being invented: the cashier holds ``V C P`` on payments and the
    finance officer ``V A X P``. So REQUESTING a void is a create — which the
    cashier can do and the finance officer cannot — and APPROVING it is the
    void action ``X``, which only the finance officer holds. The centre
    manager holds ``V P`` and so touches neither, consistent with never
    handling cash at all (D-01).
    """
    from apps.cashbox.models import ReceiptVoid

    policy.require(actor, Screen.PAYMENTS, Action.CREATE, request=request)

    if not reason_ar.strip():
        raise ValidationError("سبب الإلغاء إلزامي (BR-025).")

    with transaction.atomic():
        existing = ReceiptVoid.objects.filter(receipt=receipt).first()
        if existing is not None and existing.approved_by_id is not None:
            raise ValidationError("هذا السند ملغى أصلاً.")
        if existing is not None and existing.rejected_by_id is None:
            raise ValidationError("على هذا السند طلب إلغاء معلّق بالفعل.")
        if existing is not None:
            # A rejected request may be raised again; the earlier decision
            # stays in the audit trail, the row carries the new ask.
            existing.requested_by = actor
            existing.requested_at = timezone.now()
            existing.reason_ar = reason_ar.strip()
            existing.rejected_by = None
            existing.rejected_at = None
            existing.rejection_reason_ar = ""
            existing.save()
            record = existing
        else:
            record = ReceiptVoid.objects.create(
                receipt=receipt, requested_by=actor, reason_ar=reason_ar.strip()
            )
        write_audit(
            action="VOID",
            entity_type=RECEIPT_ENTITY,
            entity_id=str(receipt.pk),
            reference=receipt.internal_receipt_number,
            summary_ar=f"طلب إلغاء سند — {reason_ar.strip()}",
            actor=actor,
            request=request,
        )
    return record


def approve_void(*, actor: Any, void_record: Any, request: Any = None) -> Any:
    """
    Approve a void by writing REVERSING allocations (BR-025).

    Nothing is edited and nothing is deleted. The receipt keeps its amount and
    its number; a mirror-image set of allocations cancels its effect. Both
    halves stay visible, which is what makes the history readable — and is why
    ``PaymentAllocation.amount`` permits negatives but never zero.
    """
    from apps.cashbox.models import AllocationType, PaymentAllocation, ReceiptStatus

    # `X` on payments — held by the finance officer alone (§3.4 row 16).
    policy.require(actor, Screen.PAYMENTS, Action.VOID, request=request)

    # D-23 (Sprint 8D-6), on the ORIGINAL receipt's date rather than today's.
    # A void has no date of its own — the reversing allocations hang off the
    # receipt being cancelled — so voiding a September receipt in November
    # removes September's money from September's report. The period this
    # movement changes is the one that has to be open, and 8D-5's rule of
    # "check the correction's own date" gives the wrong answer here for the
    # reason that rule exists: it is the touched month that matters.
    period_service.require_open(
        void_record.receipt.received_on,
        actor=actor,
        what_ar="إلغاء سند",
        entity_type="cashbox.ReceiptVoid",
        reference=void_record.receipt.internal_receipt_number,
        request=request,
    )

    if void_record.approved_by_id is not None:
        raise ValidationError("هذا الطلب معتمد سلفاً.")
    if void_record.rejected_by_id is not None:
        raise ValidationError("هذا الطلب مرفوض؛ يُطلب الإلغاء من جديد إن لزم.")
    if void_record.requested_by_id == getattr(actor, "pk", None):
        # Also a database constraint; refused here for a readable message.
        raise PermissionDenied("لا يجوز اعتماد إلغاء طلبتَه بنفسك (D-18 · Δ-06).")

    blockers = void_blockers(void_record)
    if blockers:
        raise ValidationError(blockers)

    receipt = void_record.receipt
    consequences = void_consequences(void_record)

    with transaction.atomic():
        originals = list(
            PaymentAllocation.objects.filter(receipt=receipt, reversed_by__isnull=True)
        )
        for original in originals:
            reversal = PaymentAllocation.objects.create(
                receipt=receipt,
                charge_line=original.charge_line,
                enrollment=original.enrollment,
                amount=-original.amount,
                allocation_type=AllocationType.MANUAL,
                allocated_by=actor,
                manual_reason_ar=f"عكس تخصيص لإلغاء السند — {void_record.reason_ar}"[:255],
            )
            original.reversed_by = reversal
            original.save(update_fields=["reversed_by"])

        receipt.status = ReceiptStatus.VOIDED
        receipt.save(update_fields=["status"])

        void_record.approved_by = actor
        void_record.approved_at = timezone.now()
        void_record.save(update_fields=["approved_by", "approved_at"])

        # The cascade: an enrolment that stood on this money alone goes back
        # to waiting for it — recorded as a status change with its reason,
        # never silently. Whoever approved the void is the actor.
        from apps.operations.services import enrollment_service

        reverted = []
        for item in consequences["reverting"]:
            enrollment = item["enrollment"]
            enrollment_service.record_status_change(
                actor=actor,
                enrollment=enrollment,
                to_status=item["to_status"],
                reason_ar=(
                    f"أُلغي السند {receipt.internal_receipt_number} الذي اعتُمد التسجيل عليه — "
                    f"{void_record.reason_ar}"
                )[:255],
                reference=receipt.internal_receipt_number,
            )
            reverted.append(enrollment.code)

        write_audit(
            action="VOID",
            entity_type=RECEIPT_ENTITY,
            entity_id=str(receipt.pk),
            reference=receipt.internal_receipt_number,
            summary_ar=f"اعتماد إلغاء سند — {len(originals)} تخصيصاً عكسياً",
            actor=actor,
            changes={
                "reversed_allocations": len(originals),
                "amount": str(receipt.amount),
                "reverted_enrollments": reverted,
            },
            request=request,
        )
    return void_record


def reject_void(*, actor: Any, void_record: Any, reason_ar: str, request: Any = None) -> Any:
    """
    Turn a void request down. Same hands as approving (``X``), never the
    requester (D-18), always with a reason. The receipt stays issued.
    """
    policy.require(actor, Screen.PAYMENTS, Action.VOID, request=request)

    if void_record.approved_by_id is not None:
        raise ValidationError("هذا الطلب معتمد سلفاً ولا يُرفض.")
    if void_record.rejected_by_id is not None:
        raise ValidationError("هذا الطلب مرفوض سلفاً.")
    if void_record.requested_by_id == getattr(actor, "pk", None):
        raise PermissionDenied("لا يجوز البتّ في طلب إلغاء قدّمتَه بنفسك (D-18 · Δ-06).")
    if not reason_ar.strip():
        raise ValidationError("سبب الرفض إلزامي.")

    receipt = void_record.receipt
    with transaction.atomic():
        void_record.rejected_by = actor
        void_record.rejected_at = timezone.now()
        void_record.rejection_reason_ar = reason_ar.strip()
        void_record.save(update_fields=["rejected_by", "rejected_at", "rejection_reason_ar"])
        write_audit(
            action="VOID",
            entity_type=RECEIPT_ENTITY,
            entity_id=str(receipt.pk),
            reference=receipt.internal_receipt_number,
            summary_ar=f"رفض طلب إلغاء سند — {reason_ar.strip()}",
            actor=actor,
            request=request,
        )
    return void_record


#: Enrolment states that were reached on the strength of a payment and fall
#: back to waiting for one when that payment is voided. Final states and the
#: pre-payment states are left alone.
_REVERTIBLE_ON_VOID = ("PENDING_APPROVAL", "ACTIVE", "PAYMENT_OVERDUE")


def _settled_enrollments(receipt: Any) -> list[Any]:
    from apps.cashbox.models import PaymentAllocation

    seen: dict[int, Any] = {}
    for allocation in (
        PaymentAllocation.objects.filter(receipt=receipt, enrollment__isnull=False)
        .select_related("enrollment__cohort__program")
        .order_by("id")
    ):
        seen.setdefault(allocation.enrollment_id, allocation.enrollment)
    return list(seen.values())


def void_blockers(void_record: Any) -> list[str]:
    """
    Why this void may NOT be approved — each a sentence the screen shows
    before the button and the service raises after it.

    A void says «this receipt was a mistake». Once the day's cash has been
    counted, or a clearance or certificate was built on the money, it is no
    longer a mistake to undo but money to hand back: the instrument is then
    a refund (BR-033), not a void.
    """
    from apps.cashbox.models import DailyClosing
    from apps.operations.models import ClearanceStatus

    receipt = void_record.receipt
    reasons: list[str] = []

    closing = DailyClosing.objects.filter(
        cashier=receipt.cashier, closing_date=receipt.received_on
    ).first()
    if closing is not None:
        reasons.append(
            f"يوم السند ({receipt.received_on:%Y/%m/%d}) دخل في إقفال يومي ({closing.code}) "
            "وعُدّ ماله؛ لا يُلغى سند بعد الإقفال — يُعالَج بالاسترداد الرسمي أو بسند تصحيحي."
        )

    for enrollment in _settled_enrollments(receipt):
        for clearance in enrollment.clearances.exclude(status=ClearanceStatus.CANCELLED):
            finance_certified = clearance.steps.filter(
                step_number=2, certified_by__isnull=False
            ).exists()
            if finance_certified or clearance.status == ClearanceStatus.COMPLETED:
                reasons.append(
                    f"على التسجيل {enrollment.code} براءة ذمة ({clearance.code}) صودق مالياً "
                    "على رصيدها بهذا السند؛ لا يُلغى سند بُنيت عليه براءة — يُعالَج بالاسترداد."
                )
                break
        if (
            getattr(enrollment, "certificates", None) is not None
            and enrollment.certificates.exists()
        ):
            reasons.append(
                f"للتسجيل {enrollment.code} شهادة صادرة؛ لا يُلغى سند سبق شهادة — يُعالَج بالاسترداد."
            )
    return reasons


def void_consequences(void_record: Any) -> dict[str, Any]:
    """
    What approving this void will do beyond the reversal, for the dialog
    and for the cascade: enrolments that would be left with nothing paid
    against a live charge go back to «بانتظار الدفع».
    """
    from apps.billing.services.account_service import get_account_state

    receipt = void_record.receipt
    reverting: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for enrollment in _settled_enrollments(receipt):
        state = get_account_state(enrollment)
        paid_by_this = sum(
            (
                a.amount
                for a in receipt.allocations.filter(enrollment=enrollment, reversed_by__isnull=True)
            ),
            ZERO,
        )
        paid_after = state.total_paid - paid_by_this
        if (
            enrollment.status in _REVERTIBLE_ON_VOID
            and paid_after <= ZERO
            and state.total_due > ZERO
        ):
            reverting.append(
                {
                    "enrollment": enrollment,
                    "code": enrollment.code,
                    "from_status": enrollment.get_status_display(),
                    "to_status": "PENDING_FINANCE",
                    "balance_after": state.balance + paid_by_this,
                }
            )
        else:
            remaining.append(
                {
                    "code": enrollment.code,
                    "status": enrollment.get_status_display(),
                    "paid_after": paid_after,
                    "balance_after": state.balance + paid_by_this,
                }
            )
    return {"reverting": reverting, "remaining": remaining}


def pending_voids_by_enrollment(enrollments: list[Any]) -> dict[int, str]:
    """{enrollment pk: receipt number} for every enrolment with a void
    request still undecided on one of its issued receipts."""
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus

    ids = [e.pk for e in enrollments]
    if not ids:
        return {}
    rows = (
        PaymentAllocation.objects.filter(
            enrollment_id__in=ids,
            receipt__status=ReceiptStatus.ISSUED,
            receipt__void_record__isnull=False,
            receipt__void_record__approved_by__isnull=True,
            receipt__void_record__rejected_by__isnull=True,
        )
        .values_list("enrollment_id", "receipt__internal_receipt_number")
        .distinct()
    )
    return dict(rows)


def pending_void_for(enrollment: Any) -> str:
    """The receipt number under a pending void on this enrolment, or ''."""
    return pending_voids_by_enrollment([enrollment]).get(enrollment.pk, "")


def require_no_pending_void(enrollment: Any, *, what_ar: str) -> None:
    """
    A step that stands on money paid may not be taken while a request to
    take that money back is undecided — approve or reject it first.
    """
    number = pending_void_for(enrollment)
    if number:
        raise ValidationError(
            f"لا يمكن {what_ar} والسند {number} عليه طلب إلغاء معلّق؛ "
            "يُبتّ في الطلب (اعتماداً أو رفضاً) أولاً."
        )


#: The register's status filter — each a question the reader asks, not a
#: column value: «ملغى» is a status, «إلغاء معلّق» and «لم يدخل إقفالاً»
#: are conditions on an issued receipt.
RECEIPT_FILTERS = ("issued", "voided", "void_pending", "unclosed")


def list_receipts(
    *,
    actor: Any,
    query: str = "",
    on_date: date | None = None,
    cashier_id: int | None = None,
    participant_number: str = "",
    since: date | None = None,
    until: date | None = None,
    status: str = "",
    method: str = "",
    request: Any = None,
) -> list[dict[str, Any]]:
    """
    Receipts as rows for the payments screen (PERMISSIONS row 15).

    ``since``/``until`` bound the date — the dashboard's week chart in one
    read instead of one read per day, and the register's range. ``status``
    is one of :data:`RECEIPT_FILTERS`; ``method`` a payment-method code.

    ``participant_number`` narrows to one participant exactly — the
    participant file reads its receipts through here rather than scanning
    the register. ``query`` matches a name, a receipt number, a participant
    number, a phone or the finance department's own reference.
    """
    from django.db.models import Q

    from apps.cashbox.models import Receipt, ReceiptStatus, ReceiptVoid

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)

    queryset = Receipt.objects.select_related("participant", "payment_method", "cashier")
    if query:
        queryset = queryset.filter(
            Q(internal_receipt_number__icontains=query)
            | Q(external_receipt_ref__icontains=query)
            | Q(participant__name_ar__icontains=query)
            | Q(participant__name_en__icontains=query)
            | Q(participant__participant_number__startswith=query)
            | Q(participant__phone__icontains=query)
        )
    if on_date is not None:
        queryset = queryset.filter(received_on=on_date)
    if cashier_id is not None:
        queryset = queryset.filter(cashier_id=cashier_id)
    if participant_number:
        queryset = queryset.filter(participant__participant_number=participant_number)
    if since is not None:
        queryset = queryset.filter(received_on__gte=since)
    if until is not None:
        queryset = queryset.filter(received_on__lte=until)
    if method:
        queryset = queryset.filter(payment_method__code=method)
    if status == "issued":
        queryset = queryset.filter(status=ReceiptStatus.ISSUED)
    elif status == "voided":
        queryset = queryset.exclude(status=ReceiptStatus.ISSUED)
    elif status == "unclosed":
        queryset = queryset.filter(status=ReceiptStatus.ISSUED, daily_closing__isnull=True)
    elif status == "void_pending":
        queryset = queryset.filter(
            status=ReceiptStatus.ISSUED,
            void_record__approved_by__isnull=True,
            void_record__rejected_by__isnull=True,
            void_record__isnull=False,
        )

    receipts = list(queryset.order_by("-received_on", "-id"))
    pending = set(
        ReceiptVoid.objects.filter(
            receipt__in=receipts, approved_by__isnull=True, rejected_by__isnull=True
        ).values_list("receipt_id", flat=True)
    )
    return [
        {
            "internal_receipt_number": r.internal_receipt_number,
            "external_receipt_ref": r.external_receipt_ref,
            "participant_name": r.participant.name_ar,
            "participant_number": r.participant.participant_number,
            "received_on": r.received_on,
            "amount": r.amount,
            "payment_method": r.payment_method.name_ar,
            "payment_method_code": r.payment_method.code,
            "cashier": person_name(r.cashier),
            "status": r.status,
            "status_display": r.get_status_display(),
            "breakdown_text_ar": r.breakdown_text_ar,
            "is_closed": r.daily_closing_id is not None,
            "void_pending": r.pk in pending,
        }
        for r in receipts
    ]


def receipts_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    What the register's cards and its footer say about a set of rows: the
    money actually taken (issued receipts only), the counts by condition,
    and the take per payment method — computed once, from the rows shown.
    """
    issued = [r for r in rows if r["status"] == "ISSUED"]
    by_method: dict[str, dict[str, Any]] = {}
    for r in issued:
        slot = by_method.setdefault(
            r["payment_method_code"],
            {
                "code": r["payment_method_code"],
                "name": r["payment_method"],
                "amount": ZERO,
                "count": 0,
            },
        )
        slot["amount"] += r["amount"]
        slot["count"] += 1
    return {
        "total": sum((r["amount"] for r in issued), ZERO),
        "count": len(rows),
        "issued": len(issued),
        "voided": len(rows) - len(issued),
        "void_pending": sum(1 for r in issued if r["void_pending"]),
        "unclosed": sum(1 for r in issued if not r["is_closed"]),
        "by_method": sorted(by_method.values(), key=lambda m: -m["amount"]),
    }


def get_receipt(*, actor: Any, number: str, request: Any = None) -> dict[str, Any]:
    """One receipt with its allocations and any void request against it."""
    from apps.cashbox.models import PaymentAllocation, Receipt, ReceiptVoid
    from apps.cashbox.services.amount_words import amount_in_words_ar

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)

    receipt = Receipt.objects.select_related(
        "participant", "payment_method", "cashier", "daily_closing"
    ).get(internal_receipt_number=number)

    allocations = [
        {
            "enrollment_code": text_of(a.enrollment, "code"),
            # Read-only, for the screen: a receipt against a registration that
            # was since cancelled stays a valid receipt (it is real money), but
            # the reader must not take the line it paid for as a live debt.
            "enrollment_cancelled": text_of(a.enrollment, "status") == "CANCELLED",
            "against": text_of(a.charge_line, "description_ar", default="رصيد غير مخصَّص"),
            "amount": a.amount,
            "allocation_type": a.allocation_type,
            "manual_reason_ar": a.manual_reason_ar,
        }
        for a in PaymentAllocation.objects.filter(receipt=receipt)
        .select_related("enrollment__cohort__program", "charge_line")
        .order_by("id")
    ]
    # The enrolment the receipt settles — one per receipt in practice; named
    # once at the head of the screen and of the paper, not on every line.
    settled = next(
        (
            a.enrollment
            for a in PaymentAllocation.objects.filter(receipt=receipt)
            .select_related("enrollment__cohort__program")
            .exclude(enrollment__isnull=True)
            .order_by("id")[:1]
        ),
        None,
    )

    void = ReceiptVoid.objects.filter(receipt=receipt).order_by("-id").first()
    return {
        "internal_receipt_number": receipt.internal_receipt_number,
        "amount_words": amount_in_words_ar(receipt.amount),
        "enrollment_code": settled.code if settled else "",
        "cohort_name": (settled.cohort.name_ar or settled.cohort.code) if settled else "",
        "program_name": settled.cohort.program.name_ar if settled else "",
        "is_voided": receipt.status != "ISSUED",
        "external_receipt_ref": receipt.external_receipt_ref,
        "voucher_addable": voucher_addable(receipt),
        "participant_name": receipt.participant.name_ar,
        "participant_number": receipt.participant.participant_number,
        "received_on": receipt.received_on,
        "amount": receipt.amount,
        "payment_method": receipt.payment_method.name_ar,
        "cashier": person_name(receipt.cashier),
        "status": receipt.status,
        "status_display": receipt.get_status_display(),
        "breakdown_text_ar": receipt.breakdown_text_ar,
        "allocations": allocations,
        "has_cancelled_enrollment": any(a["enrollment_cancelled"] for a in allocations),
        "void_id": void.pk if void else None,
        "void_reason_ar": void.reason_ar if void else "",
        "void_is_approved": bool(void and void.approved_by_id),
        "void_requested_by_id": void.requested_by_id if void else None,
        "void_requested_by": person_name(void.requested_by) if void else "",
        "void_is_rejected": bool(void and void.rejected_by_id),
        "void_is_pending": bool(void and void.is_pending),
        "void_rejected_by": person_name(void.rejected_by) if void and void.rejected_by_id else "",
        "void_approved_by": person_name(void.approved_by) if void and void.approved_by_id else "",
        "void_approved_at": void.approved_at if void else None,
        "closing_code": receipt.daily_closing.code if receipt.daily_closing_id else "",
        "void_rejection_reason_ar": void.rejection_reason_ar if void else "",
        "void_blockers": void_blockers(void) if void and void.is_pending else [],
        "void_consequences": (
            void_consequences(void)
            if void and void.is_pending
            else {"reverting": [], "remaining": []}
        ),
    }


def receipt_document(*, actor: Any, number: str, request: Any = None) -> dict[str, Any]:
    """
    The printed receipt: the screen's receipt plus the letterhead. A voided
    receipt prints as voided, never hidden (BR-025). Same door as the screen.
    """
    from apps.core.services import document_settings

    receipt = get_receipt(actor=actor, number=number, request=request)
    return {**receipt, "chrome": document_settings.chrome(as_of=receipt["received_on"])}


def payment_method_choices() -> list[tuple[str, str]]:
    """(code, name) pairs for the payment form."""
    from apps.cashbox.models import PaymentMethod

    return [
        (m.code, m.name_ar) for m in PaymentMethod.objects.filter(is_active=True).order_by("code")
    ]


def method_by_code(code: str) -> Any:
    from apps.cashbox.models import PaymentMethod

    return PaymentMethod.objects.get(code=code)


def receipt_instance(*, actor: Any, number: str, request: Any = None) -> Any:
    """The Receipt object, for handing back into this module (A-05)."""
    from apps.cashbox.models import Receipt

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)
    return Receipt.objects.get(internal_receipt_number=number)


def void_instance(*, actor: Any, number: str, request: Any = None) -> Any:
    """The open void request on a receipt, for the approver."""
    from apps.cashbox.models import Receipt, ReceiptVoid

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)
    receipt = Receipt.objects.get(internal_receipt_number=number)
    return ReceiptVoid.objects.filter(
        receipt=receipt, approved_by__isnull=True, rejected_by__isnull=True
    ).latest("id")


__all__ = [
    "allocate",
    "approve_void",
    "check_minimum_first_payment",
    "default_breakdown_text",
    "external_ref_required",
    "first_payment_minimum",
    "get_receipt",
    "is_first_payment",
    "list_receipts",
    "method_by_code",
    "payment_method_choices",
    "pending_void_for",
    "pending_voids_by_enrollment",
    "receipt_document",
    "receipt_instance",
    "receipts_summary",
    "record_external_ref",
    "reject_void",
    "request_void",
    "require_no_pending_void",
    "take_payment",
    "void_blockers",
    "void_consequences",
    "void_instance",
    "voucher_addable",
]
