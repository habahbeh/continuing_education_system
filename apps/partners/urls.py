"""Partner URLs — read-only listings (Sprint 8B-2)."""

from __future__ import annotations

from django.urls import path

from apps.partners import views

app_name = "partners"

urlpatterns = [
    path("partners/", views.partners_view, name="partners"),
    path("partners/agreements/", views.agreements_view, name="agreements"),
    path(
        "partners/agreements/<path:number>/", views.agreement_detail_view, name="agreement-detail"
    ),
    path("partners/<str:code>/", views.partner_detail_view, name="partner-detail"),
]
