"""Operational URLs — dashboard, cohorts, enrolments, account, ministry file."""

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
    # The ministry file (Sprint 8G). ``mohe-submit`` sits above the detail
    # route because both live under the same prefix and "submit" is not an id.
    path("operations/mohe/", views.mohe_view, name="mohe"),
    path("operations/mohe/submit/", views.mohe_submit_view, name="mohe-submit"),
    path("operations/mohe/<int:submission_id>/", views.mohe_detail_view, name="mohe-detail"),
    path("operations/clearance/", views.clearances_view, name="clearances"),
    path("operations/clearance/<str:code>/", views.clearance_detail_view, name="clearance-detail"),
    path("operations/certificates/", views.certificates_view, name="certificates"),
    # Printed documents (Sprint 8C-1)
    path(
        "operations/clearance/<str:code>/print/",
        views.clearance_print_view,
        name="clearance-print",
    ),
    path(
        "operations/certificates/<str:number>/print/",
        views.certificate_print_view,
        name="certificate-print",
    ),
]
