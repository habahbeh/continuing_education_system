"""
The knowledge-field screen (Sprint 8L) — the ministry's classification.

Unlike a course category, this row governs nothing: no rule reads it, no
price depends on it. It is copied onto the ministry submission because §9 of
the brief lists it among what the ministry is sent. That is exactly why it
had no screen for so long — nothing broke without one, so the gap stayed
invisible until a client had to install the system from an empty database.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import KnowledgeField
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "field-probe-1234"
URL = "catalog:knowledge-fields"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        (Role.CASHIER, 403),
        (Role.FINANCE_MANAGER, 403),
        (Role.SYSTEM_ADMINISTRATOR, 403),
    ],
)
def test_the_screen_wears_the_programme_rows_permission(
    client: Client, db: Any, role: str, expected: int
) -> None:
    """Catalogue reference data borrows the programme row; no new matrix cell."""
    client.force_login(_user(role, f"kf.reach.{role.lower()}"))

    assert client.get(reverse(URL)).status_code == expected


def test_the_manager_creates_a_field_and_the_row_is_audited(client: Client, db: Any) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "kf.mgr")
    client.force_login(manager)

    client.post(
        reverse(URL),
        {"action": "create", "code": "kf-it", "name_ar": "تكنولوجيا المعلومات", "is_active": "on"},
        follow=True,
    )

    field = KnowledgeField.objects.get()
    assert field.code == "KF-IT", "the code is stored as it will be printed"
    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="catalog.KnowledgeField", actor=manager
    ).exists()


def test_a_reader_without_create_is_offered_no_form(client: Client, db: Any) -> None:
    """§3.4 — never draw a control whose use would be refused (BR-085)."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "kf.aud"))

    body = client.get(reverse(URL)).content.decode("utf-8")

    assert 'name="name_ar"' not in body
    assert "للاطلاع فقط" in body


def test_a_reader_without_create_cannot_post_one_either(client: Client, db: Any) -> None:
    client.force_login(_user(Role.AUDIT_ACCOUNT, "kf.aud.post"))

    response = client.post(reverse(URL), {"action": "create", "code": "KF-X", "name_ar": "محاولة"})

    assert response.status_code == 403
    assert not KnowledgeField.objects.exists()


def test_a_duplicate_code_is_answered_with_a_sentence_not_a_500(
    client: Client, db: Any
) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "kf.dup"))
    payload = {"action": "create", "code": "KF-IT", "name_ar": "تكنولوجيا المعلومات"}
    client.post(reverse(URL), payload)

    response = client.post(reverse(URL), payload, follow=True)

    assert response.status_code == 200
    assert KnowledgeField.objects.count() == 1


def test_a_field_is_stood_down_never_deleted(client: Client, db: Any) -> None:
    """A submission filed last term still names it."""
    client.force_login(_user(Role.CENTER_MANAGER, "kf.off"))
    KnowledgeField.objects.create(code="KF-IT", name_ar="تكنولوجيا المعلومات")

    client.post(reverse(URL), {"action": "toggle", "code": "KF-IT"}, follow=True)

    assert KnowledgeField.objects.get(code="KF-IT").is_active is False
    assert KnowledgeField.objects.count() == 1


def test_the_screen_says_it_is_the_ministry_wording_not_ours(client: Client, db: Any) -> None:
    """
    The one thing a reader must not get wrong here: this text leaves the
    building. A category is the centre's own word; this is the ministry's.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "kf.words"))

    body = client.get(reverse(URL)).content.decode("utf-8")

    assert "الوزارة" in body
    assert "BR-061" not in body, "the transfer rule belongs to the category screen, not this one"


def test_the_screen_carries_no_script_and_no_inline_style(client: Client, db: Any) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "kf.clean"))

    body = client.get(reverse(URL)).content.decode("utf-8")
    main = body.split('id="main"', 1)[1].split("</main>", 1)[0]

    assert "<script" not in main
    assert "style=" not in main
