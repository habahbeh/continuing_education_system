"""
The reports screen (§9).

**One screen, seven documents.** Granting the screen is not granting the
seven: ``policy.require_report`` decides each one and audits the refusal
(BR-080, BR-099). The finance manager reaches this screen and exactly one
report; the cashier reaches neither.

Views render and delegate. Every figure comes from the service that owns it —
nothing here adds up money.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils.translation import gettext as _

from apps.core import display
from apps.core.services import document_settings
from apps.people.constants import Action, Screen
from apps.people.permissions import policy
from apps.reporting.services import report_service


def _parse(raw: str, fallback: date) -> date:
    try:
        return date.fromisoformat(raw) if raw else fallback
    except ValueError:
        return fallback


def _period(request: HttpRequest) -> tuple[date, date]:
    """
    المدى المقروء من العنوان، أو الفصل المضغوط زرُّه، أو آخر ثلاثين يوماً.

    Sprint 8L-2 — زرّ الفصل (`?pick=`) **يغلب** التاريخين، على نمط أزرار المدى
    في `/cashbox/payments/`. وبلا هذه الأسبقية يقع اللبس الذي لا مخرج منه:
    النموذج يُرسل حقوله كلها عند كل ضغطة، فلا يُعرف من القيم وحدها أ اختار
    المستخدم فصلاً أم حرّر التاريخ. الزرّ يقول أيّهما، لأنه لا يُرسَل إلا إذا
    ضُغط.
    """
    today = date.today()
    picked = request.GET.get("pick", "").strip()
    if picked:
        span = _semester(picked)
        if span is not None:
            return span
    return (
        _parse(request.GET.get("from", "").strip(), today - timedelta(days=30)),
        _parse(request.GET.get("to", "").strip(), today),
    )


def _semester(code: str) -> tuple[date, date] | None:
    from apps.core.services import period_service

    return period_service.semester_range(code)


def _semester_buttons(request: HttpRequest, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """
    الفصول كأزرار، والمضغوط منها معلَّم.

    «الفصل» هو الوحدة التي يفكّر بها المركز؛ من/إلى وحدهما يجعلان سؤالاً
    يومياً («كم حصّلنا هذا الفصل؟») عمليةَ حسابِ تاريخين في الرأس.
    """
    from apps.core.services import period_service

    return [
        {
            "code": semester["code"],
            "name": semester["name_ar"],
            "on": (semester["starts_on"], semester["ends_on"]) == (date_from, date_to),
        }
        for semester in period_service.recent_semesters()
    ]


def reports_index(request: HttpRequest) -> HttpResponse:
    """The menu of seven, each marked with whether this role may open it."""
    policy.require(request.user, Screen.REPORTS, Action.VIEW, request=request)
    return render(
        request,
        "reporting/index.html",
        {
            "title": _("التقارير"),
            "active_screen": Screen.REPORTS,
            "reports": report_service.available_reports(actor=request.user),
        },
    )


def _build(request: HttpRequest, number: int) -> dict[str, Any]:
    """
    Run one report.

    The gate is repeated here even though every report service calls
    ``require_report`` itself, because report 5 answers about ONE enrollment
    and returns its empty shell when no code is given — a branch that reaches
    no service, and so would otherwise render a report page to a role holding
    no report at all. Gating the route as well as the service costs one
    dictionary lookup and closes that door; the refusal is raised once,
    before the service is ever reached, so it is also audited once.
    """
    policy.require_report(request.user, number, request=request)
    date_from, date_to = _period(request)
    if number == 1:
        return report_service.revenue_report(
            actor=request.user, date_from=date_from, date_to=date_to, request=request
        )
    if number == 2:
        return report_service.net_income_report(
            actor=request.user, date_from=date_from, date_to=date_to, request=request
        )
    if number == 3:
        return report_service.partner_dues_report(
            actor=request.user,
            partner_code=request.GET.get("partner", "").strip(),
            request=request,
        )
    if number == 4:
        return report_service.overdue_report(
            actor=request.user,
            as_of=date_to,
            cohort_code=request.GET.get("cohort", "").strip(),
            request=request,
        )
    if number == 5:
        # Sprint 8L · A-4 — الكشف يُفتح بتسجيل، والقائمة تُفتح بمشارك.
        # بلا رمزٍ تُعرض قائمة المشاركين قابلةً للبحث بدل شاشةٍ تطلب رمزاً
        # يُكتب من الذاكرة.
        code = request.GET.get("enrollment", "").strip()
        if not code:
            listing = report_service.participant_dues_rows(
                actor=request.user, query=request.GET.get("q", "").strip(), request=request
            )
            return {
                "number": 5,
                "title": report_service.REPORT_TITLES[5],
                "statement": None,
                **listing,
            }
        return report_service.participant_statement_report(
            actor=request.user, enrollment_code=code, request=request
        )
    if number == 6:
        return report_service.daily_closing_report(
            actor=request.user, on_date=date_to, date_from=date_from, request=request
        )
    return report_service.expenses_report(
        actor=request.user,
        date_from=date_from,
        date_to=date_to,
        category=request.GET.get("category", "").strip(),
        request=request,
    )


def _filters(
    request: HttpRequest, number: int, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """
    الحقول التي يحتاجها سؤال هذا التقرير بعينه — لا سبعة حقول لسبعة أسئلة.

    الهيكل المشترك كان يعرض «من/إلى» للسبعة جميعاً، بينما التقرير الثالث يقرأ
    ``?partner=`` والرابع ``?cohort=`` والسابع ``?category=`` من العنوان بلا
    حقلٍ يكتبها: مرشّحات تعمل ولا تُرى، وهي أسوأ من مرشّحات لا تعمل.

    ``options`` تُطلب من الخدمة التي تملك القائمة، ومن دورٍ لا يملك تلك الشاشة
    تُرفض — فيُترك الحقل نصّياً يُكتب فيه الرمز. أن يكتب الموظّف المالي رمز
    الشريك أهون من أن تُفتح له قائمة الشركاء من باب خلفي.
    """
    period = [
        {"kind": "date", "name": "from", "label": _("من تاريخ"), "value": date_from.isoformat()},
        {"kind": "date", "name": "to", "label": _("إلى تاريخ"), "value": date_to.isoformat()},
    ]
    if number in (1, 2, 6, 7):
        fields = list(period)
    elif number == 4:
        fields = [
            {"kind": "date", "name": "to", "label": _("حتى تاريخ"), "value": date_to.isoformat()}
        ]
    else:
        fields = []

    if number == 3:
        fields.append(
            _choice_field(
                request,
                name="partner",
                label=_("الشريك"),
                options=_partner_options(request),
                placeholder=_("رمز الشريك…"),
            )
        )
    if number == 4:
        fields.append(
            _choice_field(
                request,
                name="cohort",
                label=_("الدفعة"),
                options=_cohort_options(request),
                placeholder=_("رمز الدفعة…"),
            )
        )
    if number == 7:
        fields.append(
            _choice_field(
                request,
                name="category",
                label=_("التصنيف"),
                options=_category_options(),
                placeholder=_("التصنيف…"),
            )
        )
    return fields


def _choice_field(
    request: HttpRequest,
    *,
    name: str,
    label: str,
    options: list[tuple[str, str]] | None,
    placeholder: str,
) -> dict:
    value = request.GET.get(name, "").strip()
    if options is None:
        return {
            "kind": "search",
            "name": name,
            "label": label,
            "value": value,
            "placeholder": placeholder,
        }
    return {"kind": "select", "name": name, "label": label, "value": value, "options": options}


def _partner_options(request: HttpRequest) -> list[tuple[str, str]] | None:
    from apps.partners.services import partner_service

    try:
        rows = partner_service.list_partners(actor=request.user, request=request)
    except PermissionDenied:
        return None
    return [(row["code"], row["name_ar"]) for row in rows]


def _cohort_options(request: HttpRequest) -> list[tuple[str, str]] | None:
    from apps.operations.services import cohort_service

    try:
        rows = cohort_service.list_cohorts(actor=request.user, request=request)
    except PermissionDenied:
        return None
    return [(row["code"], row.get("name_ar") or row["code"]) for row in rows]


def _category_options() -> list[tuple[str, str]]:
    from apps.expenses.services import expense_service

    return expense_service.category_choices(as_of=date.today())


#: أيّ قائمة في كل تقرير تنمو بعدد الحركات فتُرقَّم، وبأيّ مفتاح في العنوان.
#: ما ليس هنا مقيَّدٌ ببُعدٍ صغير — الشركاء والصناديق وطرق الدفع والتصنيفات
#: والدفعات — وترقيمه يضيف نقرةً بلا أن يمنع جدولاً طويلاً.
PAGED: dict[int, tuple[tuple[str, str], ...]] = {
    3: (("claims", "page"),),
    4: (("rows", "page"),),
    5: (("rows", "page"),),
    6: (("receipts", "page_receipts"),),
    7: (("rows", "page"),),
}


def _paginate(request: HttpRequest, report: dict[str, Any]) -> dict[str, Any]:
    """
    يقصّ القوائم الطويلة إلى صفحة واحدة للعرض.

    الخدمة تبقى مصدر الصفوف كلها — والمجاميع والبطاقات تُحتسب على الكلّ لا على
    الصفحة، وإلّا قال التقرير «إجمالي المتأخرين» ويقصد «إجمالي أول خمسين».
    ما يمنعه هذا هو **رسمُ** آلاف الصفوف، وهو ما يدفع ثمنه القارئ.
    """
    from apps.core.pagination import page_of

    pages: dict[str, Any] = {}
    for key, param in PAGED.get(int(report["number"]), ()):
        rows = report.get(key) or []
        page = page_of(list(rows), request.GET.get(param, ""))
        report[key] = page["rows"]
        pages[param] = {**page, "param": param}
    return pages


def _query_without(request: HttpRequest, *drop: str) -> str:
    """استعلام الصفحة بلا مفاتيح الترقيم — فالفترة والمرشّحات تبقى عند الانتقال."""
    params = request.GET.copy()
    for key in drop:
        params.pop(key, None)
    return params.urlencode()


def report_view(request: HttpRequest, number: int) -> HttpResponse:
    if number not in report_service.REPORT_TITLES:
        raise Http404
    try:
        report = _build(request, number)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    date_from, date_to = _period(request)
    pages = _paginate(request, report)
    return render(
        request,
        f"reporting/report_{number}.html",
        {
            "title": report["title"],
            "active_screen": Screen.REPORTS,
            "report": report,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "exportable": number in report_service.EXPORTABLE,
            # شريط التبويب: السبعة كلها، والمحجوب عن هذا الدور معلَّم لا محذوف
            # — أن يعرف القارئ أن التقرير موجود وليس له خيرٌ من أن يظنّه غير
            # موجود.
            "reports": report_service.available_reports(actor=request.user),
            "filters": _filters(request, number, date_from, date_to),
            "semesters": _semester_buttons(request, date_from, date_to),
            "pages": pages,
            "params_qs": _query_without(request, "page", "page_receipts"),
            # الروابط بالصلاحية لا بالدور (§3.1): صفٌّ يقود إلى ملفّ الشريك
            # لا يُرسم لمن لا يملك شاشة الشركاء، وإلّا أوقعناه في رفض وكتبنا
            # في سجل التدقيق محاولةً سببها أن الشاشة دعته إليها (§3.4).
            "may_open_partner": policy.is_allowed(request.user, Screen.PARTNERS, Action.VIEW),
            "may_open_participant": policy.is_allowed(request.user, Screen.STUDENTS, Action.VIEW),
            "may_open_cohorts": policy.is_allowed(request.user, Screen.COHORTS, Action.VIEW),
            "chrome": document_settings.chrome(as_of=date.today()),
            "printed_by": display.person_name(request.user),
        },
    )


def report_export(request: HttpRequest, number: int) -> HttpResponse:
    """
    CSV, from the standard library — no Excel dependency.

    The export runs the SAME ``require_report`` gate as the screen, inside the
    service. A download route that read more loosely than the page it exports
    would be a way around the matrix wearing a spreadsheet icon.
    """
    if number not in report_service.REPORT_TITLES:
        raise Http404
    try:
        report = _build(request, number)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc

    # التصدير يقرأ الصفوف كلها لا صفحةً منها — وهو المقصد منه. والحدّ يمنع
    # ملفاً بمئة ألف صفّ يُبنى في الذاكرة، ويرفض بدل أن يبتر (B-3).
    try:
        header, rows = report_service.csv_rows(
            report, limit=report_service.export_row_limit(as_of=date.today())
        )
    except report_service.ExportTooLargeError as too_large:
        return HttpResponseBadRequest(str(too_large), content_type="text/plain; charset=utf-8")
    if not header:
        raise Http404

    encoding = report_service.export_encoding(as_of=date.today())
    response = HttpResponse(content_type=f"text/csv; charset={encoding}")
    response["Content-Disposition"] = f'attachment; filename="report-{number}.csv"'
    response.charset = encoding

    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response
