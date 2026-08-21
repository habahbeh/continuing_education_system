"""
Partner-money screens — entitlement, obligations, claims, settlements.

Views render and delegate. Permission refusals and business refusals are kept
apart the way Sprint 8B settled it: ``policy.require`` runs BEFORE the service
call so a role violation is a 403, while a rule arrives as a message carrying
its own reference (BR-051, BR-053).

**A sealed claim is shown sealed.** ``is_frozen`` drives whether the detail
screen offers anything at all. The service refuses an edit regardless, but a
screen that displayed buttons on an approved claim would be telling the
operator something untrue about a signed document.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.exceptions import ImmutableRecordError
from apps.operations.services import cohort_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.forms import (
    ClaimBuildForm,
    SettlementOpenForm,
    SettlementPaymentForm,
    SettlementSignForm,
)
from apps.settlements.services import claim_service, clawback_service, settlement_service

#: Which permission each POST action needs, checked before the service so a
#: ROLE violation is a 403 and a RULE violation is a readable message.
CLAIM_ACTIONS = {"build": Action.CREATE, "offsets": Action.EDIT, "approve": Action.APPROVE}
SETTLEMENT_ACTIONS = {
    "open": Action.CREATE,
    "attach": Action.EDIT,
    "pay": Action.EDIT,
    "sign": Action.APPROVE,
}


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


# ---------------------------------------------------------------------------
# Obligations (Screen.OBLIGATIONS) — read only
# ---------------------------------------------------------------------------
def obligations_view(request: HttpRequest) -> HttpResponse:
    """
    §5.6's obligations, as they stand.

    Read only: the two obligation types the system raises come from a name-list
    closure and a refund recovery, both automatic. Trainer salaries, field
    training expenses and the absence penalty have no creating service yet, so
    no create button is offered for a capability that does not exist.
    """
    rows = clawback_service.list_obligations(
        actor=request.user,
        partner_code=request.GET.get("partner", "").strip(),
        request=request,
    )
    return render(
        request,
        "settlements/obligations.html",
        {
            "title": _("التزامات الشركاء"),
            "active_screen": Screen.OBLIGATIONS,
            "obligations": rows,
        },
    )


# ---------------------------------------------------------------------------
# Claims (Screen.CLAIMS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def claims_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.CLAIMS, Action.CREATE)
    form = None
    if can_create:
        form = ClaimBuildForm(
            request.POST if request.POST.get("action") == "build" else None,
            cohort_choices=claim_service.claimable_cohort_choices(
                actor=request.user, request=request
            ),
        )

    if request.method == "POST":
        response = _handle_claim_list(request, form)
        if response is not None:
            return response

    return render(
        request,
        "settlements/claims.html",
        {
            "title": _("مطالبات الشركاء"),
            "active_screen": Screen.CLAIMS,
            "claims": claim_service.list_claims(actor=request.user, request=request),
            "form": form,
            "can_create": can_create,
        },
    )


def _handle_claim_list(request: HttpRequest, form: ClaimBuildForm | None) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLAIM_ACTIONS:
        policy.require(request.user, Screen.CLAIMS, CLAIM_ACTIONS[action], request=request)
    if action != "build" or form is None or not form.is_valid():
        return None

    data = form.cleaned_data
    try:
        cohort = cohort_service.get_cohort_instance(
            actor=request.user, code=data["cohort_code"], request=request
        )
        claim = claim_service.build_claim(
            actor=request.user,
            agreement=cohort.agreement,
            cohort=cohort,
            period_from=data["period_from"],
            period_to=data["period_to"],
            trigger_type=data["trigger_type"],
            trigger_reference_ar=data["trigger_reference_ar"],
            request=request,
        )
    except (DjangoValidationError, ImmutableRecordError) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("دفعة غير معروفة"))
        return None

    messages.success(request, _("بُنيت المطالبة"))
    return redirect("settlements:claim-detail", code=claim.code)


@require_http_methods(["GET", "POST"])
def claim_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    if request.method == "POST":
        response = _handle_claim_detail(request, code)
        if response is not None:
            return response

    try:
        claim = claim_service.get_claim(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(
        request,
        "settlements/claim_detail.html",
        {
            "title": _("مطالبة شريك"),
            "active_screen": Screen.CLAIMS,
            "claim": claim,
            # BR-051 — a sealed claim offers nothing, whoever is looking.
            "can_edit": policy.is_allowed(request.user, Screen.CLAIMS, Action.EDIT)
            and not claim["is_frozen"],
            "can_approve": policy.is_allowed(request.user, Screen.CLAIMS, Action.APPROVE)
            and not claim["is_frozen"],
            "current_user_id": request.user.pk,
        },
    )


def _handle_claim_detail(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in CLAIM_ACTIONS:
        policy.require(request.user, Screen.CLAIMS, CLAIM_ACTIONS[action], request=request)
    try:
        claim = claim_service.claim_instance(actor=request.user, code=code, request=request)
        if action == "offsets":
            created = claim_service.apply_offsets(actor=request.user, claim=claim, request=request)
            messages.success(request, _("طُبّقت %(n)s مقاصّة") % {"n": len(created)})
        elif action == "approve":
            claim_service.approve_claim(actor=request.user, claim=claim, request=request)
            messages.success(request, _("اعتُمدت المطالبة وخُتمت"))
        else:
            return None
    except (DjangoValidationError, ImmutableRecordError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    return redirect("settlements:claim-detail", code=code)


# ---------------------------------------------------------------------------
# Settlements (Screen.SETTLEMENTS)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def settlements_view(request: HttpRequest) -> HttpResponse:
    can_create = policy.is_allowed(request.user, Screen.SETTLEMENTS, Action.CREATE)
    form = None
    if can_create:
        form = SettlementOpenForm(
            request.POST if request.POST.get("action") == "open" else None,
            agreement_choices=settlement_service.open_agreement_choices(
                actor=request.user, request=request
            ),
            cohort_choices=settlement_service.settleable_cohort_choices(
                actor=request.user, request=request
            ),
        )

    if request.method == "POST":
        response = _handle_settlement_open(request, form)
        if response is not None:
            return response

    return render(
        request,
        "settlements/settlements.html",
        {
            "title": _("مخالصات الشركاء"),
            "active_screen": Screen.SETTLEMENTS,
            "settlements": settlement_service.list_settlements(actor=request.user, request=request),
            "form": form,
            "can_create": can_create,
        },
    )


def _handle_settlement_open(
    request: HttpRequest, form: SettlementOpenForm | None
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in SETTLEMENT_ACTIONS:
        policy.require(
            request.user, Screen.SETTLEMENTS, SETTLEMENT_ACTIONS[action], request=request
        )
    if action != "open" or form is None or not form.is_valid():
        return None

    data = form.cleaned_data
    try:
        from apps.partners.services import partner_service

        agreement = partner_service.agreement_instance(
            actor=request.user, agreement_number=data["agreement_number"], request=request
        )
        cohort = (
            cohort_service.get_cohort_instance(
                actor=request.user, code=data["cohort_code"], request=request
            )
            if data["cohort_code"]
            else None
        )
        settlement = settlement_service.open_settlement(
            actor=request.user,
            agreement=agreement,
            opens_on=data["opens_on"],
            code=data["code"],
            cohort=cohort,
            request=request,
        )
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("اتفاقية أو دفعة غير معروفة"))
        return None

    messages.success(request, _("فُتحت المخالصة"))
    return redirect("settlements:settlement-detail", code=settlement.code)


@require_http_methods(["GET", "POST"])
def settlement_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    if request.method == "POST":
        response = _handle_settlement_detail(request, code)
        if response is not None:
            return response

    try:
        settlement = settlement_service.get_settlement(
            actor=request.user, code=code, request=request
        )
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    return render(
        request,
        "settlements/settlement_detail.html",
        {
            "title": _("مخالصة شريك"),
            "active_screen": Screen.SETTLEMENTS,
            "settlement": settlement,
            "payment_form": SettlementPaymentForm(),
            "sign_form": SettlementSignForm(),
            "can_edit": policy.is_allowed(request.user, Screen.SETTLEMENTS, Action.EDIT)
            and settlement["is_open"],
            "can_approve": policy.is_allowed(request.user, Screen.SETTLEMENTS, Action.APPROVE)
            and settlement["is_open"],
        },
    )


def _handle_settlement_detail(request: HttpRequest, code: str) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action in SETTLEMENT_ACTIONS:
        policy.require(
            request.user, Screen.SETTLEMENTS, SETTLEMENT_ACTIONS[action], request=request
        )
    try:
        settlement = settlement_service.settlement_instance(
            actor=request.user, code=code, request=request
        )
        if action == "attach":
            claims = settlement_service.attach_claims(
                actor=request.user, settlement=settlement, request=request
            )
            messages.success(request, _("ضُمّت %(n)s مطالبة") % {"n": len(claims)})
        elif action == "pay":
            payment_form = SettlementPaymentForm(request.POST)
            if not payment_form.is_valid():
                messages.error(request, _("مبلغ غير صالح"))
                return None
            settlement_service.record_payment(
                actor=request.user,
                settlement=settlement,
                amount=payment_form.cleaned_data["amount"],
                request=request,
            )
            messages.success(request, _("سُجِّل الدفع"))
        elif action == "sign":
            sign_form = SettlementSignForm(request.POST)
            if not sign_form.is_valid():
                messages.error(request, _("تاريخ توقيع غير صالح"))
                return None
            settlement_service.sign_settlement(
                actor=request.user,
                settlement=settlement,
                signed_on=sign_form.cleaned_data["signed_on"],
                request=request,
            )
            messages.success(request, _("وُقّعت المخالصة"))
        else:
            return None
    except DjangoValidationError as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    return redirect("settlements:settlement-detail", code=code)
