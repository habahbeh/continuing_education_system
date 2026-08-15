"""
The daily job — WORKFLOWS §1.2 T4/T5 and BR-015.

Sprint 5 built the ``is_payment_overdue()`` predicate and deliberately stopped
there. This is the Sprint 6 half that acts on it, and these tests exist mostly
to prove it ACTS on the predicate rather than reimplementing the rule: two
copies of "what counts as overdue" would drift, and the one that drifts
decides what a partner earns (BR-045).

⚠️ Q-16 remains an assumption, so the sweep is reversible in both directions
and every move it makes is a readable history row.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.core.models import AuditEvent
from apps.operations.models import EnrollmentStatus, EnrollmentStatusHistory
from apps.operations.services import enrollment_service
from apps.operations.services.daily_service import run_daily

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)


@pytest.fixture
def active_enrolment(
    make_cohort, approve_cohort, make_enrollment, charge_and_pay, registrar, manager
):
    """An approved, active enrolment with whatever balance the test wants."""

    def _make(paid: str = "100.000", index: int = 1, code: str = "CO-DAILY"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount=paid)
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
        enrollment.refresh_from_db()
        return enrollment

    return _make


@pytest.fixture
def system_admin(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="sys.daily", password="probe-password-1234", role=Role.SYSTEM_ADMINISTRATOR
    )


# ---------------------------------------------------------------------------
# T4 / T5 — the sweep
# ---------------------------------------------------------------------------
def test_a_balance_past_the_grace_period_is_marked_overdue(active_enrolment, system_admin) -> None:
    enrollment = active_enrolment(paid="100.000")  # 270 charged, 170 owed
    report = run_daily(actor=system_admin, as_of=TERM_START + timedelta(days=31))

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE
    assert enrollment.code in report.marked_overdue
    assert report.grace_days == 30


def test_nothing_moves_inside_the_grace_period(active_enrolment, system_admin) -> None:
    enrollment = active_enrolment(paid="100.000")
    report = run_daily(actor=system_admin, as_of=TERM_START + timedelta(days=10))

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert report.marked_overdue == []


def test_a_settled_enrolment_is_restored_to_active(
    active_enrolment, system_admin, cashier, cash_method
) -> None:
    """
    T5 — the sweep runs both ways.

    A one-way job would leave a participant who paid up permanently flagged,
    and BR-045 would keep the partner's entitlement suppressed for someone who
    owes nothing.
    """
    from apps.cashbox.services import payment_service

    enrollment = active_enrolment(paid="100.000")
    late = TERM_START + timedelta(days=31)
    run_daily(actor=system_admin, as_of=late)
    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE

    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("170.000"),
        payment_method=cash_method,
        received_on=late,
    )
    report = run_daily(actor=system_admin, as_of=late + timedelta(days=1))

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert enrollment.code in report.restored_to_active


def test_a_manual_override_is_never_cleared_by_the_job(
    active_enrolment, system_admin, manager
) -> None:
    """
    Q-16 — the finance officer outranks the arithmetic.

    An enrolment flagged by a human with a stated reason ("bounced cheque")
    stays flagged even though the ledger shows nothing owed. A job silently
    clearing that would be the job overruling a person.
    """
    enrollment = active_enrolment(paid="270.000")  # fully settled
    enrollment_service.change_status(
        actor=manager,
        enrollment=enrollment,
        to_status=EnrollmentStatus.PAYMENT_OVERDUE,
        reason_ar="شيك مرتجع — أُبلغ المشارك بتاريخ 2026/09/25",
    )

    report = run_daily(actor=system_admin, as_of=TERM_START + timedelta(days=40))

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE
    assert enrollment.code in report.skipped_manual_override
    assert enrollment.code not in report.restored_to_active
    assert "شيك مرتجع" in enrollment.status_note_ar


def test_the_job_does_not_touch_a_withdrawn_enrolment(
    active_enrolment, system_admin, manager
) -> None:
    """
    A documented human decision is not the job's business.

    Sweeping a WITHDRAWN enrolment into PAYMENT_OVERDUE would overwrite
    somebody's recorded decision with an arithmetic one.
    """
    enrollment = active_enrolment(paid="100.000")
    enrollment_service.change_status(
        actor=manager,
        enrollment=enrollment,
        to_status=EnrollmentStatus.WITHDRAWN,
        reason_ar="انسحاب موثّق",
    )

    run_daily(actor=system_admin, as_of=TERM_START + timedelta(days=60))

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.WITHDRAWN


def test_the_job_uses_the_sprint_5_predicate_rather_than_its_own_rule(
    active_enrolment, system_admin
) -> None:
    """
    One definition of overdue, not two.

    Changing the setting changes the job's behaviour with no code change,
    which is only true if the job is reading the predicate.
    """
    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting
    from apps.settlements.services import assumptions

    enrollment = active_enrolment(paid="100.000")
    as_of = TERM_START + timedelta(days=20)

    assert run_daily(actor=system_admin, as_of=as_of).marked_overdue == []

    close_setting(assumptions.OVERDUE_DAYS_KEY, effective_to=date(2026, 1, 1))
    set_setting(
        assumptions.OVERDUE_DAYS_KEY,
        14,
        value_type=SettingValueType.INTEGER,
        effective_from=date(2026, 1, 2),
        note="اختبار",
    )

    report = run_daily(actor=system_admin, as_of=as_of)
    assert enrollment.code in report.marked_overdue
    assert report.grace_days == 14


def test_every_move_writes_history_and_an_audit_row(active_enrolment, system_admin) -> None:
    """ "The system did it overnight" still has to be a row someone can read."""
    enrollment = active_enrolment(paid="100.000")
    as_of = TERM_START + timedelta(days=31)
    run_daily(actor=system_admin, as_of=as_of)

    history = EnrollmentStatusHistory.objects.filter(
        enrollment=enrollment, to_status=EnrollmentStatus.PAYMENT_OVERDUE
    ).first()
    assert history is not None
    assert history.changed_by_id == system_admin.pk
    assert history.reference == f"DAILY-{as_of:%Y%m%d}"

    assert AuditEvent.objects.filter(
        entity_type="operations.Enrollment", summary_ar__contains="مهمة يومية"
    ).exists()


def test_the_report_carries_the_ministry_deadline_alerts(
    make_cohort, approve_cohort, system_admin
) -> None:
    """BR-015 — one nightly run answers both questions."""
    cohort = make_cohort("SC-NET", code="CO-DEADLINE")
    approve_cohort(cohort, deadline=date(2026, 10, 5))

    report = run_daily(actor=system_admin, as_of=date(2026, 9, 25))
    assert [a["cohort_code"] for a in report.deadline_alerts] == [cohort.code]


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
def test_the_command_runs_and_reports(active_enrolment, system_admin) -> None:
    enrollment = active_enrolment(paid="100.000")
    out = StringIO()
    call_command(
        "run_daily_operations",
        "--as-of",
        "2026-10-25",
        "--actor",
        system_admin.username,
        stdout=out,
    )

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.PAYMENT_OVERDUE
    assert enrollment.code in out.getvalue()


def test_a_dry_run_changes_nothing(active_enrolment, system_admin) -> None:
    """
    Seeing what a changed assumption WOULD do, before it does it.

    Q-16 is unsettled, so previewing the sweep is the difference between a
    reversible decision and an overnight surprise.
    """
    enrollment = active_enrolment(paid="100.000")
    out = StringIO()
    call_command(
        "run_daily_operations",
        "--as-of",
        "2026-10-25",
        "--actor",
        system_admin.username,
        "--dry-run",
        stdout=out,
    )

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert "[تجريبي]" in out.getvalue()
    assert not EnrollmentStatusHistory.objects.filter(
        to_status=EnrollmentStatus.PAYMENT_OVERDUE
    ).exists()


def test_the_command_refuses_a_malformed_date(system_admin) -> None:
    with pytest.raises(CommandError):
        call_command("run_daily_operations", "--as-of", "25-10-2026")


def test_the_command_refuses_to_run_unattributed(seeded_settings) -> None:
    """
    Every status change names who made it.

    A job with no attributable actor would write history rows nobody can be
    asked about.
    """
    with pytest.raises(CommandError, match="مدير النظام"):
        call_command("run_daily_operations", "--as-of", "2026-10-25")


def test_the_sprint_6_job_now_exists(system_admin) -> None:
    """
    The counterpart to Sprint 5's guard, which asserted its ABSENCE.

    That guard was correct while Sprint 5 owned only the predicate. Sprint 6
    owns the job, so the assertion inverts rather than being deleted — the
    scope claim stays checked in both directions.
    """
    from pathlib import Path

    commands = Path(__file__).resolve().parents[3] / "apps"
    names = {path.name for path in commands.rglob("management/commands/*.py")}
    assert "run_daily_operations.py" in names
