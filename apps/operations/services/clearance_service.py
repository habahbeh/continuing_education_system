"""
Clearing a participant out (WORKFLOWS §6, BR-072 … BR-074).

Three steps that cannot be reordered, on form ``CS Fm 7.18 Rev A``:

1. the centre recovers its property,
2. finance verifies the account is **exactly zero** — and two different people
   sign for it,
3. the centre hands over the certificate.

Step 2 is where the controls live. The balance must be zero **in either
direction**: a credit balance stops a clearance just as firmly as a debt,
because the centre owing the participant is not "close enough to settled"
(BR-073, C-09). And it takes two signatures from two people (BR-074, C-29,
C-30, D-30) — the finance officer first, then the finance manager.

The FINANCE_MANAGER requirement on the second signature is enforced HERE, in
the policy layer, not as a constraint: a role can be changed on a user
afterwards, and a constraint has to stay true for rows written years ago
(DATA_MODEL §7.7).

WORKFLOWS §6.7 names a failure this module has to answer: the balance moving
after step 2 was certified. The final close therefore RE-CHECKS it rather than
trusting the number captured at certification time.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.services import opening_balance_service
from apps.billing.services.account_service import ZERO, get_account_state
from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.core.services.settings_service import get_setting
from apps.operations.models import (
    Clearance,
    ClearanceCaseType,
    ClearanceStatus,
    ClearanceStep,
    Enrollment,
    EnrollmentStatus,
)
from apps.people.constants import Action, Screen
from apps.people.models import Role
from apps.people.permissions import policy
from apps.people.permissions.separation import assert_second_certifier_differs

ENTITY = "operations.Clearance"

#: The clearance is a formal document (CS Fm 7.18 Rev A), so its number is the
#: system's to mint, as the enrolment code is. ``CLR-YYYY-NNNNNN`` — the year
#: it was opened, then a six-digit sequence within that year.
CLEARANCE_SCOPE = "clearance"
CLEARANCE_PREFIX = "CLR-"
CLEARANCE_PADDING = 6

#: A minted code can still meet a hand-entered one from before this sprint or
#: from a migration. Skipping past it costs one number; refusing would cost
#: the clearance.
MAX_CODE_ATTEMPTS = 8
STEP_ENTITY = "operations.ClearanceStep"

STEP_NAMES = {
    1: "المركز — استرجاع العُهد",
    2: "المالية — التحقق المالي",
    3: "المركز — تسليم الشهادة",
}

FINANCE_STEP = 2
HANDOVER_STEP = 3


def steps_for_case(case_type: str) -> dict[int, str]:
    """
    §6.4 lists three steps; the third is the certificate changing hands.

    A withdrawal and a dismissal have no certificate to hand over — BR-075
    ties one to a completed programme, and someone who left or was dismissed
    did not complete it. Their clearance is still a clearance: the custody is
    recovered and the money is certified, and it closes on those two.

    The step is dropped rather than created-and-skipped so that nothing has to
    remember to skip it: ``close_clearance`` already requires every step that
    EXISTS to be done, ``_require_previous_done`` only walks backwards from a
    step being completed, and ``get_clearance`` projects the rows it finds —
    so the screen, the print-out and the close all follow without a special
    case. A handover posted against one of these fails on the missing row.
    """
    if case_type == ClearanceCaseType.GRADUATION:
        return dict(STEP_NAMES)
    return {n: name for n, name in STEP_NAMES.items() if n != HANDOVER_STEP}


#: BR-074 · §6.4 — which role signs second. A SETTING rather than a literal:
#: §8 lists five roles and §6.4 names a sixth actor ("المحاسب ثم المدير
#: المالي"), so the centre may need to move this without a code change. The
#: DEFAULT stays FINANCE_MANAGER because that is what §6.4 names.
SECOND_CERTIFIER_ROLE_KEY = "clearance_second_certifier_role"

#: §6.4 assigns the three steps to two DEPARTMENTS, not to one authority:
#: step 1 «المركز» · step 2 «المالية» · step 3 «المركز». The permission matrix
#: cannot express that on its own — every action on this screen needs
#: ``CLEARANCE.APPROVE``, and the finance officer holds it, so without these
#: settings a finance officer could recover the centre's property and hand
#: over the certificate.
#:
#: A role check on top of the permission check, in the same shape as
#: ``SECOND_CERTIFIER_ROLE_KEY``: the permission decides who may reach the
#: screen, the setting decides whose step this is.
CUSTODY_ROLE_KEY = "clearance_custody_role"
HANDOVER_ROLE_KEY = "clearance_handover_role"


def second_certifier_role(*, as_of: date) -> str:
    """The role authorised to countersign the financial step."""
    return str(
        get_setting(SECOND_CERTIFIER_ROLE_KEY, as_of=as_of, default=Role.FINANCE_MANAGER)
    ).upper()


def custody_role(*, as_of: date) -> str:
    """§6.4 step 1 — «المركز: استرجاع العُهد»."""
    return str(get_setting(CUSTODY_ROLE_KEY, as_of=as_of, default=Role.CENTER_MANAGER)).upper()


def handover_role(*, as_of: date) -> str:
    """§6.4 step 3 — «المركز: تسليم الشهادة»."""
    return str(get_setting(HANDOVER_ROLE_KEY, as_of=as_of, default=Role.CENTER_MANAGER)).upper()


class ClearanceBlockedError(ValidationError):
    """BR-073 — the account is not exactly zero, in one direction or the other."""


class StepOutOfOrderError(ValidationError):
    """BR-072 — a step was attempted before the one before it was done."""


class DepositNotSettledError(ValidationError):
    """Q-01 / BR-097 — a deposit line exists and has been neither returned nor forfeited."""


class SecondCertifierRoleError(PermissionDenied):
    """BR-074 — the second signature belongs to the finance manager alone."""


class StepRoleError(PermissionDenied):
    """§6.4 — a step attempted by a department it does not belong to."""


def _require_step_role(
    *,
    actor: Any,
    clearance: Clearance,
    step_number: int,
    required_role: str,
    request: Any,
) -> None:
    """
    Refuse a step to a role it does not belong to, and record the attempt.

    Audited like any other denial (BR-085 · BR-100): the whole point of
    separating the centre's steps from finance's is that someone can later ask
    who tried to cross the line.
    """
    if getattr(actor, "role", None) == required_role:
        return

    write_audit(
        action="DENIED_ATTEMPT",
        entity_type=STEP_ENTITY,
        reference=clearance.code,
        summary_ar=(
            f"محاولة إتمام الخطوة {step_number} بدور غير {required_role} — "
            f"{getattr(actor, 'role', None)}"
        ),
        actor=actor,
        denial_rule="BR-072",
        changes={
            "step": step_number,
            "role": getattr(actor, "role", None),
            "required_role": required_role,
        },
        request=request,
    )
    raise StepRoleError(
        f"الخطوة {step_number} ({STEP_NAMES[step_number]}) للدور {required_role} حصراً — "
        f"§6.4 تُسند خطوتَي العُهد والتسليم إلى المركز والخطوة المالية إلى المالية."
    )


def clearance_partition(opened_on: date) -> str:
    """The sequence partition a clearance falls in — its year."""
    return str(opened_on.year)


def next_clearance_code(opened_on: date) -> str:
    """
    Mint the next ``CLR-YYYY-NNNNNN`` for the year ``opened_on`` falls in.

    Same contract as ``enrollment_service.next_enrollment_code``: the shared
    counter (ADR-011) locks its row inside the caller's transaction, so two
    clerks opening at the same moment cannot receive one number, and a rolled
    back opening takes its number back. Call it INSIDE that transaction.
    """
    partition = clearance_partition(opened_on)
    for _attempt in range(MAX_CODE_ATTEMPTS):
        code = next_number(
            CLEARANCE_SCOPE,
            partition,
            prefix=f"{CLEARANCE_PREFIX}{partition}-",
            padding=CLEARANCE_PADDING,
        )
        if not Clearance.objects.filter(code=code).exists():
            return code
    raise ValidationError(
        f"تعذّر توليد رمز براءة غير مكرَّر للسنة {partition} بعد {MAX_CODE_ATTEMPTS} محاولات."
    )


#: §6.4 — «عند انتهاء الدورة (أو الانسحاب أو الفصل)»: the three ways an
#: enrolment ends, and the clearance case each one opens. The clearance does
#: not decide why the trainee left; the enrolment lifecycle decided that
#: first, and the case follows from it.
#:
#: INCOMPLETE and NOT_ATTENDED are deliberately absent. §6.4 names only these
#: three cases and nothing in the requirements says what clearance (if any) an
#: absentee gets — so until that is confirmed they are not offered, rather
#: than guessed into one of the three.
CLEARANCE_CASE_FOR_STATUS: dict[str, str] = {
    EnrollmentStatus.COMPLETED: ClearanceCaseType.GRADUATION,
    EnrollmentStatus.WITHDRAWN: ClearanceCaseType.WITHDRAWAL,
    EnrollmentStatus.DISMISSED: ClearanceCaseType.DISMISSAL,
}


class EnrollmentNotClearableError(ValidationError):
    """The enrolment is not in a status a clearance can be opened for."""


def clearance_case_for_enrollment(enrollment: Enrollment) -> str:
    """The clearance case an enrolment's final status calls for, or a refusal."""
    case_type = CLEARANCE_CASE_FOR_STATUS.get(enrollment.status)
    if case_type is None:
        raise EnrollmentNotClearableError(
            f"لا تُفتح براءة ذمة لتسجيل في الحالة «{enrollment.get_status_display()}»؛ "
            "تُفتح بعد الإكمال أو الانسحاب أو الفصل (§6.4)."
        )
    return case_type


def opening_preview(*, actor: Any, enrollment: Enrollment, request: Any = None) -> dict[str, Any]:
    """
    What opening a clearance on this enrolment would do — for the screen's
    confirmation step. Reads nothing the opening itself would not; refuses
    exactly what the opening would refuse, so the confirmation never promises
    what the next click cannot deliver.
    """
    policy.require(actor, Screen.CLEARANCE, Action.CREATE, request=request)
    case_type = clearance_case_for_enrollment(enrollment)
    return {
        "enrollment_code": enrollment.code,
        "participant_name": enrollment.participant.name_ar,
        "status_display": enrollment.get_status_display(),
        "case_type": case_type,
        "case_type_display": ClearanceCaseType(case_type).label,
    }


def open_clearance(
    *,
    actor: Any,
    enrollment: Enrollment,
    opened_on: date,
    code: str = "",
    request: Any = None,
) -> Clearance:
    """
    WORKFLOWS §6.3 C1 — open a clearance and lay out its steps.

    The rows are created up front rather than as each is reached: the
    form is a printed checklist, and a participant standing at the counter is
    entitled to see what is still outstanding.

    The case is not a parameter: it is read off the enrolment's final status
    (:func:`clearance_case_for_enrollment`), so a graduate cannot be handed a
    withdrawal clearance by a slip on the form. An enrolment that has not
    ended is refused here, not only hidden from the dropdown.

    ``code`` is normally omitted and the system mints it
    (:func:`next_clearance_code`). It stays settable for callers that carry a
    number from somewhere else — a migration, a test — never for the screen.
    """
    policy.require(actor, Screen.CLEARANCE, Action.CREATE, request=request)

    case_type = clearance_case_for_enrollment(enrollment)

    live = Clearance.objects.filter(enrollment=enrollment, active_key=1).first()
    if live is not None:
        raise ValidationError(f"للتسجيل {enrollment.code} براءة ذمة قائمة سلفاً ({live.code}).")

    if not code:
        # As in enrollment_service: the counter row is created before anyone
        # locks it, keeping a brand-new year out of insert contention.
        ensure_sequence(CLEARANCE_SCOPE, clearance_partition(opened_on), padding=CLEARANCE_PADDING)

    return _open_clearance(
        actor=actor,
        enrollment=enrollment,
        case_type=case_type,
        opened_on=opened_on,
        code=code,
        request=request,
    )


@transaction.atomic
def _open_clearance(
    *,
    actor: Any,
    enrollment: Enrollment,
    case_type: str,
    opened_on: date,
    code: str,
    request: Any,
) -> Clearance:
    clearance = Clearance.objects.create(
        code=code or next_clearance_code(opened_on),
        participant=enrollment.participant,
        enrollment=enrollment,
        case_type=case_type,
        opened_on=opened_on,
        opened_by=actor,
        status=ClearanceStatus.OPEN,
    )
    for number, name in steps_for_case(case_type).items():
        ClearanceStep.objects.create(clearance=clearance, step_number=number, name_ar=name)

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(clearance.pk),
        reference=clearance.code,
        summary_ar=f"فتح براءة ذمة — {enrollment.code} · {case_type}",
        actor=actor,
        changes={"enrollment": enrollment.code, "case_type": case_type},
        request=request,
    )
    return clearance


def _step(clearance: Clearance, number: int) -> ClearanceStep:
    step = clearance.steps.filter(step_number=number).first()
    if step is None:  # pragma: no cover - steps are created with the clearance
        raise ValidationError(f"الخطوة {number} غير موجودة على {clearance.code}.")
    return step


def _require_previous_done(clearance: Clearance, number: int) -> None:
    """BR-072 — no skipping. The order is the control, not a convention."""
    if number == 1:
        return
    previous = _step(clearance, number - 1)
    if not previous.is_done:
        raise StepOutOfOrderError(
            f"لا يمكن إتمام الخطوة {number} قبل الخطوة {number - 1} ({previous.name_ar}) — BR-072."
        )


def complete_custody_step(
    *,
    actor: Any,
    clearance: Clearance,
    custody_items: list[dict[str, Any]],
    request: Any = None,
) -> ClearanceStep:
    """
    Step 1 — the centre's property back (§6.4: «المركز — استرجاع العُهد»).

    Two checks, and they answer different questions: the PERMISSION asks
    whether this user may act on clearances at all, and ``clearance_custody_role``
    asks whether this step is theirs. The finance officer passes the first and
    fails the second, which is what §6.4 intends.

    An item left outstanding keeps the step open; WORKFLOWS §6.7 is explicit
    that this is the correct outcome and not a nuisance.

    An EMPTY list is refused once the centre has defined a standard one
    (``clearance_custody_items``). Sprint 7 accepted it silently, which meant
    the step could be completed by recovering nothing at all — on a controlled
    form whose whole first section is the property being handed back.
    """
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)
    _require_step_role(
        actor=actor,
        clearance=clearance,
        step_number=1,
        required_role=custody_role(as_of=timezone.now().date()),
        request=request,
    )

    if not custody_items and standard_custody_items(as_of=timezone.now().date()):
        raise ValidationError(
            "قائمة العُهد فارغة — §6.4 تبدأ باسترجاع عُهد المركز، "
            "والقائمة المعيارية معرّفة في الإعدادات."
        )

    outstanding = [item for item in custody_items if not item.get("returned")]
    if outstanding:
        names = "، ".join(str(item.get("name_ar", "?")) for item in outstanding)
        raise ValidationError(f"عهدة غير مُسترجعة: {names}")

    return _complete_custody(
        actor=actor, clearance=clearance, custody_items=custody_items, request=request
    )


@transaction.atomic
def _complete_custody(
    *, actor: Any, clearance: Clearance, custody_items: list[dict[str, Any]], request: Any
) -> ClearanceStep:
    step = _step(clearance, 1)
    step.custody_items = custody_items
    step.certified_by = actor
    step.certified_at = timezone.now()
    step.is_done = True
    step.save()

    if clearance.status == ClearanceStatus.OPEN:
        clearance.status = ClearanceStatus.IN_PROGRESS
        clearance.save(update_fields=["status", "active_key"])

    write_audit(
        action="APPROVE",
        entity_type=STEP_ENTITY,
        entity_id=str(step.pk),
        reference=clearance.code,
        summary_ar=f"مصادقة الخطوة 1 — استرجاع {len(custody_items)} عهدة",
        actor=actor,
        changes={"event": "CERTIFY_STEP", "step": 1, "items": len(custody_items)},
        request=request,
    )
    return step


def deposit_settlement_state(enrollment: Enrollment) -> dict[str, Any]:
    """
    Q-01 — what the financial step must see about the deposit, if any.

    A programme with no deposit policy produces no deposit line, and then the
    whole question does not arise: the fields are not merely hidden, there is
    nothing to settle. Visibility follows the DATA, never a global flag
    (PERMISSIONS §3.7, ADR-014 revised).
    """
    from apps.billing.services import deposit_service

    line = deposit_service.deposit_line_for(enrollment)
    if line is None:
        return {"applies": False, "collected": ZERO, "settled": ZERO, "is_settled": True}

    collected = deposit_service.collected_on(line)
    settled = deposit_service.settled_amount(line)
    return {
        "applies": collected > ZERO,
        "collected": collected,
        "settled": settled,
        "is_settled": collected <= ZERO or settled > ZERO,
    }


def certify_finance_step(*, actor: Any, clearance: Clearance, request: Any = None) -> ClearanceStep:
    """
    Step 2, first signature — the finance officer (BR-074).

    Certifies but does NOT close: ``is_done`` waits for the second signature,
    which is what C-29 makes true at the database level too.
    """
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)
    _require_previous_done(clearance, FINANCE_STEP)

    enrollment = clearance.enrollment
    balance = get_account_state(enrollment).balance
    deposit = deposit_settlement_state(enrollment)

    # Sprint 8D-3 · client decision 2 — «لا يُمنح براءة ذمة ولا شهادة حتى
    # تُسدَّد الذمة القديمة». Checked BEFORE the balance, because an old debt
    # is the more specific reason and the participant deserves to be told the
    # real one. Only debts the balance cannot see reach here: a posted
    # opening balance is already a charge line and falls to BR-073 below.
    old_debt = opening_balance_service.unsettled_debt_for(enrollment.participant)
    if old_debt:
        total = sum((item.amount for item in old_debt), ZERO)
        _block_on_old_debt(actor=actor, clearance=clearance, debts=old_debt, request=request)
        raise ClearanceBlockedError(
            f"براءة الذمة موقوفة — ذمة قديمة غير مسدَّدة بمقدار {total} "
            f"على {len(old_debt)} رصيد افتتاحي (BR-073 · BR-094)."
        )

    if balance != ZERO:
        _block(actor=actor, clearance=clearance, balance=balance, request=request)
        raise ClearanceBlockedError(_balance_message(balance))

    if deposit["applies"] and not deposit["is_settled"]:
        raise DepositNotSettledError(
            f"لا تُغلق الخطوة المالية قبل تسوية التأمين — المقبوض "
            f"{deposit['collected']} بلا إعادة ولا مصادرة (Q-01 · BR-097)."
        )

    return _certify_finance(
        actor=actor, clearance=clearance, balance=balance, deposit=deposit, request=request
    )


@transaction.atomic
def _block_on_old_debt(*, actor: Any, clearance: Clearance, debts: list[Any], request: Any) -> None:
    """
    Park the clearance and say which debts did it.

    The codes are listed rather than summarised: the participant standing at
    the counter is entitled to know which balance to argue about, and a total
    on its own is unanswerable.
    """
    if clearance.status != ClearanceStatus.BLOCKED:
        clearance.status = ClearanceStatus.BLOCKED
        clearance.save(update_fields=["status", "active_key"])

    total = sum((item.amount for item in debts), ZERO)
    write_audit(
        action="DENIED_ATTEMPT",
        entity_type=ENTITY,
        entity_id=str(clearance.pk),
        reference=clearance.code,
        summary_ar=f"محاولة إغلاق الخطوة المالية وعلى المشارك ذمة قديمة {total}",
        actor=actor,
        denial_rule="BR-094",
        changes={
            "old_debt_total": str(total),
            "opening_balances": ", ".join(item.code for item in debts),
        },
        request=request,
    )


def _balance_message(balance: Decimal) -> str:
    """
    WORKFLOWS §6.5 — the two directions read differently on purpose.

    🐞 The demo printed the same negative number for "owes us" and "we owe
    them", which is how CLR-002 and CLR-003 ended up indistinguishable.
    """
    if balance > ZERO:
        return f"براءة الذمة موقوفة — عليه ذمة {balance} (BR-073)."
    return f"براءة الذمة موقوفة — رصيد دائن {-balance} يجب ردّه (BR-071 · BR-073)."


@transaction.atomic
def _block(*, actor: Any, clearance: Clearance, balance: Decimal, request: Any) -> None:
    """WORKFLOWS §6.3 C3 — the clearance is parked, visibly, with its reason."""
    if clearance.status != ClearanceStatus.BLOCKED:
        clearance.status = ClearanceStatus.BLOCKED
        clearance.save(update_fields=["status", "active_key"])

    write_audit(
        action="DENIED_ATTEMPT",
        entity_type=ENTITY,
        entity_id=str(clearance.pk),
        reference=clearance.code,
        summary_ar=f"محاولة إغلاق الخطوة المالية والرصيد {balance}",
        actor=actor,
        denial_rule="BR-073",
        changes={"balance": str(balance)},
        request=request,
    )


@transaction.atomic
def _certify_finance(
    *,
    actor: Any,
    clearance: Clearance,
    balance: Decimal,
    deposit: dict[str, Any],
    request: Any,
) -> ClearanceStep:
    step = _step(clearance, FINANCE_STEP)
    step.balance_at_check = balance
    step.certified_by = actor
    step.certified_at = timezone.now()
    if deposit["applies"]:
        step.deposit_return_amount = deposit["settled"]
    step.save()

    if clearance.status == ClearanceStatus.BLOCKED:
        clearance.status = ClearanceStatus.IN_PROGRESS
        clearance.save(update_fields=["status", "active_key"])

    write_audit(
        action="APPROVE",
        entity_type=STEP_ENTITY,
        entity_id=str(step.pk),
        reference=clearance.code,
        summary_ar=f"المصادقة المالية الأولى — الرصيد {balance}",
        actor=actor,
        changes={
            "event": "CERTIFY_STEP",
            "step": 2,
            "order": "first",
            "balance": str(balance),
            "deposit_applies": deposit["applies"],
        },
        request=request,
    )
    return step


def second_certify_finance_step(
    *, actor: Any, clearance: Clearance, request: Any = None
) -> ClearanceStep:
    """
    Step 2, second signature — the finance manager, and only them (BR-074).

    Two checks that look similar and are not: the ROLE must match
    ``clearance_second_certifier_role`` (seeded FINANCE_MANAGER), and the
    PERSON must differ from the first certifier (D-30). Either one alone
    leaves the control half-built — the same finance manager signing both
    lines would satisfy the role check and defeat the purpose.

    The role is configurable; the different-person rule is not. One is a
    question about the centre's org chart, the other is what makes this a
    control at all.
    """
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)

    step = _step(clearance, FINANCE_STEP)
    if step.certified_by_id is None:
        raise StepOutOfOrderError(
            "المصادقة الثانية بعد الأولى — لم يصادق الموظف المالي بعد (BR-074)."
        )
    if step.is_done:
        raise ValidationError("الخطوة المالية مغلقة سلفاً.")

    required_role = second_certifier_role(as_of=timezone.now().date())
    if getattr(actor, "role", None) != required_role:
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=STEP_ENTITY,
            entity_id=str(step.pk),
            reference=clearance.code,
            summary_ar=f"محاولة مصادقة ثانية بدور غير {required_role}",
            actor=actor,
            denial_rule="BR-074",
            changes={"role": getattr(actor, "role", None), "required_role": required_role},
            request=request,
        )
        raise SecondCertifierRoleError(
            f"المصادقة الثانية على الخطوة المالية للدور {required_role} حصراً "
            f"(BR-074 · Q-14 · الإعداد {SECOND_CERTIFIER_ROLE_KEY})."
        )

    # D-30 — refused here for a readable message; C-30 refuses it again at the
    # database, which is what holds if a caller skips this.
    assert_second_certifier_differs(step.certified_by_id, getattr(actor, "pk", None))

    # WORKFLOWS §6.7 — the balance may have moved since the first signature.
    balance = get_account_state(clearance.enrollment).balance
    if balance != ZERO:
        _block(actor=actor, clearance=clearance, balance=balance, request=request)
        raise ClearanceBlockedError(_balance_message(balance))

    return _second_certify(actor=actor, clearance=clearance, balance=balance, request=request)


@transaction.atomic
def _second_certify(
    *, actor: Any, clearance: Clearance, balance: Decimal, request: Any
) -> ClearanceStep:
    step = _step(clearance, FINANCE_STEP)
    step.balance_at_check = balance
    step.second_certified_by = actor
    step.second_certified_at = timezone.now()
    step.is_done = True
    step.save()

    write_audit(
        action="APPROVE",
        entity_type=STEP_ENTITY,
        entity_id=str(step.pk),
        reference=clearance.code,
        summary_ar=f"المصادقة المالية الثانية — إغلاق الخطوة برصيد {balance}",
        actor=actor,
        changes={
            "event": "CERTIFY_STEP",
            "step": 2,
            "order": "second",
            "balance": str(balance),
            "first_certifier": step.certified_by_id,
        },
        request=request,
    )
    return step


class ParticipantAcknowledgementRequiredError(ValidationError):
    """§6.4 step 3 — a handover recorded without the receiver's name."""


def complete_handover_step(
    *,
    actor: Any,
    clearance: Clearance,
    participant_ack_name: str = "",
    request: Any = None,
) -> ClearanceStep:
    """
    Step 3 — the certificate changes hands (§6.4: «المركز — تسليم الشهادة»).

    The centre's step, like step 1, and gated the same way.

    §6.4 asks for TWO signatures here — «توقيع المشارك ومدير المركز». The
    manager's is ``certified_by``; the participant's is recorded by name,
    because a handover attested only by the centre proves that the centre says
    it happened. Refused in the service rather than by a CHECK constraint: a
    constraint has to stay true for rows written years ago (DATA_MODEL §7.7),
    and clearances completed before Sprint 8C-1 carry no acknowledgement.
    """
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)

    if not participant_ack_name.strip():
        raise ParticipantAcknowledgementRequiredError(
            "تسليم الشهادة يحتاج إقرار المشارك باسمه — §6.4 تطلب توقيع المشارك ومدير المركز معاً."
        )
    _require_step_role(
        actor=actor,
        clearance=clearance,
        step_number=3,
        required_role=handover_role(as_of=timezone.now().date()),
        request=request,
    )
    _require_previous_done(clearance, 3)
    return _complete_handover(
        actor=actor,
        clearance=clearance,
        participant_ack_name=participant_ack_name.strip(),
        request=request,
    )


@transaction.atomic
def _complete_handover(
    *, actor: Any, clearance: Clearance, participant_ack_name: str, request: Any
) -> ClearanceStep:
    step = _step(clearance, 3)
    step.certified_by = actor
    step.certified_at = timezone.now()
    step.participant_ack_name = participant_ack_name
    step.participant_ack_at = timezone.now()
    step.is_done = True
    step.save()

    write_audit(
        action="APPROVE",
        entity_type=STEP_ENTITY,
        entity_id=str(step.pk),
        reference=clearance.code,
        summary_ar=f"مصادقة الخطوة 3 — تسليم الشهادة إلى {participant_ack_name}",
        actor=actor,
        changes={
            "event": "CERTIFY_STEP",
            "step": 3,
            "participant_ack_name": participant_ack_name,
        },
        request=request,
    )
    return step


def close_clearance(*, actor: Any, clearance: Clearance, request: Any = None) -> Clearance:
    """
    WORKFLOWS §6.3 C5 — close it, having checked the money AGAIN.

    §6.7 names the failure this guards: the balance moving between step 2's
    certification and the close. Trusting the number captured earlier would
    complete a clearance over a live balance, and BR-075 would then let a
    certificate out to someone who owes money.
    """
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)

    missing = [s.step_number for s in clearance.steps.order_by("step_number") if not s.is_done]
    if missing:
        raise StepOutOfOrderError(f"لا تُغلق البراءة وخطواتها غير مكتملة: {missing} (BR-072).")

    balance = get_account_state(clearance.enrollment).balance
    if balance != ZERO:
        _block(actor=actor, clearance=clearance, balance=balance, request=request)
        raise ClearanceBlockedError(
            f"تغيّر الرصيد بعد المصادقة المالية — {_balance_message(balance)}"
        )

    return _close(actor=actor, clearance=clearance, request=request)


@transaction.atomic
def _close(*, actor: Any, clearance: Clearance, request: Any) -> Clearance:
    clearance.status = ClearanceStatus.COMPLETED
    clearance.completed_at = timezone.now()
    clearance.save(update_fields=["status", "completed_at", "active_key"])

    write_audit(
        action="APPROVE",
        entity_type=ENTITY,
        entity_id=str(clearance.pk),
        reference=clearance.code,
        summary_ar=f"إغلاق براءة الذمة — خطواتها ({clearance.steps.count()}) مكتملة",
        actor=actor,
        changes={"balance_rechecked": "0.000"},
        request=request,
    )
    return clearance


def cancel_clearance(
    *, actor: Any, clearance: Clearance, reason_ar: str, request: Any = None
) -> Clearance:
    """WORKFLOWS §6.3 C6 — cancelled, with a reason, and freeing the enrolment."""
    policy.require(actor, Screen.CLEARANCE, Action.APPROVE, request=request)

    if clearance.status == ClearanceStatus.COMPLETED:
        raise ValidationError("لا تُلغى براءة مكتملة.")
    if not reason_ar.strip():
        raise ValidationError("سبب الإلغاء إلزامي.")
    return _cancel(actor=actor, clearance=clearance, reason_ar=reason_ar.strip(), request=request)


@transaction.atomic
def _cancel(*, actor: Any, clearance: Clearance, reason_ar: str, request: Any) -> Clearance:
    clearance.status = ClearanceStatus.CANCELLED
    clearance.cancellation_reason_ar = reason_ar
    clearance.save(update_fields=["status", "cancellation_reason_ar", "active_key"])

    write_audit(
        action="REJECT",
        entity_type=ENTITY,
        entity_id=str(clearance.pk),
        reference=clearance.code,
        summary_ar=f"إلغاء براءة الذمة — {reason_ar}",
        actor=actor,
        changes={"reason": reason_ar},
        request=request,
    )
    return clearance


def return_credit_at_clearance(
    *,
    actor: Any,
    clearance: Clearance,
    returned_on: date,
    code: str,
    request: Any = None,
) -> Any:
    """
    BR-071 — hand the credit back so step 2 can close.

    ⚠️ **ASSUMPTION.** No approver is named in the documents for this. It runs
    inside step 2, whose dual certification (BR-074, D-30) is the control that
    already exists. Recorded as a professional reading, not a client decision.
    """
    from apps.billing.services import credit_service

    _require_previous_done(clearance, FINANCE_STEP)
    step = _step(clearance, FINANCE_STEP)
    if step.is_done:
        raise ValidationError("الخطوة المالية مغلقة — لا يُعدَّل ما صودق عليه.")

    record = credit_service.return_credit(
        actor=actor,
        enrollment=clearance.enrollment,
        returned_on=returned_on,
        reason_ar=f"ردّ رصيد دائن عند براءة الذمة {clearance.code} (BR-071)",
        code=code,
        request=request,
    )
    step.credit_return = record
    step.save(update_fields=["credit_return"])
    return record


def list_clearances(
    *, actor: Any, status: str = "", query: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Clearances as rows, each showing which step it is waiting on."""
    policy.require(actor, Screen.CLEARANCE, Action.VIEW, request=request)

    queryset = Clearance.objects.select_related("participant", "enrollment__cohort__program")
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(code__icontains=query) | queryset.filter(
            participant__name_ar__icontains=query
        )

    rows: list[dict[str, Any]] = []
    for clearance in queryset.order_by("-opened_on", "-id"):
        steps = list(clearance.steps.order_by("step_number"))
        pending = next((s.step_number for s in steps if not s.is_done), None)
        rows.append(
            {
                "code": clearance.code,
                "participant_name": clearance.participant.name_ar,
                "participant_number": clearance.participant.participant_number,
                "enrollment_code": clearance.enrollment.code,
                "program_name": clearance.enrollment.cohort.program.name_ar,
                "case_type": clearance.case_type,
                "case_type_display": clearance.get_case_type_display(),
                "opened_on": clearance.opened_on,
                "status": clearance.status,
                "status_display": clearance.get_status_display(),
                "pending_step": pending,
                "pending_step_name": STEP_NAMES.get(pending, "") if pending else "",
                "is_completed": clearance.status == ClearanceStatus.COMPLETED,
            }
        )
    return rows


def get_clearance(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """
    One clearance with its three steps and the money as it stands NOW.

    The balance is read live rather than from ``balance_at_check``: WORKFLOWS
    §6.7 is about exactly this, a balance moving after step 2 was certified.
    Showing the captured figure would tell the operator the account is settled
    when it may no longer be.
    """
    from apps.core.display import person_name

    policy.require(actor, Screen.CLEARANCE, Action.VIEW, request=request)

    clearance = Clearance.objects.select_related(
        "participant", "enrollment__cohort__program", "opened_by"
    ).get(code=code)

    state = get_account_state(clearance.enrollment)
    deposit = deposit_settlement_state(clearance.enrollment)
    as_of = timezone.now().date()

    steps = [
        {
            "step_number": step.step_number,
            "name_ar": step.name_ar,
            "is_done": step.is_done,
            "custody_items": step.custody_items,
            "balance_at_check": step.balance_at_check,
            "deposit_return_amount": step.deposit_return_amount,
            "certified_by": person_name(step.certified_by),
            "certified_by_id": step.certified_by_id,
            "certified_at": step.certified_at,
            "second_certified_by": person_name(step.second_certified_by),
            "second_certified_at": step.second_certified_at,
            # §6.4 step 3's other signature — the participant's own.
            "participant_ack_name": step.participant_ack_name,
            "participant_ack_at": step.participant_ack_at,
            "has_credit_return": step.credit_return_id is not None,
        }
        for step in clearance.steps.order_by("step_number")
    ]
    pending = next((s["step_number"] for s in steps if not s["is_done"]), None)

    return {
        "code": clearance.code,
        "participant_name": clearance.participant.name_ar,
        "participant_number": clearance.participant.participant_number,
        "enrollment_code": clearance.enrollment.code,
        "program_name": clearance.enrollment.cohort.program.name_ar,
        "case_type_display": clearance.get_case_type_display(),
        "opened_on": clearance.opened_on,
        "opened_by": person_name(clearance.opened_by),
        "status": clearance.status,
        "status_display": clearance.get_status_display(),
        "cancellation_reason_ar": clearance.cancellation_reason_ar,
        "completed_at": clearance.completed_at,
        "steps": steps,
        "pending_step": pending,
        "balance": state.balance,
        "participant_owes": state.participant_owes,
        "centre_owes": state.centre_owes,
        "is_settled": state.is_settled,
        "credit_outstanding": -state.balance if state.centre_owes else ZERO,
        "deposit": deposit,
        # The roles §6.4 assigns, so the screen can say whose turn it is.
        "custody_role": custody_role(as_of=as_of),
        "handover_role": handover_role(as_of=as_of),
        "second_certifier_role": second_certifier_role(as_of=as_of),
    }


def clearance_instance(*, actor: Any, code: str, request: Any = None) -> Clearance:
    """The Clearance object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.CLEARANCE, Action.VIEW, request=request)
    return Clearance.objects.select_related("enrollment__participant").get(code=code)


def clearable_enrollment_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    Enrolments a clearance may be opened for.

    §6.4 — «عند انتهاء الدورة (أو الانسحاب أو الفصل)». An enrolment still
    running has nothing to clear, and one that already carries a live
    clearance would be refused, so neither is offered. The label names the
    case the status will open, since the operator no longer chooses it.
    """
    policy.require(actor, Screen.CLEARANCE, Action.CREATE, request=request)

    busy = set(Clearance.objects.filter(active_key=1).values_list("enrollment_id", flat=True))
    return [
        (
            e.code,
            f"{e.code} — {e.participant.name_ar} ({e.get_status_display()} ← "
            f"{ClearanceCaseType(CLEARANCE_CASE_FOR_STATUS[e.status]).label})",
        )
        for e in Enrollment.objects.select_related("participant")
        .filter(status__in=CLEARANCE_CASE_FOR_STATUS)
        .order_by("-enrolled_on")
        if e.pk not in busy
    ]


def standard_custody_items(*, as_of: date) -> list[str]:
    """
    The centre's standard custody list, from settings (§6.4).

    «هوية المركز + بطاقة المواصلات» are what the requirement names; the list
    is data because a centre that starts issuing parking permits should not
    need a deployment.
    """
    from apps.core.services import document_settings

    return document_settings.custody_items(as_of=as_of)


def clearance_document(*, actor: Any, code: str, request: Any = None) -> dict[str, Any]:
    """
    Everything the printed clearance form shows (§6.4).

    ⚠️ Built from the REQUIREMENTS, not from the centre's blank form — which
    was not in the client folder. ``document_settings.chrome`` carries the
    marker that says so, and the template prints it. Nothing here claims the
    layout matches ``CS Fm 7.18 Rev A``; it claims to carry what §6.4 says the
    form records.

    The balance is read LIVE, as on the screen: WORKFLOWS §6.7 is about a
    balance moving after certification, and a document printed from the
    captured figure could assert a settled account that no longer is.
    """
    from apps.core.services import document_settings

    detail = get_clearance(actor=actor, code=code, request=request)
    as_of = timezone.now().date()
    detail["chrome"] = document_settings.chrome(as_of=as_of)
    detail["labels"] = document_settings.clearance_labels(as_of=as_of)
    detail["standard_custody_items"] = standard_custody_items(as_of=as_of)
    return detail


__all__ = [
    "CLEARANCE_CASE_FOR_STATUS",
    "CUSTODY_ROLE_KEY",
    "FINANCE_STEP",
    "HANDOVER_ROLE_KEY",
    "SECOND_CERTIFIER_ROLE_KEY",
    "STEP_NAMES",
    "ClearanceBlockedError",
    "DepositNotSettledError",
    "EnrollmentNotClearableError",
    "ParticipantAcknowledgementRequiredError",
    "SecondCertifierRoleError",
    "StepOutOfOrderError",
    "StepRoleError",
    "cancel_clearance",
    "certify_finance_step",
    "clearable_enrollment_choices",
    "clearance_case_for_enrollment",
    "clearance_document",
    "clearance_instance",
    "close_clearance",
    "complete_custody_step",
    "complete_handover_step",
    "custody_role",
    "deposit_settlement_state",
    "get_clearance",
    "handover_role",
    "list_clearances",
    "open_clearance",
    "opening_preview",
    "return_credit_at_clearance",
    "second_certifier_role",
    "second_certify_finance_step",
    "standard_custody_items",
]
