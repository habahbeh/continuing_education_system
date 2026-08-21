"""Billing URLs — discounts, refunds, extra fees (Sprint 8B)."""

from __future__ import annotations

from django.urls import path

from apps.billing import views

app_name = "billing"

urlpatterns = [
    path("billing/discounts/", views.discounts_view, name="discounts"),
    path("billing/refunds/", views.refunds_view, name="refunds"),
    path("billing/extra-fees/", views.extra_fees_view, name="extra-fees"),
]
