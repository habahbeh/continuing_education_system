"""
The cohorts register after its UX pass: choose, don't type.

The open-cohort dialog offers programmes grouped by kind (with their levels),
semesters with their dates, and trainers and places already on record; the
code and the name are minted when left blank. Each row names its next step
from the reader's permission. The service still decides everything (BR-007,
BR-013) — these tests hold the shape of the screen and the two generators.
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


def _open(client, active_semester, **fields):
    data = {
        "program_code": "SC-NET",
        "semester_code": active_semester.code,
        "starts_on": "2026-09-20",
        "ends_on": "2026-12-20",
        "capacity": "25",
        **fields,
    }
    return client.post(reverse("operations:cohorts"), data, follow=True)


def test_a_blank_code_and_name_are_minted_from_the_programme_and_term(
    signed_in, manager, priced_catalog, active_semester
):
    response = _open(signed_in(manager), active_semester, code="", name_ar="")

    row = next(r for r in response.context["cohorts"] if r["code"].startswith("CO-SC-NET-"))
    assert row["code"] == "CO-SC-NET-1"
    assert row["program_name"] in row["name_ar"]
    assert str(active_semester) in row["name_ar"]
    assert row["status"] == "PLANNED"


def test_the_minted_code_skips_codes_already_taken(
    signed_in, manager, priced_catalog, active_semester
):
    client = signed_in(manager)
    _open(client, active_semester, code="CO-SC-NET-1", name_ar="أولى")
    response = _open(client, active_semester, code="", name_ar="")

    codes = {r["code"] for r in response.context["cohorts"]}
    assert {"CO-SC-NET-1", "CO-SC-NET-2"} <= codes


def test_a_typed_code_and_name_are_kept(signed_in, manager, priced_catalog, active_semester):
    response = _open(signed_in(manager), active_semester, code="CO-MINE", name_ar="اسمي")

    row = next(r for r in response.context["cohorts"] if r["code"] == "CO-MINE")
    assert row["name_ar"] == "اسمي"


def test_the_dialog_offers_choices_not_blanks(
    signed_in, manager, priced_catalog, active_semester, make_cohort
):
    make_cohort("SC-NET", code="CO-HINT", trainer_name="م. خالد", location="قاعة ٣")

    page = signed_in(manager).get(reverse("operations:cohorts"))
    ctx = page.context

    assert {p["code"] for p in ctx["programs"]} >= {"SC-NET"}
    assert all("type_label" in p and "levels" in p for p in ctx["programs"])
    assert any(s["code"] == active_semester.code and s["starts_on"] for s in ctx["semesters"])
    assert "م. خالد" in ctx["suggestions"]["trainers"]
    assert "قاعة ٣" in ctx["suggestions"]["locations"]
    body = page.content.decode("utf-8")
    assert 'id="cohort-new"' in body
    assert "<optgroup" in body
    assert '<datalist id="trainers">' in body


def test_the_register_filters_by_status_from_its_own_select(
    signed_in, manager, priced_catalog, active_semester, make_cohort
):
    make_cohort("SC-NET", code="CO-F-1")

    page = signed_in(manager).get(reverse("operations:cohorts") + "?status=PLANNED")
    body = page.content.decode("utf-8")

    # The visible filter is now the STAGE — «مخطَّطة» meant two things and the
    # register is read by what comes next. The stored status stays reachable
    # from a URL, and is named in the banner above the table.
    assert 'id="f_stage"' in body
    assert page.context["cohorts"]
    assert all(r["status"] == "PLANNED" for r in page.context["cohorts"])


def test_each_row_names_its_next_step_by_approval(
    signed_in, manager, registrar, make_cohort, approve_cohort
):
    planned = make_cohort("SC-NET", code="CO-NS-PLAN")
    approved = make_cohort("SC-NET", code="CO-NS-OK")
    approve_cohort(approved, course_number="M-NS-OK")

    body = signed_in(manager).get(reverse("operations:cohorts")).content.decode("utf-8")

    assert f'href="{reverse("operations:mohe-submit")}?cohort={planned.code}"' in body
    assert f'href="{reverse("operations:enrollments")}?cohort={approved.code}"' in body
    assert f'href="{reverse("operations:mohe-submit")}?cohort={approved.code}"' not in body

    rows = {
        r["code"]: r
        for r in signed_in(manager).get(reverse("operations:cohorts")).context["cohorts"]
    }
    assert rows[approved.code]["is_mohe_approved"] is True
    assert rows[planned.code]["is_mohe_approved"] is False


def test_the_ministry_form_arrives_with_the_cohort_chosen(signed_in, manager, make_cohort):
    cohort = make_cohort("SC-NET", code="CO-PRE")

    page = signed_in(manager).get(reverse("operations:mohe-submit") + f"?cohort={cohort.code}")

    assert page.status_code == 200
    assert page.context["form"].initial["cohort_code"] == cohort.code


# ---------------------------------------------------------------------------
# The lifecycle (§3.3/12 gives the manager E and A)
#
# Four of six statuses had nothing that set them, so the register could not say
# a cohort had started, ended, or failed to fill — and §5.3 makes the last one
# the event a full refund is justified by.
# ---------------------------------------------------------------------------
def _act(client, action, cohort, **extra):
    return client.post(
        reverse("operations:cohorts"),
        {"action": action, "cohort_code": cohort.code, **extra},
        follow=True,
    )


def _row(client, code):
    rows = client.get(reverse("operations:cohorts")).context["cohorts"]
    return next(r for r in rows if r["code"] == code)


def test_the_register_answers_the_link_the_catalogue_sends(
    signed_in, manager, make_cohort
):
    """The programme row links here with a PROGRAMME code; the register found none."""
    make_cohort("SC-NET", code="CO-LINK-1")
    make_cohort("SC-NET", code="CO-LINK-2")
    client = signed_in(manager)

    found = client.get(reverse("operations:cohorts"), {"q": "SC-NET"}).context["cohorts"]

    assert {r["code"] for r in found} == {"CO-LINK-1", "CO-LINK-2"}
    assert client.get(reverse("operations:cohorts"), {"q": "هندسة"}).context["cohorts"]


def test_planned_is_split_into_the_two_stages_it_used_to_hide(
    signed_in, manager, make_cohort, approve_cohort
):
    waiting = make_cohort("SC-NET", code="CO-ST-WAIT")
    ready = make_cohort("SC-NET", code="CO-ST-OK")
    approve_cohort(ready, course_number="M-ST-OK")
    client = signed_in(manager)

    assert waiting.status == ready.status == "PLANNED"
    assert _row(client, "CO-ST-WAIT")["stage"] == "NEEDS_FILE"
    assert _row(client, "CO-ST-OK")["stage"] == "ENROLLABLE"

    page = client.get(reverse("operations:cohorts")).content.decode("utf-8")
    assert "جاهزة للتسجيل" in page and "بانتظار الملف الوزاري" in page


def test_a_cohort_starts_only_after_the_ministry_approved_it(
    signed_in, manager, make_cohort, approve_cohort
):
    unapproved = make_cohort("SC-NET", code="CO-RUN-NO")
    approved = make_cohort("SC-NET", code="CO-RUN-OK")
    approve_cohort(approved, course_number="M-RUN-OK")
    client = signed_in(manager)

    _act(client, "start", unapproved)
    unapproved.refresh_from_db()
    assert unapproved.status == "PLANNED"

    _act(client, "start", approved)
    approved.refresh_from_db()
    assert approved.status == "RUNNING"
    assert _row(client, "CO-RUN-OK")["stage"] == "RUNNING"

    _act(client, "complete", approved)
    approved.refresh_from_db()
    assert approved.status == "COMPLETED"


def test_cancelling_for_low_enrolment_keeps_the_reason_it_is_defended_by(
    signed_in, manager, make_cohort, approve_cohort
):
    cohort = make_cohort("SC-NET", code="CO-CANCEL")
    approve_cohort(cohort, course_number="M-CANCEL")
    client = signed_in(manager)

    _act(client, "cancel", cohort, reason_ar="")
    cohort.refresh_from_db()
    assert cohort.status != "CANCELLED_LOW_ENROLLMENT"

    _act(client, "cancel", cohort, reason_ar="لم يكتمل العدد الأدنى — مشاركان")
    cohort.refresh_from_db()
    assert cohort.status == "CANCELLED_LOW_ENROLLMENT"
    assert cohort.cancellation_reason_ar == "لم يكتمل العدد الأدنى — مشاركان"
    assert _row(client, "CO-CANCEL")["stage"] == "CANCELLED"


def test_the_edit_corrects_the_period_and_refuses_to_seat_fewer_than_are_enrolled(
    signed_in, manager, make_cohort, approve_cohort, make_enrollment
):
    cohort = make_cohort("SC-NET", code="CO-EDIT")
    approve_cohort(cohort, course_number="M-EDIT")
    make_enrollment(cohort=cohort)
    client = signed_in(manager)
    fields = {
        "starts_on": "2026-09-20",
        "ends_on": "2026-12-25",
        "capacity": "40",
        "delivery_method": "BLENDED",
        "trainer_name": "م. ليلى حدّاد",
        "location": "مبنى المركز",
        "agreement_number": "",
    }

    _act(client, "edit", cohort, **fields)
    cohort.refresh_from_db()
    assert cohort.capacity == 40 and cohort.trainer_name == "م. ليلى حدّاد"
    assert cohort.delivery_method == "BLENDED"

    _act(client, "edit", cohort, **{**fields, "capacity": "0"})
    cohort.refresh_from_db()
    assert cohort.capacity == 40


def test_the_register_offers_no_act_to_the_role_that_only_reads(
    signed_in, registrar, manager, make_cohort, approve_cohort
):
    cohort = make_cohort("SC-NET", code="CO-READ")
    approve_cohort(cohort, course_number="M-READ")
    client = signed_in(registrar)

    page = client.get(reverse("operations:cohorts")).content.decode("utf-8")
    assert "بدء التنفيذ…" not in page and "إلغاء لقلة التسجيل…" not in page
    assert 'id="cohort-new"' not in page
    assert client.post(
        reverse("operations:cohorts"), {"action": "start", "cohort_code": cohort.code}
    ).status_code == 403


def test_sending_the_file_moves_the_cohort_to_waiting_on_the_ministry(
    signed_in, manager, make_cohort, attach_required_documents
):
    """PENDING_MOHE existed in the vocabulary with nothing ever setting it."""
    from apps.operations.services import mohe_service

    cohort = make_cohort("SC-NET", code="CO-SENT")
    submission = mohe_service.create_submission(
        actor=manager,
        cohort=cohort,
        data={"training_axes_ar": "محاور", "target_audience_ar": "الفئة"},
    )
    attach_required_documents(submission, manager)
    mohe_service.submit_to_mohe(actor=manager, submission=submission, submitted_on=cohort.starts_on)

    cohort.refresh_from_db()
    assert cohort.status == "PENDING_MOHE"
    assert _row(signed_in(manager), "CO-SENT")["stage"] == "AT_MOHE"
