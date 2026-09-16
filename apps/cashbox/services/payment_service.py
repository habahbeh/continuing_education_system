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


def check_minimum_first_payment(*, enrollment: Any, amount: Decimal, as_of: date) -> None:
    """
    BR-020 — the first payment on a diploma must clear a minimum.

    Read from an effective-dated setting, with a per-programme override
    (Q-15), because the rule is "registration plus the first subject" and the
    centre may price that differently per diploma.
    """
    from apps.cashbox.models import PaymentAllocation, ReceiptStatus
    from apps.catalog.models import ProgramType

    program = enrollment.cohort.program
    if program.program_type != ProgramType.DIPLOMA:
        return

    already_paid = PaymentAllocation.objects.filter(
        enrollment=enrollment, receipt__status=ReceiptStatus.ISSUED
    ).exists()
    if already_paid:
        return  # The rule governs the FIRST payment only.

    minimum = program.minimum_first_payment_override
    if minimum is None:
        minimum = Decimal(str(get_setting(MIN_FIRST_PAYMENT_KEY, as_of=as_of, default="400")))

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

    external_required = get_setting(EXTERNAL_REF_REQUIRED_KEY, as_of=received_on, default=False)
    if external_required and not external_receipt_ref.strip():
        raise ValidationError(
            "رقم السند الخارجي إلزامي بالإعداد الحالي (Q-03) — external_receipt_required."
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
    if void_record.requested_by_id == getattr(actor, "pk", None):
        # Also a database constraint; refused here for a readable message.
        raise PermissionDenied("لا يجوز اعتماد إلغاء طلبتَه بنفسك (D-18 · Δ-06).")

    receipt = void_record.receipt

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

        write_audit(
            action="VOID",
            entity_type=RECEIPT_ENTITY,
            entity_id=str(receipt.pk),
            reference=receipt.internal_receipt_number,
            summary_ar=f"اعتماد إلغاء سند — {len(originals)} تخصيصاً عكسياً",
            actor=actor,
            changes={"reversed_allocations": len(originals), "amount": str(receipt.amount)},
            request=request,
        )
    return void_record


def list_receipts(
    *,
    actor: Any,
    query: str = "",
    on_date: date | None = None,
    cashier_id: int | None = None,
    request: Any = None,
) -> list[dict[str, Any]]:
    """Receipts as rows for the payments screen (PERMISSIONS row 15)."""
    from apps.cashbox.models import Receipt

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)

    queryset = Receipt.objects.select_related("participant", "payment_method", "cashier")
    if query:
        queryset = queryset.filter(internal_receipt_number__icontains=query) | queryset.filter(
            participant__name_ar__icontains=query
        )
    if on_date is not None:
        queryset = queryset.filter(received_on=on_date)
    if cashier_id is not None:
        queryset = queryset.filter(cashier_id=cashier_id)

    return [
        {
            "internal_receipt_number": r.internal_receipt_number,
            "external_receipt_ref": r.external_receipt_ref,
            "participant_name": r.participant.name_ar,
            "participant_number": r.participant.participant_number,
            "received_on": r.received_on,
            "amount": r.amount,
            "payment_method": r.payment_method.name_ar,
            "cashier": person_name(r.cashier),
            "status": r.status,
            "status_display": r.get_status_display(),
            "breakdown_text_ar": r.breakdown_text_ar,
            "is_closed": r.daily_closing_id is not None,
        }
        for r in queryset.order_by("-received_on", "-id")
    ]


def get_receipt(*, actor: Any, number: str, request: Any = None) -> dict[str, Any]:
    """One receipt with its allocations and any void request against it."""
    from apps.cashbox.models import PaymentAllocation, Receipt, ReceiptVoid

    policy.require(actor, Screen.PAYMENTS, Action.VIEW, request=request)

    receipt = Receipt.objects.select_related("participant", "payment_method", "cashier").get(
        internal_receipt_number=number
    )

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
        .select_related("enrollment", "charge_line")
        .order_by("id")
    ]

    void = ReceiptVoid.objects.filter(receipt=receipt).order_by("-id").first()
    return {
        "internal_receipt_number": receipt.internal_receipt_number,
        "external_receipt_ref": receipt.external_receipt_ref,
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
    }


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
    return ReceiptVoid.objects.filter(receipt=receipt, approved_by__isnull=True).latest("id")


__all__ = [
    "allocate",
    "approve_void",
    "check_minimum_first_payment",
    "get_receipt",
    "list_receipts",
    "method_by_code",
    "payment_method_choices",
    "receipt_instance",
    "request_void",
    "take_payment",
    "void_instance",
]
