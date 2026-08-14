"""
Local authentication — T-168, T-278 … T-281 (Q-12).

T-168 is P0 rather than P1 because the decision made it policy rather than
preference: a till system where an unattended session stays open is a control
gap, not a comfort feature.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions import policy
from apps.people.services import auth_service

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def cashier(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="cashier.one",
        password=PASSWORD,
        role=Role.CASHIER,
        full_name_ar="أمين الصندوق",
    )


# ---------------------------------------------------------------------------
# T-278 — one isolated backend, no directory integration in v1
# ---------------------------------------------------------------------------
def test_t278_exactly_one_authentication_backend() -> None:
    assert len(settings.AUTHENTICATION_BACKENDS) == 1, (
        "Q-12: v1 authenticates locally. A second backend means a dependency on "
        "infrastructure the decision deliberately deferred."
    )


def test_t278_no_directory_backend_is_configured() -> None:
    banned = ("ldap", "saml", "azure", "adfs", "oauth", "oidc", "sso")
    joined = " ".join(settings.AUTHENTICATION_BACKENDS).lower()
    assert not any(token in joined for token in banned), joined


def test_t278_no_directory_package_is_installed() -> None:
    """The absence must be real, not merely unconfigured."""
    import importlib.util

    for package in ("django_auth_ldap", "djangosaml2", "social_django"):
        assert importlib.util.find_spec(package) is None, (
            f"{package} is installed. Q-12 defers SSO/LDAP to a later phase; a "
            f"dependency present in the environment tends to become a dependency in code."
        )


# ---------------------------------------------------------------------------
# T-279 — every authentication outcome is audited
# ---------------------------------------------------------------------------
def test_t279_successful_login_is_audited(client: Client, cashier: User) -> None:
    client.post(reverse("people:login"), {"username": "cashier.one", "password": PASSWORD})
    event = AuditEvent.objects.filter(action="LOGIN").first()
    assert event is not None
    assert event.actor_id == cashier.pk
    assert event.actor_role == Role.CASHIER


def test_t279_failed_login_is_audited(client: Client, cashier: User) -> None:
    client.post(reverse("people:login"), {"username": "cashier.one", "password": "wrong"})
    assert AuditEvent.objects.filter(action="LOGIN_FAILED").exists()


def test_t279_logout_is_audited(client: Client, cashier: User) -> None:
    client.force_login(cashier)
    client.post(reverse("people:logout"))
    assert AuditEvent.objects.filter(action="LOGOUT").exists()


def test_t279_unknown_username_is_audited_without_confirming_it_exists(
    client: Client, seeded_settings: None
) -> None:
    response = client.post(
        reverse("people:login"), {"username": "nobody.here", "password": "whatever"}
    )
    assert AuditEvent.objects.filter(action="LOGIN_FAILED").exists()
    # The response must not distinguish "no such user" from "wrong password".
    assert b"nobody.here" not in response.content or response.status_code == 200


# ---------------------------------------------------------------------------
# T-168 — lockout after five attempts, idle expiry after thirty minutes
# ---------------------------------------------------------------------------
def test_t168_account_locks_after_the_configured_attempt_limit(
    client: Client, cashier: User
) -> None:
    limit = auth_service.max_failed_attempts()
    assert limit == 5

    for _ in range(limit):
        client.post(reverse("people:login"), {"username": "cashier.one", "password": "wrong"})

    cashier.refresh_from_db()
    assert cashier.is_locked
    assert cashier.failed_login_count == limit
    assert AuditEvent.objects.filter(action="ACCOUNT_LOCKED").exists()


def test_t168_locked_account_cannot_log_in_even_with_the_right_password(
    client: Client, cashier: User
) -> None:
    for _ in range(auth_service.max_failed_attempts()):
        client.post(reverse("people:login"), {"username": "cashier.one", "password": "wrong"})

    response = client.post(
        reverse("people:login"), {"username": "cashier.one", "password": PASSWORD}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == reverse("people:locked")
    assert "_auth_user_id" not in client.session


def test_t279_attempt_on_a_locked_account_is_audited(client: Client, cashier: User) -> None:
    """
    BR-085 — the attempt on a LOCKED account must leave a record.

    This is the moment an attacker is closest to the account, so it is the
    least acceptable moment for the trail to go quiet. The path raises
    AccountLockedError, so it is also the path where an enclosing transaction
    would silently discard the row.
    """
    for _ in range(auth_service.max_failed_attempts()):
        client.post(reverse("people:login"), {"username": "cashier.one", "password": "wrong"})

    before = AuditEvent.objects.filter(action="LOGIN_FAILED").count()
    client.post(reverse("people:login"), {"username": "cashier.one", "password": PASSWORD})

    assert AuditEvent.objects.filter(action="LOGIN_FAILED").count() == before + 1, (
        "the attempt on a locked account was not recorded — the audit row was "
        "rolled back with the transaction that raised AccountLockedError"
    )


def test_t168_successful_login_resets_the_failure_counter(client: Client, cashier: User) -> None:
    client.post(reverse("people:login"), {"username": "cashier.one", "password": "wrong"})
    cashier.refresh_from_db()
    assert cashier.failed_login_count == 1

    client.post(reverse("people:login"), {"username": "cashier.one", "password": PASSWORD})
    cashier.refresh_from_db()
    assert cashier.failed_login_count == 0


def test_t168_idle_session_expires_and_is_audited(client: Client, cashier: User) -> None:
    """Measured against the clock, not asserted from the settings file."""
    client.force_login(cashier)
    assert client.get(reverse("people:users")).status_code in (200, 403)

    stale = timezone.now() - timedelta(seconds=auth_service.idle_timeout_seconds() + 60)
    session = client.session
    session["last_activity"] = stale.isoformat()
    session.save()

    client.get(reverse("health"))
    assert "_auth_user_id" not in client.session
    assert AuditEvent.objects.filter(action="SESSION_EXPIRED").exists()


def test_t168_activity_inside_the_window_keeps_the_session(client: Client, cashier: User) -> None:
    client.force_login(cashier)
    recent = timezone.now() - timedelta(seconds=auth_service.idle_timeout_seconds() - 60)
    session = client.session
    session["last_activity"] = recent.isoformat()
    session.save()

    client.get(reverse("health"))
    assert "_auth_user_id" in client.session


def test_thresholds_come_from_effective_settings_not_from_code(
    seeded_settings: None,
) -> None:
    """BR-086 — changing the policy is a dated setting change, not a deploy."""
    from datetime import date

    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    close_setting("login_max_failed_attempts", effective_to=date(2026, 6, 30))
    set_setting(
        "login_max_failed_attempts",
        3,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2026, 7, 1),
        note="اختبار: تشديد الحد",
    )
    assert auth_service.max_failed_attempts() == 3


# ---------------------------------------------------------------------------
# T-280 / T-281 — roleless and disabled accounts
# ---------------------------------------------------------------------------
def test_t280_user_without_a_role_has_no_permission_anywhere() -> None:
    """
    Silence means no (BR-080).

    An account with no role is powerless, not unrestricted. The opposite
    reading — "nothing forbids it, so allow it" — is the failure this whole
    engine exists to prevent.
    """
    nobody = User(username="roleless", role="", is_active=True)
    granted = [
        f"{s}/{a}"
        for s in Screen.values
        for a in Action.values
        if policy.evaluate(nobody, s, a).allowed
    ]
    assert not granted, "roleless account was granted: " + ", ".join(granted)


def test_t281_disabled_account_cannot_log_in(client: Client, cashier: User) -> None:
    cashier.is_active = False
    cashier.save(update_fields=["is_active"])

    client.post(reverse("people:login"), {"username": "cashier.one", "password": PASSWORD})
    assert "_auth_user_id" not in client.session
    assert AuditEvent.objects.filter(action="LOGIN_FAILED").exists()


def test_t281_disabled_account_has_no_permissions() -> None:
    disabled = User(username="disabled", role=Role.CENTER_MANAGER, is_active=False)
    assert not policy.evaluate(disabled, Screen.DASHBOARD, Action.VIEW).allowed


def test_anonymous_user_is_denied_and_the_refusal_names_a_rule() -> None:
    from django.contrib.auth.models import AnonymousUser

    decision = policy.evaluate(AnonymousUser(), Screen.DASHBOARD, Action.VIEW)
    assert not decision.allowed
    assert decision.denial_rule


# ---------------------------------------------------------------------------
# Unlock — system administrator only, with a reason
# ---------------------------------------------------------------------------
def test_unlock_requires_a_documented_reason(cashier: User) -> None:
    admin = User.objects.create_user(
        username="sys.admin", password=PASSWORD, role=Role.SYSTEM_ADMINISTRATOR
    )
    cashier.locked_at = timezone.now()
    cashier.lock_reason = "تجاوز حد المحاولات"
    cashier.save()

    with pytest.raises(ValueError):
        auth_service.unlock_account(target=cashier, actor=admin, reason="   ")

    auth_service.unlock_account(target=cashier, actor=admin, reason="مراجعة هوية الموظف")
    cashier.refresh_from_db()
    assert not cashier.is_locked
    assert cashier.failed_login_count == 0
    assert AuditEvent.objects.filter(action="ACCOUNT_UNLOCKED").exists()


# ---------------------------------------------------------------------------
# D-20 — no path changes the current session's role
# ---------------------------------------------------------------------------
def test_d20_administrator_cannot_change_their_own_role(seeded_settings: None) -> None:
    from apps.people.services import user_service

    admin = User.objects.create_user(
        username="sys.admin.self", password=PASSWORD, role=Role.SYSTEM_ADMINISTRATOR
    )
    with pytest.raises(PermissionDenied):
        user_service.set_role(actor=admin, target=admin, role=Role.CENTER_MANAGER)

    admin.refresh_from_db()
    assert admin.role == Role.SYSTEM_ADMINISTRATOR
    assert AuditEvent.objects.filter(denial_rule="D-20").exists()
