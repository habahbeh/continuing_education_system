"""People URLs — authentication, users, participants, audit (Sprint 2A + 2B)."""

from __future__ import annotations

from django.urls import path

from apps.people import views

app_name = "people"

urlpatterns = [
    # Authentication (Q-12)
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/locked/", views.locked_view, name="locked"),
    path("auth/session-expired/", views.session_expired_view, name="session-expired"),
    # Users (row 33, Δ-02)
    path("auth/users/", views.users_view, name="users"),
    path("auth/users/action/", views.user_action_view, name="user-action"),
    # Participants (rows 3 and 4)
    path("participants/", views.participants_view, name="participants"),
    path("participants/new/", views.participant_new_view, name="participant-new"),
    path(
        "participants/<str:number>/",
        views.participant_detail_view,
        name="participant-detail",
    ),
    path(
        "participants/<str:number>/edit/",
        views.participant_edit_view,
        name="participant-edit",
    ),
    # Audit trail (row 34) — read only, D-11
    path("audit/", views.audit_view, name="audit"),
    # Sprint 8K — the read-only system screens from the demo sidebar. Their
    # paths stay at the root; only the app that guards them changed (ADR-008).
    path("settings/", views.settings_view, name="settings"),
    path("coverage/", views.coverage_view, name="coverage"),
    path("future/", views.future_view, name="future"),
]
