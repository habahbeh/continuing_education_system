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
    OpeningBalanceStatus,
)
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
# 6 · The clearance guard — client decision 2 (Sprint 8D-3)
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
    from apps.core.display import person_name, text_of

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

    return [
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
            "resolved_by": person_name(balance.resolved_by),
            "resolution_note_ar": balance.resolution_note_ar,
            "participant_number": text_of(balance.participant, "participant_number"),
            "credit_blocked": balance.direction == OpeningBalanceDirection.CREDIT,
        }
        for balance in queryset
    ]


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
    "AlreadyResolvedError",
    "CreditNotPostableError",
    "NoEnrollmentError",
    "NotACreditError",
    "NotALaterRegistrationError",
    "OpeningBalanceStateError",
    "SeparationOfDutiesError",
    "approve",
    "balance_instance",
    "carry_forward",
    "direction_choices",
    "list_balances",
    "mark_refund_due",
    "post",
    "propose_from_archive",
    "propose_manually",
    "reject",
    "review",
    "status_choices",
    "totals",
    "unsettled_debt_for",
    "unsettled_debt_total",
]
