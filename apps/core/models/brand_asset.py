"""
هوية المؤسسة المرئية — الشعار وما يجري مجراه (Sprint 8L, ADR-009, BR-086).

**لماذا نموذج لا إعداداً.** ``EffectiveSetting.value`` حقل نصّي بطول ٢٥٥:
يسع مساراً لا ملفاً. وحشر مسارٍ فيه يعني أن يرفع أحدهم الملف بطريقة أخرى ثم
يكتب مساره يدوياً — وهذا ليس «يُضبط من الشاشة» بل «يُضبط من الخادم».

**ولماذا مؤرّخ.** الفلسفة نفسها التي تقوم عليها ``EffectiveSetting``: تغيير
الشعار فعلٌ إداري موثَّق بمبرّره ومَن فعله ومتى، لا استبدالُ ملفٍ يمحو ما
قبله. صفٌّ يُغلق وصفٌّ يُفتح، والرجوع عن التغيير صفٌّ ثالث لا استرجاعُ نسخة
احتياطية.

**وأربع خانات لا خانة واحدة.** شعار الشاشة ملوَّن على أبيض؛ شعار الطباعة يجب
أن يبقى مقروءاً بطابعة أحادية اللون؛ العلامة المائية تحتاج نسخة باهتة أو
مفرَّغة؛ والأيقونة مربّعة صغيرة. مركزٌ يملك ملفاً واحداً يملأ به الأربع ولا
يُسأل — لكن حين يصله الملف الرسمي أحادي اللون لا ينتظر إصداراً برمجياً.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import ShortCode

#: ٢ ميغابايت. الشعار يُقدَّم في كل صفحة، فحدُّه ليس حدَّ مرفقٍ يُفتح مرّة.
MAX_BRAND_ASSET_BYTES = 2 * 1024 * 1024

#: ما يُقبل رفعه. SVG مقبول لأنه يطبع بلا تحبُّب، وثمنه تنظيفٌ عند الرفع.
ALLOWED_BRAND_MIME_TYPES: tuple[str, ...] = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
)


#: امتداد الملف المقدَّم، من نوع المحتوى لا من اسم ما رُفع — الاسم يكذب.
_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


class BrandAssetSlot(models.TextChoices):
    LOGO_PRIMARY = "LOGO_PRIMARY", _("شعار الشاشة")
    LOGO_PRINT = "LOGO_PRINT", _("شعار الطباعة")
    WATERMARK = "WATERMARK", _("العلامة المائية")
    FAVICON = "FAVICON", _("أيقونة المتصفح")


class BrandAsset(models.Model):
    slot = ShortCode(
        choices=BrandAssetSlot.choices,
        db_index=True,
        verbose_name=_("الموضع"),
    )

    # MEDIA_ROOT خارج جذر الويب؛ يُقدَّم هذا الملف عبر مسار عامّ مخصَّص يقرأ
    # هذا الجدول وحده، لأن صفحة الدخول تحتاج الشعار قبل المصادقة.
    file = models.FileField(upload_to="branding/%Y/%m/", verbose_name=_("الملف"))
    original_filename = models.CharField(max_length=255, verbose_name=_("اسم الملف الأصلي"))
    mime_type = models.CharField(max_length=100, verbose_name=_("نوع المحتوى"))
    size_bytes = models.PositiveIntegerField(verbose_name=_("الحجم"))
    sha256 = models.CharField(max_length=64, db_index=True, verbose_name=_("البصمة"))

    # فارغان لـ SVG: ملفٌّ متّجه لا أبعاد بكسل له، وكتابة صفرٍ فيهما كذب.
    width_px = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("العرض"))
    height_px = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("الارتفاع"))

    alt_text_ar = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("النص البديل"),
        help_text=_("ما يقرؤه قارئ الشاشة مكان الصورة"),
    )

    effective_from = models.DateField(verbose_name=_("سارٍ من"))
    effective_to = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("سارٍ حتى"),
        help_text=_("فارغ = سارٍ حتى إشعار آخر"),
    )

    note = models.TextField(verbose_name=_("مبرّر التغيير"))

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_brand_assets",
        verbose_name=_("رفعه"),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name=_("وقت الرفع"))

    class Meta:
        verbose_name = _("عنصر هوية")
        verbose_name_plural = _("عناصر الهوية")
        ordering = ["slot", "-effective_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="core_brand_asset_period_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0),
                name="core_brand_asset_has_content",
            ),
            # صفٌّ واحد لكل (موضع، تاريخ بدء) — كقيد EffectiveSetting نفسه.
            # منعُ التداخل الكامل يحتاج منطق مدى لا تعبّر عنه MySQL، فيُفرض في
            # ``branding_service.set_asset`` ويغطّيه اختبار.
            models.UniqueConstraint(
                fields=["slot", "effective_from"],
                name="core_brand_asset_unique_slot_from",
            ),
        ]
        indexes = [
            models.Index(fields=["slot", "effective_from"], name="core_brand_slot_from_idx"),
            models.Index(fields=["slot", "effective_to"], name="core_brand_slot_to_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_slot_display()} — {self.original_filename} ({self.effective_from})"

    @property
    def extension(self) -> str:
        return _EXTENSIONS.get(self.mime_type, "bin")

    @property
    def public_url(self) -> str:
        """
        عنوان التقديم، وفيه بصمة الملف قصداً.

        عنوانٌ ثابت لشعارٍ متغيّر يعني شعاراً قديماً عالقاً في متصفّح موظّف
        بعد تغييره؛ وبصمةٌ في العنوان تجعل التخزين المؤقّت طويلاً وآمناً معاً.
        """
        from django.urls import reverse

        return reverse(
            "core:brand-asset",
            kwargs={"slot": self.slot, "digest": self.sha256[:12], "extension": self.extension},
        )
