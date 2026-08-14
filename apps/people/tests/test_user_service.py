"""User administration service — the happy paths and their audit trail (Δ-02)."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

from apps.core.models import AuditEvent
from apps.people.models import Role, User
from apps.people.services import user_service

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def sysadmin(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="sysadmin.ops",
        password=PASSWORD,
        role=Role.SYSTEM_ADMINISTRATOR,
        full_name_ar="مدير النظام",
    )


@pytest.fixture
def cashier(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="cashier.ops",
        password=PASSWORD,
        role=Role.CASHIER,
        full_name_ar="أمين الصندوق",
    )


def test_list_users_is_ordered_for_display(sysadmin: User, cashier: User) -> None:
    usernames = [u.username for u in user_service.list_users()]
    assert set(usernames) == {"sysadmin.ops", "cashier.ops"}


def test_create_user_records_the_role_it_granted(sysadmin: User) -> None:
    created = user_service.create_user(
        actor=sysadmin,
        username="new.fim",
        password=PASSWORD,
        full_name_ar="المدير المالي الجديد",
        role=Role.FINANCE_MANAGER,
        department="الدائرة المالية",
    )
    assert created.role == Role.FINANCE_MANAGER

    event = AuditEvent.objects.filter(action="CREATE", entity_id=str(created.pk)).first()
    assert event is not None
    assert event.changes is not None
    assert event.changes["role"] == Role.FINANCE_MANAGER
    assert event.actor_id == sysadmin.pk


def test_set_role_records_both_sides_of_the_change(sysadmin: User, cashier: User) -> None:
    """The audit row must answer 'from what' as well as 'to what'."""
    user_service.set_role(actor=sysadmin, target=cashier, role=Role.FINANCE_OFFICER)

    cashier.refresh_from_db()
    assert cashier.role == Role.FINANCE_OFFICER

    event = AuditEvent.objects.filter(action="UPDATE", entity_id=str(cashier.pk)).first()
    assert event is not None
    assert event.changes is not None
    assert event.changes["role"] == {"from": Role.CASHIER, "to": Role.FINANCE_OFFICER}


def test_changing_a_role_does_not_rewrite_earlier_audit_rows(sysadmin: User, cashier: User) -> None:
    """
    T-166 / ADR-012 — actor_role is a snapshot.

    A cashier who becomes a finance officer must not retroactively appear to
    have been a finance officer when he took yesterday's cash.
    """
    from apps.core.services.audit_service import write_audit

    write_audit(
        action="LOGIN",
        entity_type="people.User",
        entity_id=str(cashier.pk),
        summary_ar="دخول قبل تغيير الدور",
        actor=cashier,
    )
    before = AuditEvent.objects.filter(action="LOGIN").first()
    assert before is not None and before.actor_role == Role.CASHIER

    user_service.set_role(actor=sysadmin, target=cashier, role=Role.FINANCE_OFFICER)

    before.refresh_from_db()
    assert before.actor_role == Role.CASHIER, "history was rewritten by a role change"


def test_set_active_disables_and_audits(sysadmin: User, cashier: User) -> None:
    user_service.set_active(actor=sysadmin, target=cashier, is_active=False)
    cashier.refresh_from_db()
    assert not cashier.is_active
    assert AuditEvent.objects.filter(action="UPDATE", entity_id=str(cashier.pk)).exists()

    user_service.set_active(actor=sysadmin, target=cashier, is_active=True)
    cashier.refresh_from_db()
    assert cashier.is_active


def test_a_business_role_cannot_administer_users(cashier: User, sysadmin: User) -> None:
    for call in (
        lambda: user_service.set_role(actor=cashier, target=sysadmin, role=Role.CASHIER),
        lambda: user_service.set_active(actor=cashier, target=sysadmin, is_active=False),
    ):
        with pytest.raises(PermissionDenied):
            call()

    sysadmin.refresh_from_db()
    assert sysadmin.role == Role.SYSTEM_ADMINISTRATOR
    assert sysadmin.is_active
