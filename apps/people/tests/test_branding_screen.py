"""
هوية المؤسسة على الشاشة — الرفع من الإعدادات وظهور الشعار (Sprint 8L).

الاختبارات هنا لا في ``apps/core/tests`` لسبب معماري لا تنظيمي: هذه تسأل عن
شاشةٍ ودورٍ ومصفوفة، و``apps.core`` بنيةٌ تحتية لا تعرف تطبيقاً تجارياً
(ADR-008 · A-03). الجدول والخدمة يُختبران هناك، والشاشة فوقهما تُختبر هنا.
"""

from __future__ import annotations

import struct
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import AuditEvent, BrandAsset, BrandAssetSlot
from apps.core.services import branding_service

pytestmark = pytest.mark.django_db

TODAY = date(2026, 9, 25)
PASSWORD = "probe-password-1234"


def _png(width: int = 512, height: int = 256, name: str = "logo.png") -> SimpleUploadedFile:
    body = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 32
    )
    return SimpleUploadedFile(name, body, content_type="image/png")


def _svg(body: bytes, name: str = "logo.svg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="image/svg+xml")


@pytest.fixture
def manager(seeded_settings):  # type: ignore[no-untyped-def]
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.brand.screen", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _upload(**over):  # type: ignore[no-untyped-def]
    payload = {
        "slot": BrandAssetSlot.LOGO_PRIMARY,
        "file": _png(),
        "alt_text_ar": "شعار جامعة البترا",
        "note": "اعتماد الشعار الرسمي من دائرة العلاقات العامة",
    }
    payload.update(over)
    return payload


def _seed_logo(actor, **kw):  # type: ignore[no-untyped-def]
    return branding_service.set_asset(
        actor=actor,
        slot=kw.pop("slot", BrandAssetSlot.LOGO_PRIMARY),
        upload=kw.pop("upload", _png()),
        effective_from=kw.pop("effective_from", TODAY),
        note="اعتماد الشعار الرسمي من دائرة العلاقات العامة",
        **kw,
    )


# ---------------------------------------------------------------------------
# شريط العنوان: الشعار يحلّ محلّ الشارة النصّية ولا يجتمعان
# ---------------------------------------------------------------------------
def test_the_topbar_keeps_the_text_badge_until_a_logo_is_uploaded(client, manager) -> None:
    client.force_login(manager)
    body = client.get(reverse("people:settings"), HTTP_HOST="127.0.0.1").content.decode()

    assert 'class="logo"' in body and ">UOP<" in body
    assert "logo-img" not in body


def test_the_topbar_shows_the_uploaded_logo(client, manager) -> None:
    asset = _seed_logo(manager, alt_text_ar="شعار جامعة البترا")
    client.force_login(manager)
    body = client.get(reverse("people:settings"), HTTP_HOST="127.0.0.1").content.decode()

    assert "logo-img" in body
    assert asset.public_url in body
    assert "شعار جامعة البترا" in body
    assert ">UOP<" not in body, "الشارة النصّية تنصرف حين يحضر الشعار، ولا يجتمعان"


def test_a_page_asks_the_database_about_the_logo_once(client, manager) -> None:
    """
    الشعار يظهر في كل صفحة من إحدى وسبعين شاشة؛ استعلامٌ زائد هنا يُدفع في كلٍّ
    منها. خانات أربع واستعلام واحد، ومعالج السياق يخزّن الناتج على الطلب.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _seed_logo(manager)
    client.force_login(manager)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("people:participants"), HTTP_HOST="127.0.0.1")

    assert response.status_code == 200, "شاشة عادية لا شاشة الهوية — تلك تقرأ الخانات بحكم عملها"

    brand_queries = [q for q in captured.captured_queries if "core_brandasset" in q["sql"]]
    assert len(brand_queries) == 1, f"استعلامات الهوية: {len(brand_queries)}"


# ---------------------------------------------------------------------------
# شاشة الإعدادات: مَن يرفع ومَن لا يرفع
# ---------------------------------------------------------------------------
def test_the_screen_lists_all_four_slots_including_the_empty_ones(client, manager) -> None:
    """أن يرى المدير أن «شعار الطباعة» لم يُرفع بعد هو نصف فائدة الشاشة."""
    client.force_login(manager)
    body = client.get(reverse("people:settings"), HTTP_HOST="127.0.0.1").content.decode()

    for label in ("شعار الشاشة", "شعار الطباعة", "العلامة المائية", "أيقونة المتصفح"):
        assert label in body
    assert "لم يُرفع بعد" in body


def test_the_centre_manager_uploads_from_the_screen(client, manager) -> None:
    client.force_login(manager)
    response = client.post(
        reverse("people:settings"), _upload(), follow=True, HTTP_HOST="127.0.0.1"
    )

    assert response.status_code == 200
    asset = BrandAsset.objects.get()
    assert asset.slot == BrandAssetSlot.LOGO_PRIMARY
    assert asset.alt_text_ar == "شعار جامعة البترا"
    assert asset.uploaded_by == manager
    assert asset.public_url in response.content.decode(), "يظهر فوراً في الشريط"


def test_a_role_without_edit_cannot_upload_and_the_refusal_is_audited(
    client, seeded_settings
) -> None:
    """المدقّق يقرأ الإعدادات ولا يغيّرها (§3.7/35 · BR-082)."""
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.brand.screen", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    client.force_login(auditor)
    response = client.post(reverse("people:settings"), _upload(), HTTP_HOST="127.0.0.1")

    assert response.status_code == 403
    assert not BrandAsset.objects.exists()
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT").exists()


def test_a_refused_file_returns_the_reason_to_the_screen(client, manager) -> None:
    """الرفض يصل إلى من رفع الملف، لا إلى سجلّ الخادم وحده."""
    client.force_login(manager)
    response = client.post(
        reverse("people:settings"),
        _upload(file=_svg(b"<svg><script>x()</script></svg>")),
        HTTP_HOST="127.0.0.1",
    )

    assert response.status_code == 200
    assert "سكربت" in response.content.decode()
    assert not BrandAsset.objects.exists()


def test_the_upload_uses_a_dialog_not_a_browser_confirm(client, manager) -> None:
    client.force_login(manager)
    body = client.get(reverse("people:settings"), HTTP_HOST="127.0.0.1").content.decode()

    assert "<dialog" in body and "showModal()" in body
    assert "confirm(" not in body and "alert(" not in body
