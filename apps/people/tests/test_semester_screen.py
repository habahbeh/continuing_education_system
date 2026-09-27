"""
شاشة الفصول الدراسية — أوّل خطوة في النظام، وكانت بلا شاشة.

الاختبارات هنا لا في ``apps/core/tests`` لسببٍ معماريّ: هذه تسأل عن شاشةٍ ودورٍ
ومصفوفة، و``apps.core`` بنيةٌ تحتية لا تعرف تطبيقاً تجارياً (ADR-008 · A-03).
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.models import AuditEvent, Semester
from apps.people.models import Role, User
from apps.people.services import semester_service

pytestmark = pytest.mark.django_db

PASSWORD = "semester-probe-1234"
URL = "/semesters/"


def _actor(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def manager(seeded_settings):  # type: ignore[no-untyped-def]
    return _actor(Role.CENTER_MANAGER, "sem.mgr")


@pytest.fixture
def payload() -> dict[str, str]:
    return {
        "action": "create",
        "code": "2027-1",
        "name_ar": "الفصل الأول 2027/2028",
        "type_code": "1",
        "academic_year": "2027/2028",
        "starts_on": "2027-09-01",
        "ends_on": "2028-01-15",
    }


# ---------------------------------------------------------------------------
# الصلاحية — لا خانة جديدة في المصفوفة، والشاشة تقرأ صفّ الإعدادات كما هو
# ---------------------------------------------------------------------------
def test_the_screen_hangs_off_the_settings_row_and_invents_no_cell() -> None:
    """صلاحية الإعدادات لا صلاحية جديدة: تعريف الفصل ضبطٌ للنظام لا فعلٌ تشغيلي."""
    from apps.people.constants import Screen

    assert semester_service.SCREEN == Screen.SETTINGS


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
    """§3.7/35 حرفياً: ``V E P`` للمدير · ``V`` للمالي · ``V P`` للتدقيق · وما عداهم لا شيء."""
    client.force_login(_actor(role, f"sem.{role.lower()}"))
    assert client.get(URL, HTTP_HOST="127.0.0.1").status_code == expected


def test_a_reader_who_may_not_edit_is_drawn_no_control_he_cannot_use(
    client, seeded_settings, active_semester
) -> None:
    """§3.4 من قواعد التحسين: زرٌّ استعماله مرفوض لا يُرسَم أصلاً (BR-085)."""
    client.force_login(_actor(Role.FINANCE_OFFICER, "sem.fin"))
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert 'data-opens="sem-new"' not in html
    assert 'data-opens="sem-edit-2026-1"' not in html
    assert "للاطلاع فقط" in html


def test_the_finance_officer_cannot_write_through_the_post_either(
    client, seeded_settings, payload
) -> None:
    """الإخفاء مجاملة والرفض هو الحماية: الطريق مغلق لا مخفيّ."""
    client.force_login(_actor(Role.FINANCE_OFFICER, "sem.fin2"))
    response = client.post(URL, payload, HTTP_HOST="127.0.0.1")

    assert response.status_code == 403
    assert not Semester.objects.filter(code="2027-1").exists()


# ---------------------------------------------------------------------------
# ما تقوله الشاشة قبل جدولها — الحالة، لأنها سؤال القارئ الوحيد
# ---------------------------------------------------------------------------
def test_with_no_active_semester_the_screen_says_no_participant_can_be_registered(
    client, manager
) -> None:
    """
    وهذا هو العطل الذي بُنيت الشاشة له: BR-001 يبني كل رقم من الفصل الحالي،
    فبلا فصلٍ حالي يُرفض كل طلب التحاق — وكانت الشاشة التي تُصلح ذلك غائبة.
    """
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "لا فصل دراسي حالي" in html
    assert "ولا يُسجَّل مشارك واحد" in html
    assert "BR-001" in html


def test_the_empty_state_explains_and_points(client, manager) -> None:
    """§8 — الحالة الفارغة تشرح وتشير: فعلٌ حقيقي لا نصٌّ يعتذر."""
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "لا فصل دراسي معرَّف بعد" in html
    assert 'class="btn2 primary empty-act" type="button" data-opens="sem-new"' in html


def test_the_current_semester_is_announced_with_the_numbers_it_mints(
    client, manager, active_semester
) -> None:
    """«وين وصلت» تُقرأ لا تُحسب: البادئة مكتوبة، لا يستنتجها القارئ من حقلين."""
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert "الفصل الحالي:" in html
    assert "20261" in html, "بادئة الأرقام غير معلنة"
    assert "20265" in html, "بادئة طلاب المركز غير معلنة (BR-002)"


# ---------------------------------------------------------------------------
# الكتابة — والفعل الذي كان الأمر السطري وحده يملكه
# ---------------------------------------------------------------------------
def test_the_screen_writes_a_semester_and_an_audit_row(client, manager, payload) -> None:
    """لوحة الإدارة تكتب في الجدول بلا صفّ تدقيق؛ هذه لا."""
    client.force_login(manager)
    response = client.post(URL, payload, HTTP_HOST="127.0.0.1", follow=True)

    assert response.status_code == 200
    semester = Semester.objects.get(code="2027-1")
    assert semester.is_active is False, "لم يُطلب جعله الحالي"
    assert AuditEvent.objects.filter(
        entity_type=semester_service.ENTITY, action="CREATE", reference="2027-1"
    ).exists()


def test_creating_it_as_current_stands_the_previous_one_down(
    client, manager, payload, active_semester
) -> None:
    """
    القيد ``core_semester_single_active`` لا يسمح بغير ذلك، والترتيب مهم:
    إنزال القائم قبل رفع الجديد، وإلّا تصادم المفتاح الفريد.
    """
    client.force_login(manager)
    client.post(URL, {**payload, "is_active": "on"}, HTTP_HOST="127.0.0.1", follow=True)

    active_semester.refresh_from_db()
    assert Semester.objects.get(code="2027-1").is_active is True
    assert active_semester.is_active is False
    assert Semester.objects.filter(is_active=True).count() == 1


def test_naming_a_semester_current_is_its_own_act(client, manager, payload) -> None:
    """الفعل الذي كان بلا طريق: لا رقم يُولَّد حتى يُعيَّن فصلٌ حالي."""
    client.force_login(manager)
    client.post(URL, payload, HTTP_HOST="127.0.0.1", follow=True)

    client.post(
        URL, {"action": "activate", "code": "2027-1"}, HTTP_HOST="127.0.0.1", follow=True
    )

    assert Semester.objects.get(code="2027-1").is_active is True
    assert AuditEvent.objects.filter(
        entity_type=semester_service.ENTITY, action="UPDATE", reference="2027-1"
    ).exists()


def test_the_dates_are_refused_beside_the_field_not_as_a_500(client, manager, payload) -> None:
    """
    ``core_semester_ends_after_starts`` يقول الشيء نفسه ويقوله ``IntegrityError``
    — أي صفحة خطأ محلّ جملةٍ بجانب الحقل.
    """
    client.force_login(manager)
    response = client.post(
        URL, {**payload, "ends_on": "2027-08-01"}, HTTP_HOST="127.0.0.1"
    )

    assert response.status_code == 200
    assert "بعد بدايته" in response.content.decode()
    assert not Semester.objects.filter(code="2027-1").exists()


def test_a_malformed_academic_year_is_refused_before_it_becomes_a_number(
    client, manager, payload
) -> None:
    """
    ``academic_year_digits`` يرفع استثناءً لحظةَ توليد رقمٍ دائم — وهي أسوأ
    لحظةٍ لاكتشاف خطأ كتابةٍ وقع قبل أشهر (T-287).
    """
    client.force_login(manager)
    response = client.post(
        URL, {**payload, "academic_year": "٢٠٢٧"}, HTTP_HOST="127.0.0.1"
    )

    assert response.status_code == 200
    assert "الرقم الجامعي يُبنى منها" in response.content.decode()
    assert not Semester.objects.filter(code="2027-1").exists()


# ---------------------------------------------------------------------------
# ما يتجمّد، ولماذا في الخدمة لا في النموذج
# ---------------------------------------------------------------------------
def test_the_year_and_the_type_freeze_once_a_number_carries_them(
    manager, active_semester, participant_data
) -> None:
    """
    BR-001: الرقم دائم ولا يُصحَّح بأثر رجعي. فنقل الفصل إلى سنةٍ أخرى يترك
    أرقاماً مطبوعة على شهادات تصف فصلاً لم يبقَ يقول ما تقوله.
    """
    from apps.people.services import participant_service

    participant_service.create_participant(actor=manager, data=dict(participant_data))
    assert semester_service.numbers_issued(active_semester) == 1

    with pytest.raises(ValidationError) as refused:
        semester_service.update_semester(
            actor=manager,
            semester=active_semester,
            data={
                "name_ar": active_semester.name_ar,
                "type_code": 2,
                "academic_year": active_semester.academic_year,
                "starts_on": active_semester.starts_on,
                "ends_on": active_semester.ends_on,
            },
        )

    assert "type_code" in refused.value.message_dict
    active_semester.refresh_from_db()
    assert active_semester.type_code == 1


def test_the_dates_stay_correctable_on_a_semester_that_has_issued_numbers(
    manager, active_semester, participant_data
) -> None:
    """المتجمّد حقلان لا الصفّ كلّه: تاريخٌ كُتب خطأً يُصحَّح."""
    from apps.people.services import participant_service

    participant_service.create_participant(actor=manager, data=dict(participant_data))

    semester_service.update_semester(
        actor=manager,
        semester=active_semester,
        data={
            "name_ar": active_semester.name_ar,
            "type_code": active_semester.type_code,
            "academic_year": active_semester.academic_year,
            "starts_on": active_semester.starts_on,
            "ends_on": date(2027, 2, 1),
        },
    )

    active_semester.refresh_from_db()
    assert active_semester.ends_on == date(2027, 2, 1)


def test_the_screen_draws_no_delete_because_the_database_would_not_refuse_one(
    client, manager, active_semester
) -> None:
    """
    سنة الفصل ورمزه أرقامٌ داخل رقم المشارك، لا مفتاح أجنبيّ — فالحذف لا تردّه
    قاعدة البيانات بل يترك الرقم يصف فصلاً لا وجود له.
    """
    client.force_login(manager)
    html = client.get(URL, HTTP_HOST="127.0.0.1").content.decode()

    assert 'value="delete"' not in html
    assert "الفصل لا يُحذف" in html


# ---------------------------------------------------------------------------
# العدّ — وأمانته
# ---------------------------------------------------------------------------
def test_centre_participants_are_counted_apart_because_their_digit_is_the_year_s(
    manager, active_semester, participant_data
) -> None:
    """
    BR-002: رمز طالب المركز 5 في كل فصول السنة. فجمعُه في عدّ الفصل يُبلغ عن
    المشاركين أنفسهم ثلاث مرّات — مرّةً تحت كل فصل من فصول السنة.
    """
    from apps.people.models import ParticipantCategory
    from apps.people.services import participant_service

    participant_service.create_participant(actor=manager, data=dict(participant_data))
    participant_service.create_participant(
        actor=manager,
        data={
            **participant_data,
            "category": ParticipantCategory.CENTER,
            "name_ar": "خالد سعيد المركزي",
            "id_document_number": "9962099999",
            "email": "khaled@example.com",
        },
    )

    row = next(r for r in semester_service.semester_rows(actor=manager) if r["code"] == "2026-1")
    assert row["numbers"] == 1, "رقمٌ جامعيّ واحد صدر بنوع هذا الفصل"
    assert row["center_numbers"] == 1, "وطالب المركز يُعدّ للسنة لا للفصل"


def test_overlapping_semesters_are_announced_rather_than_tolerated(manager, active_semester) -> None:
    """لا قيد في المخطّط يمنع فصلين يغطّيان يوماً، ولا جواب لسؤال «أيّ فصل هذا اليوم»."""
    semester_service.create_semester(
        actor=manager,
        data={
            "code": "2026-1b",
            "name_ar": "فصل متقاطع",
            "type_code": 2,
            "academic_year": "2026/2027",
            "starts_on": date(2026, 12, 1),
            "ends_on": date(2027, 3, 1),
        },
    )

    rows = {row["code"]: row for row in semester_service.semester_rows(actor=manager)}
    assert rows["2026-1"]["overlaps"] == ["2026-1b"]
    assert rows["2026-1b"]["overlaps"] == ["2026-1"]


def test_a_malformed_year_takes_down_the_row_and_not_the_screen(client, manager) -> None:
    """
    ``academic_year_digits`` يرفع استثناءً — وهو الصواب حين يُولَّد رقم دائم
    والخطأ في قائمة: صفٌّ واحد سيّئ كان سيُسقط الشاشة كلّها.
    """
    Semester.objects.create(
        code="BAD-1",
        name_ar="فصل بسنةٍ فاسدة",
        type_code=1,
        academic_year="سنة",
        starts_on=date(2025, 9, 1),
        ends_on=date(2026, 1, 1),
    )

    client.force_login(manager)
    response = client.get(URL, HTTP_HOST="127.0.0.1")

    assert response.status_code == 200
    assert "غير صالحة" in response.content.decode()


def test_the_screen_is_in_the_menu_for_whoever_may_see_it(seeded_settings) -> None:
    """مدخلٌ غائب من القائمة شاشةٌ لا يجدها أحد — وهي أوّل خطوة في النظام."""
    from apps.people.nav import nav_for

    urls = {
        item["url"]
        for group in nav_for(_actor(Role.CENTER_MANAGER, "sem.nav"))
        for item in group["items"]
    }
    assert reverse("people:semesters") in urls
    assert reverse("people:financial-periods") in urls


def test_the_template_added_no_dead_class_and_no_dependency() -> None:
    """كل صنفٍ تُرسم به الشاشة معرَّف سلفاً؛ ولا سكربت ولا نمطٌ سطريّ ولا CDN."""
    import re
    from pathlib import Path

    source = Path("templates/people/semesters.html").read_text(encoding="utf-8")
    css = Path("static/css/app.css").read_text(encoding="utf-8")

    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    assert not [c for c in used if f".{c}" not in css]
