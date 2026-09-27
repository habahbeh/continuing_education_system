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

    form_page = signed_in(registrar).get(reverse("operations:enrollment-new"))
    offered = dict(form_page.context["form"].fields["cohort_code"].choices)
    assert cohort.code not in offered

    response = signed_in(registrar).post(
        reverse("operations:enrollment-new"),
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
        reverse("operations:enrollment-new"),
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

    signed_in(registrar).post(reverse("operations:enrollment-new"), posted, follow=True)
    response = signed_in(registrar).post(reverse("operations:enrollment-new"), posted, follow=True)

    assert response.status_code == 200
    assert "لا يمكن إنشاء تسجيل مكرر" in response.content.decode("utf-8")
    assert Enrollment.objects.filter(participant=participant, cohort=cohort).count() == 1


def test_the_enrolment_screen_does_not_ask_for_a_code(signed_in, registrar):
    """
    The operator never types the register's own numbering.

    ``EN-AHMAD-001`` came from a QA script. In production the code identifies
    the row in the ministry's correspondence, so it is minted, not recalled.
    """
    form_page = signed_in(registrar).get(reverse("operations:enrollment-new"))
    assert "code" not in form_page.context["form"].fields


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


CREDIT_LABEL = "رصيد دائن لصالح المشارك"
DEBT_LABEL = "ذمة على المشارك"
SETTLED_LABEL = "مسوّى"


def _balance_kpi(response) -> str:
    """The balance KPI's markup only, so a label elsewhere on the page cannot pass the test."""
    html = response.content.decode()
    start = html.index("الرصيد")
    return html[start : html.index("</div>\n</div>", start)]


def test_an_overpaid_statement_names_the_credit_for_the_participant(
    signed_in, finance, make_cohort, approve_cohort, make_enrollment, charge_and_pay
):
    """A cashier reading «-30.000» must not take it for a debt."""
    cohort = make_cohort("SC-NET", code="CO-UI-CR")
    approve_cohort(cohort, course_number="M-UI-CR")
    enrollment = make_enrollment(cohort, index=46)
    charge_and_pay(enrollment, amount="300.000")  # 270 due, 30 over

    kpi = _balance_kpi(
        signed_in(finance).get(reverse("operations:account", args=[enrollment.code]))
    )

    assert "-30" in kpi, "the accounting figure stays visible, sign and all"
    assert CREDIT_LABEL in kpi
    assert DEBT_LABEL not in kpi and SETTLED_LABEL not in kpi


def test_an_unpaid_statement_names_the_debt(
    signed_in, finance, make_cohort, approve_cohort, make_enrollment, charge_and_pay
):
    cohort = make_cohort("SC-NET", code="CO-UI-DB")
    approve_cohort(cohort, course_number="M-UI-DB")
    enrollment = make_enrollment(cohort, index=47)
    charge_and_pay(enrollment, amount="100.000")  # 270 due, 170 still owed

    kpi = _balance_kpi(
        signed_in(finance).get(reverse("operations:account", args=[enrollment.code]))
    )

    assert "170" in kpi
    assert DEBT_LABEL in kpi
    assert CREDIT_LABEL not in kpi and SETTLED_LABEL not in kpi


def test_a_paid_up_statement_reads_settled(
    signed_in, finance, make_cohort, approve_cohort, make_enrollment, charge_and_pay
):
    cohort = make_cohort("SC-NET", code="CO-UI-SE")
    approve_cohort(cohort, course_number="M-UI-SE")
    enrollment = make_enrollment(cohort, index=48)
    charge_and_pay(enrollment, amount="270.000")

    kpi = _balance_kpi(
        signed_in(finance).get(reverse("operations:account", args=[enrollment.code]))
    )

    assert SETTLED_LABEL in kpi
    assert CREDIT_LABEL not in kpi and DEBT_LABEL not in kpi


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


# ---------------------------------------------------------------------------
# §6.4 — «تسجيل التخرج» from the enrolments list
# ---------------------------------------------------------------------------
COMPLETE_LABEL = "تسجيل التخرج"


@pytest.fixture
def active_enrollment(make_cohort, approve_cohort, make_enrollment, registrar, manager):
    from apps.operations.services import enrollment_service

    cohort = make_cohort("SC-NET", code="CO-UI-GR")
    approve_cohort(cohort, course_number="M-UI-GR")
    enrollment = make_enrollment(cohort, index=63)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    return enrollment


def test_the_manager_sees_the_graduation_button_on_an_active_row(
    signed_in, manager, active_enrollment
) -> None:
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    assert COMPLETE_LABEL in html
    assert 'value="complete"' in html


def test_the_registrar_does_not_see_the_graduation_button(
    signed_in, registrar, active_enrollment
) -> None:
    html = signed_in(registrar).get(reverse("operations:enrollments")).content.decode()
    assert COMPLETE_LABEL not in html


def test_recording_graduation_from_the_screen_completes_the_enrolment(
    signed_in, manager, active_enrollment
) -> None:
    from apps.operations.models import EnrollmentStatus

    response = signed_in(manager).post(
        reverse("operations:enrollment-action", args=[active_enrollment.code]),
        {"action": "complete"},
        follow=True,
    )
    html = response.content.decode()

    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.COMPLETED
    assert "سُجّل إكمال التسجيل، ويمكن الآن فتح براءة الذمة." in html
    assert COMPLETE_LABEL not in html, "a completed row offers no second graduation"


def test_the_registrar_cannot_post_graduation(signed_in, registrar, active_enrollment) -> None:
    from apps.operations.models import EnrollmentStatus

    response = signed_in(registrar).post(
        reverse("operations:enrollment-action", args=[active_enrollment.code]),
        {"action": "complete"},
    )
    assert response.status_code == 403
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


# ---------------------------------------------------------------------------
# §6.4 — «تسجيل انسحاب» and «تسجيل فصل» from the enrolments list
# ---------------------------------------------------------------------------
WITHDRAW_LABEL = "تسجيل انسحاب"
DISMISS_LABEL = "تسجيل فصل"


def _post_exit(client, enrollment, action: str, **fields):
    return client.post(
        reverse("operations:enrollment-action", args=[enrollment.code]),
        {"action": action, **fields},
        follow=True,
    )


def test_the_manager_sees_both_exit_forms_on_an_active_row(
    signed_in, manager, active_enrollment
) -> None:
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    assert WITHDRAW_LABEL in html and DISMISS_LABEL in html
    assert 'value="withdraw"' in html and 'value="dismiss"' in html
    assert 'name="reason_ar"' in html and 'name="decision_reference"' in html
    assert "إجراءات الحالة" in html, "behind a menu, not one click away"


def test_the_exit_forms_vanish_once_the_row_is_no_longer_active(
    signed_in, manager, active_enrollment
) -> None:
    from apps.operations.services import enrollment_service

    enrollment_service.complete_enrollment(actor=manager, enrollment=active_enrollment)
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    assert WITHDRAW_LABEL not in html and DISMISS_LABEL not in html


def test_the_registrar_sees_dismissal_but_not_withdrawal(
    signed_in, registrar, active_enrollment
) -> None:
    """§3.2/8 gives the registrar CREATE on special cases; §3.2/5 gives them no APPROVE."""
    html = signed_in(registrar).get(reverse("operations:enrollments")).content.decode()
    assert WITHDRAW_LABEL not in html
    assert DISMISS_LABEL in html


def test_the_cashier_sees_neither_exit(signed_in, cashier, active_enrollment) -> None:
    html = signed_in(cashier).get(reverse("operations:enrollments")).content.decode()
    assert WITHDRAW_LABEL not in html and DISMISS_LABEL not in html


def test_withdrawing_from_the_screen(signed_in, manager, active_enrollment) -> None:
    from apps.operations.models import EnrollmentStatus

    html = _post_exit(
        signed_in(manager), active_enrollment, "withdraw", reason_ar="انتقل إلى مدينة أخرى"
    ).content.decode()
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.WITHDRAWN
    assert "سُجّل الانسحاب، ويمكن الآن فتح براءة الذمة." in html


def test_a_withdrawal_posted_without_a_reason_is_refused_with_a_message(
    signed_in, manager, active_enrollment
) -> None:
    from apps.operations.models import EnrollmentStatus

    html = _post_exit(
        signed_in(manager), active_enrollment, "withdraw", reason_ar=""
    ).content.decode()
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE
    assert "يتطلب سبباً" in html


def test_dismissing_from_the_screen(signed_in, manager, active_enrollment) -> None:
    from apps.operations.models import EnrollmentStatus, SpecialCase

    html = _post_exit(
        signed_in(manager),
        active_enrollment,
        "dismiss",
        decision_reference="قرار 9/2026",
        reason_ar="مخالفة موثّقة",
    ).content.decode()
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.DISMISSED
    assert SpecialCase.objects.filter(
        enrollment=active_enrollment, decision_reference="قرار 9/2026"
    ).exists()
    assert "سُجّل الفصل، ويمكن الآن فتح براءة الذمة." in html


def test_a_dismissal_posted_without_its_decision_is_refused_with_a_message(
    signed_in, manager, active_enrollment
) -> None:
    from apps.operations.models import EnrollmentStatus

    html = _post_exit(
        signed_in(manager), active_enrollment, "dismiss", decision_reference="", reason_ar="سبب"
    ).content.decode()
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE
    assert "BR-067" in html


@pytest.mark.parametrize("action", ["withdraw", "dismiss"])
def test_a_tampered_exit_from_the_cashier_is_a_403(
    signed_in, cashier, active_enrollment, action
) -> None:
    from apps.operations.models import EnrollmentStatus

    response = signed_in(cashier).post(
        reverse("operations:enrollment-action", args=[active_enrollment.code]),
        {"action": action, "reason_ar": "سبب", "decision_reference": "ق"},
    )
    assert response.status_code == 403
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


def test_a_tampered_withdrawal_from_the_registrar_is_a_403(
    signed_in, registrar, active_enrollment
) -> None:
    from apps.operations.models import EnrollmentStatus

    response = signed_in(registrar).post(
        reverse("operations:enrollment-action", args=[active_enrollment.code]),
        {"action": "withdraw", "reason_ar": "سبب"},
    )
    assert response.status_code == 403
    active_enrollment.refresh_from_db()
    assert active_enrollment.status == EnrollmentStatus.ACTIVE


# ---------------------------------------------------------------------------
# Each exit is confirmed in its own dialog — never one click
# ---------------------------------------------------------------------------
def _dialog(html: str, dialog_id: str) -> str:
    start = html.index(f'<dialog class="modal" id="{dialog_id}"')
    return html[start : html.index("</dialog>", start)]


def test_no_exit_is_a_one_click_submit(signed_in, manager, active_enrollment) -> None:
    """The row holds a menu of ``type="button"`` openers; every submit lives inside a dialog."""
    import re

    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = html[html.index(active_enrollment.code) :]
    row = row[: row.index("</tr>")]

    for action in ("complete", "withdraw", "dismiss"):
        assert f'data-opens="{action}-{active_enrollment.code}"' in row
    submits = re.findall(
        r'<form[^>]*>.*?<input type="hidden" name="action" value="(\w+)">', row, re.S
    )
    assert set(submits) == {"complete", "withdraw", "dismiss"}
    for action in submits:
        form_start = row.index(f'name="action" value="{action}"')
        assert "<dialog" in row[:form_start], f"{action} form is outside a dialog"


def test_the_graduation_dialog_summarises_and_confirms(
    signed_in, manager, active_enrollment
) -> None:
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    dialog = _dialog(html, f"complete-{active_enrollment.code}")

    assert active_enrollment.code in dialog
    assert active_enrollment.participant.name_ar in dialog
    assert "منتظم" in dialog, "current status"
    assert "مكتمل" in dialog, "new status"
    assert "براءة ذمة" in dialog and "لا تُفتح البراءة تلقائياً" in dialog
    assert 'class="btn2 primary" type="submit" autofocus>تأكيد تسجيل التخرج<' in dialog
    assert 'type="button" data-closes>إلغاء<' in dialog
    assert "danger" not in dialog
    assert 'name="reason_ar"' not in dialog, "graduation needs no reason"


def test_the_withdrawal_dialog_requires_a_reason(signed_in, manager, active_enrollment) -> None:
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    dialog = _dialog(html, f"withdraw-{active_enrollment.code}")

    assert "منسحب" in dialog
    assert 'name="reason_ar" type="text" maxlength="255" required' in dialog
    assert "تأكيد تسجيل الانسحاب" in dialog
    assert 'class="btn2 danger"' not in dialog, "withdrawal is not an adverse action"


def test_the_dismissal_dialog_requires_decision_and_reason_and_is_red(
    signed_in, manager, active_enrollment
) -> None:
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    dialog = _dialog(html, f"dismiss-{active_enrollment.code}")

    assert "مفصول" in dialog
    assert 'name="decision_reference" type="text" maxlength="64" required' in dialog
    assert 'name="reason_ar" type="text" maxlength="255" required' in dialog
    assert "BR-067" in dialog and "BR-068" in dialog
    assert 'class="btn2 danger" type="submit">تأكيد تسجيل الفصل<' in dialog


def test_the_dialogs_are_wired_for_the_keyboard(signed_in, manager, active_enrollment) -> None:
    """
    Two halves, and both are asserted: the page declares the wiring, and the
    shared script performs it.

    The behaviour used to be copied into this template, so the assertion could
    read it here. It lives in ``static/js/ui.js`` now — one copy for every
    screen — which is why the page is checked for the contract and the file for
    the behaviour. Checking only the page would pass on a screen that declares
    ``data-opens`` and loads nothing to answer it.
    """
    from pathlib import Path

    from django.conf import settings

    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()

    assert "js/ui.js" in html, "the page declares openers but loads nothing to open them"
    for action in ("complete", "withdraw", "dismiss"):
        assert f'data-opens="{action}-{active_enrollment.code}"' in html
        assert f'aria-labelledby="{action}-{active_enrollment.code}-t"' in html
    assert "data-closes" in html

    script = (Path(settings.BASE_DIR) / "static" / "js" / "ui.js").read_text(encoding="utf-8")
    assert "showModal()" in script
    assert "opener.focus()" in script, "focus returns to the opener on close"
    assert 'closest("dialog")?.close()' in script


def test_the_registrar_gets_only_the_dismissal_dialog(
    signed_in, registrar, active_enrollment
) -> None:
    html = signed_in(registrar).get(reverse("operations:enrollments")).content.decode()
    assert f'id="dismiss-{active_enrollment.code}"' in html
    assert f'id="complete-{active_enrollment.code}"' not in html
    assert f'id="withdraw-{active_enrollment.code}"' not in html


def test_a_finished_row_has_no_menu_and_no_dialogs(signed_in, manager, active_enrollment) -> None:
    from apps.operations.services import enrollment_service

    enrollment_service.withdraw_enrollment(
        actor=manager, enrollment=active_enrollment, reason_ar="سبب"
    )
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    # Scoped to the page's own body: the shell carries the quick-jump palette,
    # which is a <dialog> on every screen and is not this row drawing one.
    body = html.split('id="main"', 1)[1].split("</main>", 1)[0]
    assert "إجراءات الحالة" not in body
    assert "<dialog" not in body


# ---------------------------------------------------------------------------
# The creation card at the foot of the page — what it suggests, and what it
# says when it refuses
# ---------------------------------------------------------------------------
def test_the_enrolment_date_is_offered_as_today(signed_in, registrar) -> None:
    """
    The answer is today on all but the rare back-dated entry, and the system
    knows today. The admission form was fixed for exactly this; the screen
    used more often than it was still asking the clerk to type it.
    """
    from django.utils import timezone

    html = signed_in(registrar).get(reverse("operations:enrollment-new")).content.decode()
    today = timezone.localdate().isoformat()

    assert 'name="enrolled_on"' in html
    assert f'value="{today}"' in html, "today is not suggested"


def test_the_suggested_date_is_read_per_request_and_not_frozen_at_import(
    signed_in, registrar
) -> None:
    """
    A module imported at boot must not freeze a date: a server running since
    Monday would suggest Monday all week. The initial is a callable.
    """
    from apps.operations.forms import EnrollmentForm

    assert callable(EnrollmentForm.base_fields["enrolled_on"].initial)


def test_an_empty_post_is_still_a_submission(signed_in, registrar) -> None:
    """
    ``request.POST or None`` read an empty body as "not submitted", so a POST
    carrying nothing came back as a pristine form with no error, no message
    and a silent 200 — the reader could not tell whether anything had been
    recorded. The method decides, not the payload.
    """
    response = signed_in(registrar).post(reverse("operations:enrollment-new"), {})

    form = response.context["form"]
    assert form.is_bound, "an empty POST is a submission that failed, not a page visit"
    assert set(form.errors) == {"participant_number", "cohort_code", "enrolled_on"}


def test_a_refusal_counts_and_names_every_field_at_the_top(
    signed_in, registrar, active_enrollment
) -> None:
    """
    The creation card sits below the fold on a page two and a half screens
    tall, and a rejected field draws its message inside the card. Without a
    summary the refusal landed the reader at the top of a page that looked
    unchanged.
    """
    response = signed_in(registrar).post(
        reverse("operations:enrollment-new"),
        {"participant_number": active_enrollment.participant.participant_number},
    )
    html = response.content.decode()

    assert 'id="err-summary"' in html
    assert "حقول تحتاج تصحيحاً قبل التسجيل" in html
    # Every named field is a link to the control that carries the error.
    for name in ("cohort_code", "enrolled_on"):
        assert f'href="#id_{name}"' in html
    assert "participant_number" not in html.split('id="err-summary"', 1)[1].split("</div>", 2)[1]


def test_a_page_that_refused_nothing_carries_no_summary(signed_in, registrar) -> None:
    """A banner that is always there is a banner nobody reads."""
    html = signed_in(registrar).get(reverse("operations:enrollments")).content.decode()

    assert 'id="err-summary"' not in html


# ---------------------------------------------------------------------------
# The register's own navigation — the row is a junction, not a dead end
# ---------------------------------------------------------------------------
def test_a_search_keeps_the_participant_it_was_filtered_by(
    signed_in, manager, active_enrollment
) -> None:
    """
    The participants register links here as ?participant=<number>, and the
    search form used to carry the cohort and the status filters and not that
    one — so the first search silently widened the list back to everything,
    with no word about the filter it had dropped.
    """
    url = reverse("operations:enrollments")
    client = signed_in(manager)
    number = active_enrollment.participant.participant_number

    filtered = client.get(url, {"participant": number})
    form = filtered.content.decode().split('class="filterbar"', 1)[1].split("</form>", 1)[0]
    assert f'name="participant" value="{number}"' in form

    searched = client.get(url, {"participant": number, "q": active_enrollment.code})
    assert [row["code"] for row in searched.context["enrollments"]] == [active_enrollment.code]
    assert any(label == "المشارك" for label, _value in searched.context["active_filters"])


def test_the_row_links_to_the_file_and_the_cohort_for_a_reader_who_may_open_them(
    signed_in, manager, active_enrollment
) -> None:
    """The two ways out of a row: whose enrolment this is, and onto what."""
    row = _row_of(
        signed_in(manager).get(reverse("operations:enrollments")).content.decode(),
        active_enrollment.code,
    )
    number = active_enrollment.participant.participant_number

    assert reverse("people:participant-detail", args=[number]) in row
    assert f'{reverse("operations:cohorts")}?q={active_enrollment.cohort.code}' in row


def test_the_row_offers_no_cohort_link_to_a_reader_the_register_would_refuse(
    signed_in, seeded_settings, active_enrollment
) -> None:
    """
    §3 — a link into a refusal spends a click and files a DENIED_ATTEMPT
    (BR-085). The finance manager reads enrolments and not the cohort
    register, so the code stays text for them while the name still links.
    """
    from apps.people.models import Role, User

    finance_manager = User.objects.create_user(
        username="finmgr.rowlink", password=PASSWORD, role=Role.FINANCE_MANAGER
    )
    row = _row_of(
        signed_in(finance_manager).get(reverse("operations:enrollments")).content.decode(),
        active_enrollment.code,
    )

    assert reverse("operations:cohorts") not in row
    assert active_enrollment.cohort.code in row  # …the code is still printed


def test_the_row_is_one_line_and_what_left_it_is_a_click_away(
    signed_in, manager, active_enrollment
) -> None:
    """
    The grid was a stack of paragraphs: a code over its date, a name over its
    number, a cohort over its name over its programme, and a status under a
    whole sentence. The row measured 115px — 147 on two of seven — so a
    registrar saw two and a half enrolments on a 1440x900 screen and scrolled
    a register meant to be scanned.

    Nothing was deleted. Everything that left the line is in the detail row
    beneath it, in the page and readable to a screen reader before anything is
    clicked.
    """
    import re

    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = _row_of(html, active_enrollment.code)
    # What the eye reads, not what the markup carries: the participant number
    # is still in the href of their file, and belongs there.
    visible = re.sub(r"<[^>]+>", " ", row)
    number = active_enrollment.participant.participant_number

    # The line itself carries identity, balance, status, actions — and no more.
    assert active_enrollment.code in visible
    assert number not in visible, "the participant number moved to the detail row"
    assert active_enrollment.cohort.name_ar not in visible, "so did the cohort's name"
    assert "<br>" not in row, "a cell that breaks its line is a cell with two lines"

    detail = html.split(f'id="det-{active_enrollment.code}"', 1)[1].split("</tr>", 1)[0]
    assert number in detail
    assert active_enrollment.cohort.name_ar in detail
    assert active_enrollment.cohort.program.name_ar in detail
    for label in ("المستحق", "المدفوع", "الخصم", "تاريخ التسجيل"):
        assert label in detail, f"«{label}» left the row without arriving anywhere"


def test_the_detail_row_is_hidden_by_the_markup_and_not_by_a_stylesheet(
    signed_in, manager, active_enrollment
) -> None:
    """
    ``hidden`` rather than a CSS rule: what the markup hides the button shows,
    so the detail works on a page whose stylesheet never arrived — and a screen
    reader is not handed seven expanded rows it did not ask for.
    """
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    detail_tag = html.split(f'id="det-{active_enrollment.code}"', 1)[0].rsplit("<tr", 1)[1]

    assert "hidden" in html.split(f'id="det-{active_enrollment.code}"', 1)[1].split(">", 1)[0]
    assert "row-detail" in detail_tag
    assert f'data-expands="det-{active_enrollment.code}"' in html
    assert 'aria-expanded="false"' in html
    assert "js/ui.js" in html, "the opener is declared but nothing would answer it"


def test_the_empty_row_spans_the_table_that_is_actually_drawn(
    signed_in, manager, seeded_settings
) -> None:
    """A hard-coded span goes stale the moment a column moves — and one just did."""
    import re

    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]

    assert re.search(rf'colspan="{head.count("<th")}"[^>]*class="empty"', html)


def test_the_status_cell_says_its_condition_without_a_sentence_in_it(
    signed_in, manager, active_enrollment
) -> None:
    """
    «معتمد مع رصيد متبقٍ — الشهادة/البراءة تتطلب التسديد» is a condition on the
    status, not a second status, and a full sentence inside a table cell slows
    the scan it is meant to serve. It is a dot carrying its whole text in
    ``title`` now — so nothing is lost to the reader who needs it, and nothing
    is spent on the reader who does not.
    """
    import re

    # The fixture is already ACTIVE, which is to say already approved.
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = _row_of(html, active_enrollment.code)

    assert 'class="chip' in row
    assert "معتمد" in row  # …the fact survives, in the title and for the screen reader
    assert row.count("<br>") == 0, "the row is one line"
    # The sentence is in the tooltip, not spread across the cell.
    assert "الشهادة/البراءة تتطلب التسديد" not in re.sub(r"title=\"[^\"]*\"", "", row)


def test_the_row_actions_stay_on_the_screen_at_every_width() -> None:
    """
    Nine columns and four controls held on one line made the table 1366px
    inside a 1134px wrapper at 1440 — the actions cell starting at -207, so
    «كشف الحساب» and «اعتماد» sat behind a horizontal scroll on a full desktop
    screen, and at 1024 the page itself was dragged 238px sideways.

    The row carries seven columns now and the actions fit on one line where
    there is room: measured at 1440 as 1134 inside 1134 with no overflow and
    rows of 59-79px. At 1024 one line would overflow by 363px, so they wrap
    below 1180 — a taller row being the lesser harm than a table that leaves
    the screen.
    """
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static" / "src" / "input.css").read_text(encoding="utf-8")
    block = css.split("/* سجل التسجيلات:", 1)[1].split("/* سجل المشاركين:", 1)[0]

    assert ".enr-tbl td.acts { @apply whitespace-nowrap; }" in block
    assert "@media (max-width: 1180px)" in block
    assert "whitespace-normal" in block


# ---------------------------------------------------------------------------
# The clearance step, offered on the enrolment row it belongs to (§6.4)
# ---------------------------------------------------------------------------
def _row_of(html: str, code: str) -> str:
    """The one row, so a link on a neighbour's row cannot satisfy an assertion."""
    at = html.index(code)
    return html[html.rindex("<tr>", 0, at) : html.index("</tr>", at)]


def test_an_exited_enrolment_offers_the_clearance_it_has_not_got_yet(
    signed_in, manager, active_enrollment
) -> None:
    """
    §6.4 — the exit is followed by a clearance, and the operator should not
    have to leave the register and find the same person again to start it.
    """
    from apps.operations.models import Clearance
    from apps.operations.services import enrollment_service

    enrollment_service.dismiss_enrollment(
        actor=manager,
        enrollment=active_enrollment,
        decision_reference="ق 5/2026",
        reason_ar="مخالفة",
    )
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = _row_of(html, active_enrollment.code)

    assert f'href="{reverse("operations:clearances")}">فتح براءة ذمة<' in row
    # The offer is a way in, never the act itself: no clearance was opened.
    assert "عرض براءة الذمة" not in row
    assert not Clearance.objects.filter(enrollment=active_enrollment).exists()


def test_a_running_enrolment_is_offered_no_clearance(signed_in, manager, active_enrollment) -> None:
    """An enrolment still running has nothing to clear, so it is offered nothing."""
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = _row_of(html, active_enrollment.code)

    # The dialogs on a running row explain the clearance in prose, so the
    # assertion is on the control — a link into the clearance screens.
    assert f'href="{reverse("operations:clearances")}"' not in row


def test_once_the_clearance_exists_the_row_points_at_it(
    signed_in, manager, active_enrollment
) -> None:
    """The second visit is a reading, not a second opening."""
    from apps.operations.services import clearance_service, enrollment_service

    enrollment_service.dismiss_enrollment(
        actor=manager,
        enrollment=active_enrollment,
        decision_reference="ق 5/2026",
        reason_ar="مخالفة",
    )
    clearance = clearance_service.open_clearance(
        actor=manager, enrollment=active_enrollment, opened_on=TERM_START
    )
    html = signed_in(manager).get(reverse("operations:enrollments")).content.decode()
    row = _row_of(html, active_enrollment.code)

    detail = reverse("operations:clearance-detail", args=[clearance.code])
    assert f'href="{detail}">عرض براءة الذمة<' in row
    assert f'href="{reverse("operations:clearances")}">فتح براءة ذمة<' not in row, (
        "an enrolment already cleared is not offered a second"
    )


def test_the_row_offers_no_clearance_to_a_role_that_may_not_open_one(
    signed_in, cashier, manager, active_enrollment
) -> None:
    """
    Hiding is courtesy, not the guard — but a reader must not be shown a door
    that would refuse them and write a DENIED_ATTEMPT for it (BR-085).
    """
    from apps.operations.services import enrollment_service

    enrollment_service.dismiss_enrollment(
        actor=manager,
        enrollment=active_enrollment,
        decision_reference="ق 5/2026",
        reason_ar="مخالفة",
    )
    html = signed_in(cashier).get(reverse("operations:enrollments")).content.decode()
    assert f'href="{reverse("operations:clearances")}">فتح براءة ذمة<' not in html


def test_each_confirmed_exit_lands_in_clearance_with_its_case(
    signed_in, manager, make_cohort, approve_cohort, make_enrollment, registrar
) -> None:
    """The whole point, end to end from the screen: three exits, three cases, no clearance yet."""
    from apps.operations.models import Clearance
    from apps.operations.services import clearance_service, enrollment_service

    def active(index: int):
        cohort = make_cohort("SC-NET", code=f"CO-E2E-{index}")
        approve_cohort(cohort, course_number=f"M-E2E-{index}")
        enrollment = make_enrollment(cohort, index=index)
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
        return enrollment

    graduate, leaver, dismissed = active(90), active(91), active(92)
    client = signed_in(manager)
    _post_exit(client, graduate, "complete")
    _post_exit(client, leaver, "withdraw", reason_ar="سفر")
    _post_exit(client, dismissed, "dismiss", decision_reference="ق 5/2026", reason_ar="مخالفة")

    assert not Clearance.objects.exists()
    offered = dict(clearance_service.clearable_enrollment_choices(actor=manager))
    assert "تخرج" in offered[graduate.code]
    assert "انسحاب" in offered[leaver.code]
    assert "فصل" in offered[dismissed.code]


def test_a_pricing_refusal_reaches_the_registrar_as_a_message_not_a_500(
    client, registrar, make_cohort, approve_cohort, make_participant
) -> None:
    """
    The enrolment screen used to 500 on every pricing refusal.

    Found on a fresh install: the price list carried no registration fee rule
    for the participant's category, so ``resolve_registration_fee`` refused
    (BR-009) — and the exception escaped the view. The registrar met a Django
    error page, lost the typed form, and learned nothing about what was missing
    or who could fix it. The refusal is correct; arriving as a 500 is not.
    """
    from apps.catalog.models import RegistrationFeeRule
    from apps.operations.models import Enrollment

    cohort = make_cohort("SC-NET", code="CO-PRICE-1")
    approve_cohort(cohort)
    participant = make_participant(index=77)
    # The one row the whole pricing depends on, taken away.
    RegistrationFeeRule.objects.all().delete()

    client.force_login(registrar)
    response = client.post(
        reverse("operations:enrollment-new"),
        {
            "participant_number": participant.participant_number,
            "cohort_code": cohort.code,
            "enrolled_on": "2026-09-26",
        },
        follow=True,
    )
    body = response.content.decode("utf-8")

    assert response.status_code == 200, "a pricing refusal is not a server error"
    assert not Enrollment.objects.filter(participant=participant).exists()
    assert "تعذّر تحميل الرسوم" in body
    assert "قائمة الأسعار" in body, "the message must say where this is fixed"
