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
from django.utils.translation import gettext as _

from apps.people.constants import Action, Screen
from apps.people.permissions import policy


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


# ---------------------------------------------------------------------------
# Sprint 8K — demo-parity review screens
# ---------------------------------------------------------------------------
def settings_view(request: HttpRequest) -> HttpResponse:
    """Read-only settings landing page for client/demo parity."""
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)
    return render(
        request,
        "core/settings.html",
        {"title": _("الإعدادات"), "active_screen": Screen.SETTINGS},
    )


def coverage_view(request: HttpRequest) -> HttpResponse:
    """Requirement coverage matrix; informational, no business mutation."""
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)
    rows = [
        ("§3.1/1", _("لوحة المؤشرات"), _("لوحة المؤشرات"), _("منفذ")),
        ("§3.1/2", _("مسار التسجيل والدفع"), _("مسار التسجيل والدفع"), _("إرشادي")),
        (
            "§3.2/3-8",
            _("المشاركون والتسجيل والحالات الخاصة"),
            _("المشاركون، التسجيلات، النقل، الحالات الخاصة"),
            _("منفذ/إرشادي"),
        ),
        (
            "§3.3/9-15",
            _("البرامج والأسعار واعتماد الوزارة"),
            _("البرامج، القوائم، الدفعات، اعتماد الوزارة"),
            _("منفذ"),
        ),
        (
            "§3.4/16-22",
            _("الدفع والإقفال والحركات المالية"),
            _("الدفعات، الإقفال، الخصومات، الاستردادات، الرسوم، المصروفات"),
            _("منفذ"),
        ),
        (
            "§3.5/23-29",
            _("الشركاء والاتفاقيات والاستحقاقات"),
            _("الشركاء، الاتفاقيات، الاستحقاق، المطالبات، المخالصات، الالتزامات"),
            _("منفذ/إرشادي"),
        ),
        (
            "§3.6/30-31",
            _("براءة الذمة والشهادات"),
            _("براءة الذمة، الشهادات، نماذج الطباعة"),
            _("منفذ"),
        ),
        (
            "§3.7/32-38",
            _("التقارير والنظام والتغطية والنطاق المستقبلي"),
            _("التقارير، المستخدمون، التدقيق، الإعدادات، الترحيل، التغطية، النطاق المستقبلي"),
            _("منفذ/إرشادي"),
        ),
    ]
    return render(
        request,
        "core/coverage.html",
        {"title": _("مصفوفة تغطية المتطلبات"), "active_screen": "coverage", "rows": rows},
    )


def future_view(request: HttpRequest) -> HttpResponse:
    """Future-scope page; visible so deferred items are not mistaken for omissions."""
    policy.require(request.user, Screen.SETTINGS, Action.VIEW, request=request)
    return render(
        request,
        "core/future.html",
        {"title": _("النطاق المستقبلي"), "active_screen": "future"},
    )
