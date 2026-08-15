"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import date

import pytest


@pytest.fixture
def seeded_settings(db: None) -> None:
    """Run the seed_settings command so setting-dependent code has values."""
    from django.core.management import call_command

    call_command("seed_settings", verbosity=0)


@pytest.fixture
def today() -> date:
    return date(2026, 8, 14)


@pytest.fixture
def user(db: None):  # type: ignore[no-untyped-def]
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="r.faouri",
        password="test-password-1234",
        full_name_ar="أ. رامي الفاعوري",
        role=Role.CENTER_MANAGER,
        department="مركز التعليم المستمر",
    )


@pytest.fixture
def active_semester(db: None):  # type: ignore[no-untyped-def]
    """
    The active semester participant numbering reads (BR-001).

    First semester of 2026/2027, so a university participant is numbered
    2026 1 xxxx and a centre participant 2026 5 xxxx (BR-002).
    """
    from apps.core.models import Semester

    return Semester.objects.create(
        code="2026-1",
        name_ar="الفصل الأول 2026/2027",
        type_code=1,
        academic_year="2026/2027",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )


@pytest.fixture
def participant_data():  # type: ignore[no-untyped-def]
    """A minimally valid application form payload."""
    from apps.people.models import IdDocumentType, ParticipantCategory

    return {
        "category": ParticipantCategory.UNIVERSITY,
        "name_ar": "سارة أحمد محمود العبادي",
        "name_en": "Sara Ahmad Mahmoud Al-Abbadi",
        "id_document_type": IdDocumentType.NATIONAL_ID,
        "id_document_number": "9962012345",
        "nationality": "أردنية",
        "gender": "FEMALE",
        "date_of_birth": date(1996, 4, 12),
        "qualification": "BACHELOR",
        "city": "AMMAN",
        "phone": "0791234567",
        "email": "sara@example.com",
        "registered_on": date(2026, 9, 10),
        "no_refund_pledge_accepted": True,
    }
