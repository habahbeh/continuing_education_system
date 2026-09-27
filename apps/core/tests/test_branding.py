"""
هوية المؤسسة — النموذج والخدمة والمسار العامّ (Sprint 8L).

ما يُثبَت هنا ثلاثة: أن تغيير الشعار لا يمحو ما قبله، وأن ما لا يصلح شعاراً
يُردّ بسببه لا بصمت، وأن غياب الشعار نتيجةٌ صحيحة تُعرَض نصّاً لا مربّعاً
مكسوراً.
"""

from __future__ import annotations

import struct
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import AuditEvent, BrandAsset, BrandAssetSlot
from apps.core.services import branding_service

pytestmark = pytest.mark.django_db

TODAY = date(2026, 9, 25)
LATER = date(2026, 11, 1)


def _png(width: int = 512, height: int = 256, name: str = "logo.png") -> SimpleUploadedFile:
    """أصغر ما يُقرأ منه عرضٌ وارتفاع — ترويسة PNG سليمة وكتلة IHDR."""
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


# الفاعل يأتي من فيكستشر `user` في conftest الجذر — مدير مركز جاهز. وتعريف
# واحدٍ هنا كان سيجعل `apps.core` يستورد `apps.people.models`، وcore بنية
# تحتية لا تعرف تطبيقاً تجارياً واحداً (ADR-008 · A-03).


def _set(actor, *, upload=None, effective_from=TODAY, slot=BrandAssetSlot.LOGO_PRIMARY, **kw):
    return branding_service.set_asset(
        actor=actor,
        slot=slot,
        upload=upload if upload is not None else _png(),
        effective_from=effective_from,
        note=kw.pop("note", "اعتماد الشعار الرسمي من دائرة العلاقات العامة"),
        **kw,
    )


# ---------------------------------------------------------------------------
# التأريخ: صفٌّ يُغلق ولا يُمحى
# ---------------------------------------------------------------------------
def test_a_second_upload_closes_the_first_and_deletes_nothing(user) -> None:
    first = _set(user)
    second = _set(user, upload=_png(name="new.png"), effective_from=LATER)

    first.refresh_from_db()
    assert first.effective_to == date(2026, 10, 31), "يُغلق في اليوم السابق فلا تداخل ولا فجوة"
    assert second.effective_to is None
    assert BrandAsset.objects.count() == 2, "التاريخ يبقى؛ لا صفَّ يُحذف عند التغيير"


def test_the_asset_read_is_the_one_in_force_on_that_date(user) -> None:
    first = _set(user)
    second = _set(user, upload=_png(name="new.png"), effective_from=LATER)

    assert branding_service.active_asset(BrandAssetSlot.LOGO_PRIMARY, as_of=TODAY) == first
    assert branding_service.active_asset(BrandAssetSlot.LOGO_PRIMARY, as_of=LATER) == second
    before = branding_service.active_asset(BrandAssetSlot.LOGO_PRIMARY, as_of=date(2026, 1, 1))
    assert before is None, "قبل أول رفع لم يكن هناك شعار — وهذه نتيجة لا عطل"


def test_changing_the_identity_is_audited(user) -> None:
    _set(user)
    event = AuditEvent.objects.filter(entity_type="core.BrandAsset").latest("id")
    assert event.action == "SETTING_CHANGE", "يُقرأ مع تغييرات القواعد لا مع إنشاء الصفوف"
    changes = event.changes or {}
    assert "العلاقات العامة" in changes["note"]


# ---------------------------------------------------------------------------
# الرفض: ما لا يصلح شعاراً يُردّ بسببه
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("upload", "fragment"),
    [
        (SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf"), "غير مقبول"),
        (SimpleUploadedFile("empty.png", b"", content_type="image/png"), "فارغ"),
        (_svg(b'<svg onload="steal()"></svg>'), "سكربت"),
        (_svg(b"<svg><script>x()</script></svg>"), "سكربت"),
    ],
)
def test_an_unusable_file_is_refused_with_its_reason(user, upload, fragment) -> None:
    with pytest.raises(ValidationError) as refusal:
        _set(user, upload=upload)
    assert fragment in str(refusal.value)
    assert not BrandAsset.objects.exists()


def test_a_tiny_raster_is_refused_because_it_prints_grainy(user) -> None:
    with pytest.raises(ValidationError) as refusal:
        _set(user, upload=_png(width=32, height=32))
    assert "32px" in str(refusal.value)


def test_an_oversized_file_is_refused(user) -> None:
    from apps.core.models.brand_asset import MAX_BRAND_ASSET_BYTES

    big = SimpleUploadedFile(
        "big.png", b"\x89PNG" + b"\x00" * MAX_BRAND_ASSET_BYTES, content_type="image/png"
    )
    with pytest.raises(ValidationError) as refusal:
        _set(user, upload=big)
    assert "الحد المسموح" in str(refusal.value)


def test_a_change_without_a_reason_is_refused(user) -> None:
    with pytest.raises(ValidationError):
        _set(user, note="   ")


# ---------------------------------------------------------------------------
# القراءة والغياب
# ---------------------------------------------------------------------------
def test_no_upload_means_no_url_and_no_broken_image(db) -> None:
    assert branding_service.asset_url(BrandAssetSlot.LOGO_PRIMARY, as_of=TODAY) is None
    context = branding_service.brand_context(as_of=TODAY)
    assert set(context) == {"logo_primary", "logo_print", "watermark", "favicon"}
    assert all(value is None for value in context.values())


def test_the_url_carries_the_file_digest(user) -> None:
    asset = _set(user)
    url = branding_service.asset_url(BrandAssetSlot.LOGO_PRIMARY, as_of=TODAY)
    assert url is not None
    assert url == asset.public_url
    assert asset.sha256[:12] in url
    assert url.endswith(".png")


def test_the_dimensions_are_read_from_the_file_header(user) -> None:
    asset = _set(user, upload=_png(width=300, height=120))
    assert (asset.width_px, asset.height_px) == (300, 120)


def test_an_svg_has_no_pixel_dimensions_and_claims_none(user) -> None:
    asset = _set(user, upload=_svg(b'<svg viewBox="0 0 10 10"></svg>'))
    assert asset.width_px is None and asset.height_px is None


# ---------------------------------------------------------------------------
# المسار العامّ
# ---------------------------------------------------------------------------
def test_the_logo_is_served_without_signing_in(client, user) -> None:
    """صفحة الدخول تحتاج الشعار قبل أن يوجد مستخدم."""
    asset = _set(user)
    response = client.get(asset.public_url)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert "immutable" in response["Cache-Control"]
    assert "sandbox" in response["Content-Security-Policy"]
    assert response["X-Content-Type-Options"] == "nosniff"


def test_a_wrong_digest_or_extension_is_a_404(client, user) -> None:
    asset = _set(user)
    wrong_digest = reverse(
        "core:brand-asset",
        kwargs={"slot": asset.slot, "digest": "0" * 12, "extension": "png"},
    )
    wrong_extension = reverse(
        "core:brand-asset",
        kwargs={"slot": asset.slot, "digest": asset.sha256[:12], "extension": "svg"},
    )
    assert client.get(wrong_digest).status_code == 404
    assert client.get(wrong_extension).status_code == 404


def test_a_superseded_logo_is_still_served_at_its_own_url(client, user) -> None:
    """
    العنوان مُعنوَن بالمحتوى: شعارٌ قديم بقي في تخزين متصفّح يُقدَّم كما كان،
    والصفحة الجديدة تطلب بصمةً جديدة فتأخذ الملف الجديد.
    """
    first = _set(user)
    _set(user, upload=_png(name="new.png", width=400), effective_from=LATER)

    assert client.get(first.public_url).status_code == 200


# ---------------------------------------------------------------------------
# الظهور: الشاشة والمستند — والغياب كما كان


# ---------------------------------------------------------------------------
def test_the_letterhead_prefers_the_print_logo(user) -> None:
    """الملوَّن يخرج لطخةً رمادية على طابعة أحادية، فالمخصَّص للطباعة أولاً."""
    from apps.core.services import document_settings

    _set(user, slot=BrandAssetSlot.LOGO_PRIMARY)
    print_logo = _set(user, slot=BrandAssetSlot.LOGO_PRINT, upload=_png(name="mono.png"))

    head = document_settings.letterhead(as_of=TODAY)
    assert head["logo_url"] == print_logo.public_url


def test_the_letterhead_falls_back_to_the_screen_logo(user) -> None:
    """مركزٌ رفع ملفاً واحداً لا يُترك بلا شعار على الورق."""
    from apps.core.services import document_settings

    primary = _set(user, slot=BrandAssetSlot.LOGO_PRIMARY)
    assert document_settings.letterhead(as_of=TODAY)["logo_url"] == primary.public_url


def test_the_letterhead_says_nothing_when_nothing_was_uploaded(db) -> None:
    from apps.core.services import document_settings

    head = document_settings.letterhead(as_of=TODAY)
    assert head["logo_url"] == ""
    assert head["university_ar"], "الاسم يبقى — المستند لا يُطبع بلا هوية نصّية"


def test_the_printed_letterhead_draws_the_logo_only_when_there_is_one(user) -> None:
    from django.template.loader import render_to_string

    from apps.core.services import document_settings

    blank = render_to_string(
        "print/_doc_head.html", {"chrome": document_settings.chrome(as_of=TODAY)}
    )
    assert "doc-logo" not in blank, "بلا رفعٍ لا يُرسم وسمُ صورةٍ فارغ"

    _set(user, slot=BrandAssetSlot.LOGO_PRINT)
    with_logo = render_to_string(
        "print/_doc_head.html", {"chrome": document_settings.chrome(as_of=TODAY)}
    )
    assert "doc-logo" in with_logo
    assert "جامعة البترا" in with_logo, "الشعار يُضاف إلى الاسمين ولا يحلّ محلّهما"
