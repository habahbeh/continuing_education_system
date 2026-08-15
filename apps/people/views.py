"""
Authentication and user-administration views (Q-12, Δ-02).

Views render and delegate. Every permission question is answered by
``permissions.policy.require`` — never by an ``if user.role ==`` in here, and
never by hiding a button in a template. A-05 keeps this honest: views may not
import models directly.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.people.constants import (
    PARTICIPANT_CATEGORY_CHOICES,
    ROLE_CHOICES,
    Action,
    Screen,
)
from apps.people.forms import LoginForm
from apps.people.participant_forms import ParticipantEditForm, ParticipantForm
from apps.people.permissions import policy
from apps.people.services import (
    audit_query_service,
    auth_service,
    participant_service,
    user_service,
)
from apps.people.services.participant_numbering import NoActiveSemesterError


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("health")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = auth_service.attempt_login(
                request,
                form.cleaned_data["username"],
                form.cleaned_data["password"],
            )
        except auth_service.AccountLockedError:
            return redirect("people:locked")

        if user is not None:
            return redirect("health")

        # One message for every failure mode. Telling the visitor which half
        # was wrong tells them which usernames exist.
        messages.error(request, _("اسم المستخدم أو كلمة المرور غير صحيحة"))

    return render(request, "people/login.html", {"form": form})


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> HttpResponse:
    auth_service.perform_logout(request)
    return redirect("people:login")


def locked_view(request: HttpRequest) -> HttpResponse:
    return render(request, "people/locked.html", status=403)


def session_expired_view(request: HttpRequest) -> HttpResponse:
    return render(request, "people/session_expired.html", status=440)


def users_view(request: HttpRequest) -> HttpResponse:
    """
    The users screen (PERMISSIONS.md row 33).

    CENTER_MANAGER and AUDIT_ACCOUNT read it; SYSTEM_ADMINISTRATOR administers
    it. There is no control here that changes the current session's role, at
    any privilege level (D-20).
    """
    policy.require(request.user, Screen.USERS, Action.VIEW, request=request)
    return render(
        request,
        "people/users.html",
        {
            "users": user_service.list_users(),
            "can_edit": policy.is_allowed(request.user, Screen.USERS, Action.EDIT),
            "roles": ROLE_CHOICES,
        },
    )


@require_http_methods(["POST"])
def user_action_view(request: HttpRequest) -> HttpResponse:
    """
    System-administrator actions on a user account (Δ-02).

    Each branch delegates to a service that runs its own policy check — this
    view chooses which operation, never whether it is allowed.
    """
    action = request.POST.get("action", "")
    target = user_service.get_user(pk=request.POST.get("user_id", ""))

    try:
        if action == "set_role":
            user_service.set_role(
                actor=request.user,
                target=target,
                role=request.POST.get("role", ""),
                request=request,
            )
            messages.success(request, _("تم تحديث الدور"))
        elif action == "toggle_active":
            user_service.set_active(
                actor=request.user,
                target=target,
                is_active=not target.is_active,
                request=request,
            )
            messages.success(request, _("تم تحديث حالة الحساب"))
        elif action == "unlock":
            auth_service.unlock_account(
                target=target,
                actor=request.user,
                reason=request.POST.get("reason", ""),
                request=request,
            )
            messages.success(request, _("تم فكّ قفل الحساب"))
        else:
            messages.error(request, _("إجراء غير معروف"))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("people:users")


# ---------------------------------------------------------------------------
# Participants (PERMISSIONS.md rows 3 and 4)
# ---------------------------------------------------------------------------
def participants_view(request: HttpRequest) -> HttpResponse:
    """
    The participants list.

    Rows arrive already projected to the caller's permitted field set — a
    restricted role's forbidden fields are absent from the response, not merely
    unrendered (BR-101, T-283).
    """
    rows = participant_service.list_participants(
        actor=request.user,
        query=request.GET.get("q", "").strip(),
        category=request.GET.get("category", "").strip(),
        request=request,
    )
    return render(
        request,
        "people/participants.html",
        {
            "participants": rows,
            "columns": participant_service.visible_fields_for(request.user),
            "categories": PARTICIPANT_CATEGORY_CHOICES,
            "query": request.GET.get("q", ""),
            "selected_category": request.GET.get("category", ""),
            "can_create": policy.is_allowed(request.user, Screen.STUDENT_NEW, Action.CREATE),
            "can_edit": policy.is_allowed(request.user, Screen.STUDENTS, Action.EDIT),
        },
    )


def participant_detail_view(request: HttpRequest, number: str) -> HttpResponse:
    try:
        rows = participant_service.get_participant_display(
            actor=request.user, participant_number=number, request=request
        )
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد مشارك بهذا الرقم")) from None

    values = {key: value for key, _label, value in rows}
    return render(
        request,
        "people/participant_detail.html",
        {
            "rows": rows,
            "number": values["participant_number"],
            "name_ar": values["name_ar"],
            "can_edit": policy.is_allowed(request.user, Screen.STUDENTS, Action.EDIT),
        },
    )


@require_http_methods(["GET", "POST"])
def participant_new_view(request: HttpRequest) -> HttpResponse:
    policy.require(request.user, Screen.STUDENT_NEW, Action.VIEW, request=request)

    form = ParticipantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            participant = participant_service.create_participant(
                actor=request.user,
                data=form.to_service_data(),
                duplicate_override_reason=form.cleaned_data["duplicate_override_reason"],
                request=request,
            )
        except DjangoValidationError as exc:
            _apply_errors(form, exc)
        except NoActiveSemesterError as exc:
            # BR-001 — no silent fallback to today's year.
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("تم إنشاء المشارك برقم %(number)s") % {"number": participant.participant_number},
            )
            return redirect("people:participant-detail", number=participant.participant_number)

    return render(request, "people/participant_new.html", {"form": form})


@require_http_methods(["GET", "POST"])
def participant_edit_view(request: HttpRequest, number: str) -> HttpResponse:
    policy.require(request.user, Screen.STUDENTS, Action.EDIT, request=request)

    try:
        participant = participant_service.get_editable(
            actor=request.user, participant_number=number, request=request
        )
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد مشارك بهذا الرقم")) from None

    # Only the fields that exist on the model — the form also carries the
    # BR-005 override reason, which is an answer to a question, not a stored value.
    initial = {
        field: getattr(participant, field)
        for field in ParticipantEditForm.base_fields
        if hasattr(participant, field)
    }
    form = ParticipantEditForm(request.POST or None, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            participant_service.update_participant(
                actor=request.user,
                participant=participant,
                data=form.to_service_data(),
                request=request,
            )
        except DjangoValidationError as exc:
            _apply_errors(form, exc)
        else:
            messages.success(request, _("تم حفظ التعديلات"))
            return redirect("people:participant-detail", number=number)

    return render(request, "people/participant_edit.html", {"form": form, "number": number})


def _apply_errors(form: ParticipantForm, exc: DjangoValidationError) -> None:
    """Surface a service-layer ValidationError next to the field it concerns."""
    for field, messages_list in getattr(exc, "message_dict", {"__all__": [str(exc)]}).items():
        for message in messages_list:
            form.add_error(field if field in form.fields else None, message)


# ---------------------------------------------------------------------------
# Audit trail (PERMISSIONS.md row 34) — read only, D-11
# ---------------------------------------------------------------------------
@require_http_methods(["GET"])
def audit_view(request: HttpRequest) -> HttpResponse:
    """
    Browsing the audit trail.

    GET only, by decorator. BR-084 makes the log append-only for every role
    without exception, and Sprint 1 removed UPDATE and DELETE from the
    application database user — this screen simply offers nothing to try.
    """
    events = audit_query_service.browse_audit(
        actor=request.user,
        action=request.GET.get("action", "").strip(),
        actor_username=request.GET.get("actor", "").strip(),
        entity_type=request.GET.get("entity", "").strip(),
        request=request,
    )
    return render(
        request,
        "core/audit.html",
        {
            "events": events,
            "actions": audit_query_service.distinct_actions(),
            "filters": {
                "action": request.GET.get("action", ""),
                "actor": request.GET.get("actor", ""),
                "entity": request.GET.get("entity", ""),
            },
        },
    )
