"""
Sprint 8F-0 — the catalogue admin, and the controls it must not open.

The admin exists here as a setup door, not as a way round the rules the
services enforce. Three things are therefore worth asserting more than the
happy path: that an approved price list is genuinely frozen (D-14), that the
only route to APPROVED runs through ``record_external_approval`` and its
evidence (BR-008, D-31), and that the door answers to the permission matrix
rather than to ``is_staff``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from django.contrib import admin as django_admin
from django.test import Client
from django.urls import reverse

from apps.catalog.admin import MatrixGatedAdmin, PriceListAdmin
from apps.catalog.models import (
    CourseCategory,
    DepositPolicy,
    KnowledgeField,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    RegistrationFeeRule,
    Subject,
)
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "admin-probe-1234"
CATALOGUE_MODELS = (
    CourseCategory,
    KnowledgeField,
    Program,
    Subject,
    DepositPolicy,
    PriceList,
    PriceListItem,
    RegistrationFeeRule,
)


def _staff(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role, is_staff=True)


@pytest.fixture
def manager(seeded_settings: None) -> User:
    return _staff(Role.CENTER_MANAGER, "mgr.admin")


@pytest.fixture
def draft_list(active_semester: Any) -> PriceList:
    return PriceList.objects.create(
        code="PL-DRAFT",
        name_ar="قائمة مسودة",
        semester=active_semester,
        issued_on=date(2026, 8, 1),
        effective_from=date(2026, 9, 1),
        proposed_by_text="مدير المركز",
        approved_by_text="رئيس الجامعة",
        decision_reference="قرار 2026/44",
    )


@pytest.fixture
def program(db: None) -> Program:
    category = CourseCategory.objects.create(code="CAT-IT", name_ar="تكنولوجيا المعلومات")
    return Program.objects.create(
        code="SC-NET",
        program_type="SHORT_COURSE",
        name_ar="هندسة الشبكات",
        course_category=category,
        training_hours=60,
    )


def _approve(client: Client, price_lists: list[PriceList]) -> Any:
    return client.post(
        reverse("admin:catalog_pricelist_changelist"),
        {
            "action": "record_president_approval",
            "_selected_action": [str(pl.pk) for pl in price_lists],
        },
        follow=True,
    )


# ---------------------------------------------------------------------------
# The matrix, not is_staff
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("role", "may_view", "may_add"),
    [
        (Role.CENTER_MANAGER, True, True),
        (Role.REGISTRATION_OFFICER, True, False),  # row 13 gives REG "V P"
        (Role.FINANCE_OFFICER, True, False),
        (Role.AUDIT_ACCOUNT, True, False),
        (Role.CASHIER, False, False),  # the cashier holds nothing on pricing
        (Role.FINANCE_MANAGER, False, False),
    ],
)
def test_the_price_list_admin_answers_to_the_permission_matrix(
    rf: Any, role: str, may_view: bool, may_add: bool
) -> None:
    model_admin = django_admin.site._registry[PriceList]
    request = rf.get("/admin/")
    request.user = _staff(role, f"probe.{role.lower()}")

    assert model_admin.has_view_permission(request) is may_view
    assert model_admin.has_module_permission(request) is may_view
    assert model_admin.has_add_permission(request) is may_add
    assert model_admin.has_change_permission(request) is may_add


def test_the_system_administrator_holds_no_catalogue_rights(rf: Any) -> None:
    """
    ``SYSADMIN_ALLOW`` grants two screens — users and audit — and the
    catalogue is not among them. Reusing ``SystemAdministratorOnlyMixin`` here
    would have invented an authority the matrix withholds.
    """
    request = rf.get("/admin/")
    request.user = _staff(Role.SYSTEM_ADMINISTRATOR, "sysadmin.catalogue")

    for model in CATALOGUE_MODELS:
        model_admin = django_admin.site._registry[model]
        assert not model_admin.has_module_permission(request), model.__name__


def test_no_catalogue_table_offers_a_delete_button(rf: Any) -> None:
    """PROTECT keys and a frozen list make deletion an offer nothing honours."""
    request = rf.get("/admin/")
    request.user = _staff(Role.CENTER_MANAGER, "mgr.delete.probe")

    for model in CATALOGUE_MODELS:
        model_admin = django_admin.site._registry[model]
        assert not model_admin.has_delete_permission(request), model.__name__
        assert "delete_selected" not in (model_admin.actions or ()), model.__name__


# ---------------------------------------------------------------------------
# Approval is a service call, never a status edit
# ---------------------------------------------------------------------------
def test_status_is_never_editable_on_a_price_list_form(rf: Any, draft_list: PriceList) -> None:
    model_admin = django_admin.site._registry[PriceList]
    request = rf.get("/admin/")
    request.user = _staff(Role.CENTER_MANAGER, "mgr.status.probe")

    assert "status" in model_admin.get_readonly_fields(request, draft_list)
    assert "approved_at" in model_admin.get_readonly_fields(request, draft_list)
    assert "status" in model_admin.get_readonly_fields(request, None)


def test_posting_a_status_to_the_change_form_does_not_approve(
    client: Client, manager: User, draft_list: PriceList
) -> None:
    """A readonly field is ignored on POST — assert that, do not assume it."""
    client.force_login(manager)
    url = reverse("admin:catalog_pricelist_change", args=[draft_list.pk])
    client.post(
        url,
        {
            "code": draft_list.code,
            "name_ar": draft_list.name_ar,
            "semester": str(draft_list.semester_id),
            "issued_on": draft_list.issued_on.isoformat(),
            "effective_from": draft_list.effective_from.isoformat(),
            "proposed_by_text": draft_list.proposed_by_text,
            "approved_by_text": draft_list.approved_by_text,
            "decision_reference": draft_list.decision_reference,
            "status": PriceListStatus.APPROVED,
        },
        follow=True,
    )
    draft_list.refresh_from_db()
    assert draft_list.status == PriceListStatus.DRAFT
    assert draft_list.approved_semester_key is None


def test_the_action_approves_through_the_service_and_audits_it(
    client: Client, manager: User, draft_list: PriceList
) -> None:
    from apps.core.models import AuditEvent

    client.force_login(manager)
    _approve(client, [draft_list])

    draft_list.refresh_from_db()
    assert draft_list.status == PriceListStatus.APPROVED
    assert draft_list.approved_at is not None

    event = AuditEvent.objects.get(action="APPROVE", entity_type="catalog.PriceList")
    assert event.reference == "PL-DRAFT"
    assert event.actor_id == manager.pk


def test_the_action_refuses_a_list_with_no_president_and_no_reference(
    client: Client, manager: User, active_semester: Any
) -> None:
    """
    BR-008 · D-31 — the approver's name and the decision reference are the
    only evidence the approval happened, so the action has nothing to record
    without them.
    """
    bare = PriceList.objects.create(
        code="PL-BARE",
        name_ar="قائمة بلا سند",
        semester=active_semester,
        issued_on=date(2026, 8, 1),
        effective_from=date(2026, 9, 1),
    )
    client.force_login(manager)
    response = _approve(client, [bare])

    bare.refresh_from_db()
    assert bare.status == PriceListStatus.DRAFT
    assert "PL-BARE" in response.content.decode("utf-8")


def test_the_action_is_refused_for_a_role_without_edit(
    client: Client, seeded_settings: None, draft_list: PriceList
) -> None:
    """The refusal comes from ``policy.require``, so it leaves a trail."""
    from apps.core.models import AuditEvent

    registrar = _staff(Role.REGISTRATION_OFFICER, "reg.action.probe")
    client.force_login(registrar)
    _approve(client, [draft_list])

    draft_list.refresh_from_db()
    assert draft_list.status == PriceListStatus.DRAFT
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=registrar).exists()


def test_approving_a_second_list_archives_the_first(
    client: Client, manager: User, draft_list: PriceList, active_semester: Any
) -> None:
    """BR-008 — one approved list per semester, and the old one kept as history."""
    successor = PriceList.objects.create(
        code="PL-NEXT",
        name_ar="قائمة لاحقة",
        semester=active_semester,
        issued_on=date(2026, 10, 1),
        effective_from=date(2026, 11, 1),
        approved_by_text="رئيس الجامعة",
        decision_reference="قرار 2026/61",
    )
    client.force_login(manager)
    _approve(client, [draft_list])
    _approve(client, [successor])

    draft_list.refresh_from_db()
    successor.refresh_from_db()
    assert draft_list.status == PriceListStatus.ARCHIVED
    assert draft_list.archived_at is not None
    assert successor.status == PriceListStatus.APPROVED
    assert (
        PriceList.objects.filter(semester=active_semester, status=PriceListStatus.APPROVED).count()
        == 1
    )


def test_the_admin_action_uses_the_existing_service_and_not_a_second_one() -> None:
    """
    A guard against the duplicate this sprint deliberately did not write.

    ``record_external_approval`` already approves a price list, archives its
    predecessor and demands the president's evidence. A second function with
    an approving name — ``approve_price_list`` — would have been an easier
    door to the same state with none of that, so its absence is asserted
    rather than remembered.
    """
    import inspect

    from apps.catalog.services import catalog_service

    assert not hasattr(catalog_service, "approve_price_list")
    source = inspect.getsource(PriceListAdmin.record_president_approval)
    assert "record_external_approval" in source


# ---------------------------------------------------------------------------
# D-14 — an approved list is frozen whole
# ---------------------------------------------------------------------------
def test_an_approved_list_is_readonly_in_every_field(
    rf: Any, client: Client, manager: User, draft_list: PriceList
) -> None:
    client.force_login(manager)
    _approve(client, [draft_list])
    draft_list.refresh_from_db()

    model_admin = django_admin.site._registry[PriceList]
    request = rf.get("/admin/")
    request.user = manager

    readonly = model_admin.get_readonly_fields(request, draft_list)
    for field in ("code", "name_ar", "semester", "issued_on", "effective_from", "status"):
        assert field in readonly, field
    assert not model_admin.has_change_permission(request, draft_list)
    assert model_admin.has_view_permission(request, draft_list), "frozen hides saving, not reading"


def test_no_item_may_be_added_to_an_approved_list(
    client: Client, manager: User, draft_list: PriceList, program: Program
) -> None:
    client.force_login(manager)
    _approve(client, [draft_list])

    response = client.post(
        reverse("admin:catalog_pricelistitem_add"),
        {
            "price_list": str(draft_list.pk),
            "program": str(program.pk),
            "level": "",
            "course_fee": "250.000",
            "deposit_amount": "",
            "deposit_policy": "",
            "notes": "",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert not PriceListItem.objects.filter(price_list=draft_list).exists()


def test_an_item_on_an_approved_list_cannot_be_changed(
    rf: Any, client: Client, manager: User, draft_list: PriceList, program: Program
) -> None:
    item = PriceListItem.objects.create(
        price_list=draft_list, program=program, course_fee=Decimal("250.000")
    )
    client.force_login(manager)
    _approve(client, [draft_list])
    item.refresh_from_db()

    model_admin = django_admin.site._registry[PriceListItem]
    request = rf.get("/admin/")
    request.user = manager
    assert not model_admin.has_change_permission(request, item)


def test_a_fee_rule_on_an_approved_list_cannot_be_changed(
    rf: Any, client: Client, manager: User, draft_list: PriceList
) -> None:
    rule = RegistrationFeeRule.objects.create(
        price_list=draft_list, participant_category="UNIVERSITY", fee=Decimal("15.000")
    )
    client.force_login(manager)
    _approve(client, [draft_list])
    rule.refresh_from_db()

    model_admin = django_admin.site._registry[RegistrationFeeRule]
    request = rf.get("/admin/")
    request.user = manager
    assert not model_admin.has_change_permission(request, rule)


# ---------------------------------------------------------------------------
# The back door leaves the same trail as the front one
# ---------------------------------------------------------------------------
def test_creating_a_programme_through_the_admin_is_audited(client: Client, manager: User) -> None:
    from apps.core.models import AuditEvent

    category = CourseCategory.objects.create(code="CAT-BUS", name_ar="الأعمال")
    client.force_login(manager)
    client.post(
        reverse("admin:catalog_program_add"),
        {
            "code": "SC-CMA",
            "program_type": "SHORT_COURSE",
            "name_ar": "CMA",
            "name_en": "",
            "training_hours": "60",
            "knowledge_field": "",
            "specialization": "",
            "course_category": str(category.pk),
            "levels_count": "",
            "consumables_per_student": "0.000",
            "minimum_first_payment_override": "",
            "is_active": "on",
            "subjects-TOTAL_FORMS": "0",
            "subjects-INITIAL_FORMS": "0",
            "subjects-MIN_NUM_FORMS": "0",
            "subjects-MAX_NUM_FORMS": "1000",
        },
        follow=True,
    )

    assert Program.objects.filter(code="SC-CMA").exists()
    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="catalog.Program", reference="SC-CMA"
    ).exists()


# ---------------------------------------------------------------------------
# Two decisions recorded as tests, so they are not re-litigated by accident
# ---------------------------------------------------------------------------
def test_a_programme_may_still_be_retired_from_the_admin(rf: Any, program: Program) -> None:
    """
    ``Program.is_active`` is deliberately NOT readonly.

    The sprint brief asked for a readonly ``Program.status``; the model has no
    such field. ``is_active`` is the nearest thing and it is not a
    service-owned state machine — it defaults to True, so a programme is
    active from creation and freezing the field would protect nothing already
    protected. What it would remove is the only way to retire a programme.
    BR-006 is untouched by this: it was never gated on the flag, and
    ``approve_program`` still enforces the subject-price reconciliation.
    """
    model_admin = django_admin.site._registry[Program]
    request = rf.get("/admin/")
    request.user = _staff(Role.CENTER_MANAGER, "mgr.retire.probe")

    assert "is_active" not in model_admin.get_readonly_fields(request, program)


def test_the_three_lookup_tables_borrow_the_screen_they_configure(rf: Any) -> None:
    """
    Course categories, knowledge fields and deposit policies have no matrix
    row of their own, so each is governed by the screen of the thing it
    configures — the programme screens for the first two, the price-list
    screen for the third. Nobody gains anything they did not already hold.
    """
    from apps.people.constants import Screen

    def screens_of(model: type[Any]) -> set[str]:
        return set(cast(MatrixGatedAdmin, django_admin.site._registry[model]).screens)

    assert screens_of(CourseCategory) == {
        Screen.PROGRAMS,
        Screen.SHORT_COURSES,
        Screen.ONLINE_COURSES,
    }
    assert screens_of(KnowledgeField) == screens_of(CourseCategory)
    assert screens_of(DepositPolicy) == {Screen.PRICELISTS}
