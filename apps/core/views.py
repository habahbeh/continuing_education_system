"""
Core views — the health check, and the front door.

Views contain no business logic — they call services and render (ADR-008).

**Neither view names a business app.** ``home`` sends the visitor to
``LOGIN_REDIRECT_URL`` or ``LOGIN_URL`` rather than to ``operations:dashboard``
and ``people:login``, so core stays ignorant of what those apps are called
(A-03) and the destination is a setting rather than an import.
"""

from __future__ import annotations

import django
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


def home(request: HttpRequest) -> HttpResponse:
    """
    The front door (Sprint 8I-1).

    Until now ``/`` was the health check, so the first thing a client saw —
    and the page every successful login landed on — was a panel reporting the
    Django version, the MySQL version and whether STRICT_ALL_TABLES was set.
    That is a page for whoever deploys the system, not for whoever uses it,
    and it is still served at ``/health/`` for exactly that reader.
    """
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    return redirect(settings.LOGIN_URL)


def health(request: HttpRequest) -> HttpResponse:
    """System health page: Django version and database connectivity."""
    db_ok = False
    db_detail = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@sql_mode LIKE '%STRICT_ALL_TABLES%'")
            version, strict = cursor.fetchone()
        db_ok = True
        db_detail = f"MySQL {version} · STRICT_ALL_TABLES: {'نعم' if strict else 'لا'}"
    except Exception as exc:
        db_detail = str(exc)

    return render(
        request,
        "core/health.html",
        {
            "django_version": django.get_version(),
            "db_ok": db_ok,
            "db_detail": db_detail,
        },
    )
