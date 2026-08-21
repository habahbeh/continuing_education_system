"""
Trainer absences, their penalty and its exception (تناغم بند 13, BR-057/058).

The signed clause, in full, because every word of it is load-bearing:

    «يغرم الفريق الثاني ما يعادل **ثلاثة أضعاف نفقات المحاضرة** التي يغيب عنها
    مدرسوه **(ما عدا الحالات الطارئة والتي يوافق عليها الفريق الأول خطياً)**
    وفي حال تكرر الغياب **لأكثر من أربع محاضرات** يحق للفريق الأول استبدال
    المدرس بمدرس آخر من قبله ويلتزم الفريق الثاني باعطائه مكافأة بالغاً ما بلغت
    أجوره.»

Three rules, and two places where the demo and the requirements drift from it:

* **the multiplier** — three times the lecture's cost, from
  ``trainer_absence_multiplier``. Seeded in Sprint 1 and, until now, read by
  nothing at all.
* **the exception** — an emergency absence approved IN WRITING is neither
  fined nor counted. It needs a per-absence record, which is why
  ``TrainerAbsence`` exists; a counter would erase the excused lecture
  entirely.
* **the threshold** — replacement when absences exceed four. «لأكثر من أربع»
  is a STRICT comparison: the alert fires on the fifth. The demo says «بعد 4
  غيابات» and requirements.md echoes it, but the signed agreement outranks
  both, and the difference is one lecture's worth of a replacement trainer's
  fee.

The replacement trainer's own fee is NOT computed here. «بالغاً ما بلغت أجوره»
is a negotiated figure, not a formula, and it is recorded as a TRAINER_SALARIES
obligation once someone knows what it came to.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.services.account_service import ZERO
from apps.core.display import person_name, text_of
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.models import ObligationType, PartnerObligation, TrainerAbsence

ENTITY = "settlements.TrainerAbsence"
OBLIGATION_ENTITY = "settlements.PartnerObligation"

MULTIPLIER_KEY = "trainer_absence_multiplier"
REPLACE_LIMIT_KEY = "trainer_absence_replace_limit"


class WaiverApprovalRequiredError(ValidationError):
    """The exception exists only in writing — «يوافق عليها الفريق الأول خطياً»."""


class NoPenaltyDueError(ValidationError):
    """Every absence in scope is either waived or already penalised."""


class LectureCostMissingError(ValidationError):
    """BR-057 — a penalty is a formula, and one input is not on the cohort."""


def multiplier(*, as_of: date) -> int:
    """Three, per the signed clause — but read from the setting."""
    return int(get_setting(MULTIPLIER_KEY, as_of=as_of, default=3))


def replace_limit(*, as_of: date) -> int:
    """Four. Compared STRICTLY — see the module docstring."""
    return int(get_setting(REPLACE_LIMIT_KEY, as_of=as_of, default=4))


def record_absence(
    *,
    actor: Any,
    cohort: Any,
    trainer_name: str,
    occurred_on: date,
    is_waived: bool = False,
    waiver_approval_ref: str = "",
    waiver_approval_date: date | None = None,
    note_ar: str = "",
    request: Any = None,
) -> TrainerAbsence:
    """
    Record one missed lecture, waived or not.

    A waiver without its written approval is refused here for a readable
    message and by ``settlements_absence_waiver_has_approval`` for everything
    else — which is the clause's own condition, not an extra one.
    """
    policy.require(actor, Screen.OBLIGATIONS, Action.CREATE, request=request)

    if not trainer_name.strip():
        raise ValidationError("اسم المدرب إلزامي.")

    if is_waived and (not waiver_approval_ref.strip() or waiver_approval_date is None):
        raise WaiverApprovalRequiredError(
            "إعفاء الغياب يحتاج مرجع الموافقة الخطية وتاريخها — "
            "«ما عدا الحالات الطارئة والتي يوافق عليها الفريق الأول خطياً» (تناغم بند 13)."
        )

    if TrainerAbsence.objects.filter(
        cohort=cohort, trainer_name=trainer_name.strip(), occurred_on=occurred_on
    ).exists():
        raise ValidationError(
            f"غياب {trainer_name} في {occurred_on} مسجَّل سلفاً على الدفعة {cohort.code} — "
            "المحاضرة الواحدة لا تُغرَّم مرتين."
        )

    return _record_absence(
        actor=actor,
        cohort=cohort,
        trainer_name=trainer_name.strip(),
        occurred_on=occurred_on,
        is_waived=is_waived,
        waiver_approval_ref=waiver_approval_ref.strip(),
        waiver_approval_date=waiver_approval_date,
        note_ar=note_ar.strip(),
        request=request,
    )


@transaction.atomic
def _record_absence(
    *,
    actor: Any,
    cohort: Any,
    trainer_name: str,
    occurred_on: date,
    is_waived: bool,
    waiver_approval_ref: str,
    waiver_approval_date: date | None,
    note_ar: str,
    request: Any,
) -> TrainerAbsence:
    absence = TrainerAbsence.objects.create(
        cohort=cohort,
        trainer_name=trainer_name,
        occurred_on=occurred_on,
        is_waived=is_waived,
        waiver_approval_ref=waiver_approval_ref,
        waiver_approval_date=waiver_approval_date,
        note_ar=note_ar,
        recorded_by=actor,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(absence.pk),
        reference=cohort.code,
        summary_ar=(
            f"تسجيل غياب {trainer_name} في {occurred_on}"
            + ("  — معفى بموافقة خطية" if is_waived else "")
        ),
        actor=actor,
        changes={
            "trainer_name": trainer_name,
            "occurred_on": occurred_on.isoformat(),
            "is_waived": is_waived,
            "waiver_approval_ref": waiver_approval_ref,
            "cohort": cohort.code,
        },
        request=request,
    )
    return absence


def waive_absence(
    *,
    actor: Any,
    absence: TrainerAbsence,
    approval_ref: str,
    approval_date: date,
    request: Any = None,
) -> TrainerAbsence:
    """
    Excuse an absence already recorded — emergencies are often known later.

    An absence already covered by a raised penalty is NOT waivable: the
    obligation exists, may already have been offset against a claim, and
    unpicking it silently would restate what a partner was paid. Waiving the
    obligation itself is a separate, visible act (``ObligationStatus.WAIVED``).
    """
    policy.require(actor, Screen.OBLIGATIONS, Action.EDIT, request=request)

    if absence.is_waived:
        raise ValidationError("الغياب معفى سلفاً.")
    if absence.obligation_id is not None:
        raise ValidationError(
            f"الغياب مشمول بغرامة مرفوعة ({text_of(absence.obligation, 'code')}) — "
            "يُعالَج بالتنازل عن الالتزام لا بإعفاء الغياب."
        )
    if not approval_ref.strip():
        raise WaiverApprovalRequiredError("مرجع الموافقة الخطية إلزامي (تناغم بند 13).")

    return _waive(
        actor=actor,
        absence=absence,
        approval_ref=approval_ref.strip(),
        approval_date=approval_date,
        request=request,
    )


@transaction.atomic
def _waive(
    *,
    actor: Any,
    absence: TrainerAbsence,
    approval_ref: str,
    approval_date: date,
    request: Any,
) -> TrainerAbsence:
    absence.is_waived = True
    absence.waiver_approval_ref = approval_ref
    absence.waiver_approval_date = approval_date
    absence.save(update_fields=["is_waived", "waiver_approval_ref", "waiver_approval_date"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(absence.pk),
        reference=absence.cohort.code,
        summary_ar=f"إعفاء غياب {absence.trainer_name} في {absence.occurred_on} بموافقة خطية",
        actor=actor,
        changes={
            "is_waived": True,
            "waiver_approval_ref": approval_ref,
            "waiver_approval_date": approval_date.isoformat(),
        },
        request=request,
    )
    return absence


def penalisable(cohort: Any, trainer_name: str) -> Any:
    """Absences that are neither waived nor already covered by a penalty."""
    return TrainerAbsence.objects.filter(
        cohort=cohort, trainer_name=trainer_name, is_waived=False, obligation__isnull=True
    ).order_by("occurred_on")


def countable(cohort: Any, trainer_name: str) -> int:
    """
    How many absences count toward replacement.

    Waived ones are excluded — the clause excuses them from the fine, and an
    excused lecture cannot also be evidence for removing the trainer.
    Penalised ones ARE counted: they happened.
    """
    return TrainerAbsence.objects.filter(
        cohort=cohort, trainer_name=trainer_name, is_waived=False
    ).count()


def replacement_is_due(*, cohort: Any, trainer_name: str, as_of: date) -> bool:
    """
    «في حال تكرر الغياب لأكثر من أربع محاضرات» — STRICTLY more than.

    Four absences do not trigger it; the fifth does. The demo's «بعد 4
    غيابات» reads as the fourth, and the signed agreement outranks the demo.
    """
    return countable(cohort, trainer_name) > replace_limit(as_of=as_of)


def raise_penalty(
    *,
    actor: Any,
    cohort: Any,
    trainer_name: str,
    occurred_on: date,
    code: str,
    request: Any = None,
) -> PartnerObligation:
    """
    Turn the outstanding absences into one penalty obligation (BR-057).

    ``amount = multiplier × lecture_cost × count``, and all three inputs are
    stamped on the obligation because ``settlements_obligation_penalty_has_inputs``
    refuses a penalty that cannot show its working — "a penalty without its
    inputs cannot be defended to the partner".
    """
    policy.require(actor, Screen.OBLIGATIONS, Action.CREATE, request=request)

    agreement = getattr(cohort, "agreement", None)
    if agreement is None:
        raise ValidationError(f"الدفعة {cohort.code} بلا اتفاقية — لا شريك تُقيَّد عليه الغرامة.")

    lecture_cost = cohort.lecture_cost
    if lecture_cost is None or lecture_cost <= ZERO:
        raise LectureCostMissingError(
            f"كلفة المحاضرة غير محدَّدة على الدفعة {cohort.code} — "
            "الغرامة ثلاثة أضعافها ولا تُحتسب بدونها (BR-057)."
        )

    absences = list(penalisable(cohort, trainer_name))
    if not absences:
        raise NoPenaltyDueError(
            f"لا غيابات قابلة للتغريم للمدرب {trainer_name} على الدفعة {cohort.code} — "
            "إمّا معفاة بموافقة خطية أو مشمولة بغرامة سابقة."
        )
    if PartnerObligation.objects.filter(code=code).exists():
        raise ValidationError(f"رمز الالتزام {code} مستعمل سلفاً.")

    return _raise_penalty(
        actor=actor,
        cohort=cohort,
        agreement=agreement,
        trainer_name=trainer_name,
        absences=absences,
        lecture_cost=lecture_cost,
        occurred_on=occurred_on,
        code=code,
        request=request,
    )


@transaction.atomic
def _raise_penalty(
    *,
    actor: Any,
    cohort: Any,
    agreement: Any,
    trainer_name: str,
    absences: list[TrainerAbsence],
    lecture_cost: Decimal,
    occurred_on: date,
    code: str,
    request: Any,
) -> PartnerObligation:
    from apps.core.money import round_money

    factor = multiplier(as_of=occurred_on)
    count = len(absences)
    amount = round_money(lecture_cost * factor * count)

    obligation = PartnerObligation.objects.create(
        code=code,
        partner=agreement.partner,
        # Pinned: the absences happened on this cohort under this contract.
        restricted_to_agreement=agreement,
        obligation_type=ObligationType.TRAINER_ABSENCE_PENALTY,
        cohort=cohort,
        amount=amount,
        absence_count=count,
        lecture_cost=lecture_cost,
        multiplier=factor,
        statement_reference=(
            f"غرامة غياب {trainer_name} — {count} محاضرة × {lecture_cost} × {factor} (تناغم بند 13)"
        ),
        occurred_on=occurred_on,
        created_by=actor,
    )
    TrainerAbsence.objects.filter(pk__in=[a.pk for a in absences]).update(obligation=obligation)

    write_audit(
        action="CREATE",
        entity_type=OBLIGATION_ENTITY,
        entity_id=str(obligation.pk),
        reference=code,
        summary_ar=f"غرامة غياب مدرب {amount} — {count} محاضرة",
        actor=actor,
        changes={
            "trainer_name": trainer_name,
            "absence_count": count,
            "lecture_cost": str(lecture_cost),
            "multiplier": factor,
            "amount": str(amount),
            "absences": [a.occurred_on.isoformat() for a in absences],
            "cohort": cohort.code,
        },
        request=request,
    )
    return obligation


# ---------------------------------------------------------------------------
# Reads (A-05)
# ---------------------------------------------------------------------------
def list_absences(
    *, actor: Any, cohort_code: str = "", request: Any = None
) -> list[dict[str, Any]]:
    """Absences as rows, each showing whether it counts and why."""
    policy.require(actor, Screen.OBLIGATIONS, Action.VIEW, request=request)

    queryset = TrainerAbsence.objects.select_related("cohort", "obligation", "recorded_by")
    if cohort_code:
        queryset = queryset.filter(cohort__code=cohort_code)

    return [
        {
            "id": a.pk,
            "cohort_code": a.cohort.code,
            "trainer_name": a.trainer_name,
            "occurred_on": a.occurred_on,
            "is_waived": a.is_waived,
            "counts_toward_penalty": a.counts_toward_penalty,
            "waiver_approval_ref": a.waiver_approval_ref,
            "waiver_approval_date": a.waiver_approval_date,
            "obligation_code": text_of(a.obligation, "code"),
            "note_ar": a.note_ar,
            "recorded_by": person_name(a.recorded_by),
        }
        for a in queryset.order_by("-occurred_on", "-id")
    ]


def replacement_alerts(*, actor: Any, as_of: date, request: Any = None) -> list[dict[str, Any]]:
    """
    Trainers whose absences now exceed the limit (BR-058).

    An alert, not an action: replacing a trainer is a decision the centre
    makes with the partner, and the replacement's fee is negotiated. The
    system says the threshold was crossed and stops there.
    """
    policy.require(actor, Screen.OBLIGATIONS, Action.VIEW, request=request)

    limit = replace_limit(as_of=as_of)
    seen: dict[tuple[int, str], int] = {}
    for absence in TrainerAbsence.objects.filter(is_waived=False).select_related("cohort"):
        key = (absence.cohort_id, absence.trainer_name)
        seen[key] = seen.get(key, 0) + 1

    from apps.operations.models import Cohort

    alerts: list[dict[str, Any]] = []
    for (cohort_id, trainer_name), count in seen.items():
        if count <= limit:
            continue
        cohort = Cohort.objects.get(pk=cohort_id)
        alerts.append(
            {
                "cohort_code": cohort.code,
                "cohort_name": cohort.name_ar,
                "trainer_name": trainer_name,
                "absence_count": count,
                "limit": limit,
            }
        )
    return sorted(alerts, key=lambda a: (-a["absence_count"], a["cohort_code"]))


def absence_instance(*, actor: Any, absence_id: int, request: Any = None) -> TrainerAbsence:
    """The TrainerAbsence object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.OBLIGATIONS, Action.VIEW, request=request)
    return TrainerAbsence.objects.select_related("cohort", "obligation").get(pk=absence_id)


def absent_trainer_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """(cohort_code|trainer, label) pairs with outstanding penalisable absences."""
    policy.require(actor, Screen.OBLIGATIONS, Action.VIEW, request=request)

    pairs: dict[str, str] = {}
    for absence in TrainerAbsence.objects.filter(
        is_waived=False, obligation__isnull=True
    ).select_related("cohort"):
        key = f"{absence.cohort.code}|{absence.trainer_name}"
        pairs[key] = f"{absence.cohort.code} — {absence.trainer_name}"
    return sorted(pairs.items())


__all__ = [
    "LectureCostMissingError",
    "NoPenaltyDueError",
    "WaiverApprovalRequiredError",
    "absence_instance",
    "absent_trainer_choices",
    "countable",
    "list_absences",
    "multiplier",
    "penalisable",
    "raise_penalty",
    "record_absence",
    "replace_limit",
    "replacement_alerts",
    "replacement_is_due",
    "waive_absence",
]
