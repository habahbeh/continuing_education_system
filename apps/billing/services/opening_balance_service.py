"""
Opening balances — the reviewed bridge from archive to ledger (BR-094).

Sprint 8D-1 read twenty-five workbooks into tables that cannot touch money,
and proved it by counting every ledger row before and after. This module is
the single door in that wall, and everything about it is shaped by the fact
that it is the only one.

**Four hands** (D-24). Propose, review, approve, post. The reviewer is not the
proposer and the approver is neither — checked here in words the centre can
act on, and again by CHECK constraints so a service written next year cannot
route around it.

**One at a time** (D-25). ``propose_from_archive`` takes ONE archived
enrolment. There is no bulk variant, and the absence is the control rather
than an omission: two hundred balances approved in a batch is two hundred
balances nobody read.

**Decide, then act.** Every public function runs ``policy.require`` and its
guards OUTSIDE the transaction. A refusal writes DENIED_ATTEMPT and raises; if
that ran inside an atomic block the rollback would take the evidence with it,
which is the defect BR-085 and the durability tests exist to prevent.

**Only ``post`` touches the ledger.** Proposing, reviewing, approving and
rejecting create no ``ChargeLine``, no ``Receipt``, no allocation — a test
counts fourteen ledger tables across the whole workflow to prove it. And
``post`` writes exactly one row: a charge line marked
``is_partner_shareable=False``, so a 2022 debt collected in 2026 can never
appear in a partner's base. ``entitlement_service`` already filters on that
flag; nothing new had to be taught to respect it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import (
    ChargeLine,
    ChargeType,
    OpeningBalance,
    OpeningBalanceDirection,
    OpeningBalanceRefund,
    OpeningBalanceStatus,
)
from apps.core.display import person_name, text_of
from apps.core.services import period_service
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "billing.OpeningBalance"
ZERO = Decimal("0.000")


class OpeningBalanceStateError(Exception):
    """The balance is not in a state where this step makes sense."""


class SeparationOfDutiesError(Exception):
    """D-24 · BR-094 — the same hand tried to fill two of the four roles."""


class CreditNotPostableError(Exception):
    """Posting a credit would mean inventing a receipt. Out of scope, on purpose."""


class AlreadyPostedError(Exception):
    """The ledger row exists. A second post would collect the debt twice."""


class NoEnrollmentError(Exception):
    """A balance with nowhere to land cannot be approved."""


class NotACreditError(Exception):
    """The two credit outcomes belong to credits alone."""


class AlreadyResolvedError(Exception):
    """The credit has already been carried forward or declared refundable."""


class NotALaterRegistrationError(Exception):
    """Client decision 1 — a credit is carried forward, never backward."""


class NotRefundDueError(Exception):
    """Only a balance declared refundable can be paid out."""


class AlreadyRefundedError(Exception):
    """The cash already left. A second payout would pay it twice."""


class MissingPayoutDetailsError(Exception):
    """Cash leaving the centre carries a voucher number and a named payee."""


class NotReversibleError(Exception):
    """There is no live payout on this balance to reverse."""


# ---------------------------------------------------------------------------
# 1 · Propose
# ---------------------------------------------------------------------------
def propose_from_archive(
    *,
    actor: Any,
    historical_enrollment: Any,
    code: str,
    direction: str,
    amount: Decimal,
    as_of: date,
    description_ar: str,
    request: Any = None,
) -> OpeningBalance:
    """
    Turn ONE archived enrolment into ONE draft. Writes nothing to the ledger.

    The amount is passed in rather than computed from the archive row, and the
    difference matters. The sheets do carry «المبلغ المتبقي», but 146 of 163
    checked rows do not reconcile with their own subject columns and 21 go
    negative — deriving a debt from them automatically would launder a
    spreadsheet error into a demand for money. The proposer reads the row,
    decides what it means, and types the figure they are prepared to defend.
    The row is recorded beside it so the reviewer can check them against each
    other.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.CREATE, request=request)

    _validate_new(code=code, direction=direction, amount=amount, description_ar=description_ar)

    if OpeningBalance.objects.filter(source_enrollment=historical_enrollment).exists():
        raise ValidationError(
            "لهذا الصف التاريخي رصيد افتتاحي سلفاً — الازدواج هو ما يجعل الذمة تُحصَّل مرتين."
        )

    source_row = historical_enrollment.source_row
    return _create(
        actor=actor,
        code=code,
        direction=direction,
        amount=amount,
        as_of=as_of,
        description_ar=description_ar,
        historical_enrollment=historical_enrollment,
        workbook=historical_enrollment.batch.source_filename,
        sheet=source_row.sheet_name,
        row_number=source_row.source_row,
        legacy_number=historical_enrollment.participant.legacy_number,
        request=request,
    )


def propose_manually(
    *,
    actor: Any,
    code: str,
    direction: str,
    amount: Decimal,
    as_of: date,
    description_ar: str,
    legacy_number: str = "",
    request: Any = None,
) -> OpeningBalance:
    """
    A draft from a paper file the workbooks never held.

    The three delivered workbooks cover three programmes. The centre ran more
    than three, so a balance with no archive row behind it is an ordinary
    case, not a loophole — it goes through the same four hands.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.CREATE, request=request)
    _validate_new(code=code, direction=direction, amount=amount, description_ar=description_ar)
    return _create(
        actor=actor,
        code=code,
        direction=direction,
        amount=amount,
        as_of=as_of,
        description_ar=description_ar,
        historical_enrollment=None,
        workbook="",
        sheet="",
        row_number=None,
        legacy_number=legacy_number.strip()[:32],
        request=request,
    )


def _validate_new(*, code: str, direction: str, amount: Decimal, description_ar: str) -> None:
    if direction not in OpeningBalanceDirection.values:
        raise ValidationError(f"اتجاه رصيد غير معروف: {direction}")
    if amount is None or amount <= ZERO:
        raise ValidationError("مبلغ الرصيد الافتتاحي يجب أن يكون موجباً.")
    if not description_ar.strip():
        raise ValidationError("البيان إلزامي — الرصيد الذي لا يشرح نفسه لا يُراجَع.")
    if OpeningBalance.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الرصيد {code} مستعمل سلفاً.")


@transaction.atomic
def _create(
    *,
    actor: Any,
    code: str,
    direction: str,
    amount: Decimal,
    as_of: date,
    description_ar: str,
    historical_enrollment: Any,
    workbook: str,
    sheet: str,
    row_number: int | None,
    legacy_number: str,
    request: Any,
) -> OpeningBalance:
    """The write half — permission already decided by the caller (BR-085)."""
    balance = OpeningBalance.objects.create(
        code=code,
        source_enrollment=historical_enrollment,
        source_workbook=workbook[:255],
        source_sheet=sheet[:120],
        source_row=row_number,
        source_legacy_number=legacy_number,
        direction=direction,
        amount=amount,
        as_of=as_of,
        description_ar=description_ar.strip()[:255],
        status=OpeningBalanceStatus.DRAFT,
        created_by=actor,
    )
    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(balance.pk),
        reference=code,
        summary_ar=f"اقتراح رصيد افتتاحي {amount} — {balance.get_direction_display()}",
        actor=actor,
        changes={
            "direction": direction,
            "amount": str(amount),
            "as_of": as_of.isoformat(),
            "source_workbook": workbook,
            "source_sheet": sheet,
            "source_row": str(row_number) if row_number is not None else "",
            "legacy_number": legacy_number,
            "ledger_effect": "لا شيء — الاقتراح لا يُنشئ بنداً ولا سنداً",
        },
        request=request,
    )
    return balance


# ---------------------------------------------------------------------------
# 2 · Review
# ---------------------------------------------------------------------------
def review(
    *,
    actor: Any,
    balance: OpeningBalance,
    enrollment: Any = None,
    participant: Any = None,
    note_ar: str,
    request: Any = None,
) -> OpeningBalance:
    """
    A second person checks the figure against its source. Still no ledger row.

    This is also where the enrolment and the participant are attached: working
    out whose old debt this is, and which live enrolment it belongs to, is the
    reviewer's job and it is a judgement, not a lookup. Nothing is matched
    automatically — the archive holds eight legacy numbers carrying two
    different names, and a matcher that guessed would attach one person's debt
    to another's account.

    The participant is recorded separately from the enrolment (Sprint 8D-3)
    because client decision 2 blocks the PERSON from a clearance, and a debt
    whose owner never registered again has no enrolment to be found through.
    Passing the enrolment alone fills in its participant, since that link is
    itself already reviewed; nothing is inferred from a name or a number.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.EDIT, request=request)

    if balance.status != OpeningBalanceStatus.DRAFT:
        raise OpeningBalanceStateError(
            f"لا تُراجع إلا المسودة — حالة {balance.code} الآن {balance.get_status_display()}."
        )
    if actor.pk == balance.created_by_id:
        raise SeparationOfDutiesError(
            "المراجعة لغير من اقترح الرصيد — مراجعة الشخص لقيده لا تُعدّ مراجعة (D-24 · BR-094)."
        )
    if not note_ar.strip():
        raise ValidationError("ملاحظة المراجعة إلزامية — ماذا قابلتَ الرقم به؟")

    return _apply_review(
        actor=actor,
        balance=balance,
        enrollment=enrollment,
        participant=participant,
        note_ar=note_ar,
        request=request,
    )


@transaction.atomic
def _apply_review(
    *,
    actor: Any,
    balance: OpeningBalance,
    enrollment: Any,
    participant: Any,
    note_ar: str,
    request: Any,
) -> OpeningBalance:
    fields = ["status", "reviewed_by", "reviewed_at", "review_note_ar"]
    balance.status = OpeningBalanceStatus.REVIEWED
    balance.reviewed_by = actor
    balance.reviewed_at = timezone.now()
    balance.review_note_ar = note_ar.strip()[:255]
    if enrollment is not None:
        balance.enrollment = enrollment
        fields.append("enrollment")
    # Taken from the enrolment when one is given — that link was reviewed by a
    # person too, so following it is not a guess.
    resolved_participant = participant or getattr(enrollment, "participant", None)
    if resolved_participant is not None:
        balance.participant = resolved_participant
        fields.append("participant")
    balance.save(update_fields=fields)

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(balance.pk),
        reference=balance.code,
        summary_ar=f"مراجعة الرصيد الافتتاحي {balance.code}",
        actor=actor,
        changes={
            "status": OpeningBalanceStatus.REVIEWED,
            "enrollment": getattr(enrollment, "code", ""),
            "participant": getattr(resolved_participant, "participant_number", ""),
            "note_ar": balance.review_note_ar,
            "ledger_effect": "لا شيء — المراجعة لا تُنشئ بنداً",
        },
        request=request,
    )
    return balance


# ---------------------------------------------------------------------------
# 3 · Approve or reject
# ---------------------------------------------------------------------------
def approve(
    *, actor: Any, balance: OpeningBalance, note_ar: str = "", request: Any = None
) -> OpeningBalance:
    """
    The centre manager authorises it. STILL no ledger row — approval is
    permission to post, not the posting.

    Keeping the two apart is what makes the ledger write a deliberate,
    separately audited act rather than a side effect of a click.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.APPROVE, request=request)

    if balance.status != OpeningBalanceStatus.REVIEWED:
        raise OpeningBalanceStateError(
            f"لا يُعتمد إلا المُراجَع — حالة {balance.code} الآن {balance.get_status_display()}."
        )
    if actor.pk in {balance.created_by_id, balance.reviewed_by_id}:
        raise SeparationOfDutiesError(
            "الاعتماد لغير من اقترح ولغير من راجع — ثلاثة أشخاص لا اثنان (D-24 · BR-094)."
        )
    if balance.enrollment_id is None:
        raise NoEnrollmentError(
            "لا اعتماد بلا تسجيل يستقرّ عليه الرصيد — الذمة التي لا مكان لها لا تُحصَّل."
        )

    return _apply_decision(
        actor=actor,
        balance=balance,
        status=OpeningBalanceStatus.APPROVED,
        note_ar=note_ar,
        request=request,
    )


def reject(
    *, actor: Any, balance: OpeningBalance, note_ar: str, request: Any = None
) -> OpeningBalance:
    """
    Refuse it, with a reason. The record stays.

    A rejected balance is an outcome, not a deletion: somebody proposed a debt
    and somebody declined it, and both halves belong on the file.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.APPROVE, request=request)

    if balance.status not in {OpeningBalanceStatus.DRAFT, OpeningBalanceStatus.REVIEWED}:
        raise OpeningBalanceStateError(
            f"لا يُرفض إلا ما لم يُعتمد بعد — حالة {balance.code} الآن "
            f"{balance.get_status_display()}."
        )
    if not note_ar.strip():
        raise ValidationError("سبب الرفض إلزامي — من اقترح الرصيد يحتاج أن يعرف ما العيب.")

    return _apply_decision(
        actor=actor,
        balance=balance,
        status=OpeningBalanceStatus.REJECTED,
        note_ar=note_ar,
        request=request,
    )


@transaction.atomic
def _apply_decision(
    *, actor: Any, balance: OpeningBalance, status: str, note_ar: str, request: Any
) -> OpeningBalance:
    fields = ["status", "decision_note_ar"]
    balance.status = status
    balance.decision_note_ar = note_ar.strip()[:255]
    if status == OpeningBalanceStatus.APPROVED:
        balance.approved_by = actor
        balance.approved_at = timezone.now()
        fields += ["approved_by", "approved_at"]
    balance.save(update_fields=fields)

    write_audit(
        action="APPROVE" if status == OpeningBalanceStatus.APPROVED else "REJECT",
        entity_type=ENTITY,
        entity_id=str(balance.pk),
        reference=balance.code,
        summary_ar=(
            f"{'اعتماد' if status == OpeningBalanceStatus.APPROVED else 'رفض'} "
            f"الرصيد الافتتاحي {balance.code} — {balance.amount}"
        ),
        actor=actor,
        changes={
            "status": status,
            "note_ar": balance.decision_note_ar,
            "ledger_effect": "لا شيء بعد — الاعتماد إذن بالترحيل لا ترحيل",
        },
        request=request,
    )
    return balance


# ---------------------------------------------------------------------------
# 4 · Post — the only function in this module that writes to the ledger
# ---------------------------------------------------------------------------
def post(*, actor: Any, balance: OpeningBalance, request: Any = None) -> ChargeLine:
    """
    Create the charge line. **The only ledger write in the entire archive path.**

    Three properties, each with a test behind it:

    * it runs only from APPROVED, so the four hands have all been used;
    * it refuses CREDIT by name, because posting one would require a receipt
      for money the centre never received through this system;
    * it can run once. ``posted_charge_line`` is a OneToOne, so a second
      attempt collides at the database even if every guard above were removed.

    The line carries ``is_partner_shareable=False``. That is not decoration:
    ``entitlement_service.shareable_collected`` filters on exactly this flag,
    so when the participant eventually pays, no partner claim is created out
    of a debt that predates the agreement.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.APPROVE, request=request)

    if balance.status == OpeningBalanceStatus.POSTED or balance.posted_charge_line_id is not None:
        raise AlreadyPostedError(
            f"الرصيد {balance.code} مُرحَّل سلفاً — الترحيل مرة واحدة، وإلا حُصِّلت الذمة مرتين."
        )
    if balance.status != OpeningBalanceStatus.APPROVED:
        raise OpeningBalanceStateError(
            f"لا يُرحَّل إلا المعتمَد — حالة {balance.code} الآن {balance.get_status_display()}."
        )
    if balance.direction == OpeningBalanceDirection.CREDIT:
        raise CreditNotPostableError(
            "الرصيد الدائن لا يُرحَّل في هذه المرحلة — ترحيله يستلزم إنشاء سند قبض عن مالٍ "
            "لم يستلمه هذا النظام، وهو بعينه التلفيق الذي بُني الأرشيف لتفاديه. "
            "ردّ الأرصدة الدائنة التاريخية قرار يُتخذ على الورق أولاً."
        )
    if balance.enrollment_id is None:
        raise NoEnrollmentError("لا ترحيل بلا تسجيل — البند يحتاج مكاناً يستقرّ فيه.")

    return _write_charge_line(actor=actor, balance=balance, request=request)


@transaction.atomic
def _write_charge_line(*, actor: Any, balance: OpeningBalance, request: Any) -> ChargeLine:
    """The write half — permission and state already decided by the caller."""
    enrollment = balance.enrollment
    if enrollment is None:  # pragma: no cover - post() refuses this first
        raise NoEnrollmentError("لا ترحيل بلا تسجيل.")

    line = ChargeLine.objects.create(
        enrollment=enrollment,
        charge_type=ChargeType.OPENING_BALANCE,
        description_ar=balance.description_ar,
        net_amount=balance.amount,
        is_taxable=False,
        tax_rate_snapshot=None,
        tax_amount=ZERO,
        gross_amount=balance.amount,
        charged_on=balance.as_of,
        # BR-046 — a debt from before the agreement is not the partner's
        # business. The entitlement query filters on this flag, so the
        # exclusion holds for good rather than until someone forgets.
        is_partner_shareable=False,
        is_revenue=True,
    )

    balance.status = OpeningBalanceStatus.POSTED
    balance.posted_charge_line = line
    balance.posted_by = actor
    balance.posted_at = timezone.now()
    balance.save(update_fields=["status", "posted_charge_line", "posted_by", "posted_at"])

    write_audit(
        action="CREATE",
        entity_type="billing.ChargeLine",
        entity_id=str(line.pk),
        reference=balance.code,
        summary_ar=(f"ترحيل رصيد افتتاحي {balance.amount} إلى التسجيل {enrollment.code}"),
        actor=actor,
        changes={
            "opening_balance": balance.code,
            "charge_type": ChargeType.OPENING_BALANCE,
            "amount": str(balance.amount),
            "enrollment": enrollment.code,
            "is_partner_shareable": "False — دين سابق للاتفاقية لا يدخل وعاء الشريك (BR-046)",
            "source_workbook": balance.source_workbook,
            "source_sheet": balance.source_sheet,
            "source_row": str(balance.source_row) if balance.source_row is not None else "",
        },
        request=request,
    )
    return line


# ---------------------------------------------------------------------------
# 5 · What becomes of a CREDIT — client decision 1 (Sprint 8D-3)
# ---------------------------------------------------------------------------
def carry_forward(
    *, actor: Any, balance: OpeningBalance, enrollment: Any, note_ar: str, request: Any = None
) -> OpeningBalance:
    """
    Apply an approved credit to a LATER registration.

    «إذا كان الطالب سجّل مواد لاحقاً، يُرحَّل له الرصيد على ذلك التسجيل.»

    No receipt is created, and that is the point. The centre never received
    this money through this system, so there is nothing to issue a receipt
    for; what exists is an acknowledged obligation, and applying it reduces
    what the participant owes on the new enrolment. ``get_account_state``
    carries it as its own term, so it can never be mistaken for a payment or
    counted as revenue.

    "Later" is enforced rather than assumed: the enrolment must have been
    entered on or after the date the credit is stated as of. Applying a 2022
    credit to a 2021 enrolment would be rewriting a closed year.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.APPROVE, request=request)

    _guard_credit_outcome(balance)
    if enrollment is None:
        raise NoEnrollmentError("لا ترحيل بلا تسجيل لاحق يُرحَّل إليه الرصيد.")
    if not note_ar.strip():
        raise ValidationError("مسوّغ الترحيل إلزامي.")

    enrolled_on = getattr(enrollment, "enrolled_on", None)
    if enrolled_on is not None and enrolled_on < balance.as_of:
        raise NotALaterRegistrationError(
            f"التسجيل {enrollment.code} مؤرخ {enrolled_on} وهو أسبق من تاريخ الرصيد "
            f"{balance.as_of} — الرصيد يُرحَّل إلى تسجيل لاحق لا سابق."
        )

    return _resolve_credit(
        actor=actor,
        balance=balance,
        status=OpeningBalanceStatus.APPLIED,
        enrollment=enrollment,
        note_ar=note_ar,
        request=request,
    )


def mark_refund_due(
    *, actor: Any, balance: OpeningBalance, note_ar: str, request: Any = None
) -> OpeningBalance:
    """
    Declare that the centre owes this money back in cash.

    «إذا لم يكن للطالب تسجيل لاحق، يُعاد له الرصيد.»

    The declaration is the whole of it. Handing the cash over is a movement
    the cashbox records when it actually happens, with a real document and a
    real date — this service will not manufacture one, because a refund
    receipt for money that never arrived is exactly the invention Sprint 8D-1
    refused. What this creates is a standing, audited obligation that the
    screen shows until somebody settles it.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.APPROVE, request=request)

    _guard_credit_outcome(balance)
    if not note_ar.strip():
        raise ValidationError("مسوّغ الردّ إلزامي — لماذا لا يُرحَّل الرصيد؟ (لا تسجيل لاحق؟)")

    return _resolve_credit(
        actor=actor,
        balance=balance,
        status=OpeningBalanceStatus.REFUND_DUE,
        enrollment=None,
        note_ar=note_ar,
        request=request,
    )


def _guard_credit_outcome(balance: OpeningBalance) -> None:
    """Shared refusals for both outcomes, checked before any transaction."""
    if balance.direction != OpeningBalanceDirection.CREDIT:
        raise NotACreditError(
            f"الرصيد {balance.code} ذمة لا رصيد دائن — الترحيل والردّ للأرصدة الدائنة وحدها."
        )
    if balance.status in {OpeningBalanceStatus.APPLIED, OpeningBalanceStatus.REFUND_DUE}:
        raise AlreadyResolvedError(
            f"الرصيد {balance.code} مُسوّى سلفاً ({balance.get_status_display()}) — "
            "التسوية مرة واحدة، وإلا نال المشارك رصيده مرتين."
        )
    if balance.status != OpeningBalanceStatus.APPROVED:
        raise OpeningBalanceStateError(
            f"لا تُسوّى إلا الأرصدة المعتمَدة — حالة {balance.code} الآن "
            f"{balance.get_status_display()}."
        )


@transaction.atomic
def _resolve_credit(
    *,
    actor: Any,
    balance: OpeningBalance,
    status: str,
    enrollment: Any,
    note_ar: str,
    request: Any,
) -> OpeningBalance:
    """The write half — permission and state already decided by the caller."""
    fields = ["status", "resolved_by", "resolved_at", "resolution_note_ar"]
    balance.status = status
    balance.resolved_by = actor
    balance.resolved_at = timezone.now()
    balance.resolution_note_ar = note_ar.strip()[:255]
    if enrollment is not None:
        balance.enrollment = enrollment
        fields.append("enrollment")
    balance.save(update_fields=fields)

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(balance.pk),
        reference=balance.code,
        summary_ar=(f"تسوية رصيد دائن {balance.amount} — {balance.get_status_display()}"),
        actor=actor,
        changes={
            "status": status,
            "enrollment": getattr(enrollment, "code", ""),
            "note_ar": balance.resolution_note_ar,
            "receipt_created": "لا — لم يُنشأ سند قبض عن مال لم يستلمه النظام",
            "ledger_effect": (
                "يخفض ما على التسجيل عبر معادلة الرصيد"
                if status == OpeningBalanceStatus.APPLIED
                else "لا شيء — التزام قائم حتى يُدفع نقداً بسند حقيقي"
            ),
        },
        request=request,
    )
    return balance


# ---------------------------------------------------------------------------
# 6 · Paying a REFUND_DUE out — Sprint 8D-4
# ---------------------------------------------------------------------------
def pay_refund_due(
    *,
    actor: Any,
    balance: OpeningBalance,
    code: str,
    amount: Decimal,
    paid_on: date,
    payment_method: Any,
    external_reference: str,
    payee_name_ar: str,
    note_ar: str = "",
    request: Any = None,
) -> OpeningBalanceRefund:
    """
    Hand the money back, against a real voucher, once.

    Sprint 8D-3 could declare that the centre owed a historical credit but had
    no way to record the cash leaving, so the obligation stood open forever.
    This closes it.

    **The finance officer executes; the manager declared.** ``Action.EDIT``
    rather than ``APPROVE``, and the split is the one ``Refund`` already uses:
    the manager decides a refund is owed, the officer pays it. No role on this
    screen holds both, so the two hands are structural rather than merely
    checked — and the service checks the PEOPLE too, because a role split is
    not a person split when somebody holds two accounts.

    **No ``Receipt``.** A receipt asserts that cash arrived; this is cash
    leaving. Issuing one would state the opposite of what happened and inflate
    the day's takings by the amount handed back. ``system_total_for``
    reconciles issued receipts only, which is the established cashbox design
    and the reason a payout belongs beside the other payouts rather than
    inside the till count.

    **Once, while one stands.** ``(opening_balance, active_key)`` is unique and
    ``active_key`` is NULL on a reversed payout, so a second LIVE payout
    collides at the database even if these guards were removed — while a
    corrected re-payment after a reversal is allowed, which is the whole point
    of Sprint 8D-5.

    **``paid_on`` is the caller's** (Sprint 8D-5). The cash may have left last
    Tuesday and reached the clerk today; forcing today's date would file the
    voucher in the wrong month. What is refused is a date inside a period
    somebody has closed (D-23) — and nothing else, because no other movement
    in this system bounds its own date either.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.EDIT, request=request)

    if balance.direction != OpeningBalanceDirection.CREDIT:
        raise NotACreditError(f"الرصيد {balance.code} ذمة على المشارك لا رصيد دائن — لا يُصرف.")
    if balance.status == OpeningBalanceStatus.REFUNDED:
        raise AlreadyRefundedError(
            f"الرصيد {balance.code} مصروف سلفاً — الصرف مرة واحدة، وإلا قُبض المبلغ مرتين."
        )
    if balance.status != OpeningBalanceStatus.REFUND_DUE:
        raise NotRefundDueError(
            f"لا يُصرف إلا ما أُعلن مستحقاً للردّ — حالة {balance.code} الآن "
            f"{balance.get_status_display()}."
        )
    if balance.resolved_by_id is not None and balance.resolved_by_id == actor.pk:
        raise SeparationOfDutiesError(
            "الصرف لغير من أعلن الاستحقاق — من قرّر أن المبلغ مستحق لا يصرفه بنفسه (D-18 · BR-094)."
        )
    if not external_reference.strip() or not payee_name_ar.strip():
        raise MissingPayoutDetailsError(
            "رقم سند الصرف واسم المستلم إلزامان — النقد الخارج يحمل مستنده ومن استلمه."
        )
    if amount != balance.amount:
        raise ValidationError(
            f"المبلغ المصروف {amount} لا يساوي الرصيد المستحق {balance.amount} — "
            "الصرف الجزئي غير مدعوم في هذه المرحلة."
        )
    if OpeningBalanceRefund.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الصرف {code} مستعمل سلفاً.")

    _guard_movement_date(
        paid_on,
        what_ar="صرف رصيد افتتاحي",
        actor=actor,
        reference=code,
        request=request,
    )

    return _write_payout(
        actor=actor,
        balance=balance,
        code=code,
        amount=amount,
        paid_on=paid_on,
        payment_method=payment_method,
        external_reference=external_reference.strip(),
        payee_name_ar=payee_name_ar.strip(),
        note_ar=note_ar,
        request=request,
    )


@transaction.atomic
def _write_payout(
    *,
    actor: Any,
    balance: OpeningBalance,
    code: str,
    amount: Decimal,
    paid_on: date,
    payment_method: Any,
    external_reference: str,
    payee_name_ar: str,
    note_ar: str,
    request: Any,
) -> OpeningBalanceRefund:
    """The write half — permission and state already decided by the caller."""
    payout = OpeningBalanceRefund.objects.create(
        code=code,
        opening_balance=balance,
        amount=amount,
        paid_on=paid_on,
        payment_method=payment_method,
        external_reference=external_reference[:64],
        payee_name_ar=payee_name_ar[:150],
        note_ar=note_ar.strip()[:255],
        paid_by=actor,
    )

    balance.status = OpeningBalanceStatus.REFUNDED
    balance.save(update_fields=["status"])

    write_audit(
        action="CREATE",
        entity_type="billing.OpeningBalanceRefund",
        entity_id=str(payout.pk),
        reference=code,
        summary_ar=f"صرف رصيد افتتاحي دائن {amount} إلى {payee_name_ar}",
        actor=actor,
        changes={
            "opening_balance": balance.code,
            "amount": str(amount),
            "paid_on": paid_on.isoformat(),
            "payment_method": getattr(payment_method, "code", ""),
            "external_reference": external_reference,
            "payee_name_ar": payee_name_ar,
            "declared_by": text_of(balance.resolved_by, "username"),
            "receipt_created": "لا — النقد خارج، والسند يؤكد دخولاً لم يحدث",
            "daily_closing_effect": "لا شيء — الإقفال يطابق السندات الصادرة وحدها",
        },
        request=request,
    )
    return payout


def reverse_refund_payout(
    *,
    actor: Any,
    balance: OpeningBalance,
    reversed_on: date,
    reason_ar: str,
    reversal_reference: str = "",
    request: Any = None,
) -> OpeningBalanceRefund:
    """
    Correct a mistaken payout. The obligation comes back.

    **Reopening rather than a terminal state.** A payout goes wrong for
    ordinary reasons — the wrong payee, a bounced cheque, the money never
    actually handed over — and in every one of them the centre STILL owes the
    participant. Marking the balance dead would leave a real obligation with
    no row to act on, so the status returns to ``REFUND_DUE`` and it appears
    in the queue again, payable afresh.

    **Nothing is deleted or edited.** The original row keeps its amount, its
    date, its voucher and its payee exactly as recorded — what happened,
    happened, and the paper voucher in the file still has a row to match. The
    reversal is a second set of facts written beside the first, which is also
    why the original's ``paid_on`` may sit in a period that is now closed: the
    guard applies to the REVERSAL's own date, not retroactively to a movement
    already recorded.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.EDIT, request=request)

    payout = (
        OpeningBalanceRefund.objects.filter(opening_balance=balance, reversed_at__isnull=True)
        .order_by("-id")
        .first()
    )
    if payout is None:
        raise NotReversibleError(
            f"لا صرف قائم على الرصيد {balance.code} — "
            f"حالته {balance.get_status_display()}، ولا شيء يُعكس."
        )
    if not reason_ar.strip():
        raise ValidationError(
            "سبب عكس الصرف إلزامي — العكس الصامت يترك سند صرف في الملف بلا تفسير."
        )

    _guard_movement_date(
        reversed_on,
        what_ar="عكس صرف",
        actor=actor,
        reference=payout.code,
        request=request,
    )

    return _write_reversal(
        actor=actor,
        balance=balance,
        payout=payout,
        reversed_on=reversed_on,
        reason_ar=reason_ar,
        reversal_reference=reversal_reference,
        request=request,
    )


@transaction.atomic
def _write_reversal(
    *,
    actor: Any,
    balance: OpeningBalance,
    payout: OpeningBalanceRefund,
    reversed_on: date,
    reason_ar: str,
    reversal_reference: str,
    request: Any,
) -> OpeningBalanceRefund:
    """The write half — permission and state already decided by the caller."""
    payout.reversed_on = reversed_on
    payout.reversed_by = actor
    payout.reversed_at = timezone.now()
    payout.reversal_reason_ar = reason_ar.strip()[:255]
    payout.reversal_reference = reversal_reference.strip()[:64]
    payout.active_key = None
    payout.save(
        update_fields=[
            "reversed_on",
            "reversed_by",
            "reversed_at",
            "reversal_reason_ar",
            "reversal_reference",
            "active_key",
        ]
    )

    balance.status = OpeningBalanceStatus.REFUND_DUE
    balance.save(update_fields=["status"])

    write_audit(
        action="UPDATE",
        entity_type="billing.OpeningBalanceRefund",
        entity_id=str(payout.pk),
        reference=payout.code,
        summary_ar=f"عكس صرف رصيد افتتاحي {payout.amount} — {reason_ar.strip()}",
        actor=actor,
        changes={
            "opening_balance": balance.code,
            "original_paid_on": payout.paid_on.isoformat(),
            "original_voucher": payout.external_reference,
            "original_amount": str(payout.amount),
            "reversed_on": reversed_on.isoformat(),
            "reason_ar": payout.reversal_reason_ar,
            "reversal_reference": payout.reversal_reference,
            "balance_status": OpeningBalanceStatus.REFUND_DUE,
            "original_row": "محفوظ كما هو — التصحيح يُضاف ولا يمحو",
            "receipt_created": "لا — لم يُنشأ سند قبض عن ردّ معكوس",
        },
        request=request,
    )
    return payout


def _guard_movement_date(
    on_date: date,
    *,
    what_ar: str,
    actor: Any = None,
    reference: str = "",
    request: Any = None,
) -> None:
    """
    One check, and deliberately only one: the period must not be closed (D-23).

    A future-date guard was written here first and then removed. No other
    money movement in this system has one — ``take_payment`` accepts a
    forward-dated ``received_on``, and so does an expense — so refusing one
    here would make this screen reject a date the till accepts three clicks
    away. If the centre wants cash dates bounded by today, that is a rule for
    every movement at once, not a rule this sprint invents for the smallest
    of them.

    Backdating is the POINT: cash paid last Tuesday and entered on Thursday
    belongs to Tuesday, and forcing today's date is what put Sprint 8D-4's
    payouts in the wrong day.
    """
    period_service.require_open(
        on_date,
        actor=actor,
        what_ar=what_ar,
        entity_type="billing.OpeningBalanceRefund",
        reference=reference,
        request=request,
    )


def outstanding_refunds(*, actor: Any, request: Any = None) -> list[dict[str, Any]]:
    """
    What the centre still owes back and has not yet handed over.

    Kept separate from ``totals`` because this is the operational list — the
    queue somebody works through — rather than a figure on a dashboard.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.VIEW, request=request)
    return [
        {
            "code": balance.code,
            "amount": balance.amount,
            "as_of": balance.as_of,
            "participant_number": text_of(balance.participant, "participant_number"),
            "participant_name": text_of(balance.participant, "name_ar"),
            "legacy_number": balance.source_legacy_number,
            "declared_by": person_name(balance.resolved_by),
            "declared_at": balance.resolved_at,
            "note_ar": balance.resolution_note_ar,
        }
        for balance in OpeningBalance.objects.filter(
            status=OpeningBalanceStatus.REFUND_DUE,
            direction=OpeningBalanceDirection.CREDIT,
        ).select_related("participant", "resolved_by")
    ]


def refunds_paid_between(*, date_from: date, date_to: date) -> Decimal:
    """
    Cash handed back in a window, for report 2 to DISCLOSE.

    Not permission-gated: the report that calls it runs its own
    ``require_report`` (BR-099), and a second gate here would refuse a reader
    who is entitled to the report the answer it is made of.
    """
    rows = OpeningBalanceRefund.objects.filter(
        paid_on__gte=date_from, paid_on__lte=date_to, reversed_at__isnull=True
    ).values_list("amount", flat=True)
    return sum(rows, ZERO)


def refunds_reversed_between(*, date_from: date, date_to: date) -> Decimal:
    """
    Payouts taken back in a window, by the date of the REVERSAL.

    Reported on its own date rather than the original payout's, because that
    is when the money came back — and a reversal often lands in a later month
    than the payment it corrects.
    """
    rows = OpeningBalanceRefund.objects.filter(
        reversed_on__gte=date_from, reversed_on__lte=date_to
    ).values_list("amount", flat=True)
    return sum(rows, ZERO)


# ---------------------------------------------------------------------------
# 7 · The clearance guard — client decision 2 (Sprint 8D-3)
# ---------------------------------------------------------------------------
def unsettled_debt_for(participant: Any) -> list[OpeningBalance]:
    """
    Old debts that must stop this participant being cleared.

    «الذمة القديمة تبقى معلّقة، ولا يُمنح براءة ذمة ولا شهادة حتى تُسدَّد.»

    Deliberately NOT a permission-gated read: it is called from inside the
    clearance step, on behalf of whoever is certifying it, and adding a second
    screen's permission there would refuse a finance officer the answer to a
    question their own screen is asking.

    Only debts the balance equation cannot see are returned — see
    ``OpeningBalance.blocks_clearance``. A posted debt is already a charge
    line, and BR-073 stops the clearance on it under its own rule.
    """
    if participant is None:
        return []
    return [
        balance
        for balance in OpeningBalance.objects.filter(
            participant=participant, direction=OpeningBalanceDirection.RECEIVABLE
        ).exclude(status__in=[OpeningBalanceStatus.POSTED, OpeningBalanceStatus.REJECTED])
        if balance.blocks_clearance
    ]


def unsettled_debt_total(participant: Any) -> Decimal:
    return sum((balance.amount for balance in unsettled_debt_for(participant)), ZERO)


# ---------------------------------------------------------------------------
# Reads (A-05 — the screens ask here, never the models)
# ---------------------------------------------------------------------------
def list_balances(
    *, actor: Any, status: str = "", direction: str = "", request: Any = None
) -> list[dict[str, Any]]:
    policy.require(actor, Screen.OPENING_BALANCES, Action.VIEW, request=request)
    queryset = OpeningBalance.objects.select_related(
        "created_by",
        "reviewed_by",
        "approved_by",
        "posted_by",
        "resolved_by",
        "participant",
        "enrollment",
        "posted_charge_line",
    )
    if status:
        queryset = queryset.filter(status=status)
    if direction:
        queryset = queryset.filter(direction=direction)
    # Newest payout first, so ``next(iter(...))`` picks the live one: a
    # reversed payout is only ever followed by a newer live one.
    queryset = queryset.prefetch_related("refund_payouts")

    rows = []
    for balance in queryset:
        # The LIVE payout if there is one, otherwise the most recent reversed
        # one so the screen can still show what was corrected. Chosen
        # explicitly rather than by taking the first of an ordered set —
        # relying on ordering for correctness is how a reversed payout ends up
        # displayed as current.
        payouts = list(balance.refund_payouts.all())
        payout = next((p for p in payouts if not p.is_reversed), None) or next(iter(payouts), None)
        rows.append(
            {
                "id": balance.pk,
                "code": balance.code,
                "direction": balance.direction,
                "direction_display": balance.get_direction_display(),
                "amount": balance.amount,
                "as_of": balance.as_of,
                "description_ar": balance.description_ar,
                "status": balance.status,
                "status_display": balance.get_status_display(),
                "enrollment_code": text_of(balance.enrollment, "code"),
                "legacy_number": balance.source_legacy_number,
                "source": _source_label(balance),
                "created_by": person_name(balance.created_by),
                "reviewed_by": person_name(balance.reviewed_by),
                "approved_by": person_name(balance.approved_by),
                "posted_by": person_name(balance.posted_by),
                "posted_at": balance.posted_at,
                "review_note_ar": balance.review_note_ar,
                "decision_note_ar": balance.decision_note_ar,
                "is_postable": balance.is_postable,
                "is_resolvable_credit": balance.is_resolvable_credit,
                "is_refund_payable": balance.is_refund_payable,
                "payout_reference": text_of(payout, "external_reference"),
                "payout_code": text_of(payout, "code"),
                "payout_paid_on": getattr(payout, "paid_on", None),
                "payout_paid_by": person_name(getattr(payout, "paid_by", None)),
                "payout_reversed_on": getattr(payout, "reversed_on", None),
                "payout_reversal_reason": text_of(payout, "reversal_reason_ar"),
                "payout_is_reversed": bool(payout is not None and payout.is_reversed),
                "is_reversible": bool(payout is not None and not payout.is_reversed),
                "resolved_by": person_name(balance.resolved_by),
                "resolution_note_ar": balance.resolution_note_ar,
                "participant_number": text_of(balance.participant, "participant_number"),
                "credit_blocked": balance.direction == OpeningBalanceDirection.CREDIT,
            }
        )
    return rows


def _source_label(balance: OpeningBalance) -> str:
    if not balance.source_workbook:
        return ""
    return f"{balance.source_workbook} · {balance.source_sheet} · {balance.source_row}"


def totals(*, actor: Any, request: Any = None) -> dict[str, Any]:
    """
    What is outstanding, and how much of it has actually reached the ledger.

    The two are shown apart on purpose: a reader must be able to see that
    approving a balance did not move money, and that only the posted column
    exists in the accounts.
    """
    policy.require(actor, Screen.OPENING_BALANCES, Action.VIEW, request=request)

    def _sum(**filters: Any) -> Decimal:
        rows = OpeningBalance.objects.filter(**filters).values_list("amount", flat=True)
        return sum(rows, ZERO)

    return {
        "draft": _sum(status=OpeningBalanceStatus.DRAFT),
        "reviewed": _sum(status=OpeningBalanceStatus.REVIEWED),
        "approved_not_posted": _sum(status=OpeningBalanceStatus.APPROVED),
        "posted": _sum(status=OpeningBalanceStatus.POSTED),
        # Sprint 8D-3 — the two ends a credit can come to, shown apart. One
        # reduces a later bill; the other is cash the centre still owes.
        "credit_applied": _sum(status=OpeningBalanceStatus.APPLIED),
        "refund_due": _sum(status=OpeningBalanceStatus.REFUND_DUE),
        "refunded": _sum(status=OpeningBalanceStatus.REFUNDED),
        "refunds_reversed": sum(
            OpeningBalanceRefund.objects.filter(reversed_at__isnull=False).values_list(
                "amount", flat=True
            ),
            ZERO,
        ),
        "credit_carried": _sum(direction=OpeningBalanceDirection.CREDIT),
    }


def balance_instance(*, actor: Any, code: str, request: Any = None) -> OpeningBalance:
    """The object, for handing back into a step (A-05)."""
    policy.require(actor, Screen.OPENING_BALANCES, Action.VIEW, request=request)
    return OpeningBalance.objects.select_related("enrollment", "posted_charge_line").get(code=code)


def status_choices() -> list[tuple[str, str]]:
    return [(value, str(label)) for value, label in OpeningBalanceStatus.choices]


def direction_choices() -> list[tuple[str, str]]:
    return [(value, str(label)) for value, label in OpeningBalanceDirection.choices]


__all__ = [
    "AlreadyPostedError",
    "AlreadyRefundedError",
    "AlreadyResolvedError",
    "CreditNotPostableError",
    "MissingPayoutDetailsError",
    "NoEnrollmentError",
    "NotACreditError",
    "NotALaterRegistrationError",
    "NotRefundDueError",
    "NotReversibleError",
    "OpeningBalanceStateError",
    "SeparationOfDutiesError",
    "approve",
    "balance_instance",
    "carry_forward",
    "direction_choices",
    "list_balances",
    "mark_refund_due",
    "outstanding_refunds",
    "pay_refund_due",
    "post",
    "propose_from_archive",
    "propose_manually",
    "refunds_paid_between",
    "refunds_reversed_between",
    "reject",
    "reverse_refund_payout",
    "review",
    "status_choices",
    "totals",
    "unsettled_debt_for",
    "unsettled_debt_total",
]
