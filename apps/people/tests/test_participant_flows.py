"""
End-to-end participant journeys through the screens.

The service tests prove the rules; these prove a clerk can actually complete
the task, and that the failure paths reach them as a readable Arabic message
rather than a stack trace.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditEvent, Semester
from apps.people.models import Participant, Role, User
from apps.people.services import participant_service

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def registrar(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="reg.flow",
        password=PASSWORD,
        role=Role.REGISTRATION_OFFICER,
        full_name_ar="موظف التسجيل",
    )


@pytest.fixture
def form_payload(participant_data: dict) -> dict:
    payload = dict(participant_data)
    payload["date_of_birth"] = "1996-04-12"
    payload["registered_on"] = "2026-09-10"
    payload["no_refund_pledge_accepted"] = "on"
    return payload


def test_registrar_creates_a_participant_end_to_end(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    client.force_login(registrar)

    assert client.get(reverse("people:participant-new")).status_code == 200

    response = client.post(reverse("people:participant-new"), form_payload, follow=True)
    assert response.status_code == 200

    participant = Participant.objects.get()
    assert participant.participant_number == "202610001"
    assert participant.no_refund_pledge_at is not None
    assert AuditEvent.objects.filter(action="CREATE", reference="202610001").exists()


def test_the_form_refuses_to_save_without_the_pledge(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    """BR-003 — the message lands on the field, and nothing is written."""
    client.force_login(registrar)
    form_payload.pop("no_refund_pledge_accepted")

    response = client.post(reverse("people:participant-new"), form_payload)
    assert response.status_code == 200
    assert "يجب الإقرار بالتعهّد" in response.content.decode()
    assert not Participant.objects.exists()


def test_the_form_refuses_an_exemption_without_an_approval_reference(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    client.force_login(registrar)
    form_payload.update({"category": "EMPLOYEE", "is_exempt": "on"})

    response = client.post(reverse("people:participant-new"), form_payload)
    assert "الإعفاء يتطلب رقم موافقة" in response.content.decode()
    assert not Participant.objects.exists()


def test_a_duplicate_identity_document_warns_before_it_saves(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    """
    BR-005 — the first attempt is stopped and shown the existing record; the
    second, carrying a reason, goes through and the reason is recorded.
    """
    client.force_login(registrar)
    client.post(reverse("people:participant-new"), form_payload)

    second = dict(form_payload, name_ar="شخص آخر بنفس وثيقة الهوية")
    warned = client.post(reverse("people:participant-new"), second)
    assert "202610001" in warned.content.decode()
    assert Participant.objects.count() == 1

    second["duplicate_override_reason"] = "توأمان — مراجعة الأحوال المدنية"
    client.post(reverse("people:participant-new"), second)
    assert Participant.objects.count() == 2
    assert AuditEvent.objects.filter(summary_ar__contains="تجاوز تحذير").exists()


def test_t287_creating_without_an_active_semester_shows_a_readable_error(
    client: Client, registrar: User, form_payload: dict
) -> None:
    """
    BR-001 at the screen.

    No semester means no number, and the clerk is told so — rather than the
    system inventing a year that will be printed on a certificate.
    """
    assert not Semester.objects.filter(is_active=True).exists()
    client.force_login(registrar)

    response = client.post(reverse("people:participant-new"), form_payload, follow=True)
    body = response.content.decode()
    assert "لا يوجد فصل دراسي نشط" in body
    assert not Participant.objects.exists()


def test_editing_a_participant_records_the_change(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    client.force_login(registrar)
    client.post(reverse("people:participant-new"), form_payload)
    number = Participant.objects.get().participant_number

    assert client.get(reverse("people:participant-edit", args=[number])).status_code == 200

    edited = dict(form_payload, phone="0788888888")
    client.post(reverse("people:participant-edit", args=[number]), edited)

    assert Participant.objects.get().phone == "0788888888"
    event = AuditEvent.objects.filter(action="UPDATE").first()
    assert event is not None and event.changes is not None
    assert event.changes["phone"]["to"] == "0788888888"


def test_the_participant_number_cannot_be_edited(
    client: Client, registrar: User, active_semester: Semester, form_payload: dict
) -> None:
    """BR-001 — permanent means the form does not even offer the field."""
    client.force_login(registrar)
    client.post(reverse("people:participant-new"), form_payload)
    number = Participant.objects.get().participant_number

    tampered = dict(form_payload, participant_number="202619999", phone="0788888888")
    client.post(reverse("people:participant-edit", args=[number]), tampered)

    assert Participant.objects.get().participant_number == number


def test_search_and_filter_narrow_the_list(
    client: Client, registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_service.create_participant(actor=registrar, data=participant_data)
    participant_service.create_participant(
        actor=registrar,
        data=dict(
            participant_data,
            name_ar="خالد سليمان عوض الرشيد",
            id_document_number="9881112223",
            category="CENTER",
        ),
    )
    client.force_login(registrar)

    body = client.get(reverse("people:participants"), {"q": "خالد"}).content.decode()
    assert "خالد سليمان عوض الرشيد" in body
    assert "سارة أحمد محمود العبادي" not in body

    by_category = client.get(
        reverse("people:participants"), {"category": "CENTER"}
    ).content.decode()
    assert "خالد" in by_category


def test_missing_participant_returns_404(
    client: Client, registrar: User, active_semester: Semester
) -> None:
    client.force_login(registrar)
    assert client.get(reverse("people:participant-detail", args=["999999999"])).status_code == 404


def test_audit_screen_filters_by_action(
    client: Client, registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_service.create_participant(actor=registrar, data=participant_data)
    manager = User.objects.create_user(
        username="mgr.filter", password=PASSWORD, role=Role.CENTER_MANAGER
    )
    client.force_login(manager)

    body = client.get(reverse("people:audit"), {"action": "CREATE"}).content.decode()
    assert "202610001" in body

    filtered = client.get(reverse("people:audit"), {"action": "LOGOUT"}).content.decode()
    assert "202610001" not in filtered


def test_locked_and_expired_pages_render(client: Client) -> None:
    assert client.get(reverse("people:locked")).status_code == 403
    assert client.get(reverse("people:session-expired")).status_code == 440
