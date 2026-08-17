"""
T-165 — the Django admin is a second door, and it answers to Δ-02.
Plus the Sprint 2A scope guard: no business model may exist yet.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions import policy

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


def _staff(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role, is_staff=True)


# ---------------------------------------------------------------------------
# T-165 — admin closed to everyone but SYSTEM_ADMINISTRATOR
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "role",
    [
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.FINANCE_MANAGER,
        Role.CASHIER,
        Role.AUDIT_ACCOUNT,
    ],
)
def test_t165_user_admin_is_closed_to_every_business_role(rf, role: str) -> None:
    model_admin = django_admin.site._registry[User]
    request = rf.get("/admin/")
    request.user = _staff(role, f"staff.{role.lower()}")

    assert not model_admin.has_module_permission(request)
    assert not model_admin.has_view_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_delete_permission(request)


def test_t165_system_administrator_may_use_the_user_admin(rf) -> None:
    model_admin = django_admin.site._registry[User]
    request = rf.get("/admin/")
    request.user = _staff(Role.SYSTEM_ADMINISTRATOR, "staff.sysadmin")

    assert model_admin.has_module_permission(request)
    assert model_admin.has_change_permission(request)


def test_t165_a_superuser_flag_alone_does_not_open_the_admin(rf) -> None:
    """
    Δ-02 names the ROLE as the authority.

    A stray superuser account — created for a deploy, forgotten afterwards —
    must not be a way around a documented boundary.
    """
    model_admin = django_admin.site._registry[User]
    request = rf.get("/admin/")
    request.user = User.objects.create_superuser(
        username="root.no.role", password=PASSWORD, email=""
    )
    assert not model_admin.has_module_permission(request)


def test_t165_audit_event_admin_remains_read_only(rf) -> None:
    """Sprint 1's guarantee must survive Sprint 2 (A-08, D-11)."""
    from apps.core.models import AuditEvent

    model_admin = django_admin.site._registry[AuditEvent]
    request = rf.get("/admin/")
    request.user = _staff(Role.SYSTEM_ADMINISTRATOR, "staff.audit.probe")
    # Read-only even for the system administrator: D-11 binds EVERY role.
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)


# ---------------------------------------------------------------------------
# The users screen (row 33)
# ---------------------------------------------------------------------------
def test_users_screen_readable_by_manager_and_auditor_only() -> None:
    for role, expected in [
        (Role.CENTER_MANAGER, True),
        (Role.AUDIT_ACCOUNT, True),
        (Role.REGISTRATION_OFFICER, False),
        (Role.FINANCE_OFFICER, False),
        (Role.FINANCE_MANAGER, False),
        (Role.CASHIER, False),
    ]:
        user = User(username="probe", role=role, is_active=True)
        assert policy.evaluate(user, Screen.USERS, Action.VIEW).allowed is expected, role


def test_centre_manager_reads_the_users_screen_but_cannot_edit_it() -> None:
    """Δ-02 — user administration is not a centre manager function."""
    manager = User(username="mgr", role=Role.CENTER_MANAGER, is_active=True)
    assert policy.evaluate(manager, Screen.USERS, Action.VIEW).allowed
    assert not policy.evaluate(manager, Screen.USERS, Action.EDIT).allowed
    assert not policy.evaluate(manager, Screen.USERS, Action.CREATE).allowed


def test_users_view_refuses_an_unauthorised_role(client: Client, seeded_settings: None) -> None:
    cashier = User.objects.create_user(username="cash.probe", password=PASSWORD, role=Role.CASHIER)
    client.force_login(cashier)
    response = client.get(reverse("people:users"))
    assert response.status_code == 403


def test_system_administrator_may_administer_users(seeded_settings: None) -> None:
    from apps.people.services import user_service

    admin = User.objects.create_user(
        username="sysadmin.svc", password=PASSWORD, role=Role.SYSTEM_ADMINISTRATOR
    )
    created = user_service.create_user(
        actor=admin,
        username="new.cashier",
        password=PASSWORD,
        full_name_ar="أمين صندوق جديد",
        role=Role.CASHIER,
    )
    assert created.role == Role.CASHIER


def test_centre_manager_may_not_create_users(seeded_settings: None) -> None:
    from apps.people.services import user_service

    manager = User.objects.create_user(
        username="mgr.svc", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    with pytest.raises(PermissionDenied):
        user_service.create_user(
            actor=manager, username="x", password=PASSWORD, full_name_ar="س", role=Role.CASHIER
        )


# ---------------------------------------------------------------------------
# Sprint 3 scope — nothing from a LATER sprint may have been built
# ---------------------------------------------------------------------------
def test_no_business_model_exists_yet() -> None:
    """
    The scope guard, widened one sprint at a time.

    2A allowed no business model; 2B added people.Participant; 3 the
    catalogue; 4 billing and the till; 5 partners and settlements; 6 the rest
    of the participant lifecycle; 7 clearance and certificates. Expenses,
    reporting and the archive still belong to later sprints, and a model that
    appears early is scope that was never approved.
    """
    from django.apps import apps as django_apps

    allowed = {
        "core": {
            "Semester",
            "EffectiveSetting",
            "NumberSequence",
            "AuditEvent",
            "Attachment",
            "FinancialPeriod",
        },
        "people": {"User", "Participant"},
        # Sprint 4 — billing and the till.
        "billing": {
            "ChargeLine",
            "DepositReturn",
            "DepositForfeiture",
            "Discount",
            "ExtraFee",
            "Refund",
            # Sprint 7 — BR-071's "independent financial movement".
            "CreditReturn",
        },
        "cashbox": {
            "PaymentMethod",
            "Receipt",
            "PaymentAllocation",
            "ReceiptVoid",
            "DailyClosing",
        },
        # Sprint 6 — the lifecycle completed. Cohort and Enrollment arrived
        # early as the Sprint 4 prerequisite (every billing entity has a
        # foreign key to Enrollment); the other four are Sprint 6's own.
        "operations": {
            "Cohort",
            "Enrollment",
            "MoheSubmission",
            "EnrollmentStatusHistory",
            "Transfer",
            "SpecialCase",
            # Sprint 7 — the ending: clearance, its steps, the certificate.
            "Clearance",
            "ClearanceStep",
            "Certificate",
        },
        # Sprint 3 — the catalogue and pricing.
        "catalog": {
            "CourseCategory",
            "KnowledgeField",
            "Program",
            "Subject",
            "DepositPolicy",
            "PriceList",
            "PriceListItem",
            "RegistrationFeeRule",
        },
        # Sprint 5 — partners, entitlement and claims.
        "partners": {"Partner", "Agreement", "AgreementProgramSnapshot"},
        "settlements": {
            "Entitlement",
            "PartnerClaim",
            "PartnerClaimLine",
            "PartnerObligation",
            "ClaimDeduction",
            "PartnerSettlement",
        },
    }
    business_apps = {
        "expenses",
        "reporting",
        "datamigration",
    }

    offenders = []
    for model in django_apps.get_models():
        label = model._meta.app_label
        if label in business_apps or (label in allowed and model.__name__ not in allowed[label]):
            offenders.append(f"{label}.{model.__name__}")

    assert not offenders, "Models outside the Sprint 7 scope: " + ", ".join(offenders)


def test_later_sprint_models_do_not_exist() -> None:
    """
    The guard that keeps a PREREQUISITE from becoming a land grab.

    ✅ Sprint 7 delivered clearance and certificates, so those have moved into
    the allowed set above too. What remains is everything Sprint 8 and later
    own — named individually, because a list of absences is only worth having
    if it is specific.
    """
    from django.apps import apps as django_apps

    names = {m.__name__ for m in django_apps.get_models()}
    for deferred in (
        # Sprint 7 — the end of the participant's life in the system. Sprint 6
        # CREATES the credit balance a cheaper transfer leaves behind; only
        # returning it (BR-071) waits for clearance.
        "TaxRule",
        "OpeningBalance",
        "EnrollmentApplication",
        # The expense side of the partner relationship (DATA_MODEL §10.1).
        # Sprint 5 records what a partner is OWED and what they owe back;
        # what the centre pays out is a separate ledger in a later sprint.
        "Expense",
        "MigrationBatch",
        "MigrationRow",
    ):
        assert deferred not in names, f"{deferred} belongs to a later sprint"


def test_participant_exists_and_carries_no_money() -> None:
    """Sprint 2B's one model, and the field it must never grow."""
    from apps.people.models import Participant

    types = {f.get_internal_type() for f in Participant._meta.get_fields()}
    assert "DecimalField" not in types and "FloatField" not in types
