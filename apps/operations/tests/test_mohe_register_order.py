"""
The ministry register's own query: what it counts, what it narrows to, and
the order it hands the work back in.

Separated from ``test_mohe_screens`` because none of this is about the screen.
These are claims about SQL — that two tallies cost no round trips, that
«قاربت» and «انتهت» are decided by the database rather than by Python after
the fact, and that the default order leads with what someone has to do today.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.operations.models import MoheStatus, MoheSubmission
from apps.operations.services import mohe_service
from apps.people.models import User

pytestmark = pytest.mark.django_db

ALERT_DAYS = 15


def _as_of() -> date:
    return timezone.localdate()


def _file(
    *,
    cohort: Any,
    status: str,
    deadline: date | None = None,
    submitted: date | None = None,
    created_by: User,
    number: str = "",
) -> MoheSubmission:
    """
    A ministry file put straight into the state under test.

    The lifecycle services are exercised to death elsewhere; walking every
    fixture through them here would make an ordering test depend on BR-016's
    attachments, and a failure would say nothing about ordering.
    """
    decided = submitted
    if status in (MoheStatus.APPROVED, MoheStatus.REJECTED) and decided is None:
        # C-16 is a database constraint, not a nicety: an approval with no
        # decision date is an approval nobody can produce evidence for, and
        # the check refuses the row outright.
        decided = _as_of() - timedelta(days=7)
    return MoheSubmission.objects.create(
        cohort=cohort,
        status=status,
        submitted_on=submitted,
        decided_on=decided,
        registration_deadline=deadline,
        mohe_course_number=number or ("M-1" if status == MoheStatus.APPROVED else ""),
        rejection_reason_ar="لم تكفِ المحاور" if status == MoheStatus.REJECTED else "",
        created_by=created_by,
    )


@pytest.fixture
def six_states(make_cohort, registrar, manager) -> dict[str, MoheSubmission]:
    """One file in each of the six positions the default order ranks."""
    today = _as_of()
    made = {
        "rejected": _file(
            cohort=make_cohort(code="CO-ORD-REJ"),
            status=MoheStatus.REJECTED,
            submitted=today - timedelta(days=40),
            created_by=manager,
        ),
        "soon": _file(
            cohort=make_cohort(code="CO-ORD-SOON"),
            status=MoheStatus.APPROVED,
            deadline=today + timedelta(days=3),
            created_by=manager,
        ),
        "soonest": _file(
            cohort=make_cohort(code="CO-ORD-SOONEST"),
            status=MoheStatus.APPROVED,
            deadline=today + timedelta(days=1),
            created_by=manager,
        ),
        "submitted": _file(
            cohort=make_cohort(code="CO-ORD-SENT"),
            status=MoheStatus.SUBMITTED,
            submitted=today - timedelta(days=20),
            created_by=manager,
        ),
        "draft": _file(
            cohort=make_cohort(code="CO-ORD-DRAFT"),
            status=MoheStatus.DRAFT,
            created_by=manager,
        ),
        "open": _file(
            cohort=make_cohort(code="CO-ORD-OPEN"),
            status=MoheStatus.APPROVED,
            deadline=today + timedelta(days=90),
            created_by=manager,
        ),
        "expired": _file(
            cohort=make_cohort(code="CO-ORD-GONE"),
            status=MoheStatus.APPROVED,
            deadline=today - timedelta(days=30),
            created_by=manager,
        ),
    }
    return made


def _codes(rows: list[dict[str, Any]]) -> list[str]:
    return [r["cohort_code"] for r in rows]


# ---------------------------------------------------------------------------
# The default order
# ---------------------------------------------------------------------------
def test_the_register_leads_with_the_work_and_ends_with_what_is_closed(
    manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    ``-created_at`` ranked by the day a draft was OPENED — the one date nobody
    waits on. A file whose ministry window shut tomorrow could sit at the
    bottom of a register with no pager.
    """
    rows = mohe_service.list_submissions(actor=manager)

    assert _codes(rows) == [
        "CO-ORD-REJ",  # needs resubmitting (BR-014)
        "CO-ORD-SOONEST",  # window closing, nearest first
        "CO-ORD-SOON",
        "CO-ORD-SENT",  # waiting on the ministry
        "CO-ORD-DRAFT",
        "CO-ORD-OPEN",  # room left
        "CO-ORD-GONE",  # already shut
    ]


def test_the_waiting_are_ordered_oldest_first_and_the_closed_newest_first(
    manager: User, make_cohort: Any
) -> None:
    """
    Two groups, two directions — which is why the order needs two keys.

    The file that has waited longest on the ministry is the one to chase; the
    window that shut most recently is the one still worth asking about.
    """
    today = _as_of()
    for days in (10, 60, 35):
        _file(
            cohort=make_cohort(code=f"CO-W{days}"),
            status=MoheStatus.SUBMITTED,
            submitted=today - timedelta(days=days),
            created_by=manager,
        )
    for days in (5, 90, 40):
        _file(
            cohort=make_cohort(code=f"CO-X{days}"),
            status=MoheStatus.APPROVED,
            deadline=today - timedelta(days=days),
            created_by=manager,
        )

    codes = _codes(mohe_service.list_submissions(actor=manager))

    assert codes[:3] == ["CO-W60", "CO-W35", "CO-W10"]
    assert codes[3:] == ["CO-X5", "CO-X40", "CO-X90"]


def test_an_approved_file_with_no_window_recorded_is_not_treated_as_expired(
    manager: User, make_cohort: Any
) -> None:
    """Nothing has run out on it — it sits with the open ones, after them."""
    today = _as_of()
    _file(
        cohort=make_cohort(code="CO-NULL"),
        status=MoheStatus.APPROVED,
        deadline=None,
        created_by=manager,
    )
    _file(
        cohort=make_cohort(code="CO-SHUT"),
        status=MoheStatus.APPROVED,
        deadline=today - timedelta(days=2),
        created_by=manager,
    )

    assert _codes(mohe_service.list_submissions(actor=manager)) == ["CO-NULL", "CO-SHUT"]


# ---------------------------------------------------------------------------
# Sorting by a named column
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("direction, first", [("asc", "CO-W60"), ("desc", "CO-W10")])
def test_a_named_sort_replaces_the_priority_order(
    manager: User, make_cohort: Any, direction: str, first: str
) -> None:
    today = _as_of()
    for days in (10, 60):
        _file(
            cohort=make_cohort(code=f"CO-W{days}"),
            status=MoheStatus.SUBMITTED,
            submitted=today - timedelta(days=days),
            created_by=manager,
        )

    rows = mohe_service.list_submissions(
        actor=manager, sort="submitted_on", direction=direction
    )
    assert _codes(rows)[0] == first


def test_a_sort_nobody_defined_is_ignored_rather_than_refused(
    manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """It reaches the service off the URL, so it must answer, not raise."""
    invented = mohe_service.list_submissions(actor=manager, sort="; DROP TABLE", direction="up")
    assert _codes(invented) == _codes(mohe_service.list_submissions(actor=manager))


def test_a_sorted_column_puts_the_rows_that_have_no_value_last(
    manager: User, make_cohort: Any
) -> None:
    """A draft has no decision date, and «no answer yet» is not «earliest»."""
    _file(cohort=make_cohort(code="CO-NODATE"), status=MoheStatus.DRAFT, created_by=manager)
    _file(
        cohort=make_cohort(code="CO-DATED"),
        status=MoheStatus.SUBMITTED,
        submitted=_as_of() - timedelta(days=5),
        created_by=manager,
    )

    for direction in ("asc", "desc"):
        rows = mohe_service.list_submissions(
            actor=manager, sort="decided_on", direction=direction
        )
        assert _codes(rows)[-1] == "CO-NODATE", direction


# ---------------------------------------------------------------------------
# Narrowing by the deadline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "narrowing, expected",
    [
        ("soon", ["CO-ORD-SOONEST", "CO-ORD-SOON"]),
        ("expired", ["CO-ORD-GONE"]),
        ("open", ["CO-ORD-OPEN"]),
        ("", None),
        ("whatever", None),
    ],
)
def test_the_deadline_filter_narrows_by_the_same_edges_the_chips_use(
    manager: User,
    six_states: dict[str, MoheSubmission],
    narrowing: str,
    expected: list[str] | None,
) -> None:
    rows = mohe_service.list_submissions(actor=manager, deadline=narrowing)

    if expected is None:
        assert len(rows) == 7, "an unknown value narrows nothing"
    else:
        assert _codes(rows) == expected


def test_no_unapproved_file_hides_inside_the_deadline_filter(
    manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    A file the ministry has not approved has no window at all.

    «مفتوحة» reads as «room left», and a draft slipping in under it would say
    a registration is open on a cohort BR-013 forbids registering onto.
    """
    for narrowing in ("soon", "expired", "open"):
        rows = mohe_service.list_submissions(actor=manager, deadline=narrowing)
        assert {r["status"] for r in rows} <= {MoheStatus.APPROVED}, narrowing


def test_the_filter_and_the_chip_agree_on_every_row(
    manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """One edge, computed in SQL for the filter and in Python for the chip."""
    for narrowing in ("soon", "expired", "open"):
        rows = mohe_service.list_submissions(actor=manager, deadline=narrowing)
        assert {r["deadline_stage"] for r in rows} == {narrowing}, narrowing


def test_the_stage_is_a_word_and_says_none_where_the_question_does_not_arise(
    manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    rows = mohe_service.list_submissions(actor=manager)
    stages = {r["cohort_code"]: r["deadline_stage"] for r in rows}

    assert stages["CO-ORD-DRAFT"] == "none"
    assert stages["CO-ORD-REJ"] == "none"
    assert stages["CO-ORD-SENT"] == "none"
    assert stages["CO-ORD-SOONEST"] == "soon"
    assert stages["CO-ORD-OPEN"] == "open"
    assert stages["CO-ORD-GONE"] == "expired"
    assert set(stages.values()) <= set(mohe_service.DEADLINE_STAGES)


# ---------------------------------------------------------------------------
# The cost of the page
# ---------------------------------------------------------------------------
def test_the_register_costs_the_same_whether_it_draws_one_file_or_many(
    manager: User, registrar: User, make_cohort: Any, approve_cohort: Any, make_enrollment: Any
) -> None:
    """
    The counts used to be read off ``Cohort.enrolled_count``, a property that
    queries: one file per row meant one query per row, and this register has
    no pager. Both tallies now ride the page's own query.
    """
    from apps.operations.services import enrollment_service

    first = make_cohort(code="CO-N1")
    approve_cohort(first, course_number="M-N1")
    enrolment = make_enrollment(first, index=301)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrolment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrolment)

    with CaptureQueriesContext(connection) as one_row:
        rows = mohe_service.list_submissions(actor=manager)
    assert len(rows) == 1

    for n in range(2, 8):
        cohort = make_cohort(code=f"CO-N{n}")
        approve_cohort(cohort, course_number=f"M-N{n}")
        for index in range(310 + n * 5, 313 + n * 5):
            extra = make_enrollment(cohort, index=index)
            enrollment_service.record_voucher(actor=registrar, enrollment=extra)
            enrollment_service.approve_enrollment(actor=manager, enrollment=extra)

    with CaptureQueriesContext(connection) as many_rows:
        rows = mohe_service.list_submissions(actor=manager)
    assert len(rows) == 7

    assert len(many_rows) == len(one_row), (
        f"{len(one_row)} queries for one file and {len(many_rows)} for seven — "
        "the count is being read a row at a time again"
    )


def test_both_tallies_are_right_and_are_not_the_same_number(
    manager: User, registrar: User, make_cohort: Any, approve_cohort: Any, make_enrollment: Any
) -> None:
    """
    The column counts seats taken; the link counts names that may be handed to
    the ministry. A booked-but-unapproved enrolment is in the first and not
    the second, which is the whole reason there are two numbers on the row.
    """
    from apps.operations.services import enrollment_service

    cohort = make_cohort(code="CO-TALLY")
    approve_cohort(cohort, course_number="M-TALLY")
    approved = make_enrollment(cohort, index=401)
    enrollment_service.record_voucher(actor=registrar, enrollment=approved)
    enrollment_service.approve_enrollment(actor=manager, enrollment=approved)
    make_enrollment(cohort, index=402)  # booked, nobody has approved it

    (row,) = mohe_service.list_submissions(actor=manager)

    assert row["enrolled_count"] == 2
    assert row["approved_count"] == 1


def test_the_single_file_read_still_answers_both_counts(
    manager: User, registrar: User, make_cohort: Any, approve_cohort: Any, make_enrollment: Any
) -> None:
    """``get_submission`` annotates nothing, so the row must fall back."""
    from apps.operations.services import enrollment_service

    cohort = make_cohort(code="CO-ONE")
    submission = approve_cohort(cohort, course_number="M-ONE")
    enrolment = make_enrollment(cohort, index=403)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrolment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrolment)
    make_enrollment(cohort, index=404)

    detail = mohe_service.get_submission(actor=manager, submission_id=submission.pk)

    assert detail["enrolled_count"] == 2
    assert detail["approved_count"] == 1


# ---------------------------------------------------------------------------
# What a decision may say about its own dates
#
# Both layers are tested apart on purpose. The form is the courtesy — it puts
# the refusal beside the field before anything is written. The service is the
# rule — it holds for the seed command, the shell and any caller that never
# saw a form. A test that only pressed the button would not notice the day
# one of them was removed.
# ---------------------------------------------------------------------------
SENT_ON = date(2026, 9, 1)
DECIDED = date(2026, 9, 10)


def _decision(**overrides: Any) -> dict[str, Any]:
    data = {
        "decided_on": DECIDED.isoformat(),
        "mohe_course_number": "M-2026-1",
        "registration_deadline": (DECIDED + timedelta(days=30)).isoformat(),
        "rejection_reason_ar": "",
    }
    data.update(overrides)
    return data


@pytest.fixture
def awaiting(make_cohort, registrar, manager, attach_required_documents) -> MoheSubmission:
    """One file that actually left the centre, so «لا يسبق» has a date to hold."""
    submission = mohe_service.create_submission(
        actor=registrar,
        cohort=make_cohort(code="CO-DATES"),
        data={"training_axes_ar": "محاور", "target_audience_ar": "الفئة"},
    )
    attach_required_documents(submission, registrar)
    return mohe_service.submit_to_mohe(
        actor=manager, submission=submission, submitted_on=SENT_ON
    )


# --- the form ---------------------------------------------------------------
def test_the_form_refuses_an_approval_with_no_registration_window() -> None:
    from apps.operations.forms import MoheDecisionForm

    form = MoheDecisionForm(
        _decision(registration_deadline=""), approved=True, submitted_on=SENT_ON
    )

    assert not form.is_valid()
    assert "BR-019" in str(form.errors["registration_deadline"])


@pytest.mark.parametrize("offset", [0, -1, -30])
def test_the_form_refuses_a_window_that_shuts_on_or_before_the_decision(offset: int) -> None:
    """
    Zero days is the case the demo actually holds, and it is the worst of the
    three: it looks like a real date, and the row reads «معتمد» beside
    «انتهت المهلة» from the minute it is saved.
    """
    from apps.operations.forms import MoheDecisionForm

    form = MoheDecisionForm(
        _decision(registration_deadline=(DECIDED + timedelta(days=offset)).isoformat()),
        approved=True,
        submitted_on=SENT_ON,
    )

    assert not form.is_valid()
    assert "بعد تاريخ القرار" in str(form.errors["registration_deadline"])


def test_the_form_refuses_a_decision_dated_before_the_file_was_sent() -> None:
    from apps.operations.forms import MoheDecisionForm

    form = MoheDecisionForm(
        _decision(decided_on=(SENT_ON - timedelta(days=1)).isoformat()),
        approved=True,
        submitted_on=SENT_ON,
    )

    assert not form.is_valid()
    assert "لا يسبق" in str(form.errors["decided_on"])


def test_a_rejection_needs_no_window_at_all() -> None:
    """Nothing was approved, so nothing is open to register onto."""
    from apps.operations.forms import MoheDecisionForm

    form = MoheDecisionForm(
        _decision(
            registration_deadline="", mohe_course_number="", rejection_reason_ar="لم تكفِ المحاور"
        ),
        approved=False,
        submitted_on=SENT_ON,
    )

    assert form.is_valid(), form.errors


def test_the_form_reports_every_refusal_at_once_not_one_per_round_trip() -> None:
    """
    C-16 and BR-019 both apply to an empty approval, and the POST redirects —
    so a form that reported one of them would send the typist round again to
    be told the other.
    """
    from apps.operations.forms import MoheDecisionForm

    form = MoheDecisionForm(
        _decision(mohe_course_number="", registration_deadline=""),
        approved=True,
        submitted_on=SENT_ON,
    )

    assert not form.is_valid()
    assert "C-16" in str(form.errors["mohe_course_number"])
    assert "BR-019" in str(form.errors["registration_deadline"])


# --- the service ------------------------------------------------------------
def test_the_service_refuses_the_same_three_with_no_form_in_sight(
    manager: User, awaiting: MoheSubmission
) -> None:
    """The rule has to hold for a seed command and a shell, not only a button."""
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="BR-019"):
        mohe_service.record_decision(
            actor=manager,
            submission=awaiting,
            approved=True,
            decided_on=DECIDED,
            mohe_course_number="M-X",
            registration_deadline=None,
        )
    with pytest.raises(ValidationError, match="بعد تاريخ القرار"):
        mohe_service.record_decision(
            actor=manager,
            submission=awaiting,
            approved=True,
            decided_on=DECIDED,
            mohe_course_number="M-X",
            registration_deadline=DECIDED,
        )
    with pytest.raises(ValidationError, match="لا يسبق"):
        mohe_service.record_decision(
            actor=manager,
            submission=awaiting,
            approved=True,
            decided_on=SENT_ON - timedelta(days=1),
            mohe_course_number="M-X",
            registration_deadline=DECIDED + timedelta(days=30),
        )

    awaiting.refresh_from_db()
    assert awaiting.status == MoheStatus.SUBMITTED, "nothing was written by a refused call"


def test_a_sound_approval_still_goes_through(
    manager: User, awaiting: MoheSubmission
) -> None:
    """The guard must refuse the nonsense and nothing else."""
    saved = mohe_service.record_decision(
        actor=manager,
        submission=awaiting,
        approved=True,
        decided_on=DECIDED,
        mohe_course_number="M-2026-9",
        registration_deadline=DECIDED + timedelta(days=1),
    )

    assert saved.status == MoheStatus.APPROVED
    assert saved.registration_deadline == DECIDED + timedelta(days=1)


def test_the_screen_shows_the_refusal_it_was_given_not_a_stock_sentence(
    client: Any, manager: User, awaiting: MoheSubmission
) -> None:
    """
    The POST redirects, so the bound form never reaches the page: whatever is
    wrong, the typist used to read «تاريخ القرار مطلوب».
    """
    from django.urls import reverse

    client.force_login(manager)
    response = client.post(
        reverse("operations:mohe-detail", args=[awaiting.pk]),
        {"action": "approve", **_decision(registration_deadline="", mohe_course_number="")},
        follow=True,
    )

    said = " ".join(str(m) for m in response.context["messages"])
    assert "C-16" in said
    assert "BR-019" in said
    assert "تاريخ القرار مطلوب" not in said
    awaiting.refresh_from_db()
    assert awaiting.status == MoheStatus.SUBMITTED


# ---------------------------------------------------------------------------
# What the register draws once the order and the filters reached it
# ---------------------------------------------------------------------------
def test_the_sortable_headers_announce_what_they_are_doing(
    client: Any, manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    `aria-sort` belongs on the header that IS sorted and on no other — «none»
    everywhere would tell a screen reader the table is unsorted while it is
    not, and «ascending» on three columns at once is simply false.
    """
    from django.urls import reverse

    register = reverse("operations:mohe")
    client.force_login(manager)

    columns = client.get(register, {"sort": "decided_on", "dir": "desc"}).context["sort_columns"]

    assert columns["decided_on"]["aria"] == "descending"
    assert columns["decided_on"]["arrow"] == "↓"
    assert columns["submitted_on"]["aria"] == "none"
    assert columns["registration_deadline"]["aria"] == "none"
    # Pressing the sorted column reverses it; pressing another starts it
    # ascending, because «oldest first» is what a reader means by a date
    # column they have just opened.
    assert columns["decided_on"]["url"].endswith("sort=decided_on&dir=asc")
    assert columns["submitted_on"]["url"].endswith("sort=submitted_on&dir=asc")


def test_a_sorted_header_keeps_the_filter_that_was_already_on(
    client: Any, manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """Pressing a column must not quietly widen the list under the reader."""
    from django.urls import reverse

    client.force_login(manager)
    response = client.get(reverse("operations:mohe"), {"status": "APPROVED", "q": "CO-ORD"})

    for column in response.context["sort_columns"].values():
        assert "status=APPROVED" in column["url"]
        assert "q=CO-ORD" in column["url"]


def test_the_deadline_filter_is_offered_beside_the_status_and_names_itself(
    client: Any, manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    The deadline is not a fifth status: «معتمد» and «قاربت» sit on one row,
    and one list mixing them would make either unsearchable inside the other.
    """
    from django.urls import reverse

    client.force_login(manager)
    response = client.get(reverse("operations:mohe"), {"deadline": "expired"})
    page = response.content.decode()

    assert 'name="deadline"' in page
    assert 'id="f_deadline"' in page
    assert _codes(response.context["submissions"]) == ["CO-ORD-GONE"]
    # The narrowed register says so, naming the filter rather than the code.
    assert ("المهلة", "انتهت المهلة") in [
        (str(label), str(value)) for label, value in response.context["active_filters"]
    ]


def test_an_approved_file_whose_window_shut_is_not_drawn_green(
    client: Any, manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    «معتمد» in green beside «انتهت» in red told the reader two opposite things
    about one row. The file IS still approved — it simply accepts no more
    names — so the chip goes neutral rather than red, and says both.
    """
    from django.urls import reverse

    client.force_login(manager)
    page = client.get(reverse("operations:mohe"), {"deadline": "expired"}).content.decode()

    assert '<span class="chip closed dot">معتمد · مغلق</span>' in page
    assert '<span class="chip ok dot">معتمد</span>' not in page


def test_the_resubmission_row_links_to_the_file_it_answers(
    client: Any,
    manager: User,
    registrar: User,
    make_cohort: Any,
    attach_required_documents: Any,
) -> None:
    """
    It used to say «إعادة إرسال» on a chip and not say WHICH file — so the
    rejection and the answer to it could not be read as one story.
    """
    from django.urls import reverse

    cohort = make_cohort(code="CO-CHAIN")
    first = mohe_service.create_submission(
        actor=registrar, cohort=cohort, data={"training_axes_ar": "محاور"}
    )
    attach_required_documents(first, registrar)
    mohe_service.submit_to_mohe(actor=manager, submission=first, submitted_on=SENT_ON)
    mohe_service.record_decision(
        actor=manager,
        submission=first,
        approved=False,
        decided_on=DECIDED,
        rejection_reason_ar="لم تكفِ المحاور",
    )
    again = mohe_service.resubmit(
        actor=registrar, rejected=first, data={"training_axes_ar": "محاور أوسع"}
    )

    client.force_login(manager)
    page = client.get(reverse("operations:mohe")).content.decode()

    assert reverse("operations:mohe-detail", args=[first.pk]) in page
    assert reverse("operations:mohe-detail", args=[again.pk]) in page
    assert "إعادة إرسال لـ" in page


def test_the_rejection_reason_carries_the_date_it_was_given(
    client: Any, manager: User, make_cohort: Any
) -> None:
    """``decided_on`` is the decision date for both outcomes — no second field."""
    from django.urls import reverse

    rejected = _file(
        cohort=make_cohort(code="CO-WHEN"),
        status=MoheStatus.REJECTED,
        submitted=DECIDED - timedelta(days=5),
        created_by=manager,
    )

    client.force_login(manager)
    page = client.get(reverse("operations:mohe")).content.decode()

    assert rejected.rejection_reason_ar in page
    assert rejected.decided_on.strftime("%Y/%m/%d") in page


def test_the_export_is_one_control_that_says_what_it_downloads(
    client: Any, manager: User, six_states: dict[str, MoheSubmission]
) -> None:
    """
    Three buttons in a row above a table read as tabs that filter it. They
    are downloads, and the names they offer are of trainees, not of files.
    """
    from django.urls import reverse

    client.force_login(manager)
    page = client.get(reverse("operations:mohe")).content.decode()

    assert "تصدير أسماء المتدربين" in page
    assert 'aria-haspopup="menu"' in page
    assert "تشمل الدفعات المعتمدة فقط، وتتبع البحث والفلتر الحاليين." in page
    for scope in ("pending", "uploaded", "all"):
        assert f"scope={scope}" in page
