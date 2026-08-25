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

from datetime import date

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.exceptions import ImmutableRecordError
from apps.operations.services import cohort_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.settlements.forms import (
    AbsenceForm,
    ClaimBuildForm,
    ObligationForm,
    PenaltyForm,
    SettlementOpenForm,
    SettlementPaymentForm,
    SettlementSignForm,
)
from apps.settlements.services import (
    absence_service,
    claim_service,
    clawback_service,
    entitlement_service,
    obligation_service,
    settlement_service,
)

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
@require_http_methods(["GET", "POST"])
def obligations_view(request: HttpRequest) -> HttpResponse:
    """
    §5.6's obligations — now recordable, not only readable.

    Three of the five types are entered by a human here (trainer salaries,
    field-training expenses, a withdrawing partner's return). The advance
    clawback and the refund recovery are still raised automatically, and the
    absence penalty is computed from the absence register rather than typed,
    because BR-057 makes it a formula that must show its inputs.
    """
    can_create = policy.is_allowed(request.user, Screen.OBLIGATIONS, Action.CREATE)
    form = None
    if can_create:
        from apps.partners.services import partner_service

        form = ObligationForm(
            request.POST if request.POST.get("action") == "record" else None,
            partner_choices=[
                (p["code"], p["name_ar"])
                for p in partner_service.list_partners(actor=request.user, request=request)
            ]
            if policy.is_allowed(request.user, Screen.PARTNERS, Action.VIEW)
            else [],
            type_choices=obligation_service.manual_type_choices(),
            cohort_choices=settlement_service.settleable_cohort_choices(
                actor=request.user, request=request
            ),
        )

    if request.method == "POST":
        response = _handle_obligation(request, form)
        if response is not None:
            return response

    return render(
        request,
        "settlements/obligations.html",
        {
            "title": _("التزامات الشركاء"),
            "active_screen": Screen.OBLIGATIONS,
            "obligations": clawback_service.list_obligations(
                actor=request.user,
                partner_code=request.GET.get("partner", "").strip(),
                request=request,
            ),
            "form": form,
            "can_create": can_create,
        },
    )


def _handle_obligation(request: HttpRequest, form: ObligationForm | None) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action == "record":
        policy.require(request.user, Screen.OBLIGATIONS, Action.CREATE, request=request)
    if action != "record" or form is None or not form.is_valid():
        return None

    from apps.partners.services import partner_service

    data = form.cleaned_data
    try:
        partner = partner_service.partner_instance(
            actor=request.user, code=data["partner_code"], request=request
        )
        cohort = (
            cohort_service.get_cohort_instance(
                actor=request.user, code=data["cohort_code"], request=request
            )
            if data["cohort_code"]
            else None
        )
        obligation_service.record_obligation(
            actor=request.user,
            partner=partner,
            obligation_type=data["obligation_type"],
            amount=data["amount"],
            occurred_on=data["occurred_on"],
            statement_reference=data["statement_reference"],
            code=data["code"],
            cohort=cohort,
            request=request,
        )
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("شريك أو دفعة غير معروفة"))
        return None

    messages.success(request, _("قُيّد الالتزام"))
    return redirect("settlements:obligations")


# ---------------------------------------------------------------------------
# Trainer absences (Screen.OBLIGATIONS — the absence is a cause, not an entity)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def absences_view(request: HttpRequest) -> HttpResponse:
    """
    The absence register (تناغم بند 13).

    Governed by ``Screen.OBLIGATIONS`` rather than a screen of its own: an
    absence exists in this system only because it causes an obligation, and
    giving it a separate permission row would let the two drift apart.
    """
    can_create = policy.is_allowed(request.user, Screen.OBLIGATIONS, Action.CREATE)
    absence_form = penalty_form = None
    if can_create:
        cohorts = settlement_service.settleable_cohort_choices(actor=request.user, request=request)
        absence_form = AbsenceForm(
            request.POST if request.POST.get("action") == "record" else None,
            cohort_choices=cohorts,
        )
        penalty_form = PenaltyForm(
            request.POST if request.POST.get("action") == "penalise" else None,
            target_choices=absence_service.absent_trainer_choices(
                actor=request.user, request=request
            ),
        )

    if request.method == "POST":
        response = _handle_absence(request, absence_form, penalty_form)
        if response is not None:
            return response

    return render(
        request,
        "settlements/absences.html",
        {
            "title": _("غيابات المدربين"),
            "active_screen": Screen.OBLIGATIONS,
            "absences": absence_service.list_absences(
                actor=request.user,
                cohort_code=request.GET.get("cohort", "").strip(),
                request=request,
            ),
            "alerts": absence_service.replacement_alerts(
                actor=request.user, as_of=date.today(), request=request
            ),
            "replace_limit": absence_service.replace_limit(as_of=date.today()),
            "multiplier": absence_service.multiplier(as_of=date.today()),
            "absence_form": absence_form,
            "penalty_form": penalty_form,
            "can_create": can_create,
        },
    )


def _handle_absence(
    request: HttpRequest, absence_form: AbsenceForm | None, penalty_form: PenaltyForm | None
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    permission = {"record": Action.CREATE, "penalise": Action.CREATE, "waive": Action.EDIT}.get(
        action
    )
    if permission is None:
        return None
    policy.require(request.user, Screen.OBLIGATIONS, permission, request=request)

    try:
        if action == "record":
            if absence_form is None or not absence_form.is_valid():
                return None
            _record_absence(request, absence_form)
        elif action == "penalise":
            if penalty_form is None or not penalty_form.is_valid():
                return None
            _raise_penalty(request, penalty_form)
        else:
            _waive_absence(request)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    return redirect("settlements:absences")


def _record_absence(request: HttpRequest, form: AbsenceForm) -> None:
    data = form.cleaned_data
    cohort = cohort_service.get_cohort_instance(
        actor=request.user, code=data["cohort_code"], request=request
    )
    absence_service.record_absence(
        actor=request.user,
        cohort=cohort,
        trainer_name=data["trainer_name"],
        occurred_on=data["occurred_on"],
        is_waived=data["is_waived"],
        waiver_approval_ref=data["waiver_approval_ref"],
        waiver_approval_date=data["waiver_approval_date"],
        note_ar=data["note_ar"],
        request=request,
    )
    messages.success(request, _("سُجِّل الغياب"))


def _raise_penalty(request: HttpRequest, form: PenaltyForm) -> None:
    cohort_code, trainer_name = form.cleaned_data["target"].split("|", 1)
    cohort = cohort_service.get_cohort_instance(
        actor=request.user, code=cohort_code, request=request
    )
    obligation = absence_service.raise_penalty(
        actor=request.user,
        cohort=cohort,
        trainer_name=trainer_name,
        occurred_on=form.cleaned_data["occurred_on"],
        code=form.cleaned_data["code"],
        request=request,
    )
    messages.success(request, _("رُفعت غرامة الغياب %(a)s") % {"a": obligation.amount})


def _waive_absence(request: HttpRequest) -> None:
    absence = absence_service.absence_instance(
        actor=request.user, absence_id=int(request.POST.get("id", "0")), request=request
    )
    raw = request.POST.get("approval_date", "")
    try:
        approval_date = date.fromisoformat(raw)
    except ValueError as exc:
        raise DjangoValidationError("تاريخ الموافقة غير صالح.") from exc

    absence_service.waive_absence(
        actor=request.user,
        absence=absence,
        approval_ref=request.POST.get("approval_ref", ""),
        approval_date=approval_date,
        request=request,
    )
    messages.success(request, _("أُعفي الغياب بموافقة خطية"))


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


# ---------------------------------------------------------------------------
# Sprint 8K — entitlement landing page from the demo
# ---------------------------------------------------------------------------
def entitlement_view(request: HttpRequest) -> HttpResponse:
    """
    How a partner comes to be owed money — read only, and deliberately so.

    The entitlement figure itself is never computed here. ``entitlement_service``
    works one enrolment against one agreement at a time, and a landing page that
    picked enrolments and agreements for the reader would be building the claim
    screen over again under a second name. The claim screen already does that,
    and this page sends the reader to it.

    What it does take from the service is real: ``INELIGIBLE_STATUSES`` is the
    BR-045 list itself, read from the module rather than retyped here, so a
    status added to the rule shows up on this page without anyone remembering to
    update it.
    """
    policy.require(request.user, Screen.ENTITLEMENT, Action.VIEW, request=request)

    ineligible = [
        {"status": status, "reason": reason}
        for status, reason in entitlement_service.ineligibility_reasons()
    ]

    chain = [
        {
            "number": 1,
            "title": _("الاتفاقية"),
            "what": _(
                "هي التي تقرر: نموذج الاحتساب، وما الذي يُشارَك وما الذي يُستثنى. "
                "الاستثناءات تأتي من العقد الموقّع لا من الشيفرة (BR-046)، "
                "والتأمينات مستثناة افتراضياً ما لم ينص العقد صراحةً على غير ذلك "
                "(BR-092 · Q-01)."
            ),
        },
        {
            "number": 2,
            "title": _("ما حُصِّل فعلاً"),
            "what": _(
                "الاستحقاق يتبع النقد المقبوض لا الفاتورة (BR-044): تُقرأ التخصيصات "
                "على السندات الصادرة، لا المبالغ المستحقة على الورق. الفاتورة وحدها "
                "لا تُكسب الشريك شيئاً."
            ),
        },
        {
            "number": 3,
            "title": _("الأهلية"),
            "what": _(
                "بعض التسجيلات لا تُكسب الشريك شيئاً مهما حُصِّل عليها (BR-045) — "
                "القائمة كاملة في الجدول أدناه."
            ),
        },
        {
            "number": 4,
            "title": _("المطالبة"),
            "what": _(
                "تُجمَّد الحسبة لفترة: يُطبَّق نموذج الاحتساب على ما حُصِّل من "
                "المؤهّلين، وبعد الاعتماد لا يعدّلها أحد (BR-051)."
            ),
        },
        {
            "number": 5,
            "title": _("الالتزامات والخصم"),
            "what": _(
                "ما على الشريك — غياب مدرّس، أو استرداد دفعة مقدّمة — لا يُطالَب به "
                "بفاتورة، بل يُخصم من مطالبة لاحقة (BR-036)."
            ),
        },
        {
            "number": 6,
            "title": _("المخالصة"),
            "what": _("التوقيع النهائي على ما استقر بعد الخصم — وبها ينتهي أثر الفترة."),
        },
    ]

    models = [
        (_("نسبة مئوية"), _("نسبة من الأساس المحصَّل (BR-046).")),
        (_("مبلغ ثابت لكل طالب"), _("مبلغ مقطوع عن كل طالب مؤهَّل، مهما كان الأساس (BR-048).")),
        (_("عمولة خدمة"), _("عمولة مقطوعة على خدمة مُنجَزة (BR-049).")),
    ]

    links = [
        {"url": reverse(route), "label": label}
        for screen, route, label in (
            (Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),
            (Screen.CLAIMS, "settlements:claims", _("المطالبات")),
            (Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات")),
            (Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
            (Screen.OBLIGATIONS, "settlements:absences", _("غيابات المدربين")),
        )
        if policy.is_allowed(request.user, screen, Action.VIEW)
    ]

    return render(
        request,
        "settlements/entitlement.html",
        {
            "title": _("استحقاق الشركاء"),
            "active_screen": Screen.ENTITLEMENT,
            "chain": chain,
            "models": models,
            "ineligible": ineligible,
            "links": links,
        },
    )
