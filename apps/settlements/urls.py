"""Settlement URLs — obligations, claims, settlements (Sprint 8B-2)."""

from __future__ import annotations

from django.urls import path

from apps.settlements import views

app_name = "settlements"

urlpatterns = [
    path("settlements/entitlement/", views.entitlement_view, name="entitlement"),
    path("settlements/obligations/", views.obligations_view, name="obligations"),
    path("settlements/absences/", views.absences_view, name="absences"),
    path("settlements/claims/", views.claims_view, name="claims"),
    path("settlements/claims/<str:code>/", views.claim_detail_view, name="claim-detail"),
    path("settlements/settlements/", views.settlements_view, name="settlements"),
    path(
        "settlements/settlements/<str:code>/",
        views.settlement_detail_view,
        name="settlement-detail",
    ),
]
