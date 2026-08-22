"""
Fixtures for the archive tests.

``sample_workbook`` builds a real ``.xlsx`` in a temp directory carrying the
shapes the delivered files actually contain — a ``#REF!`` identity, a category
word where a number belongs, a hyphenated pair, one number under two different
names, a compound receipt reference, an overpayment, and a subject grid that
does not sum. Building it rather than shipping a fixture file keeps the
client's data out of the repository while testing against its exact defects.
"""

from __future__ import annotations

import pytest

PASSWORD = "probe-password-1234"

#: Row 1 metadata and row 2 headers, as all twenty-five delivered sheets carry
#: them: partner in B, programme in C, term in D, subject prices from N.
META = {"B": "تناغم", "C": " ادارة الاعمال", "D": "ف 1 2022", "K": "م  / ج", "N": 100, "O": 100}
HEADERS = {
    "A": "التسلسل",
    "B": "اسم الطالب",
    "C": "الرقم الجامعي",
    "D": "التاريخ",
    "E": "رقم سند القبض",
    "F": "قيمة الدورة",
    "G": "القيمة الدفوعة ",
    "H": "رسوم الدورة ",
    "I": "رسوم التسجيل",
    "J": "المبلغ المتبقي",
    "K": "1.2",
    "L": "ملاحظات",
    "M": "0.5",
    "N": "Icdl",
    "O": "مهارات شخصية",
}

#: (serial, name, number, date/note, receipt, value, paid, tuition, reg, rest,
#:  k, label, m, subject N, subject O)
ROWS = [
    # An ordinary, complete row.
    (
        1,
        "انوار رضوان الاحمد",
        "202251004",
        "42267",
        "1991",
        1450,
        1450,
        1150,
        300,
        0,
        2,
        "طالب مركز",
        575,
        100,
        100,
    ),
    # Ten digits — longer than production's nine, which is the point.
    (
        2,
        "سارة الخطيب محمود",
        "2022501086",
        "17/3/2022",
        "2050",
        1450,
        1450,
        1150,
        300,
        0,
        2,
        "طالب مركز",
        575,
        100,
        100,
    ),
    # Two numbers in one cell: an old centre number and a university number.
    (
        3,
        "ساره محمد امين",
        "20155173-200920437",
        "",
        "",
        105,
        105,
        90,
        15,
        0,
        1,
        "طالب جامعة",
        45,
        45,
        45,
    ),
    # The same number as row 1, under a different name. No matcher may resolve
    # this, and the tests assert that none tries.
    (4, "تمارا عامر الشيب", "202251004", "", "", 105, 105, 90, 15, 0, 1, "طالب جامعة", 45, 45, 45),
    # A deleted reference. Kept, never read as zero.
    (5, "ايمان زياد خليل", "#REF!", "", "", 105, 105, 90, 15, 0, 1, "طالب جامعة", 45, 45, 45),
    # A category word where a number belongs — 43 rows like this were delivered.
    (6, "خالد سليم الحاج", "مركز", "", "", 105, 105, 90, 15, 0, 2, "طالب مركز", 45, 45, 45),
    # Overpayment: the sheet's own «المبلغ المتبقي» goes negative.
    (
        7,
        "يارا محمود حسن",
        "202251021",
        "",
        "2294+2610+2092",
        1450,
        1675,
        1375,
        300,
        -225,
        2,
        "طالب مركز",
        687,
        100,
        100,
    ),
    # Subject columns that do not sum to the tuition — 146 of 163 delivered
    # rows behave this way.
    (
        8,
        "كاترين سليم فوزي",
        "202251024",
        "تحويل 300 الى انجليزي",
        "",
        1450,
        650,
        350,
        300,
        800,
        2,
        "طالب مركز",
        175,
        50,
        25,
    ),
]


def _write(path, rows=None):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "20221تناغم"
    for column, value in META.items():
        sheet[f"{column}1"] = value
    for column, value in HEADERS.items():
        sheet[f"{column}2"] = value
    letters = "ABCDEFGHIJKLMNO"
    for index, row in enumerate(rows if rows is not None else ROWS, start=3):
        for letter, value in zip(letters, row, strict=True):
            sheet[f"{letter}{index}"] = value
    workbook.save(path)
    return path


@pytest.fixture
def sample_workbook(tmp_path):
    """A workbook carrying the delivered files' defects, in miniature."""
    return _write(tmp_path / "sample.xlsx")


@pytest.fixture
def workbook_factory(tmp_path):
    """Build a variant workbook — for the idempotency and copy tests."""

    def _make(name: str, rows=None):
        return _write(tmp_path / name, rows)

    return _make


@pytest.fixture
def manager(seeded_settings):
    """Reads workbooks in and archives them — C and A on Screen.MIGRATION."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.migration", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def registrar(seeded_settings):
    """Holds E — the identity link, and nothing else on this screen."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="reg.migration", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )


@pytest.fixture
def finance(seeded_settings):
    """VIEW only. Deliberately given no say in an identity decision."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.migration", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def committed_batch(manager, sample_workbook):
    """A workbook read, validated and archived."""
    from apps.datamigration.services import archive_service, batch_service, validation_service

    batch = batch_service.import_workbook(
        actor=manager, path=sample_workbook, code="MB-001", note_ar="اختبار"
    )
    validation_service.validate(actor=manager, batch=batch)
    archive_service.commit(actor=manager, batch=batch)
    batch.refresh_from_db()
    return batch


@pytest.fixture
def live_participant(seeded_settings, active_semester, registrar):
    """A production participant with a properly generated nine-digit number."""
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service

    return participant_service.create_participant(
        actor=registrar,
        data={
            "category": ParticipantCategory.UNIVERSITY,
            "name_ar": "انوار رضوان الاحمد الرباعي",
            "id_document_type": IdDocumentType.NATIONAL_ID,
            "id_document_number": "9990000123",
            "registered_on": __import__("datetime").date(2026, 9, 20),
            "no_refund_pledge_accepted": True,
        },
    )


# The settlement fixtures give the ledger-isolation tests a REAL partner
# cohort to measure against — an archive that moved no partner share is only
# meaningful if there was a partner share to move.
from apps.settlements.tests.conftest import (  # noqa: E402, F401
    cash_method,
    cohort_with_agreement,
    make_paid_enrollment,
    partner,
    percent_agreement,
    priced_catalog,
)
from apps.settlements.tests.conftest import cashier as cashier  # noqa: E402
