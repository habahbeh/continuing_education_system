"""
The quick-enrolment dialog on the participants screen.

A shorter road to the same door: the dialog posts to ``enrollment-quick``,
which runs the enrolments screen's own gate and service. So the proofs are
the same three as for that screen — the dialog is drawn only for a reader
who may enrol, an unapproved cohort is neither offered nor accepted, and a
successful post creates the enrolment with its charges and returns the
reader to the list they came from.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


def _post(client, participant, cohort, **extra):
    return client.post(
        reverse("operations:enrollment-quick"),
        {
            "participant_number": participant.participant_number,
            "cohort_code": cohort.code,
            "enrolled_on": "2026-09-20",
            "next": reverse("people:participants") + "?q=x",
            **extra,
        },
    )


def test_the_dialog_groups_approved_cohorts_by_programme_type(
    signed_in, registrar, make_cohort, approve_cohort, make_participant
):
    approved = make_cohort("SC-NET", code="CO-QK-OK")
    approve_cohort(approved, course_number="M-QK-OK")
    make_cohort("SC-NET", code="CO-QK-NO")
    make_participant(index=61)

    page = signed_in(registrar).get(reverse("people:participants"))
    body = page.content.decode("utf-8")

    groups = page.context["enroll_groups"]
    offered = {c["code"] for g in groups for c in g["cohorts"]}
    assert offered == {"CO-QK-OK"}, "only the ministry-approved cohort is offered"
    assert all(g["type"] and g["label"] for g in groups)
    assert 'id="enroll-modal"' in body
    assert 'data-opens="enroll-modal"' in body
    assert f'action="{reverse("operations:enrollment-quick")}"' in body


def test_the_dialog_is_not_drawn_for_a_reader_who_cannot_enrol(
    signed_in, cashier, finance, make_cohort, approve_cohort, make_participant
):
    approve_cohort(make_cohort("SC-NET", code="CO-QK-RO"), course_number="M-QK-RO")
    make_participant(index=62)

    for reader in (cashier, finance):
        page = signed_in(reader).get(reverse("people:participants"))
        assert page.status_code == 200
        assert page.context["enroll_groups"] == []
        assert 'id="enroll-modal"' not in page.content.decode("utf-8")


def test_a_post_from_the_dialog_enrols_and_returns_to_the_list(
    signed_in, registrar, make_cohort, approve_cohort, make_participant
):
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-QK-GO")
    approve_cohort(cohort, course_number="M-QK-GO")
    participant = make_participant(index=63)

    response = _post(signed_in(registrar), participant, cohort)

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("people:participants") + "?q=x"
    enrollment = Enrollment.objects.get(cohort=cohort, participant=participant)
    assert enrollment.charge_lines.exists(), "the charges ride along, as on the enrolments screen"


def test_an_unapproved_cohort_is_refused_by_the_dialog_route(
    signed_in, registrar, make_cohort, make_participant
):
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-QK-BAD")
    participant = make_participant(index=64)

    response = _post(signed_in(registrar), participant, cohort)

    assert response.status_code == 302
    assert not Enrollment.objects.filter(cohort=cohort).exists()


def test_the_dialog_route_holds_the_role_boundary(
    signed_in, cashier, make_cohort, approve_cohort, make_participant
):
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-QK-DENY")
    approve_cohort(cohort, course_number="M-QK-DENY")
    participant = make_participant(index=65)

    response = _post(signed_in(cashier), participant, cohort)

    assert response.status_code == 403
    assert not Enrollment.objects.filter(cohort=cohort).exists()


def test_an_off_site_next_is_ignored(
    signed_in, registrar, make_cohort, approve_cohort, make_participant
):
    cohort = make_cohort("SC-NET", code="CO-QK-NEXT")
    approve_cohort(cohort, course_number="M-QK-NEXT")
    participant = make_participant(index=66)

    response = _post(signed_in(registrar), participant, cohort, next="https://evil.example/")

    assert response.headers["Location"] == reverse("people:participants")
