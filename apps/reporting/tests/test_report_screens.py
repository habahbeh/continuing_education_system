"""
التقارير السبعة على الشاشة وعلى الورق (Sprint 8L).

الاختبارات هنا تسأل عمّا يراه القارئ: هل ظهر الرقم الذي احتسبته الخدمة، وهل
جمع عمودُ الجدول ما تقوله البطاقة، وهل ثبت عدد الاستعلامات حين كثرت الصفوف.
وهي غير اختبارات `test_reports.py` التي تسأل عن الحمولة نفسها.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD = f"?from={TERM_START.isoformat()}&to=2026-12-31"
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):  # type: ignore[no-untyped-def]
    def _in(user):
        client.force_login(user)
        return client

    return _in


def _url(number: int, query: str = PERIOD) -> str:
    return reverse("reporting:report", args=[number]) + query


# ---------------------------------------------------------------------------
# التقرير 1 — الإيراد
# ---------------------------------------------------------------------------
def test_report_1_shows_the_cohort_breakdown_the_demo_promised(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """«كم قُبض؟» يتبعه دائماً «من أيّ دفعة؟» — والثاني كان غائباً حتى 8L."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    make_paid_enrollment(cohort_with_agreement, index=2, amount="100.000")

    response = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1")
    report = response.context["report"]

    assert response.status_code == 200
    assert len(report["by_cohort"]) == 1
    row = report["by_cohort"][0]
    assert row["code"] == cohort_with_agreement.code
    assert row["participants"] == 2
    assert row["collected"] == Decimal("370.000")


def test_report_1_cohort_column_adds_up_to_the_headline_total(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    البطاقة والجدول يقرآن الصفوف نفسها، فإن اختلفا فالخلل فوقهما.

    ما لا يُنسب إلى دفعة (الدفعات غير المخصَّصة) يبقى خارج الجدول عمداً، فيُطرح
    من المقارنة بدل أن يُحشر في صفّ لا دفعة له.
    """
    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="100.000")

    report = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").context["report"]
    cohort_sum = sum(row["collected"] for row in report["by_cohort"])

    assert cohort_sum == report["total"] - report["unallocated_credit"]


def test_report_1_counts_receipts_not_allocations(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """سندٌ واحد يُنزَّل على بندين ليس سندين — والبطاقة تقول «سند»."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")

    report = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").context["report"]
    assert report["receipt_count"] == 1


def test_report_1_query_count_does_not_follow_the_number_of_cohorts(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    التفصيل يُجمَّع من الصفوف المجلوبة أصلاً — لا استعلام لكل دفعة ولا لكل مبلغ.

    القياس بين ثلاثة صفوف وستّة لا بين واحد وستّة: أول تصيير في الجلسة يدفع
    ثمن تهيئة لا علاقة لها بعدد الصفوف (قراءة إعداد، كتابة جلسة)، وقياسه مع
    النموّ يخلط ثمناً يُدفع مرّة بثمنٍ يُدفع لكل صفّ.
    """
    client = signed_in(finance)
    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="100.000")
    client.get(_url(1), HTTP_HOST="127.0.0.1")  # تسخين: الجلسة تُكتب مرّة
    with CaptureQueriesContext(connection) as first:
        client.get(_url(1), HTTP_HOST="127.0.0.1")

    for index in (4, 5, 6, 7, 8, 9):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="100.000")
    with CaptureQueriesContext(connection) as later:
        client.get(_url(1), HTTP_HOST="127.0.0.1")

    assert len(later) == len(first), f"{len(first)} → {len(later)} استعلاماً بثلاثة أضعاف الصفوف"


def test_the_display_setting_is_read_once_per_page_not_once_per_amount(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    قُيس قبل الإصلاح: ١٢ استعلاماً عن `money_display_dp` في تصيير واحد.

    السبب أن `format_money` تقرأ الإعداد في كل نداء؛ والوسم يقرؤه مرّة ويضعه
    على الطلب. الاختبار هنا لأن الانحدار صامت: الصفحة تبقى صحيحة وتبطؤ وحدها.
    """
    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="100.000")

    with CaptureQueriesContext(connection) as captured:
        signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1")

    reads = [q for q in captured.captured_queries if "money_display_dp" in str(q["sql"])]
    assert len(reads) <= 1, f"قراءات إعداد العرض: {len(reads)}"


# ---------------------------------------------------------------------------
# الهيكل المشترك
# ---------------------------------------------------------------------------
def test_money_is_printed_with_two_places_not_three(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """ثلاث خانات تخزينية على ورقة مالية خطأ عرضٍ لا دقّة زائدة (Q-04)."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    assert "270.00" in body
    assert "270.000" not in body


def test_the_tab_strip_marks_what_the_role_may_not_open(signed_in, registrar) -> None:
    """المحجوب يبقى في الشريط معلَّماً: معرفة أنه موجود خيرٌ من ظنّه غير موجود."""
    body = signed_in(registrar).get(_url(4), HTTP_HOST="127.0.0.1").content.decode()

    assert body.count("rpt-tab") >= 7, "السبعة كلها في الشريط"
    assert "is-off" in body, "ما ليس من نصيب الدور معلَّم"
    assert "إجمالي الإيرادات ضمن فترة زمنية" in body


def test_the_paper_header_names_the_report_its_period_and_who_printed_it(
    signed_in, finance
) -> None:
    """ورقةٌ تُحفَظ في ملف يجب أن تقول بنفسها ما هي — الشاشة لها سياق والورقة لا."""
    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    assert "print-only" in body
    assert "جامعة البترا" in body
    assert "طبعه" in body
    assert TERM_START.isoformat() in body


def test_the_third_report_offers_a_partner_field_it_used_to_read_silently(
    signed_in, finance
) -> None:
    """`?partner=` كان يُقرأ من العنوان بلا حقلٍ يكتبه."""
    body = signed_in(finance).get(reverse("reporting:report", args=[3]), HTTP_HOST="127.0.0.1")

    assert 'name="partner"' in body.content.decode()


def test_the_seventh_report_offers_a_category_field(signed_in, finance) -> None:
    body = signed_in(finance).get(_url(7), HTTP_HOST="127.0.0.1").content.decode()

    assert 'name="category"' in body


def test_the_index_says_what_each_report_answers(signed_in, finance) -> None:
    body = (
        signed_in(finance).get(reverse("reporting:reports"), HTTP_HOST="127.0.0.1").content.decode()
    )

    assert 'class="stepper as-menu"' in body, "بأسلوب مسار العمل في اللوحة"
    assert "كم قُبض في المدى، وعلى أيّ بند؟" in body


# ---------------------------------------------------------------------------
# التقرير 2 — صافي الدخل
# ---------------------------------------------------------------------------
def test_report_2_ladder_adds_up_exactly(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    ما يظهر على الشاشة هو الطرح نفسه لا شرحاً له.

    السلّم أربعة أسطر، وكلٌّ منها يجب أن يساوي ما قبله ناقص ما طُرح — وإلّا
    كان على الورقة حسابٌ ثانٍ للمال، وهو ما تقوم طبقة الخدمات كلها لمنعه.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")

    report = signed_in(finance).get(_url(2), HTTP_HOST="127.0.0.1").context["report"]

    assert report["after_partners"] == report["collected"] - report["partner_total"]
    assert report["net_income"] == report["after_partners"] - report["expenses_total"]


def test_report_2_discloses_what_it_does_not_subtract(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """التأمين والذمّة السابقة والاسترداد التاريخي: تُذكر ولا تُطرح."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")

    response = signed_in(finance).get(_url(2), HTTP_HOST="127.0.0.1")
    report = response.context["report"]
    body = response.content.decode()

    assert "أرقام تُفصَح ولا تُطرح" in body
    for key in (
        "deposits_excluded",
        "prior_year_settlements",
        "total_cash_in",
        "historical_refunds_paid",
        "historical_refunds_reversed",
        "historical_refunds_net",
    ):
        assert key in report, f"{key} محتسَب في الحمولة ويجب أن يُعرض"

    # الإفصاح إفصاح: لا يمسّ الصافي.
    assert (
        report["net_income"]
        == report["collected"] - report["partner_total"] - report["expenses_total"]
    )


def test_report_2_shows_what_the_university_kept_from_each_partner(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    «بعد حصص الشركاء» بلا بسطٍ لكل شريك نصفُ جواب.

    حصة الجامعة مشتقّة من رقمين على مطالبة معتمدة، لا محتسَبة من جديد هنا.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    report = signed_in(finance).get(_url(2), HTTP_HOST="127.0.0.1").context["report"]

    for row in report["by_partner"]:
        assert row["university_share"] == row["gross_collected"] - row["net_payable"]


# ---------------------------------------------------------------------------
# التقرير 4 — المتأخرون
# ---------------------------------------------------------------------------
def test_report_4_query_count_is_flat_however_many_are_overdue(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    قِيس في 8L-0: ثلاثة عشر استعلاماً إضافياً لكل متأخّر (٣٠ لصفّ و٩٦ لستّة).

    السبب ``get_enrollment`` داخل حلقة الصفوف ومعه تقييمٌ مفرد للتأخّر. تقريرٌ
    يبطؤ كلّما زاد المتأخرون يكون أبطأ ما يكون في اليوم الذي يُحتاج فيه.
    """
    client = signed_in(finance)
    url = _url(4, "?from=2026-01-01&to=2026-12-31")
    for index in (1, 2):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="10.000")
    client.get(url, HTTP_HOST="127.0.0.1")  # تسخين
    with CaptureQueriesContext(connection) as few:
        client.get(url, HTTP_HOST="127.0.0.1")

    for index in (3, 4, 5, 6, 7, 8):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="10.000")
    with CaptureQueriesContext(connection) as many:
        response = client.get(url, HTTP_HOST="127.0.0.1")

    assert len(response.context["report"]["rows"]) > len(few.captured_queries) / 40
    assert len(many) == len(few), f"{len(few)} → {len(many)} استعلاماً بأربعة أضعاف الصفوف"


def test_report_4_carries_the_phone_number_for_the_chase(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """ملاحقةُ ذمّة بلا رقمٍ في الصفّ تُرسل الموظّف إلى شاشة أخرى لكل اسم."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="10.000")

    report = (
        signed_in(finance)
        .get(_url(4, "?from=2026-01-01&to=2026-12-31"), HTTP_HOST="127.0.0.1")
        .context["report"]
    )

    for row in report["rows"]:
        assert "participant_phone" in row
        assert "enrolled_on" in row


def test_report_4_totals_match_the_rows(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="10.000")

    report = (
        signed_in(finance)
        .get(_url(4, "?from=2026-01-01&to=2026-12-31"), HTTP_HOST="127.0.0.1")
        .context["report"]
    )

    assert report["total_outstanding"] == sum(r["balance"] for r in report["rows"])
    assert report["total_due_sum"] == sum(r["total_due"] for r in report["rows"])
    assert report["count"] == len(report["rows"])
    assert report["voiding_count"] == sum(
        1 for r in report["rows"] if r["voids_partner_entitlement"]
    )


def test_the_bulk_overdue_rule_agrees_with_the_single_one(
    finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    تطبيقٌ واحد للقاعدة لا اثنان.

    المفرد يفوّض إلى المجمَّع، فلا يمكن أن يفترقا — وهذا الاختبار يمسك اليوم
    الذي يعيد فيه أحدهم كتابة المفرد ظنّاً أنه يسرّعه.
    """
    from datetime import date as _date

    from apps.operations.models import Enrollment
    from apps.settlements.services import entitlement_service

    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="10.000")
    enrollments = list(Enrollment.objects.select_related("cohort", "participant"))
    as_of = _date(2026, 12, 31)

    bulk = entitlement_service.payment_overdue_map(enrollments, as_of=as_of)
    for enrollment in enrollments:
        assert bulk[enrollment.pk] == entitlement_service.is_payment_overdue(
            enrollment, as_of=as_of
        )


# ---------------------------------------------------------------------------
# التقرير 5 — كشف الطالب
# ---------------------------------------------------------------------------
def test_report_5_opens_on_a_searchable_list_not_on_a_code_prompt(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """مَن لا يحفظ رمز التسجيل كان لا يصل إلى الكشف أصلاً."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")

    response = signed_in(finance).get(reverse("reporting:report", args=[5]), HTTP_HOST="127.0.0.1")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'name="q"' in body
    assert response.context["report"]["rows"], "القائمة تعرض المشاركين"


def test_report_5_sums_a_participant_not_an_enrolment(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §9.5 نصّه «للطالب»، وطالبٌ له تسجيلان له حسابٌ واحد.

    الصفّ يجمع تسجيلات المشارك، ومجموع مستحقّاته يساوي مجموع مستحقّات
    تسجيلاته — وهو ما يمنع أن يصير الكشف رأياً ثانياً في الحساب.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="100.000")
    report = (
        signed_in(finance)
        .get(reverse("reporting:report", args=[5]), HTTP_HOST="127.0.0.1")
        .context["report"]
    )

    row = next(r for r in report["rows"] if r["enrollments"])
    assert row["balance"] == row["total_due"] - row["total_paid"] - row.get("credit_applied", 0)
    assert row["enrollments"] == len(row["codes"])


def test_report_5_search_folds_the_arabic_hamza(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """ترتيب MySQL يرى «أحمد» و«احمد» سلسلتين مختلفتين؛ البحث لا يراهما كذلك."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="10.000")

    folded = (
        signed_in(finance)
        .get(reverse("reporting:report", args=[5]) + "?q=مشارك", HTTP_HOST="127.0.0.1")
        .context["report"]
    )

    assert folded["query"] == "مشارك"
    assert folded["rows"], "الاسم المزروع «مشارك رقم … الرباعي»"


def test_report_5_statement_is_the_screen_the_participant_already_has(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """مستندان يقرآن حساباً واحداً هما الطريق إلى أن يختلفا — فهو مستند واحد."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount="100.000")
    code = "EN-PRT-1"

    response = signed_in(finance).get(
        reverse("reporting:report", args=[5]) + f"?enrollment={code}", HTTP_HOST="127.0.0.1"
    )

    assert response.status_code == 200
    assert response.context["report"]["statement"] is not None


def test_report_5_query_count_is_flat_however_many_participants(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """القائمة تُبنى باستعلامات مجمَّعة — لا استعلام لكل مشارك."""
    client = signed_in(finance)
    url = reverse("reporting:report", args=[5])
    for index in (1, 2):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="50.000")
    client.get(url, HTTP_HOST="127.0.0.1")
    with CaptureQueriesContext(connection) as few:
        client.get(url, HTTP_HOST="127.0.0.1")

    for index in (3, 4, 5, 6, 7, 8):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="50.000")
    with CaptureQueriesContext(connection) as many:
        client.get(url, HTTP_HOST="127.0.0.1")

    assert len(many) == len(few), f"{len(few)} → {len(many)} استعلاماً بأربعة أضعاف المشاركين"


# ---------------------------------------------------------------------------
# مراجعة 8L-2 — ما كشفته المقارنة بالديمو
# ---------------------------------------------------------------------------
def test_the_tab_strip_carries_the_period_it_used_to_drop(signed_in, finance) -> None:
    """
    مدقّق يحدّد سنةً ثم ينقر التبويب الثاني كان يعود إلى «آخر ٣٠ يوماً» صامتاً.

    في أداةٍ مالية تَغيُّرُ الأرقام بلا سبب ظاهر يُقرأ خللاً في النظام لا في
    التنقّل — وهو أخطر من نقصٍ بصري.
    """
    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    assert f'href="/reports/2/?from={TERM_START.isoformat()}' in body
    assert 'href="/reports/2/"' not in body


def test_no_report_template_carries_an_inline_style(signed_in, finance) -> None:
    """
    §6.4 — أُزيلت الأنماط السطرية في 8J-5 ولا تعود.

    و`input.css` يشرح البديل عند تعريف `.bar`: «الخانة الممتلئة صنف، لا عرضٌ
    محسوب». الاختبار على القوالب لا على الصفحة، لأن الصفحة قد لا تُظهر الصفّ
    الذي يحمل المخالفة.
    """
    from pathlib import Path

    templates = Path("templates/reporting")
    offenders = [
        f"{path.name}:{index}"
        for path in templates.glob("*.html")
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if 'style="' in line
    ]
    assert not offenders, f"أنماط سطرية: {offenders}"


def test_the_percentage_bar_is_segments_not_a_computed_width(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    make_paid_enrollment(cohort_with_agreement, index=1, amount="100.000")
    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    assert 'class="bar calm"' in body
    # `style="width:` لا `width:` وحدها: الصفحة تحمل ورقة أنماط فيها
    # `max-width` وهي ليست نمطاً سطرياً.
    assert 'style="width:' not in body


def test_the_semester_button_overrides_the_dates(signed_in, finance, active_semester) -> None:
    """
    «الفصل» هو الوحدة التي يفكّر بها المركز؛ التاريخان يجعلانه حساباً في الرأس.

    والزرّ يغلب التاريخين لأنه لا يُرسَل إلا إذا ضُغط — وبلا هذه الأسبقية لا
    يُعرف من القيم وحدها أ اختار المستخدم فصلاً أم حرّر التاريخ.
    """
    url = reverse("reporting:report", args=[1]) + (
        f"?from=2026-01-01&to=2026-01-31&pick={active_semester.code}"
    )
    response = signed_in(finance).get(url, HTTP_HOST="127.0.0.1")

    assert response.context["date_from"] == active_semester.starts_on.isoformat()
    assert response.context["date_to"] == active_semester.ends_on.isoformat()


def test_every_report_tab_carries_an_icon(signed_in, finance) -> None:
    """سبعة عناوين متشابهة الطول في شريط واحد تُقرأ كتلةً رمادية."""
    from apps.reporting.services import report_service

    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    for number, icon in report_service.REPORT_ICONS.items():
        assert f'href="#i-{icon}"' in body, f"التقرير {number} بلا أيقونة"


def test_a_row_links_only_where_the_role_may_follow(
    signed_in, finance, registrar, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §3.4 — لا تُرسل قارئاً إلى رفض.

    موظف التسجيل يفتح المشاركين ولا يفتح الشركاء، فاسم المشارك رابطٌ له واسم
    الشريك ليس كذلك — ورابطٌ يوقعه في 403 يلوّث سجل التدقيق بمحاولةٍ سببها أن
    الشاشة دعته إليها.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="10.000")
    overdue = _url(4, "?from=2026-01-01&to=2026-12-31")

    as_registrar = signed_in(registrar).get(overdue, HTTP_HOST="127.0.0.1")
    assert as_registrar.context["may_open_participant"] is True
    assert as_registrar.context["may_open_partner"] is False

    body = signed_in(finance).get(overdue, HTTP_HOST="127.0.0.1").content.decode()
    if signed_in(finance).get(overdue, HTTP_HOST="127.0.0.1").context["report"]["rows"]:
        assert "/participants/" in body


def test_an_empty_partner_table_names_the_cause_above_it(signed_in, finance) -> None:
    """
    جدولٌ فارغ يقول «لا مطالبات» صادقٌ وناقص.

    السبب قد يكون أن لا دفعة مرتبطة باتفاقية أصلاً — حلقةٌ أعلى من التقرير،
    والقارئ الذي لا يعرفها يبحث عن الخلل في التقرير.
    """
    response = signed_in(finance).get(_url(3), HTTP_HOST="127.0.0.1")
    report = response.context["report"]
    body = response.content.decode()

    assert report["any_cohort_has_agreement"] is False
    assert "لا دفعة مرتبطة باتفاقية شريك" in body


def test_report_6_shows_when_the_closing_was_approved(signed_in, finance) -> None:
    body = signed_in(finance).get(_url(6), HTTP_HOST="127.0.0.1").content.decode()

    assert "وقت الاعتماد" in body


def test_report_2_shows_where_every_collected_dinar_went(signed_in, finance) -> None:
    """السلّم يقول المبالغ، والأشرطة تقول النِّسب — و«٨٨٪» هي المقارنة لا الرقم."""
    body = signed_in(finance).get(_url(2), HTTP_HOST="127.0.0.1").content.decode()

    assert "أين ذهب كل دينار حُصِّل" in body
    assert "split-bars" in body


def test_the_index_cards_carry_their_icons_too(signed_in, finance) -> None:
    """البطاقة والتبويب يعرضان الشيء نفسه؛ أن يختلفا يجعلهما شاشتين لا واحدة."""
    from apps.reporting.services import report_service

    body = (
        signed_in(finance).get(reverse("reporting:reports"), HTTP_HOST="127.0.0.1").content.decode()
    )

    for icon in report_service.REPORT_ICONS.values():
        assert f'href="#i-{icon}"' in body


# ---------------------------------------------------------------------------
# B-1 · B-2 · B-3 — ما وافق عليه المستخدم بعد المراجعة
# ---------------------------------------------------------------------------
def test_a_long_report_draws_one_page_not_every_row(
    signed_in, finance, manager, cohort_with_agreement
) -> None:
    """
    ٥٠ صفّاً على الشاشة، والباقي بنقرة — كسجلّ السندات حرفياً.

    الشاشة المرجعية تُرقّم منذ 8B؛ التقارير كانت تُنزِل كل صفّ إلى المتصفّح.
    """
    from datetime import date as _date

    from apps.expenses.services import expense_service

    for index in range(55):
        expense = expense_service.record(
            actor=finance,
            category="CONSUMABLES",
            amount=Decimal("5.000"),
            incurred_on=_date(2026, 10, 1),
            description_ar=f"قيد اختبار {index}",
        )
        expense_service.approve(actor=manager, expense=expense)

    url = _url(7, "?from=2026-01-01&to=2026-12-31")
    first = signed_in(finance).get(url, HTTP_HOST="127.0.0.1")

    assert len(first.context["report"]["rows"]) == 50
    assert first.context["pages"]["page"]["total"] == 55
    assert first.context["pages"]["page"]["pages"] == 2

    second = signed_in(finance).get(url + "&page=2", HTTP_HOST="127.0.0.1")
    assert len(second.context["report"]["rows"]) == 5


def test_the_totals_count_every_row_not_the_page(
    signed_in, finance, manager, cohort_with_agreement
) -> None:
    """
    أخطر ما في الترقيم أن يصير «الإجمالي» إجماليَ أول خمسين.

    البطاقات والمجاميع تُحتسب في الخدمة على الصفوف كلها، والقصّ للعرض وحده.
    """
    from datetime import date as _date

    from apps.expenses.services import expense_service

    for index in range(55):
        expense = expense_service.record(
            actor=finance,
            category="CONSUMABLES",
            amount=Decimal("5.000"),
            incurred_on=_date(2026, 10, 1),
            description_ar=f"قيد مجموع {index}",
        )
        expense_service.approve(actor=manager, expense=expense)

    report = (
        signed_in(finance)
        .get(_url(7, "?from=2026-01-01&to=2026-12-31"), HTTP_HOST="127.0.0.1")
        .context["report"]
    )

    assert report["approved_total"] == Decimal("275.000"), "٥٥ × ٥ — لا ٥٠ × ٥"
    assert len(report["rows"]) == 50


def test_a_nonsense_page_number_lands_on_page_one(signed_in, finance) -> None:
    """رقم الصفحة يأتي من عنوانٍ يحرّره أي أحد؛ سجلٌّ يسقط على `?page=abc` سجلٌّ معطوب."""
    response = signed_in(finance).get(
        _url(7, "?from=2026-01-01&to=2026-12-31&page=abc"), HTTP_HOST="127.0.0.1"
    )

    assert response.status_code == 200
    assert response.context["pages"]["page"]["number"] == 1


def test_the_pager_keeps_the_period(signed_in, finance) -> None:
    """الانتقال بين الصفحات لا يُسقط المرشّحات — العيب نفسه الذي أُصلح في التبويب."""
    response = signed_in(finance).get(_url(7), HTTP_HOST="127.0.0.1")

    assert f"from={TERM_START.isoformat()}" in response.context["params_qs"]
    assert "page" not in response.context["params_qs"]


def test_the_export_carries_every_row_not_the_page(signed_in, finance, manager) -> None:
    """التصدير يقرأ الكلّ — وإلّا كان ملفاً يحمل صفحةً ويبدو جدولاً."""
    from datetime import date as _date

    from apps.expenses.services import expense_service

    for index in range(55):
        expense = expense_service.record(
            actor=finance,
            category="CONSUMABLES",
            amount=Decimal("5.000"),
            incurred_on=_date(2026, 10, 1),
            description_ar=f"قيد تصدير {index}",
        )
        expense_service.approve(actor=manager, expense=expense)

    response = signed_in(finance).get(
        reverse("reporting:report-export", args=[7]) + "?from=2026-01-01&to=2026-12-31",
        HTTP_HOST="127.0.0.1",
    )
    lines = response.content.decode("utf-8-sig").strip().splitlines()

    assert len(lines) == 56, "٥٥ صفّاً وترويسة"


def test_an_oversized_export_is_refused_not_truncated(signed_in, finance) -> None:
    """
    ملفٌّ مبتور يفتحه محاسب فيراه كاملاً هو أسوأ ما يخرج من تقرير مالي.

    لا شيء على الورقة يقول إن ثمّة بقيّة؛ فالرفض يقول ما العمل.
    """
    from apps.reporting.services import report_service

    report = {"number": 7, "rows": [{"code": str(i)} for i in range(10)]}
    with pytest.raises(report_service.ExportTooLargeError) as refusal:
        report_service.csv_rows(report, limit=5)

    assert "ضيّق المدى" in str(refusal.value)


def test_report_1_shows_collection_rate_not_share_of_revenue(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    «المحصَّل ÷ المستحق» لا «المقبوض ÷ إيراد المدى».

    الأولى تكافئ الدفعة الكبيرة، والثانية تكشف الدفعة المتعثّرة ولو كانت
    صغيرة — وهي التي يُتابَع أهلها.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount="100.000")

    report = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").context["report"]
    row = report["by_cohort"][0]

    assert row["total_due"] > row["collected"], "دُفع جزءٌ من المستحق"
    assert row["outstanding"] == row["total_due"] - row["collected"]
    assert row["enrolled"] >= row["participants"], "المقام كل المسجّلين لا الدافعين"


def test_report_1_cohort_due_costs_no_query_per_cohort(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    client = signed_in(finance)
    for index in (1, 2, 3):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="50.000")
    client.get(_url(1), HTTP_HOST="127.0.0.1")
    with CaptureQueriesContext(connection) as few:
        client.get(_url(1), HTTP_HOST="127.0.0.1")

    for index in (4, 5, 6, 7, 8, 9):
        make_paid_enrollment(cohort_with_agreement, index=index, amount="50.000")
    with CaptureQueriesContext(connection) as many:
        client.get(_url(1), HTTP_HOST="127.0.0.1")

    assert len(many) == len(few), f"{len(few)} → {len(many)} استعلاماً"


def test_every_report_counter_carries_an_icon(signed_in, finance) -> None:
    """
    كل عدّاد في النظام له رمز — اللوحة والصندوق — وعدّادات التقارير كانت بلا رموز.

    والرمز نفسه المستعمَل في شريط التبويب، فالبطاقة والتبويب يقولان الشيء
    نفسه ولا يصيران شاشتين.
    """
    for number in (1, 2, 3, 4, 6, 7):
        body = signed_in(finance).get(_url(number), HTTP_HOST="127.0.0.1").content.decode()
        tiles = body.count('class="kpi tile')
        icons = body.count('<span class="ico"')
        assert tiles > 0, f"التقرير {number} بلا عدّادات"
        assert icons >= tiles, f"التقرير {number}: {tiles} عدّاداً و{icons} رمزاً"


def test_the_counters_are_figures_not_links(signed_in, finance) -> None:
    """
    العدّاد رقمٌ يُقرأ لا وجهةٌ تُفتح.

    بطاقةٌ تبدو قابلة للنقر ولا تُنقر تَعِد بما لا يقع (§2.2)، وهذا هو الفرق
    بين عدّاد التقرير ومحطة «مسار العمل» التي نُقلت إلى فهرس التقارير.
    """
    body = signed_in(finance).get(_url(1), HTTP_HOST="127.0.0.1").content.decode()

    assert '<a class="kpi' not in body
