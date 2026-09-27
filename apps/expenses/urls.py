"""Expenses URLs (Sprint 8C-2)."""

from __future__ import annotations

from django.urls import path

from apps.expenses import views

app_name = "expenses"

urlpatterns = [
    path("expenses/", views.expenses_view, name="expenses"),
    path("expenses/period-check/", views.period_check_view, name="period-check"),
]
