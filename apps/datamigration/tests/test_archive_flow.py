"""
Reading, validating and archiving — and the defects the sheets actually carry.

Each test here names a real property of the delivered workbooks. The sample
workbook is built to carry them in miniature: a ``#REF!`` identity, a category
word where a number belongs, a hyphenated pair of numbers, one number under
two different names, a compound receipt reference, an overpayment, and a
subject grid that does not sum.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.datamigration.models import (
    BatchStatus,
    Finding,
    HistoricalParticipant,
    HistoricalPayment,
    MigrationRow,
    RowState,
)
from apps.datamigration.services import archive_service, batch_service, validation_service

pytestmark = pytest.mark.django_db


def _row(batch, source_row: int) -> MigrationRow:
    return batch.rows.get(source_row=source_row)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_a_workbook_is_read_as_text_and_nothing_more(manager, sample_workbook) -> None:
    """
    No number is parsed at this stage, and no blank becomes a zero.

    The raw layer is what lets a parsing decision be revisited later without
    the file, so it must not have made any decisions of its own.
    """
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R1")

    assert batch.status == BatchStatus.DRAFT
    assert batch.row_count == 8
    assert batch.sheet_count == 1

    row = _row(batch, 3)
    assert row.state == RowState.RAW
    assert row.raw_row["C"]["v"] == "202251004"
    assert row.raw_row["F"]["v"] == "1450"  # text, not 1450.0
    assert row.findings == []


def test_the_header_rows_are_kept_but_are_not_records(manager, sample_workbook) -> None:
    """
    Rows 1 and 2 are metadata, so they are not ``MigrationRow``.

    They still have to be somewhere: which columns hold subjects is decided by
    whether row 1 prices what row 2 names, and re-opening the workbook to find
    out would let the answer change between the dry run and the commit.
    """
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R2")

    assert batch.rows.filter(source_row__lte=2).count() == 0
    header = batch.sheet_headers["20221تناغم"]
    assert header["meta"]["C"]["v"] == "ادارة الاعمال"
    assert header["columns"]["C"]["v"] == "الرقم الجامعي"


def test_the_same_workbook_is_not_read_twice(manager, sample_workbook) -> None:
    """
    Idempotency, anchored on the file's bytes rather than its name.

    This is what makes the import command safe to run twice by accident, which
    is the way it will actually be run twice.
    """
    batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R3")

    with pytest.raises(batch_service.DuplicateSourceError, match="MB-R3"):
        batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R4")


def test_a_renamed_copy_is_still_the_same_workbook(manager, sample_workbook, tmp_path) -> None:
    """
    The fingerprint is of the content, so renaming the file changes nothing.

    The twin is a byte copy rather than a second ``Workbook().save()``: an
    xlsx carries its own creation timestamp, so re-saving the same data a
    second later produces different bytes. That would make this test pass or
    fail depending on how busy the machine was.
    """
    import shutil

    batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R5")
    twin = tmp_path / "renamed.xlsx"
    shutil.copyfile(sample_workbook, twin)

    with pytest.raises(batch_service.DuplicateSourceError):
        batch_service.import_workbook(actor=manager, path=twin, code="MB-R6")


def test_a_superseded_batch_frees_its_file_for_a_better_reading(
    manager, sample_workbook, workbook_factory
) -> None:
    """
    Correction is a new batch, never an edit. Both survive.

    A question about what the archive once said still has an answer, which is
    the whole reason nothing is deleted here.
    """
    first = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R7")
    other = batch_service.import_workbook(
        actor=manager, path=workbook_factory("other.xlsx", rows=[]), code="MB-R8"
    )
    batch_service.supersede(actor=manager, batch=first, successor=other)

    again = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-R9")

    first.refresh_from_db()
    assert first.status == BatchStatus.SUPERSEDED
    assert first.rows.count() == 8  # kept, not deleted
    assert again.row_count == 8


# ---------------------------------------------------------------------------
# The dry run
# ---------------------------------------------------------------------------
def test_the_dry_run_creates_no_archive_row(manager, sample_workbook) -> None:
    """«تشغيل تجريبي بلا كتابة فعلية» — and it means it."""
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V1")
    report = validation_service.validate(actor=manager, batch=batch)

    assert report["archivable"] > 0
    assert batch.participants.count() == 0
    assert batch.enrollments.count() == 0
    assert batch.payments.count() == 0
    assert batch.cohorts.count() == 0


def test_a_ref_error_is_reported_and_never_read_as_zero(manager, sample_workbook) -> None:
    """
    852 ``#REF!`` cells were delivered. A deleted reference is not a value.

    The row keeps the marker verbatim, is flagged, and — because the marker
    sits in the identity column — is set aside as unidentified rather than
    archived with an empty number.
    """
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V2")
    validation_service.validate(actor=manager, batch=batch)

    row = _row(batch, 7)
    assert row.raw_row["C"]["v"] == "#REF!"
    assert Finding.REF_ERROR in row.findings
    assert Finding.UNIDENTIFIED_NO_USABLE_NUMBER in row.findings
    assert row.state == RowState.UNIDENTIFIED


def test_a_category_word_in_the_number_column_is_unidentified(manager, sample_workbook) -> None:
    """«مركز», «جامعة», «خريج», «ط جامعة» — 43 rows of them in the real files."""
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V3")
    validation_service.validate(actor=manager, batch=batch)

    row = _row(batch, 8)
    assert row.state == RowState.UNIDENTIFIED
    assert Finding.NON_NUMERIC_IDENTITY in row.findings
    assert Finding.UNIDENTIFIED_NO_USABLE_NUMBER in row.findings
    assert "لا رقم جامعي صالح" in row.reason_ar


def test_the_report_reconciles_against_the_sheet(manager, sample_workbook) -> None:
    """
    ``read = archivable + unidentified + rejected``.

    Stated as a property rather than left implicit, because a migration that
    quietly loses rows looks exactly like one that had fewer rows to begin
    with.
    """
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V4")
    report = validation_service.validate(actor=manager, batch=batch)

    assert report["rows_read"] == 8
    assert report["archivable"] == 6
    assert report["unidentified"] == 2
    assert report["reconciles"] is True


def test_the_findings_describe_the_sheet_and_do_not_fix_it(manager, sample_workbook) -> None:
    """Each of the delivered defects, reported and left alone."""
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V5")
    validation_service.validate(actor=manager, batch=batch)

    assert Finding.SUBJECT_SUM_MISMATCH in _row(batch, 10).findings
    assert Finding.OVERPAYMENT in _row(batch, 9).findings
    assert Finding.NEGATIVE_BALANCE in _row(batch, 9).findings
    assert Finding.RECEIPT_REF_COMPOUND in _row(batch, 9).findings
    assert Finding.DATE_UNPARSEABLE in _row(batch, 10).findings

    # And the row that does not add up is still archivable — it is a real
    # record of something, not a broken one.
    assert _row(batch, 10).state == RowState.VALID


def test_validation_is_rerunnable_without_the_file(manager, sample_workbook) -> None:
    """Findings are recomputed from the raw layer, so they never double up."""
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-V6")
    first = validation_service.validate(actor=manager, batch=batch)
    second = validation_service.validate(actor=manager, batch=batch)

    assert first["findings"] == second["findings"]
    assert _row(batch, 7).findings == _row(batch, 7).findings


# ---------------------------------------------------------------------------
# Committing
# ---------------------------------------------------------------------------
def test_nothing_is_archived_before_it_is_validated(manager, sample_workbook) -> None:
    """The dry run is not advisory — it produces what the archiver agrees to."""
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-C1")

    with pytest.raises(archive_service.NotValidatedError):
        archive_service.commit(actor=manager, batch=batch)


def test_committing_twice_is_refused(manager, committed_batch) -> None:
    """D-27 · BR-087 — archived once. A second pass would double the archive."""
    with pytest.raises(archive_service.AlreadyCommittedError):
        archive_service.commit(actor=manager, batch=committed_batch)

    assert committed_batch.participants.count() == 6


def test_an_archived_batch_cannot_be_revalidated(manager, committed_batch) -> None:
    """Editing an archived record is refused; correction is a new batch."""
    with pytest.raises(batch_service.ArchivedBatchError, match="D-27"):
        validation_service.validate(actor=manager, batch=committed_batch)


def test_unidentified_rows_never_become_participants(manager, committed_batch) -> None:
    """
    They stay as ``MigrationRow`` — preserved, counted, and not people.

    Eight rows read, two without a usable number, six participants.
    """
    assert committed_batch.rows.count() == 8
    assert committed_batch.rows.filter(state=RowState.UNIDENTIFIED).count() == 2
    assert committed_batch.participants.count() == 6

    for row in committed_batch.rows.filter(state=RowState.UNIDENTIFIED):
        assert not hasattr(row, "historical_participant") or row.historical_participant is None


def test_a_hyphenated_cell_is_split_and_both_halves_are_kept(manager, committed_batch) -> None:
    """
    ``20155173-200920437`` — an old centre number and a university number.

    Eight cells in the delivered files look like this. Neither half is
    discarded, and the raw cell still holds the original text.
    """
    historical = committed_batch.participants.get(legacy_number="20155173")

    assert historical.legacy_alt_number == "200920437"
    assert historical.source_row.cell("C") == "20155173-200920437"
    assert Finding.LEGACY_NUMBER_COMPOSITE in historical.source_row.findings


def test_a_legacy_number_longer_than_nine_digits_is_archived_as_it_is(
    manager, committed_batch
) -> None:
    """
    Ten digits, stored whole.

    This is the entire reason ``legacy_number`` lives in the archive: the
    production column would refuse it, and rightly.
    """
    historical = committed_batch.participants.get(legacy_number="2022501086")

    assert len(historical.legacy_number) == 10
    assert historical.linked_participant_id is None


def test_a_payment_row_appears_only_where_money_was_recorded(manager, committed_batch) -> None:
    """
    A zero collected column is an enrolment nobody paid for.

    And a payment carries no invented receipt number: 76% of the delivered
    rows name none, so the reference is stored as the text it was, blank
    included.
    """
    payments = HistoricalPayment.objects.filter(batch=committed_batch)
    assert payments.count() == 6
    assert all(payment.amount > Decimal("0.000") for payment in payments)

    compound = payments.get(legacy_receipt_ref="2294+2610+2092")
    assert compound.parsed_date is None  # a compound is not half a date

    blank = payments.filter(legacy_receipt_ref="")
    assert blank.exists()


def test_the_sheet_header_becomes_a_cohort_of_labels(manager, committed_batch) -> None:
    """
    Labels, not foreign keys.

    «ادارة الاعمال» in a 2022 sheet is not established to be the programme of
    that name in today's catalogue, and a foreign key would let a historical
    row inherit a current price.
    """
    cohort = committed_batch.cohorts.get()

    assert cohort.program_label_ar == "ادارة الاعمال"
    assert cohort.partner_label_ar == "تناغم"
    assert cohort.term_label == "ف 1 2022"
    assert set(cohort.subject_prices) == {"N", "O"}


def test_an_unrecognised_partner_is_left_blank_rather_than_guessed(
    manager, workbook_factory, sample_workbook
) -> None:
    """
    A wrong partner on an archived cohort is worse than none.

    Positional guessing was tried and it labelled a «تناغم» cohort with the
    workbook's own name, because the partner sits in a different cell in every
    file. The names now come from ``archive_partner_labels``; a sheet naming
    none of them archives blank, and a human fills it in.
    """
    from apps.datamigration.services import mapping

    recognised = {"meta": {"B": {"v": "تناغم"}, "C": {"v": "ادارة الاعمال"}}, "columns": {}}
    assert mapping.partner_label(recognised, ["تناغم", "المثالية"]) == "تناغم"

    # Generatave: B1 is the workbook's name, D1 is the partner.
    elsewhere = {
        "meta": {"B": {"v": "Generatave"}, "C": {"v": "ذكاء اصطناعي"}, "D": {"v": "تناغم"}},
        "columns": {},
    }
    assert mapping.partner_label(elsewhere, ["تناغم"]) == "تناغم"

    # Nothing recognised — blank, not the first plausible-looking cell.
    assert mapping.partner_label(elsewhere, ["المثالية"]) == ""
    assert mapping.partner_label(elsewhere, []) == ""


def test_the_commit_summary_reconciles(manager, committed_batch) -> None:
    from apps.datamigration.models import MigrationBatch

    batch = MigrationBatch.objects.get(pk=committed_batch.pk)
    assert batch.status == BatchStatus.COMMITTED
    assert batch.committed_by is not None
    assert batch.committed_at is not None
    assert (
        batch.participants.count() + batch.rows.filter(state=RowState.UNIDENTIFIED).count()
        == batch.row_count
    )


def test_the_archive_holds_decimals_not_floats(manager, committed_batch) -> None:
    """
    ADR-005 — openpyxl hands back Python floats and this is where they stop.

    ``1450.0`` crossing as a float would be invisible until a fils went
    missing three sprints later.
    """
    enrollment = HistoricalParticipant.objects.get(
        batch=committed_batch, legacy_number="202251024"
    ).enrollments.get()

    assert isinstance(enrollment.course_value, Decimal)
    assert enrollment.course_value == Decimal("1450.000")
    assert enrollment.collected == Decimal("650.000")
    assert enrollment.balance == Decimal("800.000")
