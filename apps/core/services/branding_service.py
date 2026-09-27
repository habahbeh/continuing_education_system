"""
هوية المؤسسة المرئية — القراءة والكتابة (Sprint 8L).

**بنية تحتية لا قاعدة عمل** (A-03): هذه الوحدة تقرأ ``core.BrandAsset`` ولا
شيء غيره، ولا تقرّر مَن يحقّ له تغيير الشعار — ذلك سؤال المصفوفة، تسأله شاشة
الإعدادات قبل أن تنادي ``set_asset``.

``as_of`` مطلوبة بلا قيمة افتراضية، كما في ``settings_service``: المستند
المطبوع اليوم يحمل شعار اليوم، والمستند الذي يُعاد طبعه بتاريخٍ سابق يحمل ما
كان سارياً حينها — وأن تُنسى هذه الوسيطة فتُقرأ «اليوم» في موضع يقصد تاريخ
الحدث هو الخلل الصامت الذي تمنعه هذه التوقيعة.
"""

from __future__ import annotations

import hashlib
import re
import struct
from datetime import date, timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.core.models import BrandAsset
from apps.core.models.brand_asset import (
    ALLOWED_BRAND_MIME_TYPES,
    MAX_BRAND_ASSET_BYTES,
    BrandAssetSlot,
)
from apps.core.services.audit_service import write_audit

ENTITY = "core.BrandAsset"

_CHUNK = 64 * 1024

#: أنماطٌ تجعل ملفّ SVG برنامجاً لا صورة. يُرفض الملف ولا يُنظَّف: تعديلُ ملفٍّ
#: رسميٍّ من خلف صاحبه أسوأ من ردّه إليه، وردُّه يقول له ما العيب بالضبط.
_SVG_DANGER = re.compile(
    rb"<\s*script|<\s*foreignObject|javascript\s*:|\son[a-z]+\s*=|<\s*!ENTITY",
    re.IGNORECASE,
)

#: أقلّ عرضٍ مفيد لشعارٍ يُطبَع. أصغر منه يخرج متحبّباً على الورق.
MIN_RASTER_WIDTH_PX = 64


def _digest_and_size(upload: Any) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    for chunk in upload.chunks(_CHUNK):
        sha.update(chunk)
        size += len(chunk)
    upload.seek(0)
    return sha.hexdigest(), size


def _dimensions(head: bytes, mime_type: str) -> tuple[int | None, int | None]:
    """
    العرض والارتفاع من ترويسة الملف نفسه — بلا Pillow.

    المشروع لا يحمل اعتمادية معالجة صور، وإضافتها لأجل رقمين يُقرآن من ثمانية
    بايتات مبالغة. وما لا يُقاس يبقى ``None``: حقلٌ فارغ أصدق من صفرٍ مُختلَق.
    """
    try:
        if mime_type == "image/png" and head[:8] == b"\x89PNG\r\n\x1a\n":
            width, height = struct.unpack(">II", head[16:24])
            return int(width), int(height)
        if mime_type == "image/jpeg" and head[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(head):
                if head[index] != 0xFF:
                    index += 1
                    continue
                marker = head[index + 1]
                # SOF0…SOF15 عدا DHT/JPG/DAC — وهي وحدها التي تحمل الأبعاد.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", head[index + 5 : index + 9])
                    return int(width), int(height)
                (segment,) = struct.unpack(">H", head[index + 2 : index + 4])
                index += 2 + segment
    except (struct.error, IndexError, ValueError):
        return None, None
    return None, None


def active_asset(slot: str, *, as_of: date) -> BrandAsset | None:
    """الصفّ السارّي في ``as_of``، أو ``None`` حين لم يُرفع شيء بعد."""
    return (
        BrandAsset.objects.filter(slot=slot, effective_from__lte=as_of)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
        .order_by("-effective_from")
        .first()
    )


def asset_url(slot: str, *, as_of: date) -> str | None:
    """
    عنوان تقديم الملف، أو ``None`` — والـ ``None`` هنا نتيجة صحيحة.

    العنوان يحمل بصمة الملف، فتغيير الشعار يغيّر العنوان: يُخزَّن مؤقّتاً
    طويلاً بلا أن يعلق شعارٌ قديم في متصفّح أحد.
    """
    asset = active_asset(slot, as_of=as_of)
    return None if asset is None else asset.public_url


def brand_context(*, as_of: date) -> dict[str, Any]:
    """
    ما يحتاجه كل قالب: الشعار إن وُجد، ولا شيء إن لم يوجد.

    **استعلام واحد للخانات الأربع**، لا استعلاماً لكل خانة. هذه الدالة تُنادى
    في كل صفحة من صفحات النظام، وأربعة استعلامات في كل صفحة لأجل شعارٍ لا
    يتغيّر ثمنٌ يُدفع واحداً وسبعين مرّة.
    """
    rows = (
        BrandAsset.objects.filter(effective_from__lte=as_of)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
        .order_by("slot", "-effective_from")
    )
    latest: dict[str, BrandAsset] = {}
    for row in rows:
        latest.setdefault(row.slot, row)

    return {
        slot.lower(): (
            {
                "url": asset.public_url,
                "alt": asset.alt_text_ar,
                "mime_type": asset.mime_type,
            }
            if (asset := latest.get(slot)) is not None
            else None
        )
        for slot in BrandAssetSlot.values
    }


def asset_by_digest(*, slot: str, digest: str, extension: str) -> BrandAsset | None:
    """
    البحث بالبصمة لا بالتأريخ — العنوان مُعنوَن بالمحتوى.

    شعارٌ قديم بقي في تخزين متصفّحٍ يُقدَّم كما كان، وهو الصحيح؛ والصفحة
    الجديدة تطلب بصمةً جديدة فتأخذ الملف الجديد. والامتداد يُطابَق أيضاً حتى
    لا يُقدَّم PNG تحت اسم SVG.
    """
    asset = BrandAsset.objects.filter(slot=slot, sha256__startswith=digest).first()
    return asset if asset is not None and asset.extension == extension else None


def slot_state(*, as_of: date) -> list[dict[str, Any]]:
    """
    الخانات الأربع وما في كلٍّ منها اليوم — للشاشة التي تعرضها وتسمح برفعها.

    الخانة الفارغة تُعرَض فارغةً بنصّها، لا تُخفى: أن يرى المدير أن «شعار
    الطباعة» لم يُرفع بعد هو نصف الفائدة من الشاشة.
    """
    rows = (
        BrandAsset.objects.filter(effective_from__lte=as_of)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
        .order_by("slot", "-effective_from")
    )
    current: dict[str, BrandAsset | None] = dict.fromkeys(BrandAssetSlot.values)
    for row in rows:
        if current.get(row.slot) is None:
            current[row.slot] = row

    return [
        {
            "slot": slot,
            "label": BrandAssetSlot(slot).label,
            "asset": asset,
            "url": None if asset is None else asset.public_url,
            "since": None if asset is None else asset.effective_from,
        }
        for slot, asset in current.items()
    ]


@transaction.atomic
def set_asset(
    *,
    actor: Any,
    slot: str,
    upload: Any,
    effective_from: date,
    note: str,
    alt_text_ar: str = "",
    request: Any = None,
) -> BrandAsset:
    """
    يرفع ملفاً جديداً لخانةٍ ويغلق ما كان قبله.

    لا يُحذف صفٌّ أبداً: الصفّ السابق يُغلق في اليوم السابق لسريان الجديد، فلا
    تداخل ولا فجوة، ويبقى في الجدول مَن غيّر الشعار ومتى ولماذا.
    """
    if slot not in BrandAssetSlot.values:
        raise ValidationError(f"موضع هوية غير معروف: {slot}")
    if not note or not note.strip():
        raise ValidationError("تغيير الهوية يحتاج مبرّراً مكتوباً.")

    mime_type = (getattr(upload, "content_type", "") or "").lower()
    if mime_type not in ALLOWED_BRAND_MIME_TYPES:
        raise ValidationError("نوع الملف غير مقبول — المقبول: PNG · JPEG · WEBP · SVG.")

    sha256, size_bytes = _digest_and_size(upload)
    if size_bytes == 0:
        raise ValidationError("الملف فارغ — لا تُرفع صورة بلا محتوى.")
    if size_bytes > MAX_BRAND_ASSET_BYTES:
        megabytes = MAX_BRAND_ASSET_BYTES // (1024 * 1024)
        raise ValidationError(f"حجم الملف يتجاوز الحد المسموح ({megabytes} ميغابايت).")

    head = upload.read(_CHUNK)
    upload.seek(0)

    if mime_type == "image/svg+xml" and _SVG_DANGER.search(head):
        raise ValidationError(
            "ملف SVG يحمل سكربتاً أو مرجعاً خارجياً — يُرفض. "
            "صدّر الشعار من برنامج التصميم بلا تفاعل، أو ارفعه PNG."
        )

    width_px, height_px = _dimensions(head, mime_type)
    if width_px is not None and width_px < MIN_RASTER_WIDTH_PX:
        raise ValidationError(
            f"عرض الصورة {width_px}px أصغر من {MIN_RASTER_WIDTH_PX}px — تخرج متحبّبة على الورق."
        )

    superseded = (
        BrandAsset.objects.select_for_update()
        .filter(slot=slot, effective_to__isnull=True)
        .exclude(effective_from__gt=effective_from)
    )
    closed = [asset.original_filename for asset in superseded]
    superseded.update(effective_to=effective_from - timedelta(days=1))

    asset = BrandAsset.objects.create(
        slot=slot,
        file=upload,
        original_filename=(getattr(upload, "name", "") or "")[:255],
        mime_type=mime_type[:100],
        size_bytes=size_bytes,
        sha256=sha256,
        width_px=width_px,
        height_px=height_px,
        alt_text_ar=alt_text_ar[:200],
        effective_from=effective_from,
        note=note,
        uploaded_by=actor,
    )
    # ``SETTING_CHANGE`` لا ``CREATE``: ما جرى تغييرُ إعدادٍ للمؤسسة، ومَن يسأل
    # لاحقاً «مَن غيّر الشعار؟» يسأله في المجرى الذي يقرأ فيه تغييرات القواعد،
    # لا في مجرى إنشاء الصفوف.
    write_audit(
        action="SETTING_CHANGE",
        entity_type=ENTITY,
        entity_id=str(asset.pk),
        reference=slot,
        summary_ar=f"تغيير الهوية — {asset.get_slot_display()} · {asset.original_filename}",
        actor=actor,
        changes={
            "slot": slot,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "effective_from": effective_from.isoformat(),
            "superseded": closed,
            "note": note.strip(),
        },
        request=request,
    )
    return asset


__all__ = [
    "MIN_RASTER_WIDTH_PX",
    "active_asset",
    "asset_by_digest",
    "asset_url",
    "brand_context",
    "set_asset",
    "slot_state",
]
