"""
User administration (Δ-02, PERMISSIONS.md footnote 25).

Managing users and roles is NOT a centre manager function — it belongs to the
IT department's SYSTEM_ADMINISTRATOR. The centre manager sees the matrix and
nothing more.

The one thing this module deliberately does not offer, anywhere, at any
privilege level, is changing the role attached to the CURRENT session (D-20).
The demo let anyone become the centre manager from a dropdown; the role here
comes from the account, and changing an account's role takes effect the next
time that account authenticates.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "people.User"


def get_user(*, pk: Any) -> Any:
    from apps.people.models import User

    return User.objects.get(pk=pk)


def list_users() -> Any:
    from apps.people.models import User

    return User.objects.order_by("full_name_ar", "username")


def create_user(
    *,
    actor: Any,
    username: str,
    password: str,
    full_name_ar: str,
    role: str,
    department: str = "",
    request: Any = None,
) -> Any:
    from apps.people.models import User

    # Outside the transaction — see the module note on why refusals must not
    # be rolled back.
    policy.require(actor, Screen.USERS, Action.CREATE, request=request)

    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            password=password,
            full_name_ar=full_name_ar,
            role=role,
            department=department,
        )
        write_audit(
            action="CREATE",
            entity_type=ENTITY,
            entity_id=str(user.pk),
            reference=username,
            summary_ar=f"إنشاء مستخدم — {full_name_ar or username} بدور {role}",
            actor=actor,
            changes={"role": role, "department": department},
            request=request,
        )
    return user


def set_role(*, actor: Any, target: Any, role: str, request: Any = None) -> Any:
    """
    Change an account's role.

    Guarded against self-assignment: an administrator promoting their own
    account is the escalation path D-20 exists to close, and it is refused
    here regardless of privilege.
    """
    policy.require(actor, Screen.USERS, Action.EDIT, request=request)

    if getattr(actor, "pk", None) == getattr(target, "pk", None):
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=ENTITY,
            entity_id=str(target.pk),
            reference=target.get_username(),
            summary_ar="محاولة تغيير دور الحساب الحالي",
            actor=actor,
            denial_rule="D-20",
            request=request,
        )
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("لا يجوز تغيير دور حسابك الحالي (D-20)")

    previous = target.role

    with transaction.atomic():
        target.role = role
        target.save(update_fields=["role"])

        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(target.pk),
            reference=target.get_username(),
            summary_ar=f"تغيير الدور من {previous or '—'} إلى {role}",
            actor=actor,
            changes={"role": {"from": previous, "to": role}},
            request=request,
        )
    return target


def set_active(*, actor: Any, target: Any, is_active: bool, request: Any = None) -> Any:
    policy.require(actor, Screen.USERS, Action.EDIT, request=request)

    with transaction.atomic():
        target.is_active = is_active
        target.save(update_fields=["is_active"])
        write_audit(
            action="UPDATE",
            entity_type=ENTITY,
            entity_id=str(target.pk),
            reference=target.get_username(),
            summary_ar="تفعيل الحساب" if is_active else "تعطيل الحساب",
            actor=actor,
            changes={"is_active": is_active},
            request=request,
        )
    return target


__all__ = ["create_user", "get_user", "list_users", "set_active", "set_role"]
