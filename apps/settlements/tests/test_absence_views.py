"""
The absence register and the obligation it causes (تناغم بند 13, §5.6).

The service tests already hold the arithmetic. What the screen has to prove is
different: that the numbers it shows are the SETTINGS' numbers rather than
literals typed into a template, that the replacement alert appears at five
absences and not four, and that the penalty is raised from the register
instead of being typed into the obligations form.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
LECTURE = Decimal("50.000")
PASSWORD = "probe-password-1234"
ABSENCES = "settlements:absences"
OBLIGATIONS = "settlements:obligations"
TRAINER = "سعيد المدرّس"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def priced_cohort(cohort_with_agreement):
    cohort_with_agreement.lecture_cost = LECTURE
    cohort_with_agreement.save(update_fields=["lecture_cost"])
    return cohort_with_agreement


def _absent(client, cohort, day: int, **extra):
    payload = {
        "action": "record",
        "cohort_code": cohort.code,
        "trainer_name": TRAINER,
        "occurred_on": date(2026, 10, day).isoformat(),
        "is_waived": "",
        "waiver_approval_ref": "",
        "waiver_approval_date": "",
        "note_ar": "",
    }
    payload.update(extra)
    return client.post(reverse(ABSENCES), payload, follow=True)


# ---------------------------------------------------------------------------
# The register
# ---------------------------------------------------------------------------
def test_the_screen_shows_the_settings_numbers_not_literals(
    signed_in, manager, priced_cohort
) -> None:
    """
    Three times, past four — both are effective-dated settings.

    If the centre renegotiates the clause the screen must follow the setting,
    which it cannot do if the template says «×3».
    """
    page = signed_in(manager).get(reverse(ABSENCES))

    assert page.context["multiplier"] == 3
    assert page.context["replace_limit"] == 4


def test_an_absence_is_recorded_and_counts(signed_in, manager, priced_cohort) -> None:
    response = _absent(signed_in(manager), priced_cohort, day=1)

    rows = response.context["absences"]
    assert len(rows) == 1
    assert rows[0]["trainer_name"] == TRAINER
    assert rows[0]["counts_toward_penalty"] is True


def test_a_waiver_typed_without_its_approval_is_refused_on_the_page(
    signed_in, manager, priced_cohort
) -> None:
    """
    The exception exists only in writing.

    A message rather than a 403 — the manager may waive, they have simply not
    produced the approval that makes the waiver real.
    """
    response = _absent(signed_in(manager), priced_cohort, day=1, is_waived="on")

    assert response.status_code == 200
    assert response.context["absences"] == []
    assert any("موافقة" in str(m) for m in response.context["messages"])


def test_a_waived_absence_shows_its_approval_and_stops_counting(
    signed_in, manager, priced_cohort
) -> None:
    response = _absent(
        signed_in(manager),
        priced_cohort,
        day=2,
        is_waived="on",
        waiver_approval_ref="APP-2026-3",
        waiver_approval_date=date(2026, 10, 2).isoformat(),
    )

    row = response.context["absences"][0]
    assert row["is_waived"] is True
    assert row["waiver_approval_ref"] == "APP-2026-3"
    assert row["counts_toward_penalty"] is False


# ---------------------------------------------------------------------------
# The replacement threshold, read off the page
# ---------------------------------------------------------------------------
def test_four_absences_raise_no_alert_and_five_do(signed_in, manager, priced_cohort) -> None:
    """
    The demo says «بعد 4» and the signed agreement says «لأكثر من أربع».

    One lecture of difference, and the screen follows the agreement.
    """
    client = signed_in(manager)
    for day in range(1, 5):
        _absent(client, priced_cohort, day=day)

    assert client.get(reverse(ABSENCES)).context["alerts"] == []

    response = _absent(client, priced_cohort, day=5)
    alerts = response.context["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["absence_count"] == 5
    assert alerts[0]["limit"] == 4


# ---------------------------------------------------------------------------
# The penalty — raised here, never typed into the obligations form
# ---------------------------------------------------------------------------
def test_the_penalty_is_raised_from_the_register(signed_in, manager, priced_cohort) -> None:
    client = signed_in(manager)
    for day in (1, 2, 3):
        _absent(client, priced_cohort, day=day)

    response = client.post(
        reverse(ABSENCES),
        {
            "action": "penalise",
            "target": f"{priced_cohort.code}|{TRAINER}",
            "code": "OBL-ABS-UI-1",
            "occurred_on": TERM_START.isoformat(),
        },
        follow=True,
    )

    assert any("450" in str(m) for m in response.context["messages"])
    obligations = client.get(reverse(OBLIGATIONS)).context["obligations"]
    penalty = next(o for o in obligations if o["code"] == "OBL-ABS-UI-1")
    assert penalty["amount"] == Decimal("450.000")


def test_a_penalised_trainer_is_no_longer_offered_for_penalty(
    signed_in, manager, priced_cohort
) -> None:
    """
    Nothing is left to penalise, so the choice disappears.

    Offering it would invite a second penalty over the same three lectures —
    the service refuses that, and the screen should not have asked.
    """
    client = signed_in(manager)
    _absent(client, priced_cohort, day=1)
    client.post(
        reverse(ABSENCES),
        {
            "action": "penalise",
            "target": f"{priced_cohort.code}|{TRAINER}",
            "code": "OBL-ABS-UI-2",
            "occurred_on": TERM_START.isoformat(),
        },
        follow=True,
    )

    page = client.get(reverse(ABSENCES))
    targets = [value for value, _label in page.context["penalty_form"].fields["target"].choices]
    assert targets == []


def test_the_obligations_form_never_offers_the_absence_penalty(signed_in, manager) -> None:
    """
    BR-057 — it is a formula with inputs.

    Typing an amount would produce a penalty that cannot show its working, so
    the type is missing from the form as well as refused by the service.
    """
    page = signed_in(manager).get(reverse(OBLIGATIONS))

    offered = {value for value, _label in page.context["form"].fields["obligation_type"].choices}
    assert "TRAINER_ABSENCE_PENALTY" not in offered
    assert offered == {"TRAINER_SALARIES", "FIELD_TRAINING_EXPENSE", "WITHDRAWAL_RETURN"}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def test_the_cashier_cannot_open_the_absence_register(signed_in, cashier) -> None:
    assert signed_in(cashier).get(reverse(ABSENCES)).status_code == 403


def test_the_audit_account_reads_the_register_and_writes_nothing(
    signed_in, seeded_settings, priced_cohort
) -> None:
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.abs", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    client = signed_in(auditor)

    page = client.get(reverse(ABSENCES))
    assert page.status_code == 200
    assert page.context["can_create"] is False
    assert _absent(client, priced_cohort, day=1).status_code == 403
