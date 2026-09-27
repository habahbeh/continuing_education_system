"""Catalogue URLs (PERMISSIONS.md §3.3 rows 9–13)."""

from __future__ import annotations

from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    # مجالات الدورات: بيانات الكتالوج المرجعية التي كانت تُدخَل من لوحة
    # الإدارة وحدها — أي بلا خدمة وبلا سجل تدقيق (BR-061).
    path("deposit-policies/", views.deposit_policies_view, name="deposit-policies"),
    path("course-categories/", views.course_categories_view, name="course-categories"),
    path("knowledge-fields/", views.knowledge_fields_view, name="knowledge-fields"),
    path("programs/", views.diplomas_view, name="programs"),
    path("short-courses/", views.short_courses_view, name="short-courses"),
    path("online-courses/", views.online_courses_view, name="online-courses"),
    path("programs/<str:code>/", views.program_detail_view, name="program-detail"),
    path("pricelists/", views.price_lists_view, name="pricelists"),
    path("pricelists/<str:code>/", views.price_list_detail_view, name="pricelist-detail"),
]
