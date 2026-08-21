"""Root URL configuration."""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from apps.core.views import health

urlpatterns = [
    path("", health, name="health"),
    # people owns authentication (Q-12), users, participants and the audit
    # screen. Mounted at the root because the app now serves several distinct
    # sections; the auth paths keep their /auth/ prefix inside the app so they
    # are never confused with a future SSO callback mount point.
    path("", include("apps.people.urls")),
    path("", include("apps.catalog.urls")),
    # Sprint 8B — the operational screens. Mounted at the root like the rest
    # so every URL reads as a path through the centre's work rather than
    # through the code that happens to implement it.
    path("", include("apps.operations.urls")),
    path("", include("apps.cashbox.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.partners.urls")),
    path("", include("apps.settlements.urls")),
    path("admin/", admin.site.urls),
]
