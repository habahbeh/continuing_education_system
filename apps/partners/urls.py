"""
Partner URLs (Sprints 8B-2, 8F).

**Order is load-bearing here.** Agreement numbers carry slashes — «2026/14» —
so the detail route uses the ``path`` converter, whose pattern is greedy. It
would happily read ``agreements/new/`` as the agreement numbered "new" and
``agreements/2026/14/activate/`` as one numbered "2026/14/activate". The
specific routes therefore come first, and moving them below the detail route
would break both without breaking any import.
"""

from __future__ import annotations

from django.urls import path

from apps.partners import views

app_name = "partners"

urlpatterns = [
    path("partners/", views.partners_view, name="partners"),
    path("partners/new/", views.partner_new_view, name="partner-new"),
    path("partners/agreements/", views.agreements_view, name="agreements"),
    path("partners/agreements/new/", views.agreement_new_view, name="agreement-new"),
    path(
        "partners/agreements/<path:number>/activate/",
        views.agreement_activate_view,
        name="agreement-activate",
    ),
    path(
        "partners/agreements/<path:number>/", views.agreement_detail_view, name="agreement-detail"
    ),
    path("partners/<str:code>/", views.partner_detail_view, name="partner-detail"),
]
