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
