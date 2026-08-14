"""
Idle-session timeout (Q-12).

Django's SESSION_COOKIE_AGE expires a session a fixed time after it was
created or last saved. What the decision asks for is 30 minutes of INACTIVITY,
read from an EffectiveSetting so the number can be changed without a deploy.
The cookie age stays as a hard backstop; this middleware is the business rule.

The expiry writes SESSION_EXPIRED to the audit trail. A session that ends
silently is a gap in the record of who was at the till.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

SESSION_KEY = "last_activity"


class IdleSessionMiddleware:
    """Log out an authenticated session after the configured idle period."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            self._enforce(request)
        return self.get_response(request)

    def _enforce(self, request: HttpRequest) -> None:
        # Imported lazily: the middleware is constructed at startup, before the
        # app registry is ready to hand out models.
        from apps.people.services import auth_service

        now = timezone.now()
        raw = request.session.get(SESSION_KEY)

        if raw:
            try:
                last = datetime.fromisoformat(raw)
            except ValueError:
                last = now
            if (now - last).total_seconds() > auth_service.idle_timeout_seconds():
                auth_service.perform_logout(request, expired=True)
                return

        request.session[SESSION_KEY] = now.isoformat()


__all__ = ["IdleSessionMiddleware"]
