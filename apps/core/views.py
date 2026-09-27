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
from django.http.response import HttpResponseBase
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


def brand_asset(request: HttpRequest, slot: str, digest: str, extension: str) -> HttpResponseBase:
    """
    يقدّم ملف هوية واحداً — عامّاً بلا مصادقة، وبقصد.

    صفحة الدخول تحتاج الشعار **قبل** أن يوجد مستخدم، فلا يصلح لها مسار
    المرفقات المحميّ (Q-13). وما يُقدَّم هنا ليس مرفقاً على أي حال: شعار
    جامعةٍ مطبوعٌ على كل ورقة تخرج من المركز ليس سرّاً يُحرس.

    العنوان مُعنوَن بالمحتوى (بصمة الملف فيه)، فالبحث بالبصمة لا بالتأريخ:
    شعارٌ قديم بقي في تخزين متصفّح يُقدَّم كما كان — وهو الصحيح — بينما الصفحة
    الجديدة تطلب بصمةً جديدة فتحصل على الملف الجديد.

    ``Content-Security-Policy: sandbox`` لأن SVG يُقدَّم من أصل الموقع نفسه:
    الرفع يرفض ما فيه سكربت، وهذه الترويسة هي الحزام الثاني.
    """
    from django.http import FileResponse, Http404

    from apps.core.services import branding_service

    asset = branding_service.asset_by_digest(slot=slot, digest=digest, extension=extension)
    if asset is None:
        raise Http404

    response = FileResponse(asset.file.open("rb"), content_type=asset.mime_type)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["X-Content-Type-Options"] = "nosniff"
    response["ETag"] = f'"{asset.sha256}"'
    return response
