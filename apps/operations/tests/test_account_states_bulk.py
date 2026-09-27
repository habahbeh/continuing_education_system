"""
``get_account_states`` is ``get_account_state`` summed once for a set.

The list screens, the dashboard and the participant file read balances for
many enrolments at a time; per-row reads cost ten queries each. The bulk read
must say exactly what the single read says, for every field, in every state
the fixtures can put an enrolment into — and must not grow with the set.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.billing.services.account_service import get_account_state, get_account_states
from apps.operations.models import Enrollment
from apps.operations.services import enrollment_service

pytestmark = pytest.mark.django_db


def test_the_bulk_read_matches_the_single_read_field_by_field(
    make_cohort, approve_cohort, make_enrollment, charge_and_pay, registrar, manager, finance
) -> None:
    plain = make_cohort("SC-NET", code="CO-BULK-A")
    approve_cohort(plain, course_number="M-BULK-A")
    deposit = make_cohort("SC-ENG-GEN", code="CO-BULK-B", level=1)
    approve_cohort(deposit, course_number="M-BULK-B")

    settled = make_enrollment(plain, index=21)
    charge_and_pay(settled)  # 270 / 270
    partial = make_enrollment(plain, index=22)
    charge_and_pay(partial, amount="100.000")  # 170 owed
    enrollment_service.record_voucher(actor=registrar, enrollment=partial)
    enrollment_service.approve_enrollment(actor=manager, enrollment=partial)
    unpaid = make_enrollment(plain, index=23)
    charge_and_pay(unpaid, amount=None)  # charged, nothing paid
    overpaid = make_enrollment(plain, index=24)
    charge_and_pay(overpaid, amount="320.000")  # 50 credit, unallocated
    with_deposit = make_enrollment(deposit, index=25)
    charge_and_pay(with_deposit, amount=None)
    empty = make_enrollment(plain, index=26)  # no charge lines at all

    enrollments = list(
        Enrollment.objects.filter(
            pk__in=[e.pk for e in (settled, partial, unpaid, overpaid, with_deposit, empty)]
        )
    )
    bulk = get_account_states(enrollments)

    assert set(bulk) == {e.pk for e in enrollments}
    for enrollment in enrollments:
        single = get_account_state(enrollment)
        for field in fields(single):
            assert getattr(bulk[enrollment.pk], field.name) == getattr(single, field.name), (
                f"{enrollment.code}.{field.name}: bulk={getattr(bulk[enrollment.pk], field.name)} "
                f"single={getattr(single, field.name)}"
            )
    # The states are not all alike — the comparison meant something.
    assert (
        bulk[partial.pk].participant_owes
        and bulk[overpaid.pk].centre_owes
        and bulk[settled.pk].is_settled
    )


def test_the_bulk_read_does_not_grow_with_the_set(
    make_cohort, approve_cohort, make_enrollment, charge_and_pay
) -> None:
    cohort = make_cohort("SC-NET", code="CO-BULK-C")
    approve_cohort(cohort, course_number="M-BULK-C")
    one = [make_enrollment(cohort, index=31)]
    charge_and_pay(one[0], amount="50.000")
    with CaptureQueriesContext(connection) as small:
        get_account_states(one)

    many = one + [make_enrollment(cohort, index=i) for i in range(32, 38)]
    for e in many[1:]:
        charge_and_pay(e, amount="20.000")
    with CaptureQueriesContext(connection) as large:
        get_account_states(many)

    assert len(large.captured_queries) == len(small.captured_queries) <= 12
    assert get_account_states([]) == {}
