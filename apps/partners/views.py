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
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.exceptions import ImmutableRecordError
from apps.core.pagination import PAGE_SIZE as _PAGE_SIZE
from apps.core.pagination import page_of
from apps.partners.forms import AgreementForm, PartnerForm
from apps.partners.services import partner_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy


def _apply_errors(form: PartnerForm | AgreementForm, exc: DjangoValidationError) -> None:
    """Surface a service-layer refusal next to the field it concerns."""
    for field, message_list in getattr(exc, "message_dict", {"__all__": [str(exc)]}).items():
        for message in message_list:
            form.add_error(field if field in form.fields else None, message)


#: The register is read fifty rows at a time, the same slice the participants
#: and payments registers use. A register that draws every partner it holds is
#: one page that grows without limit and a table nobody can reach the end of.
#: مُعاد تصديره من `core.pagination` — الاختبارات تستورده من هنا، والقيمة لها بيتٌ واحد.
PAGE_SIZE = _PAGE_SIZE


def partners_view(request: HttpRequest) -> HttpResponse:
    """
    The register, searched and filtered.

    ``query`` is trimmed ONCE and the trimmed value is what both the service
    and the box get back — they used to disagree, so a search typed with a
    trailing space narrowed by the word and echoed the space.

    ``is_filtered`` is what lets the empty state tell the truth: a register
    with partners in it and a search that matched none of them is not an
    empty register, and saying «لا شركاء مسجّلون بعد» to someone who mistyped
    a code tells them their data is gone.
    """
    vocabularies = partner_service.partner_filter_choices()
    # The chips name the filter in the reader's words, not its stored code —
    # a register that says «الحالة: FORMER» is not a register. And the same
    # lookup decides whether the value is a filter AT ALL: a status this
    # vocabulary does not contain — a stale link, a renamed value — used to
    # empty the register while both selects still read «الكل» and no chip
    # explained it, so the screen contradicted its own controls. An unknown
    # value is not a filter; it is dropped, and the register is whole.
    status_labels = dict(vocabularies["statuses"])
    type_labels = dict(vocabularies["types"])

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    partner_type = request.GET.get("type", "").strip()
    status = status if status in status_labels else ""
    partner_type = partner_type if partner_type in type_labels else ""

    rows = partner_service.list_partners(
        actor=request.user,
        query=query,
        status=status,
        partner_type=partner_type,
        request=request,
    )
    page = page_of(rows, request.GET.get("page", ""))
    params = {k: v for k, v in (("q", query), ("status", status), ("type", partner_type)) if v}
    labels = {
        "status": status_labels.get(status, ""),
        "type": type_labels.get(partner_type, ""),
    }

    return render(
        request,
        "partners/partners.html",
        {
            "title": _("الشركاء المتعاقدون"),
            "active_screen": Screen.PARTNERS,
            "partners": page["rows"],
            "page": page,
            "query": query,
            "status": status,
            "partner_type": partner_type,
            "statuses": vocabularies["statuses"],
            "types": vocabularies["types"],
            "status_label": labels["status"],
            "type_label": labels["type"],
            "is_filtered": bool(params),
            "params_qs": urlencode(params),
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
        {
            "title": _("الشريك"),
            "active_screen": Screen.PARTNERS,
            "partner": partner,
            # The editor OPENS on VIEW (§3.5/25 is ``V C E`` for the manager
            # and ``V`` for the audit account), so the link that reaches it is
            # offered on VIEW too — the same flag the agreements register
            # uses for the same link.
            "can_record_agreement": policy.is_allowed(
                request.user, Screen.AGREEMENT_NEW, Action.VIEW
            ),
        },
    )


def agreements_view(request: HttpRequest) -> HttpResponse:
    """
    The register of signed agreements, optionally narrowed to one partner.

    ``?partner=`` was always accepted here and no screen ever sent it. The
    partners register now links its agreement count into this page, so the
    filter needs to SAY that it is on and offer the way out of it — the name
    comes off the rows themselves, and off the code alone when the filter
    matched nothing.
    """
    partner_code = request.GET.get("partner", "").strip()
    rows = partner_service.list_agreements(
        actor=request.user, partner_code=partner_code, request=request
    )
    # The name comes off the rows when there are rows, and off the register
    # when the filter matched none — «الشريك: PRT-QA-1» is the code, and a
    # chip that prints a code is the defect the partners register just had.
    # Asked only of a reader the partners screen would let in, so nobody is
    # sent into a refusal to render a label (BR-085).
    partner_name = rows[0]["partner_name"] if partner_code and rows else partner_code
    if partner_code and not rows and policy.is_allowed(request.user, Screen.PARTNERS, Action.VIEW):
        partner_name = (
            partner_service.get_partner(actor=request.user, code=partner_code, request=request).get(
                "name_ar"
            )
            or partner_code
        )
    return render(
        request,
        "partners/agreements.html",
        {
            "title": _("الاتفاقيات"),
            "active_screen": Screen.AGREEMENTS,
            "agreements": rows,
            "partner_code": partner_code,
            "partner_name": partner_name,
            "is_filtered": bool(partner_code),
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

    choices = partner_service.partner_choices(actor=request.user, request=request)
    # Arrived from a partner's card — the partner is already decided, so the
    # editor opens on it rather than making the user find the name they just
    # came from. Only a code the choices actually offer is honoured; an
    # unknown or a former partner leaves the select where it was.
    offered = {code for code, _label in choices}
    asked = request.GET.get("partner", "").strip()

    form = AgreementForm(
        request.POST or None,
        partner_choices=choices,
        agreement_choices=partner_service.agreement_choices(actor=request.user, request=request),
        initial={"partner_code": asked} if asked in offered else None,
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
