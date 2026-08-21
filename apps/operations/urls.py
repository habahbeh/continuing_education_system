"""Operational URLs — dashboard, cohorts, enrolments, account (Sprint 8B)."""

from __future__ import annotations

from django.urls import path

from apps.operations import views

app_name = "operations"

urlpatterns = [
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("operations/cohorts/", views.cohorts_view, name="cohorts"),
    path("operations/enrollments/", views.enrollments_view, name="enrollments"),
    path(
        "operations/enrollments/<str:code>/action/",
        views.enrollment_action_view,
        name="enrollment-action",
    ),
    path("operations/enrollments/<str:code>/account/", views.account_view, name="account"),
    path("operations/clearance/", views.clearances_view, name="clearances"),
    path("operations/clearance/<str:code>/", views.clearance_detail_view, name="clearance-detail"),
    path("operations/certificates/", views.certificates_view, name="certificates"),
]
