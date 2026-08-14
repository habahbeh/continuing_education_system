"""
Separation of duties — D-18 and D-30.

The entities these guard arrive in Sprints 4 to 8. The check is built and
tested now so four later sprints inherit one implementation instead of writing
four subtly different ones — and so that when ClearanceStep lands in Sprint 7,
D-30 is a wiring job rather than a design question.

T-274, T-275 and T-276 are the ClearanceStep versions of these and are
reported DEFERRED, not passed: the entity does not exist yet, and marking them
green here would be a false claim about a control that is not running.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.people.models import Role, User
from apps.people.permissions.separation import (
    assert_different_actor,
    assert_second_certifier_differs,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def officer() -> User:
    return User.objects.create_user(
        username="fin.officer", password="probe-password-1234", role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def manager() -> User:
    return User.objects.create_user(
        username="fin.manager", password="probe-password-1234", role=Role.FINANCE_MANAGER
    )


def test_d18_same_actor_on_both_sides_is_refused(officer: User) -> None:
    with pytest.raises(ValidationError) as exc:
        assert_different_actor(officer, officer, rule_id="D-18", message="لا تعتمد ما أنشأت")
    assert exc.value.code == "D-18"


def test_d18_different_actors_pass(officer: User, manager: User) -> None:
    assert_different_actor(officer, manager, rule_id="D-18", message="لا تعتمد ما أنشأت")


def test_d18_accepts_primary_keys_as_well_as_instances(officer: User) -> None:
    """Callers compare `x_id` fields far more often than loaded objects."""
    with pytest.raises(ValidationError):
        assert_different_actor(officer.pk, officer.pk, rule_id="D-18", message="x")


def test_d18_is_silent_when_one_side_is_missing(officer: User) -> None:
    """An unapproved record is not a violation — it is simply not approved yet."""
    assert_different_actor(officer, None, rule_id="D-18", message="x")
    assert_different_actor(None, officer, rule_id="D-18", message="x")


def test_d30_second_certifier_must_differ_from_the_first(officer: User) -> None:
    """
    Q-14 / BR-074 — two signatures from one person is a single control in a costume.
    """
    with pytest.raises(ValidationError) as exc:
        assert_second_certifier_differs(officer, officer)
    assert exc.value.code == "D-30"


def test_d30_finance_officer_then_finance_manager_is_the_documented_path(
    officer: User, manager: User
) -> None:
    assert_second_certifier_differs(officer, manager)
