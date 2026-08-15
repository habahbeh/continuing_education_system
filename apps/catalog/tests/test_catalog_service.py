"""
Programme approval and price-list immutability — T-089 … T-095 (BR-006 … BR-008, D-14).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import (
    PriceList,
    PriceListStatus,
    Program,
    ProgramType,
    Subject,
)
from apps.catalog.services import catalog_service as svc
from apps.core.exceptions import ImmutableRecordError
from apps.core.models import AuditEvent, Semester
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def manager(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="mgr.catalog",
        password=PASSWORD,
        role=Role.CENTER_MANAGER,
        full_name_ar="مدير المركز",
    )


@pytest.fixture
def diploma(db) -> Program:
    """Interior design: 1700 fee − 50 consumables = 1650 across four subjects."""
    program = Program.objects.create(
        code="DIP-TEST",
        program_type=ProgramType.DIPLOMA,
        name_ar="دبلوم اختباري",
        consumables_per_student=Decimal("50.000"),
    )
    for seq, price in [(1, "300.000"), (2, "450.000"), (3, "450.000"), (4, "450.000")]:
        Subject.objects.create(
            program=program, sequence=seq, name_ar=f"مادة {seq}", price=Decimal(price)
        )
    return program


# ---------------------------------------------------------------------------
# BR-006 — subject prices must reconcile (T-089, T-090)
# ---------------------------------------------------------------------------
def test_t089_reconciling_diploma_is_approved(manager: User, diploma: Program) -> None:
    svc.approve_program(actor=manager, program=diploma, course_fee=Decimal("1700.000"))
    assert AuditEvent.objects.filter(action="APPROVE", reference="DIP-TEST").exists()


def test_t090_a_ten_dinar_discrepancy_blocks_approval_and_names_it(
    manager: User, diploma: Program
) -> None:
    """
    The refusal has to be actionable.

    "Prices do not reconcile" sends someone hunting through four subjects;
    "+10" sends them to the row that moved.
    """
    subject = diploma.subjects.first()
    assert subject is not None
    subject.price += Decimal("10.000")
    subject.save()

    with pytest.raises(ValidationError) as exc:
        svc.approve_program(actor=manager, program=diploma, course_fee=Decimal("1700.000"))

    assert "+10" in str(exc.value)
    assert AuditEvent.objects.filter(denial_rule="BR-006").exists()


def test_t090_variance_is_signed_so_short_and_over_are_distinguishable(
    diploma: Program,
) -> None:
    assert svc.subject_price_variance(diploma, Decimal("1700.000")) == 0

    subject = diploma.subjects.first()
    assert subject is not None
    subject.price -= Decimal("25.000")
    subject.save()
    assert svc.subject_price_variance(diploma, Decimal("1700.000")) == Decimal("-25.000")


def test_t091_zero_priced_subjects_still_reconcile(manager: User) -> None:
    """BR-007 — a free subject is legal and does not break the sum."""
    program = Program.objects.create(
        code="DIP-FREE",
        program_type=ProgramType.DIPLOMA,
        name_ar="دبلوم بمواد مجانية",
    )
    Subject.objects.create(program=program, sequence=1, name_ar="مادة مجانية", price=0)
    Subject.objects.create(program=program, sequence=2, name_ar="مادة مجانية 2", price=0)
    Subject.objects.create(
        program=program, sequence=3, name_ar="مادة مدفوعة", price=Decimal("500.000")
    )

    svc.approve_program(actor=manager, program=program, course_fee=Decimal("500.000"))


def test_a_short_course_is_not_subject_to_br006(manager: User) -> None:
    """The rule is about diplomas made of subjects, and only those."""
    from apps.catalog.models import CourseCategory

    program = Program.objects.create(
        code="SC-NOSUB",
        program_type=ProgramType.SHORT_COURSE,
        name_ar="دورة بلا مواد",
        course_category=CourseCategory.objects.create(code="C1", name_ar="مجال"),
    )
    svc.approve_program(actor=manager, program=program, course_fee=Decimal("250.000"))


# ---------------------------------------------------------------------------
# D-14 / BR-008 — an approved list is frozen (T-092 … T-095)
# ---------------------------------------------------------------------------
@pytest.fixture
def draft_list(active_semester: Semester) -> PriceList:
    return PriceList.objects.create(
        code="PL-DRAFT",
        name_ar="قائمة مسودة",
        semester=active_semester,
        issued_on=date(2026, 8, 1),
        effective_from=date(2026, 9, 1),
    )


def test_t093_editing_an_approved_list_is_refused(
    manager: User, draft_list: PriceList, diploma: Program
) -> None:
    svc.record_external_approval(
        actor=manager,
        price_list=draft_list,
        approved_by_text="رئيس الجامعة",
        decision_reference="ق/2026/5",
    )
    with pytest.raises(ImmutableRecordError):
        svc.add_price_item(
            actor=manager,
            price_list=draft_list,
            data={"program": diploma, "course_fee": Decimal("100.000")},
        )


def test_t093_editing_an_archived_list_is_refused(
    manager: User, draft_list: PriceList, diploma: Program
) -> None:
    draft_list.status = PriceListStatus.ARCHIVED
    draft_list.save()
    with pytest.raises(ImmutableRecordError):
        svc.add_price_item(
            actor=manager,
            price_list=draft_list,
            data={"program": diploma, "course_fee": Decimal("100.000")},
        )


def test_approving_a_second_list_archives_the_first(
    manager: User, active_semester: Semester, draft_list: PriceList
) -> None:
    """BR-008 — the old list is archived, never deleted."""
    svc.record_external_approval(
        actor=manager,
        price_list=draft_list,
        approved_by_text="رئيس الجامعة",
        decision_reference="ق/2026/5",
    )
    second = PriceList.objects.create(
        code="PL-SECOND",
        name_ar="قائمة ثانية",
        semester=active_semester,
        issued_on=date(2026, 10, 1),
        effective_from=date(2026, 10, 15),
    )
    svc.record_external_approval(
        actor=manager,
        price_list=second,
        approved_by_text="رئيس الجامعة",
        decision_reference="ق/2026/9",
    )

    draft_list.refresh_from_db()
    assert draft_list.status == PriceListStatus.ARCHIVED
    assert draft_list.archived_at is not None
    assert PriceList.objects.count() == 2, "an archived list must never be deleted"


def test_t095_only_one_approved_list_per_semester(
    manager: User, active_semester: Semester, draft_list: PriceList
) -> None:
    """Enforced by the database, not by the archiving code that usually does it."""
    svc.record_external_approval(
        actor=manager,
        price_list=draft_list,
        approved_by_text="رئيس الجامعة",
        decision_reference="ق/2026/5",
    )
    rogue = PriceList.objects.create(
        code="PL-ROGUE",
        name_ar="قائمة ثانية",
        semester=active_semester,
        issued_on=date(2026, 10, 1),
        effective_from=date(2026, 10, 15),
    )
    rogue.status = PriceListStatus.APPROVED
    with pytest.raises(IntegrityError), transaction.atomic():
        rogue.save()


def test_approval_requires_the_external_approver_and_reference(
    manager: User, draft_list: PriceList
) -> None:
    """
    D-31 — the president has no account, so the reference IS the evidence.

    Without it the record says a list was approved and cannot say by whom.
    """
    with pytest.raises(ValidationError):
        svc.record_external_approval(
            actor=manager,
            price_list=draft_list,
            approved_by_text="",
            decision_reference="ق/2026/5",
        )
    with pytest.raises(ValidationError):
        svc.record_external_approval(
            actor=manager,
            price_list=draft_list,
            approved_by_text="رئيس الجامعة",
            decision_reference="  ",
        )
    draft_list.refresh_from_db()
    assert draft_list.status == PriceListStatus.DRAFT


def test_approval_records_who_and_which_decision(manager: User, draft_list: PriceList) -> None:
    svc.record_external_approval(
        actor=manager,
        price_list=draft_list,
        approved_by_text="رئيس الجامعة",
        decision_reference="ق/2026/5",
    )
    event = AuditEvent.objects.filter(action="APPROVE", reference="PL-DRAFT").first()
    assert event is not None and event.changes is not None
    assert event.changes["approved_by"] == "رئيس الجامعة"
    assert event.changes["decision_reference"] == "ق/2026/5"


# ---------------------------------------------------------------------------
# Permissions (PERMISSIONS.md §3.3)
# ---------------------------------------------------------------------------
def test_registration_officer_cannot_create_a_programme(seeded_settings: None) -> None:
    registrar = User.objects.create_user(
        username="reg.catalog", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )
    with pytest.raises(PermissionDenied):
        svc.create_program(
            actor=registrar,
            data={"code": "X", "program_type": ProgramType.DIPLOMA, "name_ar": "دبلوم"},
        )


def test_d09_registration_officer_cannot_touch_price_lists(
    seeded_settings: None, draft_list: PriceList, diploma: Program
) -> None:
    """D-09 — the registrar does not edit prices, on any path."""
    registrar = User.objects.create_user(
        username="reg.prices", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )
    with pytest.raises(PermissionDenied):
        svc.add_price_item(
            actor=registrar,
            price_list=draft_list,
            data={"program": diploma, "course_fee": Decimal("100.000")},
        )
    assert AuditEvent.objects.filter(denial_rule="D-09").exists()


def test_finance_officer_reads_prices_but_does_not_change_them(
    seeded_settings: None, draft_list: PriceList, diploma: Program
) -> None:
    """Δ-03 — read-only, because he cannot verify a fee he cannot see."""
    from apps.people.constants import Action, Screen
    from apps.people.permissions import policy

    finance = User(username="fin", role=Role.FINANCE_OFFICER, is_active=True)
    assert policy.evaluate(finance, Screen.PRICELISTS, Action.VIEW).allowed
    assert not policy.evaluate(finance, Screen.PRICELISTS, Action.EDIT).allowed
