"""
Participant database constraints — T-083, T-086, T-087, and the two added checks.

Every assertion here goes through raw SQL or a bare .save(), deliberately
bypassing the service layer and Django validation. The point is not "the form
rejects bad input" — it is that the DATABASE does, so a management command, a
bulk update or a future API cannot write a row the rules forbid.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.db import DataError, IntegrityError, connection, transaction

from apps.people.models import IdDocumentType, Participant, ParticipantCategory

pytestmark = pytest.mark.django_db

COLUMNS = (
    "participant_number, category, name_ar, name_en, id_document_type, "
    "id_document_number, nationality, gender, date_of_birth, qualification, "
    "city, phone, po_box, email, employer, registered_on, "
    "no_refund_pledge_accepted, no_refund_pledge_at, is_exempt, "
    "exemption_approval_ref, exemption_approval_date, created_at, updated_at"
)


def _insert(**overrides: Any) -> None:
    """Raw insert, bypassing Django entirely. The database is the last word."""
    row: dict[str, Any] = {
        "participant_number": "202610001",
        "category": ParticipantCategory.UNIVERSITY,
        "name_ar": "مشارك اختباري كامل الاسم",
        "name_en": "",
        "id_document_type": IdDocumentType.NATIONAL_ID,
        "id_document_number": "9990000001",
        "nationality": "",
        "gender": "",
        "date_of_birth": None,
        "qualification": "",
        "city": "",
        "phone": "",
        "po_box": "",
        "email": "",
        "employer": "",
        "registered_on": date(2026, 9, 10),
        "no_refund_pledge_accepted": True,
        "no_refund_pledge_at": "2026-09-10 08:00:00",
        "is_exempt": False,
        "exemption_approval_ref": "",
        "exemption_approval_date": None,
        "created_at": "2026-09-10 08:00:00",
        "updated_at": "2026-09-10 08:00:00",
    }
    row.update(overrides)
    placeholders = ", ".join(["%s"] * len(row))
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO people_participant ({COLUMNS}) VALUES ({placeholders})",
            list(row.values()),
        )


def test_valid_row_is_accepted() -> None:
    """The control case: the fixture itself must be legal."""
    _insert()
    assert Participant.objects.count() == 1


# ---------------------------------------------------------------------------
# T-083 / C-19 — the number is exactly nine digits
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("number", ["20261000", "20261000A", "         ", "2026-1001"])
def test_t083_malformed_participant_number_is_rejected(number: str) -> None:
    """Wrong length, letters, spaces, punctuation — all refused by the CHECK."""
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(participant_number=number)


def test_t083_overlong_number_is_rejected_by_the_column_not_the_check() -> None:
    """
    Ten digits never reach the CHECK — varchar(9) plus STRICT_ALL_TABLES stops
    it first, with DataError rather than IntegrityError.

    Asserted separately so the distinction is deliberate: without STRICT mode
    MySQL would TRUNCATE this to nine digits and store a wrong number that
    satisfies the CHECK perfectly. Two different guards, both required.
    """
    with pytest.raises(DataError), transaction.atomic():
        _insert(participant_number="2026100012")


def test_t083_nine_digits_is_accepted() -> None:
    _insert(participant_number="202650042")
    assert Participant.objects.filter(participant_number="202650042").exists()


def test_participant_number_is_unique() -> None:
    _insert(participant_number="202610001")
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(participant_number="202610001", id_document_number="9990000002")


# ---------------------------------------------------------------------------
# T-086 / BR-003 — the pledge carries its timestamp
# ---------------------------------------------------------------------------
def test_t086_accepted_pledge_without_timestamp_is_rejected() -> None:
    """
    BR-003 calls the pledge contractual evidence cited when refusing a refund.

    A flag with no timestamp is not evidence — it is an assertion nobody can date.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(no_refund_pledge_accepted=True, no_refund_pledge_at=None)


def test_t086_unaccepted_pledge_may_have_no_timestamp() -> None:
    _insert(no_refund_pledge_accepted=False, no_refund_pledge_at=None)
    assert Participant.objects.count() == 1


# ---------------------------------------------------------------------------
# T-087 / C-14 / BR-004 — no exemption without an approval reference
# ---------------------------------------------------------------------------
def test_t087_exemption_without_approval_reference_is_rejected() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(is_exempt=True, exemption_approval_ref="")


def test_t087_exemption_with_approval_reference_is_accepted() -> None:
    _insert(is_exempt=True, exemption_approval_ref="ر.ج/2026/118")
    assert Participant.objects.get().is_exempt


# ---------------------------------------------------------------------------
# The two constraints added in this sprint (approved deviation)
# ---------------------------------------------------------------------------
def test_undeclared_category_is_rejected_by_mysql() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(category="VIP_GUEST")


def test_undeclared_id_document_type_is_rejected_by_mysql() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _insert(id_document_type="DRIVING_LICENCE")


def test_every_declared_category_is_permitted() -> None:
    for index, category in enumerate(ParticipantCategory.values):
        _insert(
            participant_number=f"20261000{index}",
            category=category,
            id_document_number=f"999000100{index}",
        )
    assert Participant.objects.count() == len(ParticipantCategory.values)


# ---------------------------------------------------------------------------
# Q-31 — qualification and city stay unconstrained on purpose
# ---------------------------------------------------------------------------
def test_qualification_and_city_are_not_constrained_by_the_database() -> None:
    """
    Their permitted values are still open (Q-31).

    A CHECK here would turn answering the client's question into a data
    migration over live participants. Validation lives in the reference lists.
    """
    _insert(qualification="SOMETHING_NOT_YET_AGREED", city="A_TOWN_NOT_ON_THE_LIST")
    assert Participant.objects.count() == 1


def test_no_composite_unique_on_identity_document() -> None:
    """
    BR-005 is a WARNING in v1, not a database rule.

    DATA_MODEL §4.2 defers the unique constraint until the Sprint 8 archive is
    cleaned; enabling it now would make legitimate historical duplicates
    impossible to archive.
    """
    _insert(participant_number="202610001", id_document_number="5551234")
    _insert(participant_number="202610002", id_document_number="5551234")
    assert Participant.objects.filter(id_document_number="5551234").count() == 2


def test_participant_carries_no_monetary_field() -> None:
    """The balance is a Sprint 4 function over the ledger, never a column."""
    types = {f.name: f.get_internal_type() for f in Participant._meta.get_fields()}
    assert "DecimalField" not in types.values()
    assert "FloatField" not in types.values()
    assert not {n for n in types if n in {"balance", "paid", "amount", "total"}}
