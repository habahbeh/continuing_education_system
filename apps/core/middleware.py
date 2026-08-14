"""Audit context middleware — captures actor IP and user agent (ADR-010)."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """
    Attaches request metadata that audit_service reads.

    The middleware only carries context; it never writes audit rows itself.
    Writing is the service layer's job, so that non-HTTP callers
    (management commands, future API) produce identical records.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.audit_ip = _client_ip(request)  # type: ignore[attr-defined]
        request.audit_user_agent = request.META.get("HTTP_USER_AGENT", "")  # type: ignore[attr-defined]
        return self.get_response(request)
