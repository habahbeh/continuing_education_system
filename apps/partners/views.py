"""
Partner and agreement screens — read only (Sprint 8B-2).

**No create or edit, deliberately.** Agreements are negotiated documents
signed on paper; the system carries their terms, it does not author them. The
permission matrix grants the centre manager ``C E`` here, but no creating
service exists, and offering a button that leads nowhere would be worse than
offering none. The gap is recorded rather than papered over.

The agreement detail shows EVERY configurable term rather than a summary,
because §3.4's point is that the three contract models are data: a screen
showing only "50%" would hide the exclusions, the discount split and the
settlement cycle — exactly the fields that differ between the signed
agreements in the client file.
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from apps.partners.services import partner_service
from apps.people.constants import Screen


def partners_view(request: HttpRequest) -> HttpResponse:
    rows = partner_service.list_partners(
        actor=request.user, query=request.GET.get("q", "").strip(), request=request
    )
    return render(
        request,
        "partners/partners.html",
        {
            "title": _("الشركاء المتعاقدون"),
            "active_screen": Screen.PARTNERS,
            "partners": rows,
            "query": request.GET.get("q", ""),
        },
    )


def partner_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    partner = partner_service.get_partner(actor=request.user, code=code, request=request)
    if not partner:
        raise Http404
    return render(
        request,
        "partners/partner_detail.html",
        {"title": _("الشريك"), "active_screen": Screen.PARTNERS, "partner": partner},
    )


def agreements_view(request: HttpRequest) -> HttpResponse:
    rows = partner_service.list_agreements(
        actor=request.user,
        partner_code=request.GET.get("partner", "").strip(),
        request=request,
    )
    return render(
        request,
        "partners/agreements.html",
        {
            "title": _("الاتفاقيات"),
            "active_screen": Screen.AGREEMENTS,
            "agreements": rows,
        },
    )


def agreement_detail_view(request: HttpRequest, number: str) -> HttpResponse:
    agreement = partner_service.get_agreement(
        actor=request.user, agreement_number=number, request=request
    )
    if not agreement:
        raise Http404
    return render(
        request,
        "partners/agreement_detail.html",
        {
            "title": _("تفاصيل الاتفاقية"),
            "active_screen": Screen.AGREEMENTS,
            "agreement": agreement,
        },
    )
