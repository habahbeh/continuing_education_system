"""Cashbox URLs — receipts, payment entry, daily closing (Sprint 8B)."""

from __future__ import annotations

from django.urls import path

from apps.cashbox import views

app_name = "cashbox"

urlpatterns = [
    path("cashbox/payments/", views.payments_view, name="payments"),
    path("cashbox/payments/new/", views.payment_new_view, name="payment-new"),
    path("cashbox/payments/<str:number>/", views.receipt_detail_view, name="receipt-detail"),
    path("cashbox/closing/", views.closing_view, name="closing"),
]
