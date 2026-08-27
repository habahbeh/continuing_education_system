"""
Catalogue screens (PERMISSIONS.md §3.3 rows 9–13).

Four screens, three of which are the same list filtered by programme type —
diplomas, short courses and online courses are separate rows in the matrix
because they carry different permissions, not because they are different
things. Keeping one view and one template means a permission change lands in
one place.

Views render and delegate. Every permission question goes through
``policy.require``; nothing here decides anything by inspecting a role.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.catalog.services import catalog_service, pricing_service
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: Programme type → the screen that governs it (PERMISSIONS.md §3.3).
TYPE_BY_SCREEN: dict[str, str] = {
    Screen.PROGRAMS: "DIPLOMA",
    Screen.SHORT_COURSES: "SHORT_COURSE",
    Screen.ONLINE_COURSES: "ONLINE_COURSE",
}

#: …and the list it came from, so a detail page can offer the way back. The
#: reader already passed this screen's VIEW gate to be here, so the link
#: cannot send them into a refusal.
LIST_ROUTE_BY_SCREEN: dict[str, str] = {
    Screen.PROGRAMS: "catalog:programs",
    Screen.SHORT_COURSES: "catalog:short-courses",
    Screen.ONLINE_COURSES: "catalog:online-courses",
}


#: What each of the three catalogue screens is FOR, in the reader's words.
#: Kept here beside ``TYPE_BY_SCREEN`` so the three rows of §3.3 that differ
#: only by programme type read differently on screen as well.
SUBTITLE_BY_SCREEN: dict[str, Any] = {
    Screen.PROGRAMS: _(
        "الدبلومات المعرَّفة في الكتالوج: الرمز والمجال والساعات وحالة التفعيل. "
        "الأسعار تُقرأ من قوائم الأسعار المؤرّخة، والتشغيل من الدفعات — ولا تعريف "
        "ولا تعديل من هذه الشاشة."
    ),
    Screen.SHORT_COURSES: _(
        "الدورات القصيرة المعرَّفة في الكتالوج. مجال الدورة ليس وصفاً: هو ما يحدّ "
        "النقل المسموح بين دورتين (BR-061). الأسعار في قوائم الأسعار المؤرّخة، "
        "ولا تعريف ولا تعديل من هذه الشاشة."
    ),
    Screen.ONLINE_COURSES: _(
        "الدورات الأونلاين المعرَّفة في الكتالوج: الرمز والساعات وحالة التفعيل. "
        "الأسعار تُقرأ من قوائم الأسعار المؤرّخة، ولا تعريف ولا تعديل من هذه الشاشة."
    ),
}


def _program_list(request: HttpRequest, screen: str, title: str) -> HttpResponse:
    policy.require(request.user, screen, Action.VIEW, request=request)

    # Materialised once: the template iterates it and the counts below are read
    # off the same list, so the chips cannot disagree with the rows.
    programs = list(
        catalog_service.list_programs(
            actor=request.user, program_type=TYPE_BY_SCREEN[screen], request=request
        )
    )
    active = sum(1 for p in programs if p.is_active)
    return render(
        request,
        "catalog/programs.html",
        {
            "title": title,
            "subtitle": SUBTITLE_BY_SCREEN[screen],
            "programs": programs,
            # Counted off the rows on screen, never queried again. A bucket
            # nobody is in gets no chip rather than a zero.
            "counts": [
                row
                for row in (
                    {"label": _("نشط"), "count": active, "active": True},
                    {"label": _("غير نشط"), "count": len(programs) - active, "active": False},
                )
                if row["count"]
            ],
            "screen": screen,
            "can_create": policy.is_allowed(request.user, screen, Action.CREATE),
            "can_edit": policy.is_allowed(request.user, screen, Action.EDIT),
            "can_approve": policy.is_allowed(request.user, screen, Action.APPROVE),
        },
    )


def diplomas_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.PROGRAMS, _("الدبلومات التدريبية"))


def short_courses_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.SHORT_COURSES, _("الدورات القصيرة"))


def online_courses_view(request: HttpRequest) -> HttpResponse:
    return _program_list(request, Screen.ONLINE_COURSES, _("الدورات الأونلاين"))


def program_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    """
    One programme, with its subjects and the BR-006 reconciliation.

    The variance is shown whether or not it reconciles: a diploma that is
    short by ten dinars should say so on the screen, not only when someone
    tries to approve it.
    """
    try:
        program = catalog_service.get_program(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist:
        raise Http404(_("لا يوجد برنامج بهذا الرمز")) from None

    screen = catalog_service.screen_for(program.program_type)
    return render(
        request,
        "catalog/program_detail.html",
        {
            "program": program,
            "subjects": program.subjects.all(),
            "subject_total": pricing_service.subject_price_total(program),
            "screen": screen,
            # The list this came from, named by the screen's own label so no
            # new string is invented (Screen is a TextChoices).
            "list_url": reverse(LIST_ROUTE_BY_SCREEN[screen]),
            "list_label": Screen(screen).label,
            # ``Subject`` is documented as a DIPLOMA subject (DATA_MODEL §5.4),
            # and BR-006 weighs their sum. A short course legitimately has
            # none, so only the diploma gets told when the section is empty.
            "expects_subjects": screen == Screen.PROGRAMS,
            "can_edit": policy.is_allowed(request.user, screen, Action.EDIT),
        },
    )


def price_lists_view(request: HttpRequest) -> HttpResponse:
    policy.require(request.user, Screen.PRICELISTS, Action.VIEW, request=request)

    return render(
        request,
        "catalog/pricelists.html",
        {
            "price_lists": catalog_service.list_price_lists(actor=request.user, request=request),
            # Row 13 grants no APPROVE to anyone: the president approves
            # outside the system (footnote 8, D-31). Recording that decision
            # is an edit, which is why this asks for EDIT.
            "can_record_approval": policy.is_allowed(request.user, Screen.PRICELISTS, Action.EDIT),
        },
    )


def price_list_detail_view(request: HttpRequest, code: str) -> HttpResponse:
    try:
        price_list = catalog_service.get_price_list(actor=request.user, code=code, request=request)
    except ObjectDoesNotExist:
        raise Http404(_("لا توجد قائمة أسعار بهذا الرمز")) from None

    return render(
        request,
        "catalog/pricelist_detail.html",
        {
            "price_list": price_list,
            "items": price_list.items.select_related("program", "deposit_policy"),
            "fee_rules": price_list.registration_fee_rules.select_related("program"),
            "can_edit": policy.is_allowed(request.user, Screen.PRICELISTS, Action.EDIT)
            and not price_list.is_frozen,
        },
    )
