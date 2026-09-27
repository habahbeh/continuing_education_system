"""
The course-category screen (Sprint 8L) — the reference data that had no screen.

``CourseCategory`` is what BR-061 compares when a transfer is judged, and
``Program`` carries a database constraint refusing a short course without one.
Yet the only way to create one was the Django admin, which writes to the table
without passing the service — so the row arrived with nobody's name on it. A
client installing this system from nothing could not define their own course
fields without a developer.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CourseCategory
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "category-probe-1234"
URL = "catalog:course-categories"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3 leaves the programme rows empty for these three.
        (Role.CASHIER, 403),
        (Role.FINANCE_MANAGER, 403),
        (Role.SYSTEM_ADMINISTRATOR, 403),
    ],
)
def test_the_screen_wears_the_programme_rows_permission(
    client: Client, db: Any, role: str, expected: int
) -> None:
    """
    No new matrix cell: a course category is the catalogue reference data of
    whoever defines programmes, so it borrows that row rather than inventing
    an authority §3 never granted.
    """
    client.force_login(_user(role, f"cat.reach.{role.lower()}"))

    assert client.get(reverse(URL)).status_code == expected


def test_the_manager_creates_a_category_and_the_row_is_audited(
    client: Client, db: Any
) -> None:
    """
    The whole reason for the screen. The admin wrote the row straight to the
    table; this writes it through the service, which records who and when.
    """
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "cat.mgr")
    client.force_login(manager)

    response = client.post(
        reverse(URL),
        {"action": "create", "code": "cat-it", "name_ar": "تكنولوجيا المعلومات", "is_active": "on"},
        follow=True,
    )

    assert response.status_code == 200
    category = CourseCategory.objects.get()
    # Lower case in, upper case stored: the code is what every report prints.
    assert category.code == "CAT-IT"
    assert category.name_ar == "تكنولوجيا المعلومات"
    assert category.is_active
    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="catalog.CourseCategory", actor=manager
    ).exists(), "a category created without an audit line is a category nobody owns"


def test_a_reader_without_create_is_offered_no_form(client: Client, db: Any) -> None:
    """§3.4 — never draw a control whose use would be refused (BR-085)."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "cat.aud"))

    body = client.get(reverse(URL)).content.decode("utf-8")

    assert 'name="name_ar"' not in body
    assert "للاطلاع فقط" in body


def test_a_reader_without_create_cannot_post_one_either(client: Client, db: Any) -> None:
    """The form's absence is a courtesy; the refusal is the service's."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "cat.aud.post"))

    response = client.post(
        reverse(URL), {"action": "create", "code": "CAT-X", "name_ar": "محاولة"}
    )

    assert response.status_code == 403
    assert not CourseCategory.objects.exists()


def test_a_duplicate_code_is_answered_with_a_sentence_not_a_500(
    client: Client, db: Any
) -> None:
    """The code is unique and typed by hand, so the second use is expected."""
    client.force_login(_user(Role.CENTER_MANAGER, "cat.dup"))
    payload = {"action": "create", "code": "CAT-IT", "name_ar": "تكنولوجيا المعلومات"}
    client.post(reverse(URL), payload)

    response = client.post(reverse(URL), payload, follow=True)

    assert response.status_code == 200
    assert CourseCategory.objects.count() == 1


def test_the_list_says_how_many_programmes_stand_on_each_category(
    client: Client, make_program: Any = None
) -> None:
    """
    The count is why this is a screen and not a list: standing a category down
    matters differently when forty programmes depend on it.
    """
    from apps.catalog.models import Program, ProgramType

    manager = _user(Role.CENTER_MANAGER, "cat.count")
    category = CourseCategory.objects.create(code="CAT-IT", name_ar="تكنولوجيا المعلومات")
    Program.objects.create(
        code="SC-NET",
        program_type=ProgramType.SHORT_COURSE,
        name_ar="هندسة الشبكات",
        training_hours=60,
        course_category=category,
        consumables_per_student=0,
    )
    client.force_login(manager)

    body = client.get(reverse(URL)).content.decode("utf-8")

    assert "CAT-IT" in body
    assert "البرامج" in body, "the header names the column"


def test_a_category_is_stood_down_never_deleted(client: Client, db: Any) -> None:
    """
    A decision taken last term may rest on this field, so it is withdrawn from
    use rather than removed from the record.
    """
    manager = _user(Role.CENTER_MANAGER, "cat.off")
    CourseCategory.objects.create(code="CAT-IT", name_ar="تكنولوجيا المعلومات")
    client.force_login(manager)

    client.post(reverse(URL), {"action": "toggle", "code": "CAT-IT"}, follow=True)

    category = CourseCategory.objects.get(code="CAT-IT")
    assert category.is_active is False, "standing down is what the button does"
    assert CourseCategory.objects.count() == 1, "and it never deletes"


def test_the_screen_carries_no_script_and_no_inline_style(client: Client, db: Any) -> None:
    """§7 — shared behaviour lives in ``static/js/ui.js``."""
    client.force_login(_user(Role.CENTER_MANAGER, "cat.clean"))

    body = client.get(reverse(URL)).content.decode("utf-8")
    main = body.split('id="main"', 1)[1].split("</main>", 1)[0]

    assert "<script" not in main
    assert "style=" not in main
