"""
Partner and agreement screens (Sprints 8B-2, 8F).

Sprint 8B-2 built these read-only and recorded the gap in this docstring: the
matrix granted the centre manager ``C E`` here, no creating service existed,
and a button leading nowhere was judged worse than no button. Sprint 8F closes
it — a fresh installation could not record a signed partner or agreement at
all, which left claims, settlements, obligations and every partner figure in
the reports unreachable without test fixtures.

**Three screens, three permissions, straight off the matrix.** The partner
form needs ``C`` on §3.5/23. The agreement editor is §3.5/25, whose cell is
``V C E`` for the manager and ``V`` for the audit account — so the page OPENS
on VIEW and SUBMITS on CREATE, and the auditor may read the editor without
being able to fill it in. Activation is ``A`` on §3.5/24, a separate button on
the agreement's own page, because making a contract live is not the same act
as writing it down.

The agreement detail shows EVERY configurable term rather than a summary,
because §3.4's point is that the three contract models are data: a screen
showing only "50%" would hide the exclusions, the discount split and the
settlement cycle — exactly the fields that differ between the signed
agreements in the client file.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.exceptions import ImmutableRecordError
from apps.partners.forms import AgreementForm, PartnerForm
from apps.partners.services import partner_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy


def _apply_errors(form: PartnerForm | AgreementForm, exc: DjangoValidationError) -> None:
    """Surface a service-layer refusal next to the field it concerns."""
    for field, message_list in getattr(exc, "message_dict", {"__all__": [str(exc)]}).items():
        for message in message_list:
            form.add_error(field if field in form.fields else None, message)


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
            "can_create": policy.is_allowed(request.user, Screen.PARTNERS, Action.CREATE),
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
            "can_create": policy.is_allowed(request.user, Screen.AGREEMENT_NEW, Action.VIEW),
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
            # A draft is the only thing activation applies to; ``A`` on
            # §3.5/24 is the only cell that may press it.
            "can_activate": agreement["status"] == "DRAFT"
            and policy.is_allowed(request.user, Screen.AGREEMENTS, Action.APPROVE),
        },
    )


# ---------------------------------------------------------------------------
# Recording a signed partner and a signed agreement (Sprint 8F)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def partner_new_view(request: HttpRequest) -> HttpResponse:
    """
    §3.5/23 gives ``C`` to the centre manager alone.

    Guarded on CREATE for GET as well as POST. There is no PARTNER_NEW row in
    the matrix — creation is an action ON the partners screen — so the finance
    officer and the audit account, who hold ``V P`` there, are refused this
    page rather than shown a form they could not submit.
    """
    policy.require(request.user, Screen.PARTNERS, Action.CREATE, request=request)

    form = PartnerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            partner = partner_service.create_partner(
                actor=request.user, data=form.to_service_data(), request=request
            )
        except DjangoValidationError as exc:
            _apply_errors(form, exc)
        else:
            messages.success(request, _("تم تسجيل الشريك %(name)s") % {"name": partner.name_ar})
            return redirect("partners:partner-detail", code=partner.code)

    return render(
        request,
        "partners/partner_new.html",
        {"title": _("شريك جديد"), "active_screen": Screen.PARTNERS, "form": form},
    )


@require_http_methods(["GET", "POST"])
def agreement_new_view(request: HttpRequest) -> HttpResponse:
    """
    §3.5/25 — ``V C E`` for the manager, ``V`` for the audit account.

    The split in that row is the reason the two checks differ: the editor
    opens on VIEW so the auditor can read what the form asks for, and only
    CREATE gets past the submit. A role holding ``V`` and posting anyway is
    refused by ``policy.require`` with a DENIED_ATTEMPT row, not by a hidden
    button.
    """
    policy.require(request.user, Screen.AGREEMENT_NEW, Action.VIEW, request=request)

    form = AgreementForm(
        request.POST or None,
        partner_choices=partner_service.partner_choices(actor=request.user, request=request),
        agreement_choices=partner_service.agreement_choices(actor=request.user, request=request),
    )

    if request.method == "POST":
        policy.require(request.user, Screen.AGREEMENT_NEW, Action.CREATE, request=request)
        if form.is_valid():
            try:
                agreement = _create(request, form)
            except DjangoValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(
                    request,
                    _("سُجّلت الاتفاقية %(number)s كمسودة — تسري بعد اعتمادها")
                    % {"number": agreement.agreement_number},
                )
                return redirect("partners:agreement-detail", number=agreement.agreement_number)

    return render(
        request,
        "partners/agreement_new.html",
        {
            "title": _("تسجيل اتفاقية موقّعة"),
            "active_screen": Screen.AGREEMENT_NEW,
            "form": form,
            "can_create": policy.is_allowed(request.user, Screen.AGREEMENT_NEW, Action.CREATE),
        },
    )


def _create(request: HttpRequest, form: AgreementForm) -> Any:
    """Resolve the two related objects, then hand the terms to the service."""
    partner = partner_service.partner_instance(
        actor=request.user, code=form.cleaned_data["partner_code"], request=request
    )
    data = form.to_service_data()

    predecessor = form.cleaned_data.get("supersedes", "")
    if predecessor:
        data["supersedes"] = partner_service.agreement_instance(
            actor=request.user, agreement_number=predecessor, request=request
        )

    return partner_service.create_agreement(
        actor=request.user, partner=partner, data=data, request=request
    )


@require_http_methods(["POST"])
def agreement_activate_view(request: HttpRequest, number: str) -> HttpResponse:
    """
    §3.5/24 ``A`` — the draft becomes the contract in force.

    Its own route rather than a mode of the detail page, so the act is a POST
    to a URL that does one thing. Whatever the draft supersedes ends in the
    same transaction.
    """
    try:
        agreement = partner_service.agreement_instance(
            actor=request.user, agreement_number=number, request=request
        )
    except ObjectDoesNotExist:
        raise Http404(_("لا توجد اتفاقية بهذا الرقم")) from None

    try:
        partner_service.activate_agreement(actor=request.user, agreement=agreement, request=request)
    except PermissionDenied:
        raise
    except (DjangoValidationError, ImmutableRecordError) as exc:
        detail = getattr(exc, "messages", None)
        messages.error(request, " · ".join(str(m) for m in detail) if detail else str(exc))
    else:
        messages.success(request, _("سرت الاتفاقية %(number)s") % {"number": number})

    return redirect("partners:agreement-detail", number=number)
