"""
Local authentication service (Q-12).

Local accounts in v1: no SSO, no Active Directory, no LDAP, no SAML, no 2FA.
The decision is not "SSO is bad" — it is "do not build a dependency on the
university's directory before it exists". Everything here goes through one
replaceable backend, and no business rule anywhere reads an external identity.

Thresholds are EffectiveSettings, never literals: `session_idle_timeout_minutes`
and `login_max_failed_attempts` are business decisions that will be argued
about, and BR-086 says arguments are settled by changing a dated setting, not
by editing code.

Every outcome — success, failure, lockout, unlock, expiry — writes an
AuditEvent. In a system that takes cash, "who was logged in" is evidence.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.utils import timezone

from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting

ENTITY = "people.User"

#: Fallbacks used only if the setting row is missing entirely (a database that
#: has not been seeded). They mirror the seeded values so behaviour is never
#: MORE permissive than the decision; they are not the source of truth.
_FALLBACK_MAX_ATTEMPTS = 5
_FALLBACK_IDLE_MINUTES = 30


class AccountLockedError(Exception):
    """Raised when a locked account attempts to authenticate."""


def max_failed_attempts(*, as_of: date | None = None) -> int:
    return int(
        get_setting(
            "login_max_failed_attempts",
            as_of=as_of or timezone.localdate(),
            default=_FALLBACK_MAX_ATTEMPTS,
        )
    )


def idle_timeout_seconds(*, as_of: date | None = None) -> int:
    minutes = int(
        get_setting(
            "session_idle_timeout_minutes",
            as_of=as_of or timezone.localdate(),
            default=_FALLBACK_IDLE_MINUTES,
        )
    )
    return minutes * 60


def _find_user(username: str) -> Any:
    from apps.people.models import User

    return User.objects.filter(username=username).first()


def attempt_login(request: Any, username: str, password: str) -> Any:
    """
    Authenticate and start a session, or record why it failed.

    Returns the user on success. Raises AccountLockedError when the account is
    locked. Returns None for bad credentials.

    **Deliberately NOT wrapped in a transaction.**

    An earlier version was ``@transaction.atomic``. The locked-account branch
    writes its audit row and then raises, so the rollback discarded the record
    of an attempt on a locked account — the single moment an attacker is
    closest to the account, and the least acceptable one for the trail to go
    quiet.

    Nothing here needs all-or-nothing anyway: authentication is a sequence of
    independently true facts, ``write_audit`` is atomic in itself, and the
    counter update is a single statement. Atomicity bought nothing and cost
    evidence. See ``test_t279_attempt_on_a_locked_account_is_audited``.
    """
    candidate = _find_user(username)

    if candidate is not None and candidate.is_locked:
        write_audit(
            action="LOGIN_FAILED",
            entity_type=ENTITY,
            entity_id=str(candidate.pk),
            reference=username,
            summary_ar="محاولة دخول على حساب مقفل",
            actor=None,
            denial_rule="Q-12",
            request=request,
        )
        raise AccountLockedError(candidate.lock_reason)

    user = authenticate(request, username=username, password=password)

    if user is None:
        _record_failure(request, candidate, username)
        return None

    if not user.is_active:
        # ModelBackend already refuses inactive users; this keeps the audit
        # trail honest about WHY, instead of logging it as a bad password.
        write_audit(
            action="LOGIN_FAILED",
            entity_type=ENTITY,
            entity_id=str(user.pk),
            reference=username,
            summary_ar="محاولة دخول على حساب معطّل",
            actor=None,
            denial_rule="Q-12",
            request=request,
        )
        return None

    user.failed_login_count = 0
    user.save(update_fields=["failed_login_count"])
    login(request, user)
    request.session["last_activity"] = timezone.now().isoformat()

    write_audit(
        action="LOGIN",
        entity_type=ENTITY,
        entity_id=str(user.pk),
        reference=user.get_username(),
        summary_ar=f"تسجيل دخول — {user.full_name_ar or user.get_username()}",
        actor=user,
        request=request,
    )
    return user


def _record_failure(request: Any, candidate: Any, username: str) -> None:
    """Count the failure and lock the account once the limit is reached."""
    if candidate is None:
        # Unknown username: audited, but nothing to count. Deliberately
        # indistinguishable from a wrong password in the message shown to the
        # user, so the form does not confirm which usernames exist.
        write_audit(
            action="LOGIN_FAILED",
            entity_type=ENTITY,
            reference=username,
            summary_ar="محاولة دخول باسم مستخدم غير موجود",
            actor=None,
            denial_rule="Q-12",
            request=request,
        )
        return

    candidate.failed_login_count += 1
    limit = max_failed_attempts()
    reached_limit = candidate.failed_login_count >= limit

    if reached_limit:
        candidate.locked_at = timezone.now()
        candidate.lock_reason = f"تجاوز حد المحاولات الفاشلة ({limit})"
    candidate.save(update_fields=["failed_login_count", "locked_at", "lock_reason"])

    write_audit(
        action="LOGIN_FAILED",
        entity_type=ENTITY,
        entity_id=str(candidate.pk),
        reference=username,
        summary_ar=f"محاولة دخول فاشلة ({candidate.failed_login_count}/{limit})",
        actor=None,
        denial_rule="Q-12",
        request=request,
    )

    if reached_limit:
        write_audit(
            action="ACCOUNT_LOCKED",
            entity_type=ENTITY,
            entity_id=str(candidate.pk),
            reference=username,
            summary_ar=candidate.lock_reason,
            actor=None,
            denial_rule="Q-12",
            request=request,
        )


def perform_logout(request: Any, *, expired: bool = False) -> None:
    """End the session, recording whether the user left or the clock did."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        write_audit(
            action="SESSION_EXPIRED" if expired else "LOGOUT",
            entity_type=ENTITY,
            entity_id=str(user.pk),
            reference=user.get_username(),
            summary_ar="انتهاء الجلسة للخمول" if expired else "تسجيل خروج",
            actor=user,
            request=request,
        )
    logout(request)


@transaction.atomic
def unlock_account(*, target: Any, actor: Any, reason: str, request: Any = None) -> None:
    """
    Clear a lock. SYSTEM_ADMINISTRATOR only, with a reason (Q-12).

    The reason is mandatory because an unlock is the one moment the control can
    be waved away, and an unexplained unlock is indistinguishable from an
    unauthorised one after the fact.
    """
    if not reason or not reason.strip():
        raise ValueError("فكّ القفل يتطلب سبباً موثّقاً")

    previous = target.lock_reason
    target.locked_at = None
    target.lock_reason = ""
    target.failed_login_count = 0
    target.save(update_fields=["locked_at", "lock_reason", "failed_login_count"])

    write_audit(
        action="ACCOUNT_UNLOCKED",
        entity_type=ENTITY,
        entity_id=str(target.pk),
        reference=target.get_username(),
        summary_ar=f"فكّ قفل الحساب — {reason.strip()}",
        actor=actor,
        changes={"previous_lock_reason": previous},
        request=request,
    )


__all__ = [
    "AccountLockedError",
    "attempt_login",
    "idle_timeout_seconds",
    "max_failed_attempts",
    "perform_logout",
    "unlock_account",
]
