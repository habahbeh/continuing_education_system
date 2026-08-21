"""
The menu is derived from the matrix, not written beside it (Sprint 8B).

The risk this module exists for is drift: a sidebar hand-written from someone's
memory of §8 that slowly stops matching what ``policy.require`` actually
allows. Then a role sees a link it cannot open, or — worse — stops seeing a
screen it is supposed to use and nobody notices because the permission still
technically works.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.people import nav
from apps.people.constants import Action
from apps.people.permissions.matrix import allowed_actions

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


def _user(username: str, role: str):
    from apps.people.models import User

    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def test_every_entry_matches_the_matrix(seeded_settings) -> None:
    """No link a role may not open, for any role, on any entry."""
    from apps.people.models import Role

    for role in Role.values:
        user = _user(f"nav.{role.lower()}", role)
        for group in nav.nav_for(user):
            for item in group["items"]:
                assert Action.VIEW in allowed_actions(role, item["screen"]), (
                    f"{role} was offered {item['screen']} without VIEW"
                )


def test_the_cashier_gets_the_till_and_nothing_else(seeded_settings) -> None:
    """§8 — «الصندوق: القبض فقط + إجمالي القبض اليومي»."""
    cashier = _user("nav.cash", "CASHIER")
    screens = {i["screen"] for g in nav.nav_for(cashier) for i in g["items"]}

    assert "payments" in screens
    assert "payment-new" in screens
    assert "closing" in screens
    assert "discounts" not in screens
    assert "refunds" not in screens
    assert "users" not in screens


def test_the_centre_manager_never_sees_the_till(seeded_settings) -> None:
    """D-01 · BR-081 — the manager approves and recommends but takes no cash."""
    manager = _user("nav.mgr", "CENTER_MANAGER")
    screens = {i["screen"] for g in nav.nav_for(manager) for i in g["items"]}

    assert "payment-new" not in screens
    assert "discounts" in screens
    assert "cohorts" in screens


def test_the_audit_account_sees_everything_the_menu_offers(seeded_settings) -> None:
    """§8 — «قراءة فقط لجميع الحركات»: full menu, no actions behind it."""
    auditor = _user("nav.aud", "AUDIT_ACCOUNT")
    screens = {i["screen"] for g in nav.nav_for(auditor) for i in g["items"]}

    assert {"payments", "closing", "discounts", "refunds", "extra-fees"} <= screens


def test_an_anonymous_visitor_gets_no_menu(seeded_settings) -> None:
    """A login page with a sidebar would advertise the system to a stranger."""
    from django.contrib.auth.models import AnonymousUser

    assert nav.nav_for(AnonymousUser()) == []
    assert nav.nav_for(None) == []


def test_every_route_in_the_tree_resolves(seeded_settings) -> None:
    """
    A dead link is worse than a missing one.

    ``nav_for`` skips a route that will not reverse, which keeps the page up —
    but a permanently unreachable entry should fail the build, not hide.
    """
    for group in nav.NAV:
        for item in group.items:
            assert reverse(item.route)


def test_an_empty_group_is_dropped_with_its_heading(seeded_settings) -> None:
    """A heading over no links reads as a broken screen, not a withheld one."""
    cashier = _user("nav.cash2", "CASHIER")
    titles = [str(g["title"]) for g in nav.nav_for(cashier)]

    assert "النظام" not in titles  # users and audit are both closed to them
    assert all(g["items"] for g in nav.nav_for(cashier))
