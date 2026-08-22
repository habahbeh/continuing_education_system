"""
Storing an uploaded document (DATA_MODEL §3.5, Q-13).

**Written because Sprint 8G could not be built without it.** BR-016 refuses to
send a ministry file that is missing the trainer's CV or the entity licence,
and ``core.Attachment`` has existed since Sprint 1 to hold them — but nothing
in the system had ever WRITTEN one. Every attachment in the repository was
built by a test calling ``Attachment.objects.create`` with a hand-computed
digest. A submit button on a screen would therefore have been unpressable:
the guard is real, and there was no supported way to satisfy it.

**Infrastructure, not a business rule** (A-03). This module reads
``core.Attachment`` and nothing else, and it decides nothing about WHO may
attach WHAT to WHICH record — that answer belongs to the screen's own service,
which holds the matrix cell. ``mohe_service.attach_document`` runs
``policy.require`` and then calls this.

**Q-13 stays open and is not answered here.** Retention, virus scanning and
whether files should live outside the database are the client's questions.
What this enforces is only what the model already declares: a file with
content, within ``MAX_ATTACHMENT_BYTES``, recorded with the digest that lets
anyone check later that the bytes on disk are the bytes that were uploaded.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import Attachment
from apps.core.models.attachment import MAX_ATTACHMENT_BYTES, AttachmentPurpose
from apps.core.services.audit_service import write_audit

ENTITY = "core.Attachment"

#: Read in chunks rather than ``upload.read()``: a 10 MB ceiling is not a
#: reason to hold 10 MB per request in memory, and Django hands large uploads
#: over as a temporary file precisely so nobody has to.
_CHUNK = 64 * 1024


def _digest_and_size(upload: Any) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    for chunk in upload.chunks(_CHUNK):
        sha.update(chunk)
        size += len(chunk)
    upload.seek(0)
    return sha.hexdigest(), size


def attach(
    *,
    actor: Any,
    target: Any,
    purpose: str,
    upload: Any,
    request: Any = None,
) -> Attachment:
    """
    Store one uploaded file against one record.

    The caller has already asked the permission question. What is checked here
    is the file itself, in the words the person who chose it needs: an empty
    file and a file three times the limit are different mistakes.

    Replacing rather than accumulating is deliberate for a PURPOSE that is
    singular. BR-016 asks whether the trainer's CV is attached, not how many
    times someone tried — a second upload for the same purpose supersedes the
    first, and the audit trail keeps the fact that it happened.
    """
    from django.contrib.contenttypes.models import ContentType

    if purpose not in AttachmentPurpose.values:
        raise ValidationError(f"غرض مرفق غير معروف: {purpose}")

    sha256, size_bytes = _digest_and_size(upload)
    if size_bytes == 0:
        raise ValidationError("الملف فارغ — لا يُرفع مرفق بلا محتوى.")
    if size_bytes > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise ValidationError(f"حجم الملف يتجاوز الحد المسموح ({megabytes} ميغابايت).")

    content_type = ContentType.objects.get_for_model(type(target))
    with transaction.atomic():
        superseded = Attachment.objects.filter(
            content_type=content_type, object_id=str(target.pk), purpose=purpose
        )
        replaced = [a.original_filename for a in superseded]
        superseded.delete()

        attachment = Attachment.objects.create(
            content_type=content_type,
            object_id=str(target.pk),
            purpose=purpose,
            file=upload,
            original_filename=(getattr(upload, "name", "") or "")[:255],
            mime_type=(getattr(upload, "content_type", "") or "application/octet-stream")[:100],
            size_bytes=size_bytes,
            sha256=sha256,
            uploaded_by=actor,
        )
        write_audit(
            action="UPDATE" if replaced else "CREATE",
            entity_type=ENTITY,
            entity_id=str(attachment.pk),
            reference=f"{content_type.app_label}.{content_type.model}#{target.pk}",
            summary_ar=(
                f"رفع مرفق — {attachment.get_purpose_display()} · {attachment.original_filename}"
            ),
            actor=actor,
            changes={
                "purpose": purpose,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "replaced": replaced,
            },
            request=request,
        )
    return attachment


def attachments_for(target: Any) -> list[dict[str, Any]]:
    """The documents held against one record, projected for a screen (A-05)."""
    from django.contrib.contenttypes.models import ContentType

    content_type = ContentType.objects.get_for_model(type(target))
    return [
        {
            "purpose": a.purpose,
            "purpose_display": a.get_purpose_display(),
            "original_filename": a.original_filename,
            "size_bytes": a.size_bytes,
            "sha256": a.sha256,
            "uploaded_at": a.uploaded_at,
        }
        for a in Attachment.objects.filter(
            content_type=content_type, object_id=str(target.pk)
        ).order_by("purpose")
    ]


__all__ = ["ENTITY", "attach", "attachments_for"]
