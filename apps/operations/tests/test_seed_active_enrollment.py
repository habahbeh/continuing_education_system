"""
``seed_active_enrollment_demo`` — the state three of the screen's actions need.

The register held seven enrolments and none of them ACTIVE, so «تسجيل التخرج»
و«تسجيل انسحاب» و«تسجيل فصل» — all three offered on a live row and on no other
— could not be shown at all. The command walks the whole road to that state
rather than writing it onto a row.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.operations.models import Enrollment, EnrollmentStatus
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "seed-active-1234"


@pytest.fixture
def four_hands(seeded_settings):
    """The matrix separates these duties; the seed does not route around it."""
    for role, name in (
        (Role.REGISTRATION_OFFICER, "seed.reg"),
        (Role.CASHIER, "seed.cash"),
        (Role.CENTER_MANAGER, "seed.mgr"),
    ):
        User.objects.create_user(username=name, password=PASSWORD, role=role)


@pytest.fixture
def demo_cohort(make_cohort, approve_cohort, cash_method):
    """The command names one cohort; here it is, approved as the rules require."""
    cohort = make_cohort("SC-NET", code="CO-QA-1")
    approve_cohort(cohort, course_number="M-SEED-1")
    return cohort


def _run() -> str:
    out = StringIO()
    call_command("seed_active_enrollment_demo", stdout=out)
    return out.getvalue()


def test_it_refuses_when_a_hand_the_workflow_needs_is_missing(seeded_settings) -> None:
    """
    Separation of duties is not something a seed may quietly route around: with
    no cashier there is nobody who may issue the receipt, and inventing a
    super-administrator to do it would seed a row no workflow could produce.
    """
    User.objects.create_user(username="only.reg", password=PASSWORD, role=Role.REGISTRATION_OFFICER)

    with pytest.raises(CommandError, match="الصندوق"):
        _run()


def test_it_refuses_rather_than_inventing_the_cohort_it_names(four_hands) -> None:
    """A seed that creates what it cannot find is a seed nobody can predict."""
    with pytest.raises(CommandError, match="لا دفعة بالرمز"):
        _run()


def test_it_walks_the_whole_road_to_a_live_enrolment(
    four_hands, demo_cohort, make_participant
) -> None:
    """
    Charges raised, receipt issued, voucher recorded, approval given — in that
    order, because BR-018 refuses the approval without the voucher and the
    services would have said so.
    """
    make_participant(index=91)

    output = _run()
    enrollment = Enrollment.objects.get(code="EN-DEMO-ACTIVE")

    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert enrollment.voucher_received, "BR-018 — approved without a recorded voucher"
    assert enrollment.approved_by is not None
    assert enrollment.cohort_id == demo_cohort.pk
    for step in ("١)", "٢)", "٣)", "٤)"):
        assert step in output, f"the road did not report step {step}"


def test_the_approval_is_not_the_hand_that_enrolled(
    four_hands, demo_cohort, make_participant
) -> None:
    """A row approved by whoever created it is a row no screen would produce."""
    make_participant(index=92)

    _run()
    enrollment = Enrollment.objects.get(code="EN-DEMO-ACTIVE")

    assert enrollment.approved_by.role == Role.CENTER_MANAGER


def test_running_it_twice_changes_nothing(four_hands, demo_cohort, make_participant) -> None:
    """A seed that doubles its own rows cannot be run on a live demo."""
    make_participant(index=93)
    make_participant(index=94)

    _run()
    count = Enrollment.objects.count()
    second = _run()

    assert Enrollment.objects.count() == count
    assert "لم يُمسّ" in second


def test_the_live_row_is_what_unlocks_the_three_exits(
    client, four_hands, demo_cohort, make_participant
) -> None:
    """
    The whole reason for the command: the register offers the three exits on an
    ACTIVE row and on no other.
    """
    from django.urls import reverse

    make_participant(index=95)
    _run()

    manager = User.objects.get(username="seed.mgr")
    client.force_login(manager)
    html = client.get(reverse("operations:enrollments")).content.decode()

    assert "إجراءات الحالة" in html
    for action in ("complete", "withdraw", "dismiss"):
        assert f'data-opens="{action}-EN-DEMO-ACTIVE"' in html
