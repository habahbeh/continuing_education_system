"""Root URL configuration."""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from apps.core.views import health

urlpatterns = [
    path("", health, name="health"),
    # Q-12 — local authentication. Kept off /accounts/ so it is never confused
    # with a future SSO callback mount point.
    path("auth/", include("apps.people.urls")),
    path("admin/", admin.site.urls),
]
