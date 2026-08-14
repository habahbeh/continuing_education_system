"""Core model constraint tests — constraints must exist in the DATABASE."""

from __future__ import annotations

from datetime import date

import pytest
from django.db import IntegrityError, connection, transaction

from apps.core.models import FinancialPeriod, NumberSequence, Semester, SemesterType

pytestmark = pytest.mark.django_db


def _semester(**kwargs: object) -> Semester:
    defaults = {
        "code": "S2025-3",
        "name_ar": "الفصل الصيفي 2025/2026",
        "type_code": SemesterType.SUMMER,
        "academic_year": "2025/2026",
        "starts_on": date(2026, 6, 20),
        "ends_on": date(2026, 8, 30),
    }
    defaults.update(kwargs)
    return Semester.objects.create(**defaults)  # type: ignore[arg-type]


def test_semester_end_must_follow_start() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _semester(starts_on=date(2026, 8, 30), ends_on=date(2026, 6, 20))


def test_semester_type_code_is_restricted() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _semester(type_code=5)  # 5 is the centre-participant code, not a semester


def test_only_one_semester_can_be_active() -> None:
    """MySQL has no partial index; the generated active_flag column enforces this."""
    _semester(code="S2025-3", is_active=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        _semester(code="S2025-2", is_active=True)


def test_many_inactive_semesters_are_allowed() -> None:
    _semester(code="S2025-1", is_active=False)
    _semester(code="S2025-2", is_active=False)
    assert Semester.objects.filter(is_active=False).count() == 2


def test_number_sequence_scope_partition_is_unique() -> None:
    NumberSequence.objects.create(scope="receipt", partition="2026")
    with pytest.raises(IntegrityError), transaction.atomic():
        NumberSequence.objects.create(scope="receipt", partition="2026")


def test_financial_period_closed_requires_closer() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        FinancialPeriod.objects.create(
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 1, 31),
            status="CLOSED",
        )


def test_check_constraints_exist_in_the_database() -> None:
    """
    Hard rule 9: a constraint documented in DATA_MODEL.md must be a real DB
    constraint, not just Python validation.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT CONSTRAINT_NAME
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_TYPE = 'CHECK'
            """
        )
        found = {row[0] for row in cursor.fetchall()}

    expected = {
        "core_semester_ends_after_starts",
        "core_semester_type_code_valid",
        "core_sequence_next_value_positive",
        "core_setting_period_valid",
        "core_period_ends_after_starts",
        "core_period_closed_requires_closer",
        "core_attachment_size_within_limit",
        "people_user_role_valid",
    }
    missing = expected - found
    assert not missing, f"CHECK constraints missing from the database: {sorted(missing)}"


def test_no_enum_columns_in_the_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND DATA_TYPE = 'enum'
            """
        )
        offenders = cursor.fetchall()
    assert not offenders, f"ENUM columns found (ADR-004 forbids them): {offenders}"


def test_all_tables_are_innodb_utf8mb4() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
            """
        )
        rows = cursor.fetchall()

    assert rows, "no tables found"
    bad = [r for r in rows if r[1] != "InnoDB" or not (r[2] or "").startswith("utf8mb4")]
    assert not bad, f"tables not InnoDB/utf8mb4: {bad}"
