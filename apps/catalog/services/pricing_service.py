"""
Price resolution — the one place that answers "what does this cost?".

Given a programme, a participant category, a level and a date, this returns the
course fee, the registration fee, and whether a deposit applies. Sprint 4 turns
that answer into charge lines; Sprint 6 attaches it to an enrolment. Both call
here rather than reading the tables, so there is a single interpretation of the
price list to be right or wrong about.

Three rules shape it:

* **BR-012 — the list in force on the enrolment date.** Amounts are read from
  the price list effective on the day, then COPIED. Issuing a new list must not
  move an existing registration's numbers.
* **BR-009 — most-specific-wins.** A rule for ``(program, category)`` beats the
  general ``(NULL, category)``. Exceptions are rows, so the client's answer to
  Q-10 is data entry.
* **BR-096 — no policy, no deposit.** A programme without a deposit policy has
  no deposit line at all. Absent is not zero.

``None`` and ``0`` are different answers throughout and are never collapsed:
``fee = None`` means no registration fee is charged (JCPA, PMP), ``fee = 0``
would mean one is charged at zero. The demo lost that distinction in a pair of
columns; this module keeps it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from apps.catalog.models import (
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
    RegistrationFeeRule,
)


class NoEffectivePriceListError(Exception):
    """No approved price list covers the requested date."""


class ProgramNotPricedError(Exception):
    """The programme has no item on the effective list (at that level)."""


class LevelRequiredError(Exception):
    """BR-011 — a levelled programme was priced without a level."""


@dataclass(frozen=True)
class PriceQuote:
    """
    A resolved price, ready to be copied onto charge lines (BR-012).

    Everything here is a value, not a live reference — except the ids, which
    record WHICH list and policy produced it so the decision stays auditable.
    """

    price_list_id: int
    price_list_code: str
    program_id: int
    level: int | None

    course_fee: Decimal
    #: None means NO registration fee applies — distinct from a zero fee.
    registration_fee: Decimal | None
    registration_fee_note: str

    #: None means the programme carries no deposit at all (BR-096).
    deposit_amount: Decimal | None
    deposit_policy_id: int | None
    deposit_policy_code: str

    @property
    def has_deposit(self) -> bool:
        return self.deposit_amount is not None

    @property
    def charges_registration_fee(self) -> bool:
        return self.registration_fee is not None


def effective_price_list(*, as_of: date) -> PriceList:
    """
    The approved list in force on ``as_of`` (BR-008, BR-012).

    Archived lists stay queryable — an old enrolment must still be explainable
    — but only an APPROVED list may price a NEW registration.
    """
    price_list = (
        PriceList.objects.filter(status=PriceListStatus.APPROVED, effective_from__lte=as_of)
        .order_by("-effective_from", "-id")
        .first()
    )
    if price_list is None:
        raise NoEffectivePriceListError(
            f"لا توجد قائمة أسعار معتمدة سارية بتاريخ {as_of}. "
            "يجب اعتماد قائمة قبل التسعير (BR-008)."
        )
    return price_list


def resolve_registration_fee(
    *, program: Program, participant_category: str, price_list: PriceList
) -> tuple[Decimal | None, str]:
    """
    The registration fee for this programme and category (BR-009, BR-010).

    Returns ``(fee, note)`` where ``fee is None`` means none is charged.
    Most-specific-wins: a programme rule beats the general category rule.
    """
    # BR-010 — online courses never carry a registration fee. Structural, not
    # an exception row: the whole amount goes into the partner's base.
    if program.program_type == ProgramType.ONLINE_COURSE:
        return None, "دورة أونلاين — لا رسوم تسجيل (BR-010)"

    rules = RegistrationFeeRule.objects.filter(
        price_list=price_list, participant_category=participant_category
    ).filter(models_q_program(program))

    # program_key descending puts the specific rule (its id) before the
    # general one (0), so "most specific wins" is the ordering, not an if.
    rule = rules.order_by("-program_key").first()
    if rule is None:
        raise ProgramNotPricedError(
            f"لا توجد قاعدة رسم تسجيل للفئة {participant_category} "
            f"على القائمة {price_list.code} — لا عامة ولا خاصة بالبرنامج."
        )
    return rule.fee, rule.exception_note_ar


def models_q_program(program: Program) -> Any:
    """Rules that apply to this programme: its own, or the general one."""
    from django.db.models import Q

    return Q(program=program) | Q(program__isnull=True)


def resolve_price(
    *,
    program: Program,
    participant_category: str,
    as_of: date,
    level: int | None = None,
    price_list: PriceList | None = None,
) -> PriceQuote:
    """
    The full quote for one registration.

    ``price_list`` may be passed to re-explain a historical enrolment against
    the list it actually used; omitted, the list in force on ``as_of`` is used.
    """
    price_list = price_list or effective_price_list(as_of=as_of)

    # BR-011 — a levelled programme priced without a level would silently
    # return the first level's fee, which is how someone pays 90 for a course
    # that costs 720, or the reverse.
    if program.is_leveled and level is None:
        raise LevelRequiredError(
            f"البرنامج «{program.name_ar}» ذو مستويات — يجب تحديد المستوى قبل التسعير (BR-011)."
        )
    if not program.is_leveled and level is not None:
        raise LevelRequiredError(f"البرنامج «{program.name_ar}» بلا مستويات — لا يُمرَّر مستوى.")

    item = (
        PriceListItem.objects.select_related("deposit_policy")
        .filter(price_list=price_list, program=program, level_key=level or 0)
        .first()
    )
    if item is None:
        at_level = f" (المستوى {level})" if level else ""
        raise ProgramNotPricedError(
            f"البرنامج «{program.name_ar}»{at_level} غير مُسعَّر على القائمة {price_list.code}."
        )

    fee, note = resolve_registration_fee(
        program=program, participant_category=participant_category, price_list=price_list
    )

    return PriceQuote(
        price_list_id=price_list.pk,
        price_list_code=price_list.code,
        program_id=program.pk,
        level=level,
        course_fee=item.course_fee,
        registration_fee=fee,
        registration_fee_note=note,
        # BR-096 — the pair is enforced by C-26, so one being set means both are.
        deposit_amount=item.deposit_amount,
        deposit_policy_id=item.deposit_policy_id,
        deposit_policy_code=item.deposit_policy.code if item.deposit_policy else "",
    )


def subject_price_total(program: Program) -> Decimal:
    """Σ subject prices — the left side of BR-006."""
    from django.db.models import Sum

    total = program.subjects.aggregate(total=Sum("price"))["total"]
    return total if total is not None else Decimal("0.000")


__all__ = [
    "LevelRequiredError",
    "NoEffectivePriceListError",
    "PriceQuote",
    "ProgramNotPricedError",
    "effective_price_list",
    "resolve_price",
    "resolve_registration_fee",
    "subject_price_total",
]
