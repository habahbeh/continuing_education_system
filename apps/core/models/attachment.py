"""Generic attachment (DATA_MODEL §3.5, OPEN_QUESTIONS Q-13)."""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import ShortCode

#: 10 MB. Attachments are the contractual evidence behind every financial
#: exception in this system, so they are bounded but not tiny.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class AttachmentPurpose(models.TextChoices):
    TRAINER_CV = "TRAINER_CV", _("السيرة الذاتية للمدرب")
    ENTITY_LICENSE = "ENTITY_LICENSE", _("تراخيص الجهة")
    OFFICIAL_LETTER = "OFFICIAL_LETTER", _("كتاب رسمي")
    PRESIDENT_APPROVAL = "PRESIDENT_APPROVAL", _("موافقة رئيس الجامعة")
    SIGNED_AGREEMENT = "SIGNED_AGREEMENT", _("اتفاقية موقّعة")
    SETTLEMENT = "SETTLEMENT", _("مخالصة موقّعة")
    INVOICE = "INVOICE", _("فاتورة")
    OTHER = "OTHER", _("أخرى")


class Attachment(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")

    purpose = ShortCode(choices=AttachmentPurpose.choices, verbose_name=_("الغرض"))

    # MEDIA_ROOT sits outside the web root; files are served through a
    # permission-checked view, never a direct link (Q-13).
    file = models.FileField(upload_to="attachments/%Y/%m/", verbose_name=_("الملف"))
    original_filename = models.CharField(max_length=255, verbose_name=_("اسم الملف الأصلي"))
    mime_type = models.CharField(max_length=100, verbose_name=_("نوع المحتوى"))
    size_bytes = models.PositiveIntegerField(verbose_name=_("الحجم"))
    sha256 = models.CharField(max_length=64, db_index=True, verbose_name=_("البصمة"))

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_attachments",
        verbose_name=_("رفعه"),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name=_("وقت الرفع"))

    class Meta:
        verbose_name = _("مرفق")
        verbose_name_plural = _("المرفقات")
        ordering = ["-uploaded_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0)
                & models.Q(size_bytes__lte=MAX_ATTACHMENT_BYTES),
                name="core_attachment_size_within_limit",
            ),
        ]
        indexes = [
            models.Index(
                fields=["content_type", "object_id", "purpose"],
                name="core_attachment_target_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} — {self.original_filename}"
