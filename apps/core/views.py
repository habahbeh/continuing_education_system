"""
Core views.

Sprint 1 exposes exactly one page: a health check. No business screens.
Views contain no business logic — they call services and render (ADR-008).
"""

from __future__ import annotations

import django
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


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
