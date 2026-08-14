"""
Authentication and user-administration views (Q-12, Δ-02).

Views render and delegate. Every permission question is answered by
``permissions.policy.require`` — never by an ``if user.role ==`` in here, and
never by hiding a button in a template. A-05 keeps this honest: views may not
import models directly.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.people.constants import Action, Screen
from apps.people.forms import LoginForm
from apps.people.permissions import policy
from apps.people.services import auth_service, user_service


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
        },
    )
