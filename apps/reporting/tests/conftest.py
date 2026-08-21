"""
Fixtures for the report tests — reuses the settlement shapes.

Reports read what the other apps wrote, so the fixtures that build a partner,
an agreement, a cohort and a paying participant are exactly the ones a report
test needs. Two roles are added because reports are where per-report access
(BR-099) is proved, and that needs actors the settlement tests never sign in.
"""

from __future__ import annotations

import pytest

from apps.settlements.tests.conftest import *  # noqa: F403

PASSWORD = "probe-password-1234"


@pytest.fixture
def registrar(seeded_settings):
    """§9.4 and §9.5 are theirs; the other five are not."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="reg.reports", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )
