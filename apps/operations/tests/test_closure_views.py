"""
Closing a participant's file on screen — clearance and certificates.

The heaviest screen in the system and the one that authorises a document, so
what is proved here is that neither loosens anything: the three steps stay in
order, the balance blocks in BOTH directions, two different people sign, each
step belongs to the department §6.4 assigns it to, and a certificate cannot be
reached without a completed clearance.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="fim.ui", password=PASSWORD, role=Role.FINANCE_MANAGER)


@pytest.fixture
def settled(make_cohort, approve_cohort, make_enrollment, charge_and_pay):
    """A finished enrolment paid to exactly zero — 20 registration + 250 tuition."""
    from apps.operations.services import enrollment_service

    def _make(index: int = 1, code: str = "CO-CLS", paid: str = "270.000"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount=paid)
        enrollment.status = "COMPLETED"
        enrollment.save(update_fields=["status"])
        assert enrollment_service  # imported for the fixture's intent
        return enrollment

    return _make


def _open(client, enrollment, code="CLR-UI-1"):
    return client.post(
        reverse("operations:clearances"),
        {
            "action": "open",
            "enrollment_code": enrollment.code,
            "case_type": "GRADUATION",
            "code": code,
            "opened_on": TERM_START.isoformat(),
        },
        follow=True,
    )


def _url(code: str) -> str:
    return reverse("operations:clearance-detail", args=[code])


def _custody(client, code):
    return client.post(
        _url(code), {"action": "custody", "items": "هوية المركز\nبطاقة المواصلات"}, follow=True
    )


# ---------------------------------------------------------------------------
# §6.4's department split — the decision this sprint implemented
# ---------------------------------------------------------------------------
def test_the_finance_officer_cannot_recover_the_centres_property(
    signed_in, manager, finance, settled
) -> None:
    """
    §6.4 — «المركز: استرجاع العُهد».

    The finance officer holds CLEARANCE.APPROVE, so the permission matrix
    alone would let them through. ``clearance_custody_role`` is what makes the
    document's own division of labour expressible, and the refusal is audited
    like any other.
    """
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    refused = _custody(signed_in(finance), "CLR-UI-1")
    body = refused.content.decode()

    assert "§6.4" in body or "CENTER_MANAGER" in body
    assert AuditEvent.objects.filter(
        action="DENIED_ATTEMPT", denial_rule="BR-072", reference="CLR-UI-1"
    ).exists()


def test_the_finance_officer_cannot_hand_over_the_certificate(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """§6.4 — «المركز: تسليم الشهادة». Step 3 is the centre's too."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")
    signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)
    signed_in(finance_manager).post(_url("CLR-UI-1"), {"action": "second-certify"}, follow=True)

    refused = signed_in(finance).post(
        _url("CLR-UI-1"),
        {"action": "handover", "participant_ack_name": "سالم أحمد العمري"},
        follow=True,
    )
    page = signed_in(manager).get(_url("CLR-UI-1"))

    assert "§6.4" in refused.content.decode() or "CENTER_MANAGER" in refused.content.decode()
    assert page.context["clearance"]["steps"][2]["is_done"] is False


def test_the_centre_manager_owns_both_of_its_steps(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """The positive case — the roles §6.4 names can do their own work."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")
    signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)
    signed_in(finance_manager).post(_url("CLR-UI-1"), {"action": "second-certify"}, follow=True)
    signed_in(manager).post(
        _url("CLR-UI-1"),
        {"action": "handover", "participant_ack_name": "سالم أحمد العمري"},
        follow=True,
    )

    page = signed_in(manager).get(_url("CLR-UI-1"))
    assert all(step["is_done"] for step in page.context["clearance"]["steps"])


# ---------------------------------------------------------------------------
# The financial step
# ---------------------------------------------------------------------------
def test_a_debt_blocks_the_financial_step(signed_in, manager, finance, settled) -> None:
    """BR-073 — «عليه ذمة»، and the screen says which direction."""
    enrollment = settled(paid="200.000")  # 270 due
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")

    refused = signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)
    assert "BR-073" in refused.content.decode()


def test_a_credit_balance_blocks_it_just_as_firmly(signed_in, manager, finance, settled) -> None:
    """
    C-09 · BR-071 — a credit stops a clearance, with a DIFFERENT message.

    🐞 The demo printed the same negative number for both directions, which is
    how CLR-002 and CLR-003 became indistinguishable.
    """
    enrollment = settled(paid="300.000")  # 30 over
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")

    page = signed_in(manager).get(_url("CLR-UI-1"))
    assert page.context["clearance"]["centre_owes"] is True
    assert page.context["clearance"]["credit_outstanding"] == Decimal("30.000")

    refused = signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)
    body = refused.content.decode()
    assert "BR-071" in body
    assert "رصيد دائن" in body


def test_the_second_signature_belongs_to_the_configured_role(
    signed_in, manager, finance, settled
) -> None:
    """BR-074 — and the refusal names the setting that decides it."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")
    signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)

    refused = signed_in(finance).post(_url("CLR-UI-1"), {"action": "second-certify"}, follow=True)
    assert "BR-074" in refused.content.decode()


def test_the_finance_manager_reaches_the_screen_for_its_one_job(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """
    §8 gives the finance manager a deliberately narrow scope.

    Clearance is their only real action, so the screen must be reachable and
    the second-signature button must be the one thing offered — no deposit
    settlement, which needs REFUNDS.CREATE they do not hold.
    """
    enrollment = settled()
    _open(signed_in(manager), enrollment)
    _custody(signed_in(manager), "CLR-UI-1")
    signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)

    page = signed_in(finance_manager).get(_url("CLR-UI-1"))
    assert page.status_code == 200
    assert page.context["can_second_certify"] is True
    assert page.context["can_settle_money"] is False
    assert page.context["can_custody"] is False


def test_steps_cannot_be_skipped(signed_in, manager, finance, settled) -> None:
    """BR-072 — the order is the control, not a convention."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    refused = signed_in(finance).post(_url("CLR-UI-1"), {"action": "certify"}, follow=True)
    assert "BR-072" in refused.content.decode()


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------
def _complete(signed_in, manager, finance, finance_manager, enrollment, code="CLR-UI-1"):
    _open(signed_in(manager), enrollment, code=code)
    _custody(signed_in(manager), code)
    signed_in(finance).post(_url(code), {"action": "certify"}, follow=True)
    signed_in(finance_manager).post(_url(code), {"action": "second-certify"}, follow=True)
    signed_in(manager).post(
        _url(code), {"action": "handover", "participant_ack_name": "سالم أحمد العمري"}, follow=True
    )
    signed_in(manager).post(_url(code), {"action": "close"}, follow=True)


def test_a_blocked_participant_is_absent_from_the_issue_list(signed_in, manager, settled) -> None:
    """
    BR-075 — «لا شهادة بلا براءة ذمة».

    The demo's own guidance promised «المحجوبون لا يظهرون» and its code never
    did it. Here the list is built from completed clearances, so someone still
    blocked is simply not offered.
    """
    enrollment = settled(paid="200.000")
    _open(signed_in(manager), enrollment)

    page = signed_in(manager).get(reverse("operations:certificates"))
    offered = dict(page.context["form"].fields["enrollment_code"].choices)
    assert enrollment.code not in offered


def test_a_completed_clearance_yields_a_ten_digit_certificate(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """§7 — a ten-digit number, the programme snapshotted, the grade recorded."""
    enrollment = settled()
    _complete(signed_in, manager, finance, finance_manager, enrollment)

    response = signed_in(manager).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": "EXCELLENT",
            "issued_on": TERM_START.isoformat(),
            "duration_text": "",
            "training_hours": "40",
        },
        follow=True,
    )
    row = response.context["certificates"][0]
    assert len(row["certificate_number"]) == 10
    assert row["grade"] == "EXCELLENT"
    assert row["program_name"]


def test_a_grade_outside_the_setting_is_refused(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """
    BR-078 — the system does not compute a grade, which is not the same as
    letting anyone type anything onto a sealed document.
    """
    enrollment = settled()
    _complete(signed_in, manager, finance, finance_manager, enrollment)

    response = signed_in(manager).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": "SPLENDID",
            "issued_on": TERM_START.isoformat(),
            "duration_text": "",
            "training_hours": "",
        },
        follow=True,
    )
    from apps.operations.models import Certificate

    assert not Certificate.objects.exists()
    assert response.status_code == 200


def test_a_replacement_without_a_collected_fee_is_refused(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """BR-038 — the 15-dinar fee is COLLECTED, not merely charged."""
    enrollment = settled()
    _complete(signed_in, manager, finance, finance_manager, enrollment)
    issued = signed_in(manager).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": "GOOD",
            "issued_on": TERM_START.isoformat(),
            "duration_text": "",
            "training_hours": "",
        },
        follow=True,
    )
    number = issued.context["certificates"][0]["certificate_number"]

    refused = signed_in(manager).post(
        reverse("operations:certificates"),
        {"action": "replace", "number": number, "on_date": TERM_START.isoformat()},
        follow=True,
    )
    assert "BR-038" in refused.content.decode()


def test_a_reprint_is_audited_and_keeps_the_number(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """A reprint that renumbered would put two documents into the world."""
    enrollment = settled()
    _complete(signed_in, manager, finance, finance_manager, enrollment)
    issued = signed_in(manager).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": "PASS",
            "issued_on": TERM_START.isoformat(),
            "duration_text": "",
            "training_hours": "",
        },
        follow=True,
    )
    number = issued.context["certificates"][0]["certificate_number"]

    after = signed_in(manager).post(
        reverse("operations:certificates"),
        {"action": "reprint", "number": number},
        follow=True,
    )
    assert after.context["certificates"][0]["certificate_number"] == number
    assert AuditEvent.objects.filter(
        entity_type="operations.Certificate", reference=number, action="UPDATE"
    ).exists()


def test_the_finance_officer_cannot_issue_a_certificate(signed_in, finance, settled) -> None:
    """§7 — «تصدرها الجامعة»، and §8 puts that with the centre manager."""
    enrollment = settled()
    response = signed_in(finance).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": "GOOD",
            "issued_on": TERM_START.isoformat(),
        },
    )
    assert response.status_code == 403
