"""Root URL configuration."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

from apps.core.views import health

urlpatterns = [
    path("", health, name="health"),
    path("admin/", admin.site.urls),
]
