"""Reporting URLs — the seven of §9 (Sprint 8C-2)."""

from __future__ import annotations

from django.urls import path

from apps.reporting import views

app_name = "reporting"

urlpatterns = [
    path("reports/", views.reports_index, name="reports"),
    path("reports/<int:number>/", views.report_view, name="report"),
    path("reports/<int:number>/export/", views.report_export, name="report-export"),
]
