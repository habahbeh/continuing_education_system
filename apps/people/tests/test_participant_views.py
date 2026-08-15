"""
The four screens — T-283, T-284, T-285.

These go through the HTTP layer on purpose. The service tests already prove the
projection and the policy checks; what is under test here is that the SCREENS
route through them, because a view that queries the model directly would pass
every service test ever written and still leak.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditEvent, Semester
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions.matrix import allowed_actions
from apps.people.services import participant_service

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"

#: Fields BR-101 hides from the cashier and the finance manager, with a value
#: distinctive enough that finding it in a response body is unambiguous.
SECRET_VALUES = {
    "phone": "0791234567",
    "id_document_number": "9962012345",
    "email": "sara@example.com",
}


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def participant(seeded_settings: None, active_semester: Semester, participant_data: dict):
    registrar = _user(Role.REGISTRATION_OFFICER, "reg.seed")
    return participant_service.create_participant(actor=registrar, data=participant_data)


# ---------------------------------------------------------------------------
# T-283 — the restricted field set holds in the RESPONSE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", [Role.CASHIER, Role.FINANCE_MANAGER])
def test_t283_restricted_roles_never_receive_hidden_fields(
    client: Client, participant, role: str
) -> None:
    """
    BR-101 — asserted against the response body, not the rendered page.

    Hiding a field in the template leaves it one direct request away. This is
    the difference between a screen that looks restricted and one that is.
    """
    client.force_login(_user(role, f"probe.{role.lower()}"))

    detail = client.get(reverse("people:participant-detail", args=[participant.participant_number]))
    assert detail.status_code == 200
    body = detail.content.decode()

    assert participant.participant_number in body
    for field, secret in SECRET_VALUES.items():
        assert secret not in body, f"{role} received {field} — BR-101 breached"


@pytest.mark.parametrize("role", [Role.CASHIER, Role.FINANCE_MANAGER])
def test_t283_restricted_roles_see_no_hidden_fields_in_the_list(
    client: Client, participant, role: str
) -> None:
    client.force_login(_user(role, f"probe.list.{role.lower()}"))
    body = client.get(reverse("people:participants")).content.decode()

    for secret in SECRET_VALUES.values():
        assert secret not in body


def test_t283_a_permitted_role_does_receive_the_full_record(client: Client, participant) -> None:
    """The control case — otherwise an empty page would pass the test above."""
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.full"))
    body = client.get(
        reverse("people:participant-detail", args=[participant.participant_number])
    ).content.decode()

    for secret in SECRET_VALUES.values():
        assert secret in body


# ---------------------------------------------------------------------------
# T-284 — every screen, every role, straight at the URL
# ---------------------------------------------------------------------------
SCREEN_URLS = [
    (Screen.STUDENTS, "people:participants", ()),
    (Screen.STUDENT_NEW, "people:participant-new", ()),
    (Screen.USERS, "people:users", ()),
    (Screen.AUDIT, "people:audit", ()),
]

BUSINESS_ROLES_UNDER_TEST = [
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
]


@pytest.mark.parametrize(("screen", "route", "args"), SCREEN_URLS)
@pytest.mark.parametrize("role", BUSINESS_ROLES_UNDER_TEST)
def test_t284_screen_access_matches_the_matrix(
    client: Client, seeded_settings: None, screen: str, route: str, args: tuple, role: str
) -> None:
    """
    A direct GET, bypassing every link and button.

    The expected answer is read from the matrix — the same table PERMISSIONS.md
    §3 generates — so this test cannot drift from the document independently.
    """
    client.force_login(_user(role, f"probe.{screen}.{role.lower()}"[:150]))
    response = client.get(reverse(route, args=args))

    expected_allowed = Action.VIEW in allowed_actions(role, screen)
    if expected_allowed:
        assert response.status_code == 200, f"{role} was refused {screen}"
    else:
        assert response.status_code == 403, f"{role} reached {screen} — matrix says no"


def test_t284_anonymous_visitor_reaches_no_screen(client: Client) -> None:
    for _screen, route, args in SCREEN_URLS:
        response = client.get(reverse(route, args=args))
        assert response.status_code in (302, 403), route


def test_t284_refusal_is_audited_with_its_rule(client: Client, seeded_settings: None) -> None:
    client.force_login(_user(Role.CASHIER, "cash.denied"))
    client.get(reverse("people:audit"))

    event = AuditEvent.objects.filter(action="DENIED_ATTEMPT").order_by("-id").first()
    assert event is not None
    assert event.entity_type == Screen.AUDIT
    assert event.denial_rule, "a refusal with no rule id cannot be defended"


def test_t284_creating_a_participant_is_refused_at_the_url(
    client: Client, seeded_settings: None, active_semester: Semester, participant_data: dict
) -> None:
    """POST straight at the create endpoint with a role that may not create."""
    client.force_login(_user(Role.CASHIER, "cash.post"))
    payload = dict(participant_data)
    payload["date_of_birth"] = "1996-04-12"
    payload["registered_on"] = "2026-09-10"

    response = client.post(reverse("people:participant-new"), payload)
    assert response.status_code == 403
    from apps.people.models import Participant

    assert not Participant.objects.exists()


# ---------------------------------------------------------------------------
# T-285 — the audit screen offers no way in
# ---------------------------------------------------------------------------
def test_t285_audit_screen_rejects_every_write_verb(client: Client, seeded_settings: None) -> None:
    """
    D-11 — nobody edits or deletes an audit row, including the centre manager.

    Sprint 1 already removed UPDATE and DELETE from the application database
    user; this asserts the screen does not even offer the attempt.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.audit"))

    for verb in ("post", "put", "patch", "delete"):
        response = getattr(client, verb)(reverse("people:audit"))
        assert response.status_code == 405, f"{verb.upper()} was not rejected"


def test_t285_audit_screen_is_readable_by_the_roles_the_matrix_grants(
    client: Client, seeded_settings: None
) -> None:
    for role in (
        Role.CENTER_MANAGER,
        Role.FINANCE_OFFICER,
        Role.FINANCE_MANAGER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"audit.read.{role.lower()}"))
        assert client.get(reverse("people:audit")).status_code == 200


def test_t285_audit_screen_shows_the_denial_rule_column(
    client: Client, seeded_settings: None
) -> None:
    """A refusal is only auditable if the reason travels with it."""
    client.force_login(_user(Role.CASHIER, "cash.leaves.trace"))
    client.get(reverse("people:audit"))

    client.force_login(_user(Role.AUDIT_ACCOUNT, "aud.reader"))
    body = client.get(reverse("people:audit")).content.decode()
    assert "DENIED_ATTEMPT" in body


# ---------------------------------------------------------------------------
# The users screen, completed in this sprint
# ---------------------------------------------------------------------------
def test_system_administrator_can_change_a_role_from_the_screen(
    client: Client, seeded_settings: None
) -> None:
    admin = _user(Role.SYSTEM_ADMINISTRATOR, "sys.screen")
    target = _user(Role.CASHIER, "target.one")
    client.force_login(admin)

    client.post(
        reverse("people:user-action"),
        {"action": "set_role", "user_id": target.pk, "role": Role.FINANCE_OFFICER},
    )
    target.refresh_from_db()
    assert target.role == Role.FINANCE_OFFICER


def test_the_users_screen_offers_no_way_to_change_your_own_role(
    client: Client, seeded_settings: None
) -> None:
    """D-20 — refused at the service, for the account holding the privilege."""
    admin = _user(Role.SYSTEM_ADMINISTRATOR, "sys.self")
    client.force_login(admin)

    client.post(
        reverse("people:user-action"),
        {"action": "set_role", "user_id": admin.pk, "role": Role.CENTER_MANAGER},
    )
    admin.refresh_from_db()
    assert admin.role == Role.SYSTEM_ADMINISTRATOR
    assert AuditEvent.objects.filter(denial_rule="D-20").exists()


def test_centre_manager_cannot_act_on_the_users_screen(
    client: Client, seeded_settings: None
) -> None:
    manager = _user(Role.CENTER_MANAGER, "mgr.users")
    target = _user(Role.CASHIER, "target.two")
    client.force_login(manager)

    response = client.post(
        reverse("people:user-action"),
        {"action": "toggle_active", "user_id": target.pk},
    )
    assert response.status_code == 403
    target.refresh_from_db()
    assert target.is_active
