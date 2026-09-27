"""
Payment methods, enterable from a screen at last (DATA_MODEL §8.7).

Found by walking a fresh install as a trainee would. ``PaymentMethod`` is a
table rather than fixed choices precisely so the client can add bank transfer
or card without a migration — and that intention had no screen behind it: the
only road to a row was the Django admin, which writes past the audit trail.

Worse on a brand-new database: there was no method at all, ``payment_service``
requires one, so the till could not take a single dinar until somebody ran
``seed_payment_methods`` from a terminal.

The permission is the SETTINGS row, not the till's own — see the service's
module docstring. The cashier writes receipts; he does not invent the methods
they are written in.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.cashbox.models import PaymentMethod
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "method-probe-1234"
INDEX = "cashbox:payment-methods"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def _payload(**over: Any) -> dict[str, Any]:
    payload = {"action": "create", "code": "cash", "name_ar": "نقداً", "is_active": "on"}
    payload.update(over)
    return payload


def test_the_manager_defines_the_first_method_from_the_screen(client: Client) -> None:
    """The whole gap: a row the admin alone could write, written from a screen."""
    manager = _user(Role.CENTER_MANAGER, "pm.mgr")
    client.force_login(manager)

    response = client.post(reverse(INDEX), _payload(), follow=True)

    assert response.status_code == 200
    method = PaymentMethod.objects.get()
    assert method.code == "CASH", "the code is upper-cased — CASH is what every report prints"
    assert method.name_ar == "نقداً"
    assert method.is_active


def test_the_write_is_audited_which_the_admin_road_never_was(client: Client) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "pm.audit")
    client.force_login(manager)

    client.post(reverse(INDEX), _payload(), follow=True)

    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="cashbox.PaymentMethod", actor=manager
    ).exists()


def test_a_duplicate_code_is_refused_in_words_not_with_a_500(client: Client) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pm.dup"))
    client.post(reverse(INDEX), _payload(), follow=True)

    response = client.post(reverse(INDEX), _payload(name_ar="نقداً أيضاً"), follow=True)

    assert response.status_code == 200
    assert PaymentMethod.objects.count() == 1


def test_a_method_is_stood_down_and_never_deleted(client: Client) -> None:
    """
    A receipt whose method row had been deleted is a receipt nobody can say how
    it was paid, so the only exit offered is standing it down.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pm.off"))
    client.post(reverse(INDEX), _payload(), follow=True)

    client.post(reverse(INDEX), {"action": "toggle", "code": "CASH"}, follow=True)

    method = PaymentMethod.objects.get()
    assert not method.is_active, "the method was not stood down"
    assert PaymentMethod.objects.count() == 1, "a row was deleted where one should be disabled"


def test_standing_one_down_can_be_undone(client: Client) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pm.back"))
    client.post(reverse(INDEX), _payload(), follow=True)
    client.post(reverse(INDEX), {"action": "toggle", "code": "CASH"}, follow=True)

    client.post(reverse(INDEX), {"action": "toggle", "code": "CASH"}, follow=True)

    assert PaymentMethod.objects.get().is_active


def test_the_screen_says_the_till_is_stopped_while_no_method_is_active(
    client: Client,
) -> None:
    """§8 — an empty state that explains AND points, not one that merely lists."""
    client.force_login(_user(Role.CENTER_MANAGER, "pm.empty"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert "لا طريقة دفع نشطة" in body
    assert "لا سند قبض يُكتب بلا طريقة" in body


def test_the_count_of_receipts_is_shown_because_it_decides_what_may_change(
    client: Client, cash_method: Any, make_enrollment: Any, seeded_settings: Any
) -> None:
    """
    The column is the reason this is a screen rather than a list: a method the
    books already rest on is not one to reword lightly, and a method nothing
    points at may be renamed freely. The reader cannot tell which without the
    count, so they are given it.
    """
    from decimal import Decimal

    from django.utils import timezone

    from apps.cashbox.services import payment_service

    cashier = _user(Role.CASHIER, "pm.count.cash")
    enrollment, _quote = make_enrollment()
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=cash_method,
        received_on=timezone.localdate(),
        external_receipt_ref="PM-COUNT-1",
        breakdown_text_ar="دفعة للاختبار",
    )

    manager = _user(Role.CENTER_MANAGER, "pm.count")
    client.force_login(manager)
    body = client.get(reverse(INDEX)).content.decode("utf-8")

    from apps.cashbox.services import method_service

    rows = method_service.method_rows(actor=manager)
    counted = {row["code"]: row["receipts"] for row in rows}

    assert counted["CASH"] == 1, f"the receipt was not counted against its method: {counted}"
    assert "نقداً" in body


# -- who may, and who may not -------------------------------------------------
def test_the_cashier_does_not_invent_the_method_he_collects_by(client: Client) -> None:
    """
    Row 16 gives the CASHIER ``C`` over RECEIPTS, which is his job — not over
    the vocabulary receipts are written in. Hanging this screen off the till row
    would have let whoever takes the cash name the method it was taken by.
    """
    client.force_login(_user(Role.CASHIER, "pm.cash"))

    assert client.get(reverse(INDEX)).status_code == 403
    assert client.post(reverse(INDEX), _payload()).status_code == 403
    assert not PaymentMethod.objects.exists()


def test_the_finance_officer_reads_and_writes_nothing(client: Client) -> None:
    """§3.7/35 — FIN holds ``V`` on settings: he verifies, he does not configure."""
    client.force_login(_user(Role.FINANCE_OFFICER, "pm.fin"))

    body = client.get(reverse(INDEX))
    assert body.status_code == 200
    assert "إضافة طريقة" not in body.content.decode("utf-8"), "§3.4 — a refused act is not drawn"

    assert client.post(reverse(INDEX), _payload()).status_code == 403
    assert not PaymentMethod.objects.exists()


def test_the_audit_account_writes_nothing_ever(client: Client) -> None:
    """D-02."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pm.aud"))

    assert client.get(reverse(INDEX)).status_code == 200
    assert client.post(reverse(INDEX), _payload()).status_code == 403
    assert not PaymentMethod.objects.exists()


def test_the_screen_does_not_refuse_a_reader_before_they_type(client: Client) -> None:
    """
    A form bound with an empty ``request.POST`` reports every required field
    missing on a plain GET. The bug class this sprint has met four times.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pm.shut"))

    response = client.get(reverse(INDEX))

    assert not response.context["form"].errors
    assert "err-summary" not in response.content.decode("utf-8")


def test_the_screen_is_reachable_from_the_navigation(client: Client) -> None:
    """A screen nobody can find is a screen nobody uses."""
    client.force_login(_user(Role.CENTER_MANAGER, "pm.nav"))

    body = client.get(reverse("cashbox:payments")).content.decode("utf-8")

    assert reverse(INDEX) in body, "the sidebar does not offer the methods screen"
