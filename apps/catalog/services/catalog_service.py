"""
Programme and price-list lifecycle (BR-006 … BR-008, D-14).

Two controls live here, and both are the kind the demo left to good intentions:

**BR-006 — a diploma's subject prices must reconcile with its course fee.**
``Σ subject.price == course_fee − consumables``. It cannot be a CHECK
constraint because it aggregates across rows, so it is enforced at approval
and re-checkable afterwards by a management command.

**D-14 — an approved price list is immutable.** Not "the UI hides the edit
button": every write path refuses, and approving a new list archives the
previous one so history stays intact (BR-008).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import (
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
)
from apps.catalog.services import pricing_service
from apps.core.exceptions import ImmutableRecordError
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

PROGRAM_ENTITY = "catalog.Program"
PRICE_LIST_ENTITY = "catalog.PriceList"

#: Which screen governs a programme, by type (PERMISSIONS.md §3.3 rows 9–11).
SCREEN_BY_TYPE: dict[str, str] = {
    ProgramType.DIPLOMA: Screen.PROGRAMS,
    ProgramType.SHORT_COURSE: Screen.SHORT_COURSES,
    ProgramType.ONLINE_COURSE: Screen.ONLINE_COURSES,
}


def screen_for(program_type: str) -> str:
    return SCREEN_BY_TYPE.get(program_type, Screen.PROGRAMS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BR-006 — the subject-price reconciliation
# ---------------------------------------------------------------------------
def subject_price_variance(program: Program, course_fee: Decimal) -> Decimal:
    """
    ``Σ subjects − (course_fee − consumables)``. Zero means reconciled.

    Returned as a signed number so the screen can say "short by 10" rather
    than "invalid", which is the difference between a message someone can act
    on and one they escalate.
    """
    expected = Decimal(course_fee) - Decimal(program.consumables_per_student)
    return pricing_service.subject_price_total(program) - expected


def check_subject_prices(program: Program, course_fee: Decimal) -> None:
    """BR-006 — raise unless the subject prices reconcile."""
    variance = subject_price_variance(program, course_fee)
    if variance != 0:
        raise ValidationError(
            f"مجموع أسعار المواد لا يطابق رسوم الدورة — الفرق {variance:+} دينار. "
            f"(BR-006: مجموع المواد = رسوم الدورة − المستهلكات)"
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_programs(*, actor: Any, program_type: str, request: Any = None) -> Any:
    policy.require(actor, screen_for(program_type), Action.VIEW, request=request)
    return (
        Program.objects.filter(program_type=program_type)
        .select_related("course_category", "knowledge_field")
        .order_by("name_ar")
    )


def get_program(*, actor: Any, code: str, request: Any = None) -> Program:
    program = Program.objects.select_related("course_category").get(code=code)
    policy.require(actor, screen_for(program.program_type), Action.VIEW, request=request)
    return program


def list_price_lists(*, actor: Any, request: Any = None) -> Any:
    policy.require(actor, Screen.PRICELISTS, Action.VIEW, request=request)
    return PriceList.objects.select_related("semester").order_by("-effective_from")


def get_price_list(*, actor: Any, code: str, request: Any = None) -> PriceList:
    policy.require(actor, Screen.PRICELISTS, Action.VIEW, request=request)
    return PriceList.objects.select_related("semester").get(code=code)


# ---------------------------------------------------------------------------
# Programmes
# ---------------------------------------------------------------------------
def create_program(*, actor: Any, data: dict[str, Any], request: Any = None) -> Program:
    policy.require(actor, screen_for(data.get("program_type", "")), Action.CREATE, request=request)

    with transaction.atomic():
        program = Program(**data)
        program.full_clean()
        program.save()
        write_audit(
            action="CREATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"إنشاء برنامج — {program.name_ar}",
            actor=actor,
            changes={"program_type": program.program_type},
            request=request,
        )
    return program


def update_program(
    *, actor: Any, program: Program, data: dict[str, Any], request: Any = None
) -> Program:
    policy.require(actor, screen_for(program.program_type), Action.EDIT, request=request)

    before = {field: getattr(program, field) for field in data}
    with transaction.atomic():
        for field, value in data.items():
            setattr(program, field, value)
        program.full_clean()
        program.save()
        write_audit(
            action="UPDATE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"تعديل برنامج — {program.name_ar}",
            actor=actor,
            changes={
                f: {"from": str(before[f]), "to": str(getattr(program, f))}
                for f in data
                if before[f] != getattr(program, f)
            },
            request=request,
        )
    return program


def approve_program(
    *, actor: Any, program: Program, course_fee: Decimal, request: Any = None
) -> Program:
    """
    BR-006 gate (T-089, T-090).

    A diploma is approved only when its subjects add up. The refusal names the
    shortfall in dinars, because "the prices do not reconcile" sends someone
    hunting and "short by 10" sends them to the row.
    """
    policy.require(actor, screen_for(program.program_type), Action.APPROVE, request=request)

    if program.program_type == ProgramType.DIPLOMA:
        try:
            check_subject_prices(program, course_fee)
        except ValidationError as exc:
            write_audit(
                action="DENIED_ATTEMPT",
                entity_type=PROGRAM_ENTITY,
                entity_id=str(program.pk),
                reference=program.code,
                summary_ar=f"منع اعتماد برنامج — {exc.messages[0]}",
                actor=actor,
                denial_rule="BR-006",
                request=request,
            )
            raise

    with transaction.atomic():
        program.is_active = True
        program.save(update_fields=["is_active"])
        write_audit(
            action="APPROVE",
            entity_type=PROGRAM_ENTITY,
            entity_id=str(program.pk),
            reference=program.code,
            summary_ar=f"اعتماد برنامج — {program.name_ar}",
            actor=actor,
            changes={"course_fee": str(course_fee)},
            request=request,
        )
    return program


# ---------------------------------------------------------------------------
# Price lists
# ---------------------------------------------------------------------------
def _refuse_if_frozen(price_list: PriceList) -> None:
    """D-14 — nobody edits an approved or archived list. Not even the manager."""
    if price_list.is_frozen:
        raise ImmutableRecordError(
            f"قائمة الأسعار {price_list.code} بحالة {price_list.status} — "
            "غير قابلة للتعديل (D-14 · BR-008). التغيير بإصدار قائمة جديدة."
        )


def create_price_list(*, actor: Any, data: dict[str, Any], request: Any = None) -> PriceList:
    policy.require(actor, Screen.PRICELISTS, Action.CREATE, request=request)

    with transaction.atomic():
        price_list = PriceList(**data)
        price_list.full_clean()
        price_list.save()
        write_audit(
            action="CREATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"إنشاء قائمة أسعار — {price_list.name_ar}",
            actor=actor,
            request=request,
        )
    return price_list


def add_price_item(
    *, actor: Any, price_list: PriceList, data: dict[str, Any], request: Any = None
) -> PriceListItem:
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    with transaction.atomic():
        item = PriceListItem(price_list=price_list, **data)
        item.full_clean()
        item.save()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"إضافة بند سعر — {item.program.name_ar}",
            actor=actor,
            changes={
                "program": item.program.code,
                "course_fee": str(item.course_fee),
                "deposit_amount": str(item.deposit_amount) if item.deposit_amount else None,
            },
            request=request,
        )
    return item


def update_price_item(
    *, actor: Any, item: PriceListItem, data: dict[str, Any], request: Any = None
) -> PriceListItem:
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(item.price_list)

    with transaction.atomic():
        for field, value in data.items():
            setattr(item, field, value)
        item.full_clean()
        item.save()
        write_audit(
            action="UPDATE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(item.price_list_id),
            reference=item.price_list.code,
            summary_ar=f"تعديل بند سعر — {item.program.name_ar}",
            actor=actor,
            request=request,
        )
    return item


def record_external_approval(
    *,
    actor: Any,
    price_list: PriceList,
    approved_by_text: str,
    decision_reference: str,
    request: Any = None,
) -> PriceList:
    """
    Record the president's approval of a list, archiving the previous one (BR-008).

    **This is deliberately not an APPROVE permission.** PERMISSIONS.md row 13
    gives the centre manager ``V C E P`` on price lists and withholds ``A``,
    because footnote 8 says he RECOMMENDS and the university president
    approves — outside the system entirely (D-31). Nobody holds an internal
    approval right over a price list, and adding one to the matrix to make
    this function fit would have quietly moved the authority.

    So the act here is recording an external decision, which is an edit. The
    control is not who clicks it but that the approver's name and the decision
    reference are MANDATORY: they are the only evidence the approval happened.
    """
    policy.require(actor, Screen.PRICELISTS, Action.EDIT, request=request)
    _refuse_if_frozen(price_list)

    if not approved_by_text.strip() or not decision_reference.strip():
        raise ValidationError(
            "اعتماد القائمة يتطلب جهة الاعتماد ومرجع القرار — "
            "الاعتماد خارج النظام ولا دليل عليه سواهما (BR-008 · D-31)."
        )

    with transaction.atomic():
        superseded = (
            PriceList.objects.select_for_update()
            .filter(semester=price_list.semester, status=PriceListStatus.APPROVED)
            .exclude(pk=price_list.pk)
        )
        archived_codes = [old.code for old in superseded]
        for old in superseded:
            old.status = PriceListStatus.ARCHIVED
            old.archived_at = timezone.now()
            old.save()
            write_audit(
                action="UPDATE",
                entity_type=PRICE_LIST_ENTITY,
                entity_id=str(old.pk),
                reference=old.code,
                summary_ar=f"أرشفة قائمة أسعار — استبدلتها {price_list.code}",
                actor=actor,
                request=request,
            )

        price_list.status = PriceListStatus.APPROVED
        price_list.approved_by_text = approved_by_text.strip()
        price_list.decision_reference = decision_reference.strip()
        price_list.approved_at = timezone.now()
        price_list.save()

        write_audit(
            action="APPROVE",
            entity_type=PRICE_LIST_ENTITY,
            entity_id=str(price_list.pk),
            reference=price_list.code,
            summary_ar=f"اعتماد قائمة أسعار — {price_list.name_ar}",
            actor=actor,
            changes={
                "approved_by": approved_by_text.strip(),
                "decision_reference": decision_reference.strip(),
                "archived": archived_codes,
            },
            request=request,
        )
    return price_list


__all__ = [
    "add_price_item",
    "approve_program",
    "check_subject_prices",
    "create_price_list",
    "create_program",
    "get_price_list",
    "get_program",
    "list_price_lists",
    "list_programs",
    "record_external_approval",
    "screen_for",
    "subject_price_variance",
    "update_price_item",
    "update_program",
]
