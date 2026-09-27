"""
شاشة الفترات المالية — حارسٌ قائم لم يكن له ما يحرسه (D-23).

تسعة مواضع تسأل ``period_service.require_open``، ولا شاشة كانت تفتح فترةً ولا
تقفلها. والاختبارات هنا لا في ``apps/core/tests`` لأنها تسأل عن شاشةٍ ودورٍ
ومصفوفة، و``apps.core`` بنيةٌ تحتية لا تعرف تطبيقاً تجارياً (A-03).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent, FinancialPeriod, FinancialPeriodStatus
from apps.core.services import period_service
from apps.people.models import Role, User
from apps.people.services import fiscal_period_service

pytestmark = pytest.mark.django_db

PASSWORD = "period-probe-1234"
URL = "/financial-periods/"


def _actor(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def manager(seeded_settings):  # type: ignore[no-untyped-def]
    return _actor(Role.CENTER_MANAGER, "per.mgr")


@pytest.fixture
def september(db: None) -> FinancialPeriod:
    return FinancialPeriod.objects.create(starts_on=date(2026, 9, 1), ends_on=date(2026, 9, 30))


# ---------------------------------------------------------------------------
# الصلاحية — الإقفال ليس فعلاً صندوقيّاً، ولا خانة جديدة في المصفوفة
# ---------------------------------------------------------------------------
def test_the_screen_hangs_off_the_settings_row_and_not_off_the_till(seeded_settings) -> None:
    """
    §3.4/18 يعطي الصندوق ``V C`` على الإقفال اليومي — على عدّ النقد، وهو عمله.
    وإقفال الشهر إمضاءٌ إداريّ: من عدّ النقد ليس من يوقّع الشهر، وهو الفصل
    نفسه الذي يجريه BR-027/BR-028 داخل الإقفال اليومي.
    """
    from apps.people.constants import Screen

    assert fiscal_period_service.SCREEN == Screen.SETTINGS


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        (Role.CASHIER, 403),
        (Role.REGISTRATION_OFFICER, 403),
        (Role.FINANCE_MANAGER, 403),
    ],
)
def test_each_role_meets_what_the_matrix_grants_it(client, seeded_settings, role, expected) -> None:
    client.force_login(_actor(role, f"per.{role.lower()}"))
    assert client.get(URL, HTTP_HOST="127.0.0.1").status_code == expected


def test_a_reader_who_may_not_edit_is_offered_no_close_button(
    client, seeded_settings, september
) -> None:
    """§3.4 من قواعد التحسين — لا يُرسم زرٌّ استعماله مرفوض (BR-085)."""
    client.force_login(_actor(Role.FINANCE_OFFICER, "per.fin"))
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert f'data-opens="per-close-{september.pk}"' not in html
    assert 'data-opens="per-new"' not in html
    assert "للاطلاع فقط" in html


def test_the_cashier_cannot_close_a_month_through_the_post(client, seeded_settings, september) -> None:
    client.force_login(_actor(Role.CASHIER, "per.csh"))
    response = client.post(
        URL, {"action": "close", "period": str(september.pk)}, HTTP_HOST="127.0.0.1"
    )

    september.refresh_from_db()
    assert response.status_code == 403
    assert september.status == FinancialPeriodStatus.OPEN


# ---------------------------------------------------------------------------
# ما تقوله الشاشة قبل جدولها، وأمانته
# ---------------------------------------------------------------------------
def test_with_no_period_at_all_the_screen_says_the_guard_governs_nothing(client, manager) -> None:
    """
    وهذا هو العطل الصامت: ``period_for`` يُرجع None لتاريخٍ بلا فترة والحركة
    تُقبل، فيبقى كل شهر مفتوحاً إلى الأبد ولا شيء على أي شاشة يقول ذلك.
    """
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "لا يقع في أي فترة مالية" in html
    assert "لا فترة مالية معرَّفة بعد" in html


def test_the_screen_does_not_call_an_uncovered_date_forbidden(client, manager) -> None:
    """
    ⚠⚠ في المقابل: تاريخٌ بلا فترة **مسموح** عن قصد. شاشةٌ تقول «ممنوع» تُظهر
    للمستخدم خلاف ما تفعله الخدمة، وهو أخطر من نقص بصري.
    """
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "لا يُرفض" in html
    assert period_service.is_open(timezone.localdate()) is True, "الخدمة تسمح، فالشاشة تقول تسمح"


def test_the_empty_state_explains_and_points(client, manager) -> None:
    """§8 — تشرح وتشير: زرٌّ حقيقي لا اعتذار."""
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert 'class="btn2 primary empty-act" type="button" data-opens="per-new"' in html


def test_the_form_opens_on_the_month_after_the_last_period(manager, september) -> None:
    """أكثر الأفعال تكراراً «افتح الشهر القادم» لا يُحسب في رأس القارئ."""
    suggested = fiscal_period_service.suggested_range()

    assert suggested["starts_on"] == date(2026, 10, 1)
    assert suggested["ends_on"] == date(2026, 10, 31)


def test_the_first_period_ever_opens_on_the_current_month(manager) -> None:
    today = timezone.localdate()
    assert fiscal_period_service.suggested_range()["starts_on"] == today.replace(day=1)


# ---------------------------------------------------------------------------
# الكتابة، والتقاطع الذي لا يمنعه المخطّط
# ---------------------------------------------------------------------------
def test_the_screen_opens_a_period_and_writes_an_audit_row(client, manager) -> None:
    client.force_login(manager)
    response = client.post(
        URL,
        {"action": "create", "starts_on": "2026-11-01", "ends_on": "2026-11-30"},
        HTTP_HOST="127.0.0.1",
        follow=True,
    )

    assert response.status_code == 200
    period = FinancialPeriod.objects.get(starts_on=date(2026, 11, 1))
    assert period.status == FinancialPeriodStatus.OPEN
    assert AuditEvent.objects.filter(
        entity_type=fiscal_period_service.ENTITY, action="CREATE", reference="2026-11-01"
    ).exists()



def test_an_overlapping_period_is_refused_beside_its_field(client, manager, september) -> None:
    """
    المخطّط يمنع بدايتين في يومٍ واحد ولا يمنع فترةً تحوي أخرى. و``period_for``
    يحسم التاريخ بـ ``.first()`` تحت ``ordering = ["-starts_on"]`` — فتاريخٌ في
    فترتين، إحداهما مقفلة والأخرى مفتوحة، يجعل السند نفسه مشروعاً أو ممنوعاً
    بترتيب صفوفٍ لم يخترْه أحد.
    """
    client.force_login(manager)
    response = client.post(
        URL,
        {"action": "create", "starts_on": "2026-09-15", "ends_on": "2026-10-15"},
        HTTP_HOST="127.0.0.1",
    )

    assert response.status_code == 200
    assert "تتقاطع مع الفترة" in response.content.decode()
    assert FinancialPeriod.objects.count() == 1


def test_a_period_inside_another_is_refused_too(manager, september) -> None:
    """التقاطع لا يعني «بدايةً مشتركة»: الاحتواء تقاطعٌ كامل."""
    with pytest.raises(ValidationError) as refused:
        fiscal_period_service.create_period(
            actor=manager, data={"starts_on": date(2026, 9, 10), "ends_on": date(2026, 9, 20)}
        )

    assert "starts_on" in refused.value.message_dict


def test_the_dates_are_refused_beside_the_field_not_as_a_500(client, manager) -> None:
    """``core_period_ends_after_starts`` يقول الشيء نفسه بصفحة خطأ."""
    client.force_login(manager)
    response = client.post(
        URL,
        {"action": "create", "starts_on": "2026-11-30", "ends_on": "2026-11-01"},
        HTTP_HOST="127.0.0.1",
    )

    assert response.status_code == 200
    assert "لا تكون قبل بدايتها" in response.content.decode()
    assert not FinancialPeriod.objects.exists()


def test_a_gap_between_two_periods_is_announced(manager, september) -> None:
    """يومٌ لا تغطّيه فترة هو يومٌ لا يبلغه إقفال أبداً، وهو غير مرئيّ في قائمة تواريخ."""
    FinancialPeriod.objects.create(starts_on=date(2026, 10, 5), ends_on=date(2026, 10, 31))

    rows = {row["starts_on"]: row for row in fiscal_period_service.period_rows(actor=manager)}

    assert rows[date(2026, 10, 5)]["gap_before"] is True
    assert rows[date(2026, 10, 5)]["gap_from"] == date(2026, 10, 1)
    assert rows[date(2026, 10, 5)]["gap_to"] == date(2026, 10, 4)
    assert rows[date(2026, 9, 1)]["gap_before"] is False, "الأولى لا فجوة قبلها"


def test_two_adjacent_periods_report_no_gap(manager, september) -> None:
    FinancialPeriod.objects.create(starts_on=date(2026, 10, 1), ends_on=date(2026, 10, 31))

    rows = fiscal_period_service.period_rows(actor=manager)
    assert not [row for row in rows if row["gap_before"]]


# ---------------------------------------------------------------------------
# الإقفال — وما يرفضه فعلاً بعده
# ---------------------------------------------------------------------------
def test_closing_records_who_closed_it_and_when(client, manager, september) -> None:
    """
    ``core_period_closed_requires_closer`` لا يقبل غير ذلك: شهرٌ مقفل بلا اسمٍ
    عليه شهرٌ لم يوقّعه أحد.
    """
    client.force_login(manager)
    client.post(
        URL, {"action": "close", "period": str(september.pk)}, HTTP_HOST="127.0.0.1", follow=True
    )

    september.refresh_from_db()
    assert september.status == FinancialPeriodStatus.CLOSED
    assert september.closed_by_id == manager.pk
    assert september.closed_at is not None
    assert AuditEvent.objects.filter(
        entity_type=fiscal_period_service.ENTITY, action="UPDATE", reference="2026-09-01"
    ).exists()


def test_the_close_actually_refuses_a_movement_dated_inside_it(manager, september) -> None:
    """
    السلوك لا الادّعاء: الشاشة تكتب ما يقرأه الحارس، فالإقفال من هنا يُغيّر ما
    يقبله ``require_open`` هناك (D-23).
    """
    inside = date(2026, 9, 15)
    assert period_service.is_open(inside) is True

    fiscal_period_service.close_period(actor=manager, period=september)

    assert period_service.is_open(inside) is False
    with pytest.raises(period_service.ClosedPeriodError):
        period_service.period_for(inside, what_ar="سند قبض")


def test_a_date_outside_the_closed_period_stays_open(manager, september) -> None:
    fiscal_period_service.close_period(actor=manager, period=september)
    assert period_service.is_open(date(2026, 10, 1)) is True


def test_closing_a_closed_period_is_refused_rather_than_written_twice(manager, september) -> None:
    fiscal_period_service.close_period(actor=manager, period=september)

    with pytest.raises(ValidationError, match="مقفلة سلفاً"):
        fiscal_period_service.close_period(actor=manager, period=september)


def test_no_screen_offers_to_reopen_a_closed_period(client, manager, september) -> None:
    """
    وهذا غيابٌ مقصود: إعادة الفتح تُلغي ما تحرسه الشاشة، ومن يملكها قرارٌ
    إداري لا زرّ. فإن ظهر الزرّ يوماً فليكن بقرارٍ لا بالسهو.
    """
    fiscal_period_service.close_period(actor=manager, period=september)

    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert 'value="reopen"' not in html
    assert f'data-opens="per-close-{september.pk}"' not in html
    assert "لا تُعاد فتحها" in html
    assert not hasattr(fiscal_period_service, "reopen_period")


def test_the_confirmation_names_what_it_is_about_to_refuse(client, manager, september) -> None:
    """«بعد الإقفال» متأخّرٌ عن السؤال، فالتأكيد يسمّي ما سيُرفَض قبل أن يحدث."""
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert f'id="per-close-{september.pk}"' in html
    for refused in fiscal_period_service.REFUSES:
        assert refused in html


def test_the_row_says_when_it_covers_today(client, manager) -> None:
    """«وين أنا الآن» تُقرأ من الصفّ لا تُحسب من تاريخين."""
    today = timezone.localdate()
    FinancialPeriod.objects.create(
        starts_on=today - timedelta(days=3), ends_on=today + timedelta(days=3)
    )

    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "تغطّي اليوم" in html
    assert fiscal_period_service.today_is_covered(actor=manager) is True


def test_the_screen_is_in_the_menu_for_whoever_may_see_it(seeded_settings) -> None:
    from apps.people.nav import nav_for

    urls = {
        item["url"]
        for group in nav_for(_actor(Role.FINANCE_OFFICER, "per.nav"))
        for item in group["items"]
    }
    assert reverse("people:financial-periods") in urls


def test_the_template_added_no_dead_class_and_no_dependency() -> None:
    import re
    from pathlib import Path

    source = Path("templates/people/financial_periods.html").read_text(encoding="utf-8")
    css = Path("static/css/app.css").read_text(encoding="utf-8")

    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    assert not [c for c in used if f".{c}" not in css]
