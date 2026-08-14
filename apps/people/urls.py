"""People URLs — authentication and the users screen (Q-12, Δ-02)."""

from __future__ import annotations

from django.urls import path

from apps.people import views

app_name = "people"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("locked/", views.locked_view, name="locked"),
    path("session-expired/", views.session_expired_view, name="session-expired"),
    path("users/", views.users_view, name="users"),
]
