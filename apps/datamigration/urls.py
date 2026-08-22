"""Historical archive URLs (Sprint 8D-1)."""

from __future__ import annotations

from django.urls import path

from apps.datamigration import views

app_name = "datamigration"

urlpatterns = [
    path("migration/", views.batches_view, name="batches"),
    path("migration/links/", views.links_view, name="links"),
    path("migration/<str:code>/", views.batch_detail_view, name="batch-detail"),
]
