"""
Every Sprint 8C-2 route, against every role.

Two different things are proved here, and both need the whole grid rather than
a sample. The first is that nothing crashes: a template that renders for the
centre manager and raises for the auditor is a bug nobody meets until the
auditor opens it, and templates only fail when actually rendered.

The second is that each ROUTE honours the matrix. The matrix is the source, but
the routes are separate code, and a view that forgot its gate is exactly the
failure this grid catches — report 5, which returns an empty shell when given
no enrollment code, reached no service at all and so was reached by roles
holding no report whatsoever until this test was written.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.people.constants import Action, Screen
from apps.people.permissions import matrix
from apps.reporting.services import report_service

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"

#: The three screens Sprint 8C-2 added or made writable, with the matrix row
#: that governs each. Absences share OBLIGATIONS on purpose: an absence exists
#: only because it causes an obligation.
SCREENS = (
    ("expenses:expenses", Screen.EXPENSES),
    ("settlements:obligations", Screen.OBLIGATIONS),
    ("settlements:absences", Screen.OBLIGATIONS),
)


@pytest.fixture
def roles(seeded_settings):
    from apps.people.models import Role, User

    return {
        role: User.objects.create_user(
            username=f"grid.{role.lower()}", password=PASSWORD, role=role
        )
        for role in Role.values
    }


def _expected(role: str, screen: str) -> int:
    return 200 if Action.VIEW in matrix.allowed_actions(role, screen) else 403


def test_the_screens_answer_exactly_as_the_matrix_says(client, roles) -> None:
    observed, expected = {}, {}
    for role, user in roles.items():
        client.force_login(user)
        for name, screen in SCREENS:
            key = (role, name)
            observed[key] = client.get(reverse(name)).status_code
            expected[key] = _expected(role, screen)
        client.logout()

    assert observed == expected


def test_each_of_the_seven_reports_answers_as_its_own_row_says(client, roles) -> None:
    """
    BR-099 — granting the screen is not granting the seven.

    The finance manager holds the screen and one report; the cashier holds
    neither. Both are cells in this grid rather than assertions of their own.
    """
    observed, expected = {}, {}
    for role, user in roles.items():
        client.force_login(user)
        reachable = matrix.allowed_reports(role)
        for number in report_service.REPORT_TITLES:
            key = (role, number)
            observed[key] = client.get(reverse("reporting:report", args=[number])).status_code
            expected[key] = 200 if number in reachable else 403
        client.logout()

    assert observed == expected


def test_the_export_routes_gate_no_more_loosely_than_the_pages(client, roles) -> None:
    """
    A download route reading more loosely than the page it exports would be a
    way around the matrix wearing a spreadsheet icon.

    A report with no row list is a 404 rather than an invented table — report
    2 is a calculation, and exporting a summary as data invites someone to
    pivot on it.
    """
    observed, expected = {}, {}
    for role, user in roles.items():
        client.force_login(user)
        reachable = matrix.allowed_reports(role)
        for number in report_service.REPORT_TITLES:
            key = (role, number)
            observed[key] = client.get(
                reverse("reporting:report-export", args=[number])
            ).status_code
            if number not in reachable:
                expected[key] = 403
            else:
                expected[key] = 200 if number in report_service.EXPORTABLE else 404
        client.logout()

    assert observed == expected


def test_the_report_menu_is_reachable_by_whoever_holds_the_screen(client, roles) -> None:
    observed, expected = {}, {}
    for role, user in roles.items():
        client.force_login(user)
        observed[role] = client.get(reverse("reporting:reports")).status_code
        expected[role] = _expected(role, Screen.REPORTS)
        client.logout()

    assert observed == expected
