"""
The historical archive screens (Screen.MIGRATION).

Views render and delegate. Permission refusals arrive as 403 because
``policy.require`` runs BEFORE the service; a business refusal — an
unvalidated batch, a link without a reason — arrives as a message carrying its
own rule reference. That split was settled in Sprint 8B and is kept here.

**Three roles, three different screens in practice.** The manager reads
workbooks in and archives them. The registrar links archived names to living
participants and can do nothing else. The finance officer and the audit
account read. The matrix says so; this module only asks it.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.datamigration.forms import ImportForm, LinkForm, RowFilterForm
from apps.datamigration.services import (
    archive_service,
    batch_service,
    linking_service,
    read_service,
    validation_service,
)
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: Which permission each POST action needs, checked before any service runs.
ACTIONS = {
    "import": Action.CREATE,
    "validate": Action.CREATE,
    "commit": Action.APPROVE,
    "link": Action.EDIT,
    "unlink": Action.EDIT,
}


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "messages", None)
    return " · ".join(str(m) for m in detail) if detail else str(exc)


@require_http_methods(["GET", "POST"])
def batches_view(request: HttpRequest) -> HttpResponse:
    """The batch list, and — for whoever holds CREATE — the import form."""
    can_import = policy.is_allowed(request.user, Screen.MIGRATION, Action.CREATE)
    form = ImportForm(request.POST if request.POST.get("action") == "import" else None)

    if request.method == "POST":
        response = _handle(request, form=form)
        if response is not None:
            return response

    return render(
        request,
        "datamigration/batches.html",
        {
            "title": _("الأرشيف التاريخي"),
            "active_screen": Screen.MIGRATION,
            "batches": read_service.list_batches(actor=request.user, request=request),
            "form": form if can_import else None,
            "can_import": can_import,
            "can_commit": policy.is_allowed(request.user, Screen.MIGRATION, Action.APPROVE),
            "can_link": policy.is_allowed(request.user, Screen.MIGRATION, Action.EDIT),
        },
    )


@require_http_methods(["GET", "POST"])
def batch_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """One batch: its reconciliation, its sheets, its findings, its rows."""
    if request.method == "POST":
        response = _handle(request, code=code)
        if response is not None:
            return response

    try:
        detail = read_service.batch_detail(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    batch = batch_service.batch_instance(actor=request.user, code=code, request=request)
    filters = RowFilterForm(
        request.GET or None,
        state_choices=read_service.state_choices(),
        finding_choices=read_service.finding_choices(),
        sheet_choices=read_service.sheet_choices(actor=request.user, code=code, request=request),
    )
    filters.is_valid()

    return render(
        request,
        "datamigration/batch_detail.html",
        {
            "title": _("دفعة %(code)s") % {"code": code},
            "active_screen": Screen.MIGRATION,
            "batch": detail,
            "report": validation_service.report_for(
                actor=request.user, batch=batch, request=request
            ),
            "rows": read_service.rows_of(
                actor=request.user,
                code=code,
                state=request.GET.get("state", "").strip(),
                finding=request.GET.get("finding", "").strip(),
                sheet=request.GET.get("sheet", "").strip(),
                request=request,
            ),
            "filters": filters,
            "page_size": read_service.PAGE,
            "can_validate": policy.is_allowed(request.user, Screen.MIGRATION, Action.CREATE),
            "can_commit": policy.is_allowed(request.user, Screen.MIGRATION, Action.APPROVE),
        },
    )


@require_http_methods(["GET", "POST"])
def links_view(request: HttpRequest) -> HttpResponse:
    """
    The identity queue — the registrar's screen.

    Rows flagged ``LEGACY_NUMBER_NAME_CONFLICT`` are shown with the conflict on
    the row, before the decision rather than after it.
    """
    if request.method == "POST":
        response = _handle(request)
        if response is not None:
            return response

    can_link = policy.is_allowed(request.user, Screen.MIGRATION, Action.EDIT)
    query = request.GET.get("q", "").strip()
    return render(
        request,
        "datamigration/links.html",
        {
            "title": _("ربط السجلات التاريخية"),
            "active_screen": Screen.MIGRATION,
            "rows": read_service.link_queue(
                actor=request.user,
                code=request.GET.get("batch", "").strip(),
                only_unlinked=request.GET.get("show", "") != "all",
                request=request,
            ),
            "candidates": read_service.candidate_participants(
                actor=request.user, query=query, request=request
            )
            if can_link and query
            else [],
            "query": query,
            "form": LinkForm() if can_link else None,
            "can_link": can_link,
            "showing_all": request.GET.get("show", "") == "all",
        },
    )


def _handle(
    request: HttpRequest, *, form: ImportForm | None = None, code: str = ""
) -> HttpResponse | None:
    action = request.POST.get("action", "")
    if action not in ACTIONS:
        return None
    policy.require(request.user, Screen.MIGRATION, ACTIONS[action], request=request)

    try:
        if action == "import":
            return _import(request, form)
        if action in {"validate", "commit"}:
            return _run_batch_step(request, action, code)
        return _run_link_step(request, action)
    except (DjangoValidationError, PermissionDenied) as exc:
        messages.error(request, _message_of(exc))
        return None
    except ObjectDoesNotExist:
        messages.error(request, _("سجل غير موجود"))
        return None
    except (
        archive_service.AlreadyCommittedError,
        archive_service.NotValidatedError,
        archive_service.ReconciliationError,
        batch_service.ArchivedBatchError,
        batch_service.BatchStateError,
        batch_service.DuplicateSourceError,
        linking_service.AlreadyLinkedError,
        linking_service.UnidentifiedRowError,
    ) as exc:
        messages.error(request, str(exc))
        return None


def _import(request: HttpRequest, form: ImportForm | None) -> HttpResponse | None:
    if form is None or not form.is_valid():
        return None
    data = form.cleaned_data
    batch = batch_service.import_workbook(
        actor=request.user,
        path=data["path"],
        code=data["code"],
        note_ar=data["note_ar"],
        request=request,
    )
    messages.success(
        request,
        _("قُرئ %(rows)s صفاً — شغّل التدقيق التجريبي قبل الأرشفة") % {"rows": batch.row_count},
    )
    return redirect("datamigration:batch-detail", code=batch.code)


def _run_batch_step(request: HttpRequest, action: str, code: str) -> HttpResponse | None:
    target = code or request.POST.get("code", "")
    batch = batch_service.batch_instance(actor=request.user, code=target, request=request)
    if action == "validate":
        report = validation_service.validate(actor=request.user, batch=batch, request=request)
        messages.success(
            request,
            _("تدقيق تجريبي: %(ok)s صالح · %(un)s بلا رقم — لم يُنشأ سجل أرشيف")
            % {"ok": report["archivable"], "un": report["unidentified"]},
        )
    else:
        summary = archive_service.commit(actor=request.user, batch=batch, request=request)
        messages.success(
            request,
            _("أُرشف %(n)s صفاً — لا أثر مالي") % {"n": summary["archived"]},
        )
    return redirect("datamigration:batch-detail", code=target)


def _run_link_step(request: HttpRequest, action: str) -> HttpResponse | None:
    historical = linking_service.historical_instance(
        actor=request.user,
        historical_id=int(request.POST.get("historical_id", "0")),
        request=request,
    )
    if action == "unlink":
        linking_service.unlink(
            actor=request.user,
            historical=historical,
            note_ar=request.POST.get("note_ar", ""),
            request=request,
        )
        messages.success(request, _("فُكّ الربط"))
        return redirect("datamigration:links")

    form = LinkForm(request.POST)
    if not form.is_valid():
        return None

    from apps.people.services import participant_service

    participant = participant_service.participant_instance(
        actor=request.user,
        participant_number=form.cleaned_data["participant_number"],
        request=request,
    )
    linking_service.link(
        actor=request.user,
        historical=historical,
        participant=participant,
        note_ar=form.cleaned_data["note_ar"],
        request=request,
    )
    messages.success(request, _("رُبط السجل التاريخي بالمشارك"))
    return redirect("datamigration:links")
