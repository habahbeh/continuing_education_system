"""
Catalogue screens — access matches the matrix, on the URL not the link.

PERMISSIONS.md §3.3 gives the finance officer read-only access to programmes
and prices (Δ-03: he cannot verify a fee he cannot see) and shuts the cashier
out entirely. These assert that at the URL, because a screen reachable by
typing its address is reachable.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import PriceList, PriceListStatus
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions.matrix import allowed_actions

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"

SCREEN_ROUTES = [
    (Screen.PROGRAMS, "catalog:programs"),
    (Screen.SHORT_COURSES, "catalog:short-courses"),
    (Screen.ONLINE_COURSES, "catalog:online-courses"),
    (Screen.PRICELISTS, "catalog:pricelists"),
]

BUSINESS_ROLES = [
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
]


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def catalog(active_semester, seeded_settings):
    from django.core.management import call_command

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


@pytest.mark.parametrize(("screen", "route"), SCREEN_ROUTES)
@pytest.mark.parametrize("role", BUSINESS_ROLES)
def test_screen_access_matches_the_matrix(
    client: Client, catalog: PriceList, screen: str, route: str, role: str
) -> None:
    client.force_login(_user(role, f"probe.{screen}.{role.lower()}"[:150]))
    response = client.get(reverse(route))

    expected = Action.VIEW in allowed_actions(role, screen)
    assert response.status_code == (200 if expected else 403), (
        f"{role} on {screen}: matrix says {'allow' if expected else 'deny'}"
    )


def test_cashier_is_shut_out_of_every_catalogue_screen(client: Client, catalog: PriceList) -> None:
    """BR-083 — the cashier sees the till, not the catalogue."""
    client.force_login(_user(Role.CASHIER, "cash.catalog"))
    for _screen, route in SCREEN_ROUTES:
        assert client.get(reverse(route)).status_code == 403


def test_finance_officer_reads_prices(client: Client, catalog: PriceList) -> None:
    """Δ-03 — read-only, because verifying a collected fee needs the list."""
    client.force_login(_user(Role.FINANCE_OFFICER, "fin.catalog"))
    response = client.get(reverse("catalog:pricelists"))
    assert response.status_code == 200
    assert not response.context["can_record_approval"]


def test_manager_can_record_an_approval_but_the_screen_offers_no_approve(
    client: Client, catalog: PriceList
) -> None:
    """
    Row 13 withholds A from everyone — the president approves outside (D-31).

    Recording that decision is an edit, so the manager sees the control and
    the matrix still says nobody holds an internal approval right.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.catalog.view"))
    response = client.get(reverse("catalog:pricelists"))
    assert response.status_code == 200
    assert response.context["can_record_approval"]
    assert Action.APPROVE not in allowed_actions(Role.CENTER_MANAGER, Screen.PRICELISTS)


def test_price_list_detail_shows_the_frozen_notice(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.pl.detail"))
    body = client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).content.decode()
    assert "مجمَّدة" in body
    assert not client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).context[
        "can_edit"
    ]


def test_no_fee_and_zero_fee_read_differently_on_screen(client: Client, catalog: PriceList) -> None:
    """The distinction has to survive all the way to the page."""
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.pl.fees"))
    body = client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).content.decode()
    assert "بلا رسوم" in body


def test_programme_detail_shows_the_subject_total(client: Client, catalog: PriceList) -> None:
    """BR-006 is visible before approval, not only when it blocks one."""
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.prog.detail"))
    body = client.get(reverse("catalog:program-detail", args=["DIP-ID"])).content.decode()
    assert "1650" in body, "the subject total should be shown for reconciliation"


def test_missing_programme_returns_404(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.404"))
    assert client.get(reverse("catalog:program-detail", args=["NOPE"])).status_code == 404


def test_anonymous_visitor_reaches_no_catalogue_screen(client: Client) -> None:
    for _screen, route in SCREEN_ROUTES:
        assert client.get(reverse(route)).status_code in (302, 403)
