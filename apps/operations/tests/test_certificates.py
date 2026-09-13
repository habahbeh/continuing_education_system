"""
Certificates — T-141 … T-148, BR-075 … BR-079, C-08 · C-20.

🐞 **The demo's loophole, which is the reason C-08 exists.** Certificate
``2026000002`` was issued as a REPLACEMENT with neither a clearance nor an
original certificate to replace. Ticking one box walked straight past BR-075,
the rule the whole clearance workflow exists to enforce.

⏳ **Not here, and deliberately:** the printed certificate itself. Q-24 is
open — the approved paper forms carrying the quality-system form numbers have
not arrived — so `T-149` and `T-169` … `T-177` stay **DEFERRED to the
consolidated UI pass**, not passed. What IS testable is that the document's
content is snapshotted at issue, and that is asserted below.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.core.models import AuditEvent
from apps.operations.models import (
    Certificate,
    CertificateStatus,
    GradeSource,
)
from apps.operations.services import certificate_service, clearance_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
ISSUE_DAY = date(2026, 12, 21)


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fim.cert", password="probe-password-1234", role=Role.FINANCE_MANAGER
    )


@pytest.fixture
def cleared(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
    finance_manager,
):
    """A participant all the way through a completed clearance."""

    def _make(index: int = 1, code: str = "CO-CERT", complete: bool = True):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount="270.000")
        finish_enrollment(enrollment)

        clearance = clearance_service.open_clearance(
            actor=manager,
            enrollment=enrollment,
            opened_on=TERM_START,
            code=f"CLR-{code}",
        )
        clearance_service.complete_custody_step(
            actor=manager,
            clearance=clearance,
            custody_items=[{"name_ar": "هوية المركز", "returned": True}],
        )
        if not complete:
            return enrollment, clearance

        clearance_service.certify_finance_step(actor=finance, clearance=clearance)
        clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)
        clearance_service.complete_handover_step(
            actor=manager, clearance=clearance, participant_ack_name="سالم أحمد العمري"
        )
        clearance_service.close_clearance(actor=manager, clearance=clearance)
        return enrollment, clearance

    return _make


# ---------------------------------------------------------------------------
# T-141 — BR-075 · D-22
# ---------------------------------------------------------------------------
def test_no_certificate_without_a_completed_clearance(cleared, manager) -> None:
    """
    T-141 — the refusal the demo could not make, with its audit row.

    The gate runs before any transaction opens, so the denied attempt survives
    the raise (BR-100). A certificate is the document a participant shows an
    employer; issuing one to somebody who still owes money is the failure this
    whole sprint exists to prevent.
    """
    enrollment, _clearance = cleared(complete=False)

    with pytest.raises(certificate_service.ClearanceRequiredError):
        certificate_service.issue_certificate(
            actor=manager, enrollment=enrollment, grade="EXCELLENT", issued_on=ISSUE_DAY
        )

    event = AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="D-22").first()
    assert event is not None
    assert enrollment.code in event.summary_ar
    assert not Certificate.objects.exists()


def test_a_completed_clearance_lets_the_certificate_out(cleared, manager) -> None:
    enrollment, clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager,
        enrollment=enrollment,
        grade="EXCELLENT",
        issued_on=ISSUE_DAY,
        training_hours=60,
    )

    assert certificate.clearance_id == clearance.pk
    assert certificate.status == CertificateStatus.ISSUED
    assert certificate.grade_source == GradeSource.MANUAL


def test_a_blocked_clearance_is_not_a_completed_one(
    make_cohort,
    approve_cohort,
    make_enrollment,
    charge_and_pay,
    finish_enrollment,
    manager,
    finance,
) -> None:
    """The gate reads COMPLETED, not "a clearance exists"."""
    cohort = make_cohort("SC-NET", code="CO-BLK")
    approve_cohort(cohort, course_number="M-BLK")
    enrollment = make_enrollment(cohort, index=9)
    charge_and_pay(enrollment, amount="100.000")  # still owes 170
    finish_enrollment(enrollment)

    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        opened_on=TERM_START,
        code="CLR-BLK",
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )
    with pytest.raises(clearance_service.ClearanceBlockedError):
        clearance_service.certify_finance_step(actor=finance, clearance=clearance)

    with pytest.raises(certificate_service.ClearanceRequiredError):
        certificate_service.issue_certificate(
            actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
        )


# ---------------------------------------------------------------------------
# T-143 — C-08, the demo's replacement loophole
# ---------------------------------------------------------------------------
def test_a_replacement_without_an_original_is_refused_by_the_database(cleared, manager) -> None:
    """
    T-143 / C-08 — exactly the shape the demo stored as ``2026000002``.

    Asserted against the DATABASE: the demo's failure was that its service
    happily allowed it, so the service check alone would prove nothing.
    """
    enrollment, _clearance = cleared()

    with pytest.raises(IntegrityError), transaction.atomic():
        Certificate.objects.create(
            certificate_number="2026000099",
            participant=enrollment.participant,
            enrollment=enrollment,
            clearance=None,
            program_name_snapshot="هندسة الشبكات",
            grade="GOOD",
            issued_on=ISSUE_DAY,
            issued_by=manager,
            is_replacement=True,
            replaces=None,
        )


def test_an_original_certificate_without_a_clearance_is_refused(cleared, manager) -> None:
    """C-08's other half — a normal certificate must carry its clearance."""
    enrollment, _clearance = cleared()

    with pytest.raises(IntegrityError), transaction.atomic():
        Certificate.objects.create(
            certificate_number="2026000098",
            participant=enrollment.participant,
            enrollment=enrollment,
            clearance=None,
            program_name_snapshot="هندسة الشبكات",
            grade="GOOD",
            issued_on=ISSUE_DAY,
            issued_by=manager,
            is_replacement=False,
        )


def test_a_replacement_needs_the_fee_collected_not_merely_charged(
    cleared, manager, registrar, cashier, cash_method
) -> None:
    """
    T-144 / BR-038 — a replacement issued against an unpaid fee is a giveaway.

    The fee is raised, and the replacement is STILL refused until the money is
    actually in. "Charged" and "collected" are different facts, and this
    system's whole design turns on not conflating them.
    """
    from apps.billing.services import charge_service
    from apps.cashbox.services import payment_service

    enrollment, _clearance = cleared()
    original = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
    )

    with pytest.raises(certificate_service.ReplacementFeeNotCollectedError):
        certificate_service.issue_replacement(actor=manager, original=original, issued_on=ISSUE_DAY)

    charge_service.create_charge_line(
        actor=registrar,
        enrollment=enrollment,
        charge_type="EXTRA_FEE",
        description_ar="بدل فاقد شهادة",
        net_amount=Decimal("15.000"),
        charged_on=ISSUE_DAY,
    )
    with pytest.raises(certificate_service.ReplacementFeeNotCollectedError):
        certificate_service.issue_replacement(actor=manager, original=original, issued_on=ISSUE_DAY)

    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("15.000"),
        payment_method=cash_method,
        received_on=ISSUE_DAY,
    )
    replacement = certificate_service.issue_replacement(
        actor=manager, original=original, issued_on=ISSUE_DAY
    )

    assert replacement.is_replacement is True
    assert replacement.replaces_id == original.pk
    assert replacement.clearance_id is None
    assert replacement.replacement_fee_line is not None

    original.refresh_from_db()
    assert original.status == CertificateStatus.REPLACED


def test_a_replacement_carries_the_originals_content(
    cleared, manager, registrar, cashier, cash_method
) -> None:
    """It replaces a document; it does not restate what the document said."""
    from apps.billing.services import charge_service
    from apps.cashbox.services import payment_service

    enrollment, _clearance = cleared()
    original = certificate_service.issue_certificate(
        actor=manager,
        enrollment=enrollment,
        grade="VERY_GOOD",
        issued_on=ISSUE_DAY,
        training_hours=48,
    )
    charge_service.create_charge_line(
        actor=registrar,
        enrollment=enrollment,
        charge_type="EXTRA_FEE",
        description_ar="بدل فاقد",
        net_amount=Decimal("15.000"),
        charged_on=ISSUE_DAY,
    )
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("15.000"),
        payment_method=cash_method,
        received_on=ISSUE_DAY,
    )
    replacement = certificate_service.issue_replacement(
        actor=manager, original=original, issued_on=ISSUE_DAY
    )

    assert replacement.grade == original.grade
    assert replacement.training_hours == original.training_hours
    assert replacement.program_name_snapshot == original.program_name_snapshot
    assert replacement.certificate_number != original.certificate_number


# ---------------------------------------------------------------------------
# T-145 · T-147 — BR-076 · C-20, the number
# ---------------------------------------------------------------------------
def test_the_number_is_the_year_then_a_gapless_sequence(cleared, manager) -> None:
    """T-145 — two issues, two numbers, no gap between them."""
    first, _c1 = cleared(index=1, code="CO-N1")
    second, _c2 = cleared(index=2, code="CO-N2")

    one = certificate_service.issue_certificate(
        actor=manager, enrollment=first, grade="GOOD", issued_on=ISSUE_DAY
    )
    two = certificate_service.issue_certificate(
        actor=manager, enrollment=second, grade="GOOD", issued_on=ISSUE_DAY
    )

    assert one.certificate_number == "2026000001"
    assert two.certificate_number == "2026000002"
    assert len(one.certificate_number) == 10


def test_a_malformed_number_is_refused_by_the_database(cleared, manager) -> None:
    """
    T-147 / C-20 — ten digits, and TWO mechanisms enforce it.

    Shape (punctuation, letters, too few digits) is the CHECK constraint;
    too many digits never reaches it because the column is ``varchar(10)``.
    Both are database refusals, and distinguishing them keeps the test honest
    about which line actually holds.
    """
    from django.db import DataError

    enrollment, clearance = cleared()

    def _create(number: str) -> None:
        Certificate.objects.create(
            certificate_number=number,
            participant=enrollment.participant,
            enrollment=enrollment,
            clearance=clearance,
            program_name_snapshot="س",
            grade="GOOD",
            issued_on=ISSUE_DAY,
            issued_by=manager,
        )

    for bad in ("2026-0001", "202600001", "ABCD000001", "202600000a"):
        with pytest.raises(IntegrityError), transaction.atomic():
            _create(bad)

    with pytest.raises(DataError), transaction.atomic():
        _create("20260000012")


def test_the_number_is_unique(cleared, manager) -> None:
    first, _c1 = cleared(index=1, code="CO-U1")
    second, c2 = cleared(index=2, code="CO-U2")
    one = certificate_service.issue_certificate(
        actor=manager, enrollment=first, grade="GOOD", issued_on=ISSUE_DAY
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Certificate.objects.create(
            certificate_number=one.certificate_number,
            participant=second.participant,
            enrollment=second,
            clearance=c2,
            program_name_snapshot="س",
            grade="GOOD",
            issued_on=ISSUE_DAY,
            issued_by=manager,
        )


# ---------------------------------------------------------------------------
# T-146 — a reprint is not a reissue
# ---------------------------------------------------------------------------
def test_a_reprint_changes_nothing_and_is_audited(cleared, manager) -> None:
    """
    T-146 / WORKFLOWS §7.5 — same number, same row, a new audit line.

    A reprint that renumbered would put two documents into the world each
    claiming to be the certificate.
    """
    enrollment, _clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
    )
    number = certificate.certificate_number

    certificate_service.record_reprint(actor=manager, certificate=certificate)
    certificate.refresh_from_db()

    assert certificate.certificate_number == number
    assert Certificate.objects.count() == 1

    event = AuditEvent.objects.filter(
        entity_type="operations.Certificate", summary_ar__contains="إعادة طباعة"
    ).first()
    assert event is not None and event.changes is not None
    assert event.changes["renumbered"] is False


# ---------------------------------------------------------------------------
# T-148 — BR-078, the grade
# ---------------------------------------------------------------------------
def test_the_grade_is_mandatory_and_the_system_never_computes_it(cleared, manager) -> None:
    """T-148 — there is no grades module and no exams entity, by design."""
    enrollment, _clearance = cleared()

    with pytest.raises(certificate_service.GradeRequiredError):
        certificate_service.issue_certificate(
            actor=manager, enrollment=enrollment, grade="  ", issued_on=ISSUE_DAY
        )

    from django.apps import apps as django_apps

    names = {model.__name__ for model in django_apps.get_models()}
    assert "Grade" not in names and "Exam" not in names


def test_the_grade_vocabulary_lives_in_a_setting_not_a_constraint(
    cleared, manager, seeded_settings
) -> None:
    """
    BR-078 lists four grades; the centre owns the list.

    Same decision as qualifications and cities (Q-31): adding a grade is data
    entry, not a migration, and there is no CHECK constraint on the column.
    """
    grades = certificate_service.available_grades(as_of=ISSUE_DAY)
    assert dict(grades)["EXCELLENT"] == "ممتاز"
    assert len(grades) == 4

    enrollment, _clearance = cleared()
    with pytest.raises(certificate_service.GradeRequiredError, match="غير معرَّف"):
        certificate_service.issue_certificate(
            actor=manager,
            enrollment=enrollment,
            grade="OUTSTANDING",
            issued_on=ISSUE_DAY,
        )


def test_a_new_grade_becomes_usable_by_changing_data(cleared, manager, seeded_settings) -> None:
    """The reversibility that makes the setting worth having."""
    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    close_setting(certificate_service.GRADES_KEY, effective_to=date(2026, 1, 1))
    set_setting(
        certificate_service.GRADES_KEY,
        '[["EXCELLENT","ممتاز"],["OUTSTANDING","امتياز مع مرتبة الشرف"]]',
        value_type=SettingValueType.STRING,
        effective_from=date(2026, 1, 2),
        note="اختبار",
    )

    enrollment, _clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="OUTSTANDING", issued_on=ISSUE_DAY
    )
    assert certificate.grade == "OUTSTANDING"


# ---------------------------------------------------------------------------
# BR-077 · BR-079 — the content
# ---------------------------------------------------------------------------
def test_the_programme_name_is_snapshotted(cleared, manager) -> None:
    """
    ADR-012 — the catalogue moves on; a document that left the building cannot.

    ⏳ The printed LAYOUT (BR-077's eight fields, BR-079's "no partner name")
    is Q-24 and belongs to the UI pass — `T-149`, `T-169` … `T-177` remain
    DEFERRED. What the document will print FROM is what is asserted here.
    """
    enrollment, _clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
    )
    original_name = certificate.program_name_snapshot

    program = enrollment.cohort.program
    program.name_ar = "هندسة الشبكات — تسمية جديدة"
    program.save(update_fields=["name_ar"])

    certificate.refresh_from_db()
    assert certificate.program_name_snapshot == original_name
    assert certificate.duration_text  # the dates, captured at issue


def test_issuing_is_audited_with_the_clearance_it_rested_on(cleared, manager) -> None:
    """WORKFLOWS §7.5 — the number, the participant, and the clearance reference."""
    enrollment, clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
    )

    event = AuditEvent.objects.filter(
        entity_type="operations.Certificate", reference=certificate.certificate_number
    ).first()
    assert event is not None and event.changes is not None
    assert event.changes["event"] == "ISSUE"
    assert event.changes["clearance"] == clearance.code
    assert event.changes["grade"] == "GOOD"


def test_delivery_records_when_it_changed_hands(cleared, manager) -> None:
    enrollment, _clearance = cleared()
    certificate = certificate_service.issue_certificate(
        actor=manager, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
    )
    certificate_service.deliver(
        actor=manager, certificate=certificate, delivered_on=date(2026, 12, 22)
    )

    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.DELIVERED
    assert certificate.delivered_on == date(2026, 12, 22)


def test_the_registrar_cannot_issue_a_certificate(cleared, registrar) -> None:
    """PERMISSIONS §3.6 row 31 — CREATE belongs to the centre manager alone."""
    from django.core.exceptions import PermissionDenied

    enrollment, _clearance = cleared()
    with pytest.raises(PermissionDenied):
        certificate_service.issue_certificate(
            actor=registrar, enrollment=enrollment, grade="GOOD", issued_on=ISSUE_DAY
        )


# ---------------------------------------------------------------------------
# Scope — BR-089 is Sprint 8's
# ---------------------------------------------------------------------------
def test_historical_certificate_numbering_is_not_built(cleared) -> None:
    """
    BR-089 gives migrated certificates their own counter scope.

    Named here so "deferred to Sprint 8" stays a checked fact. The archive
    models it would number do not exist yet either.
    """
    from django.apps import apps as django_apps

    names = {model.__name__ for model in django_apps.get_models()}
    assert "HistoricalCertificate" not in names
    assert certificate_service.CERTIFICATE_SCOPE == "certificate"
