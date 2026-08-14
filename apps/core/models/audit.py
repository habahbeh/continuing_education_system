"""
Append-only audit trail (DATA_MODEL §3.4, ADR-010, BR-084 / BR-085).

Three layers of protection, all required:

1. **Application** — ``save()`` refuses to update an existing row and
   ``delete()`` always refuses.
2. **Database** — the application DB user is granted only INSERT and SELECT on
   ``core_auditevent``. See ``verify_audit_grants`` and the README.
3. **Cryptographic** — ``row_hash = SHA256(prev_hash ‖ canonical fields)``.
   Tampering breaks the chain, which ``verify_audit_chain`` detects.

Layer 3 is cheap and turns "we trust the DBA" into evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ImmutableRecordError
from apps.core.fields import DisplayRef, ShortCode

#: Seed for the first link of the chain.
GENESIS_HASH = "0" * 64


class AuditAction(models.TextChoices):
    CREATE = "CREATE", _("إنشاء")
    UPDATE = "UPDATE", _("تعديل")
    APPROVE = "APPROVE", _("اعتماد")
    REJECT = "REJECT", _("رفض")
    VOID = "VOID", _("إلغاء")
    RECEIVE_CASH = "RECEIVE_CASH", _("قبض")
    LOGIN = "LOGIN", _("دخول")
    LOGOUT = "LOGOUT", _("خروج")
    # Q-12 (Sprint 2A): the authentication lifecycle is evidence, not logging.
    # In a system that takes cash, "who was logged in, and who tried and
    # failed" is part of the audit record, so each outcome gets its own code
    # rather than being flattened into LOGIN with a note.
    LOGIN_FAILED = "LOGIN_FAILED", _("محاولة دخول فاشلة")
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED", _("قفل حساب")
    ACCOUNT_UNLOCKED = "ACCOUNT_UNLOCKED", _("فكّ قفل حساب")
    SESSION_EXPIRED = "SESSION_EXPIRED", _("انتهاء جلسة للخمول")
    VIEW_SENSITIVE = "VIEW_SENSITIVE", _("عرض حساس")
    DENIED_ATTEMPT = "DENIED_ATTEMPT", _("محاولة مرفوضة")
    SETTING_CHANGE = "SETTING_CHANGE", _("تغيير إعداد")


class AuditEvent(models.Model):
    # Explicit default rather than auto_now_add: the row hash must cover the
    # timestamp, so the timestamp has to exist BEFORE the INSERT and the event
    # must be written in ONE statement. Layer 2 strips UPDATE privilege on this
    # table, which makes a post-insert "fill in the hash" UPDATE impossible —
    # by design, not by accident.
    occurred_at = models.DateTimeField(
        default=timezone.now, db_index=True, editable=False, verbose_name=_("الوقت")
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name=_("المستخدم"),
    )
    # Snapshotted: the role AT THE TIME of the event. Changing a user's role
    # later must not rewrite history (ADR-012).
    actor_role = ShortCode(blank=True, verbose_name=_("الدور وقت الحدث"))

    action = ShortCode(choices=AuditAction.choices, verbose_name=_("الإجراء"))
    entity_type = ShortCode(verbose_name=_("الكيان"))
    entity_id = models.CharField(max_length=64, blank=True, verbose_name=_("مُعرِّف الكيان"))
    reference = DisplayRef(blank=True, verbose_name=_("المرجع المعروض"))

    summary_ar = models.CharField(max_length=500, verbose_name=_("التفصيل"))
    changes = models.JSONField(null=True, blank=True, verbose_name=_("التغييرات"))

    # Set when action == DENIED_ATTEMPT: which rule blocked it (BR-085).
    denial_rule = ShortCode(blank=True, verbose_name=_("القاعدة المانعة"))

    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("عنوان IP"))
    user_agent = models.CharField(max_length=255, blank=True, verbose_name=_("المتصفح"))

    prev_hash = models.CharField(max_length=64, editable=False, verbose_name=_("البصمة السابقة"))
    row_hash = models.CharField(max_length=64, editable=False, verbose_name=_("بصمة السجل"))

    class Meta:
        verbose_name = _("قيد تدقيق")
        verbose_name_plural = _("سجل التدقيق")
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "occurred_at"],
                name="core_audit_entity_idx",
            ),
            models.Index(fields=["actor", "occurred_at"], name="core_audit_actor_idx"),
            models.Index(fields=["action", "occurred_at"], name="core_audit_action_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(action="DENIED_ATTEMPT") | ~models.Q(denial_rule=""),
                name="core_audit_denied_requires_rule",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.occurred_at:%Y-%m-%d %H:%M}] {self.action} {self.entity_type}"

    # -- Layer 1: application immutability ----------------------------------
    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ImmutableRecordError(
                "AuditEvent is append-only (BR-084). "
                "Correct a mistake with a new event, never by editing one."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Always raises. The return type mirrors the supertype for typing only."""
        raise ImmutableRecordError("AuditEvent cannot be deleted (BR-084).")

    # -- Layer 3: hash chain ------------------------------------------------
    def canonical_payload(self) -> str:
        """
        Deterministic serialisation of the legally meaningful fields.

        ``occurred_at`` is included via isoformat. Field order is fixed by
        ``sort_keys`` so the digest is reproducible across processes.
        """
        payload: dict[str, Any] = {
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "reference": self.reference,
            "summary_ar": self.summary_ar,
            "changes": self.changes,
            "denial_rule": self.denial_rule,
            "ip_address": self.ip_address,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def compute_row_hash(self) -> str:
        material = f"{self.prev_hash}{self.canonical_payload()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
