"""
The first test of Sprint 8D-1, and the one the whole sprint is shaped around.

Importing the centre's history must not create money. Not a receipt, not a
payment, not a claim, not a fils on any report. A-04 forbids this app from
importing the financial apps, but an import rule is a statement about source
files; this asserts the CONSEQUENCE — that after a workbook has been read,
validated and archived, every ledger table holds exactly what it held before,
and the partner's share of a real cohort has not moved.

The distinction matters because the failure this guards against is not someone
writing ``from apps.cashbox...``. It is someone deciding, three sprints from
now and with the best intentions, that a historical payment "should really"
appear on the participant's statement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)


def _ledger_census() -> dict[str, int]:
    """Row counts for every table the archive must never touch."""
    from apps.billing.models import ChargeLine, CreditReturn, Discount, ExtraFee, Refund
    from apps.cashbox.models import DailyClosing, PaymentAllocation, Receipt, ReceiptVoid
    from apps.settlements.models import (
        ClaimDeduction,
        Entitlement,
        PartnerClaim,
        PartnerObligation,
        PartnerSettlement,
    )

    return {
        model.__name__: model.objects.count()
        for model in (
            ChargeLine,
            CreditReturn,
            Discount,
            ExtraFee,
            Refund,
            DailyClosing,
            PaymentAllocation,
            Receipt,
            ReceiptVoid,
            ClaimDeduction,
            Entitlement,
            PartnerClaim,
            PartnerObligation,
            PartnerSettlement,
        )
    }


def test_reading_a_workbook_writes_nothing_to_the_ledger(manager, sample_workbook) -> None:
    from apps.datamigration.services import batch_service

    before = _ledger_census()
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-1")

    assert batch.row_count == 8
    assert _ledger_census() == before


def test_the_dry_run_writes_nothing_to_the_ledger(manager, sample_workbook) -> None:
    from apps.datamigration.services import batch_service, validation_service

    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-2")
    before = _ledger_census()
    validation_service.validate(actor=manager, batch=batch)

    assert _ledger_census() == before


def test_committing_a_workbook_writes_nothing_to_the_ledger(manager, sample_workbook) -> None:
    """The whole sprint, in one assertion."""
    from apps.datamigration.services import archive_service, batch_service, validation_service

    before = _ledger_census()
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-3")
    validation_service.validate(actor=manager, batch=batch)
    summary = archive_service.commit(actor=manager, batch=batch)

    assert summary["archived"] > 0  # it really did archive something
    assert _ledger_census() == before


def test_an_archived_workbook_moves_no_partner_share_by_one_fils(
    manager,
    finance,
    sample_workbook,
    percent_agreement,
    cohort_with_agreement,
    make_paid_enrollment,
) -> None:
    """
    A real cohort with a real partner, claimed before and after the import.

    250 tuition at 50% is 125 dinars. The workbook being read describes a 2022
    «تناغم» cohort with 1450-dinar diplomas in it, and none of that may reach
    this claim — not as revenue, not as an exclusion, not as a rounding.
    """
    from apps.datamigration.services import archive_service, batch_service, validation_service
    from apps.settlements.services import claim_service, entitlement_service

    enrollment = make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")

    def _share() -> Decimal:
        """
        Recomputed live rather than read off a stored claim.

        A stored figure could not tell the two failures apart: the archive
        leaking into the base, and the archive leaving a stale number alone.
        This asks the entitlement service the same question twice.
        """
        base = entitlement_service.shareable_collected(
            enrollment, agreement=percent_agreement, as_of=PERIOD_END
        )
        return entitlement_service.partner_share_for(
            agreement=percent_agreement, base=base, student_count=1
        )

    before = _share()
    assert before == Decimal("125.000")

    claim = claim_service.build_claim(
        actor=finance,
        agreement=percent_agreement,
        cohort=cohort_with_agreement,
        period_from=TERM_START,
        period_to=PERIOD_END,
        trigger_type="END_OF_COURSE",
        trigger_reference_ar="نهاية الدورة",
    )
    assert claim.partner_share == before

    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-4")
    validation_service.validate(actor=manager, batch=batch)
    archive_service.commit(actor=manager, batch=batch)

    claim.refresh_from_db()
    assert _share() == before
    assert claim.partner_share == before


def test_the_reports_do_not_see_the_archive(
    manager, finance, sample_workbook, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §9's seven read the ledger. The archive is not in it.

    Revenue and net income are checked because they are the two figures a
    reader would most expect history to inflate.
    """
    from apps.datamigration.services import archive_service, batch_service, validation_service
    from apps.reporting.services import report_service

    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    window = {"date_from": TERM_START, "date_to": PERIOD_END}

    revenue_before = report_service.revenue_report(actor=finance, **window)["total"]
    net_before = report_service.net_income_report(actor=finance, **window)["net_income"]

    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-5")
    validation_service.validate(actor=manager, batch=batch)
    archive_service.commit(actor=manager, batch=batch)

    assert report_service.revenue_report(actor=finance, **window)["total"] == revenue_before
    assert report_service.net_income_report(actor=finance, **window)["net_income"] == net_before


def test_the_archive_creates_no_production_participant(manager, sample_workbook) -> None:
    """
    Q-02 — historical people live in archive tables, linked, never created.

    Eight rows go in and the production participant count does not move, which
    is also what keeps ``participant_number`` free of ten-digit legacy values.
    """
    from apps.datamigration.services import archive_service, batch_service, validation_service
    from apps.people.models import Participant

    before = Participant.objects.count()
    batch = batch_service.import_workbook(actor=manager, path=sample_workbook, code="MB-LEDGER-6")
    validation_service.validate(actor=manager, batch=batch)
    archive_service.commit(actor=manager, batch=batch)

    assert Participant.objects.count() == before
