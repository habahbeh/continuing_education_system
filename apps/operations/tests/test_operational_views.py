"""
The operational screens — cohorts, enrolments, the account statement.

Two things every screen here is asked to prove:

* the ROLE boundary holds against a direct POST, not merely against a hidden
  button. Hiding a control is courtesy; ``policy.require`` is the control.
* a business rule reaches the user in the SERVICE's words. A screen that
  swallowed BR-013's refusal and said "something went wrong" would leave the
  centre unable to act on it.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


# ---------------------------------------------------------------------------
# Navigation shell
# ---------------------------------------------------------------------------
def test_the_sidebar_shows_only_what_the_role_may_see(signed_in, cashier, manager) -> None:
    """
    §8 — «الصندوق: القبض فقط».

    The cashier's menu carries the payment screens and not the discounts
    screen; the centre manager's carries discounts and not payment entry,
    because D-01 keeps them away from the till.
    """
    page = signed_in(cashier).get(reverse("operations:dashboard"))
    body = page.content.decode()
    assert reverse("cashbox:payments") in body
    assert reverse("billing:discounts") not in body

    page = signed_in(manager).get(reverse("operations:dashboard"))
    body = page.content.decode()
    assert reverse("billing:discounts") in body
    assert reverse("cashbox:payment-new") not in body


def test_the_audit_account_sees_every_screen_and_no_action(signed_in, seeded_settings) -> None:
    """
    §8 — «حساب التدقيق: قراءة فقط لجميع الحركات».

    Nothing special is coded for this: the role holds V and P and no more, so
    every ``can_*`` flag comes back false on its own.
    """
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.ui", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    page = signed_in(auditor).get(reverse("billing:discounts"))

    assert page.status_code == 200
    assert page.context["can_create"] is False
    assert page.context["can_approve"] is False


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------
def test_a_cohort_opens_planned_not_running(signed_in, manager, priced_catalog, active_semester):
    """BR-013 — enrolment waits on the ministry, so the cohort cannot start ready."""
    client = signed_in(manager)
    response = client.post(
        reverse("operations:cohorts"),
        {
            "code": "CO-UI-1",
            "program_code": "SC-NET",
            "semester_code": active_semester.code,
            "name_ar": "دفعة الواجهة",
            "starts_on": "2026-09-20",
            "ends_on": "2026-12-20",
            "capacity": "25",
            "trainer_name": "",
            "location": "",
            "agreement_number": "",
        },
        follow=True,
    )
    assert response.status_code == 200
    rows = response.context["cohorts"]
    row = next(r for r in rows if r["code"] == "CO-UI-1")
    assert row["status"] == "PLANNED"


def test_the_registrar_cannot_open_a_cohort(signed_in, registrar, priced_catalog, active_semester):
    """§8 — opening a programme is the centre manager's."""
    response = signed_in(registrar).post(
        reverse("operations:cohorts"),
        {
            "code": "CO-UI-X",
            "program_code": "SC-NET",
            "semester_code": active_semester.code,
            "name_ar": "محاولة",
            "starts_on": "2026-09-20",
            "ends_on": "2026-12-20",
            "capacity": "25",
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Enrolments
# ---------------------------------------------------------------------------
def test_an_unapproved_cohort_is_not_offered_and_cannot_be_forced(
    signed_in, registrar, make_cohort, make_participant
):
    """
    BR-013 · D-21 — guarded twice, and the demo guarded it neither time.

    The dropdown omits a cohort the ministry has not approved, so the ordinary
    user never reaches the refusal. A forged POST naming it directly is
    refused by the form's own choice list, and the enrolment is not created —
    which is the point, whichever layer says no.

    The service-level refusal with its BR-013 message is pinned separately in
    ``test_enrollment_lifecycle.py``; repeating it here would test the service
    through a screen rather than testing the screen.
    """
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-UNAPPROVED")
    participant = make_participant(index=41)

    listing = signed_in(registrar).get(reverse("operations:enrollments"))
    offered = dict(listing.context["form"].fields["cohort_code"].choices)
    assert cohort.code not in offered

    response = signed_in(registrar).post(
        reverse("operations:enrollments"),
        {
            "participant_number": participant.participant_number,
            "cohort_code": cohort.code,
            "enrolled_on": "2026-09-20",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert not Enrollment.objects.filter(cohort=cohort).exists()


def test_an_approved_cohort_enrols_and_raises_the_charges(
    signed_in, registrar, make_cohort, approve_cohort, make_participant
):
    """
    One action, both halves — §6.2 puts the charge before the payment.

    An enrolment with no charge lines would show a zero balance on someone who
    has paid nothing, and the cashier would find nothing to collect against.
    """
    cohort = make_cohort("SC-NET", code="CO-UI-OK")
    approve_cohort(cohort, course_number="M-UI-OK")
    participant = make_participant(index=42)

    response = signed_in(registrar).post(
        reverse("operations:enrollments"),
        {
            "participant_number": participant.participant_number,
            "cohort_code": cohort.code,
            "enrolled_on": "2026-09-20",
        },
        follow=True,
    )
    row = next(r for r in response.context["enrollments"] if r["cohort_code"] == cohort.code)
    assert row["code"] == "EN-2026-0001"
    assert row["total_due"] > 0
    assert row["participant_owes"] is True


def test_enrolling_the_same_participant_twice_is_a_message_not_a_crash(
    signed_in, registrar, make_cohort, approve_cohort, make_participant
):
    """
    Q-19 on screen — the operator submits twice and reads a sentence.

    What the database says is ``Duplicate entry '4-1' for key
    …unique_participant_cohort``. Reaching the operator, that is a 500 on the
    enrolment screen; the constraint still stands behind the guard.
    """
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-UI-DUP")
    approve_cohort(cohort, course_number="M-UI-DUP")
    participant = make_participant(index=44)
    posted = {
        "participant_number": participant.participant_number,
        "cohort_code": cohort.code,
        "enrolled_on": "2026-09-20",
    }

    signed_in(registrar).post(reverse("operations:enrollments"), posted, follow=True)
    response = signed_in(registrar).post(
        reverse("operations:enrollments"), posted, follow=True
    )

    assert response.status_code == 200
    assert "لا يمكن إنشاء تسجيل مكرر" in response.content.decode("utf-8")
    assert Enrollment.objects.filter(participant=participant, cohort=cohort).count() == 1


def test_the_enrolment_screen_does_not_ask_for_a_code(signed_in, registrar):
    """
    The operator never types the register's own numbering.

    ``EN-AHMAD-001`` came from a QA script. In production the code identifies
    the row in the ministry's correspondence, so it is minted, not recalled.
    """
    listing = signed_in(registrar).get(reverse("operations:enrollments"))
    assert "code" not in listing.context["form"].fields


def test_approval_without_a_voucher_is_refused_on_screen(
    signed_in, manager, registrar, make_cohort, approve_cohort, make_enrollment
):
    """BR-018 — the voucher is logged before anyone may approve."""
    cohort = make_cohort("SC-NET", code="CO-UI-V")
    approve_cohort(cohort, course_number="M-UI-V")
    enrollment = make_enrollment(cohort, index=43)

    response = signed_in(manager).post(
        reverse("operations:enrollment-action", args=[enrollment.code]),
        {"action": "approve"},
        follow=True,
    )
    assert "BR-018" in response.content.decode()


# ---------------------------------------------------------------------------
# The account statement
# ---------------------------------------------------------------------------
def test_the_statement_reads_the_balance_in_the_right_direction(
    signed_in, finance, make_cohort, approve_cohort, make_enrollment, charge_and_pay
):
    """
    WORKFLOWS §6.5 — «عليه» and «له» are different statements.

    The demo printed one negative number for both. Overpaying leaves a credit
    the statement must name as the centre's debt, not the participant's.
    """
    cohort = make_cohort("SC-NET", code="CO-UI-ST")
    approve_cohort(cohort, course_number="M-UI-ST")
    enrollment = make_enrollment(cohort, index=44)
    charge_and_pay(enrollment, amount="300.000")  # 270 due, 30 over

    response = signed_in(finance).get(reverse("operations:account", args=[enrollment.code]))
    state = response.context["statement"]["state"]

    assert state.centre_owes is True
    assert state.participant_owes is False


def test_the_statement_lists_charges_and_payments(
    signed_in, finance, make_cohort, approve_cohort, make_enrollment, charge_and_pay
):
    cohort = make_cohort("SC-NET", code="CO-UI-ST2")
    approve_cohort(cohort, course_number="M-UI-ST2")
    enrollment = make_enrollment(cohort, index=45)
    charge_and_pay(enrollment, amount="270.000")

    statement = (
        signed_in(finance)
        .get(reverse("operations:account", args=[enrollment.code]))
        .context["statement"]
    )

    assert len(statement["charges"]) >= 2
    assert len(statement["payments"]) >= 1
    assert statement["state"].is_settled is True
