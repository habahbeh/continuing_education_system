"""Arabic text integrity in MySQL (QA gate T-193, T-194)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.core.models import Semester, SemesterType

pytestmark = pytest.mark.django_db

SAMPLES = [
    "الفصل الصيفي 2025/2026",
    "مُحَمَّدٌ",  # full diacritics
    "أ إ آ ا ة ه ى ي ؤ ئ ء",  # hamza / ta-marbuta / alef-maqsura variants
    "براءة الذمة CS Fm 7.18 Rev A",  # mixed Arabic + Latin + digits
    "تناغم — صرح ‹المثالية›",  # Arabic punctuation
]


@pytest.mark.parametrize("text", SAMPLES)
def test_arabic_round_trips_byte_for_byte(text: str) -> None:
    semester = Semester.objects.create(
        code=f"T{abs(hash(text)) % 10000}",
        name_ar=text,
        type_code=SemesterType.FIRST,
        academic_year="2025/2026",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 6, 1),
    )
    semester.refresh_from_db()
    assert semester.name_ar == text
    assert semester.name_ar.encode("utf-8") == text.encode("utf-8")


def test_four_byte_characters_are_accepted() -> None:
    """Proves utf8mb4 rather than utf8mb3 — a 3-byte charset would error here."""
    text = "شهادة 🎓 معتمدة"
    semester = Semester.objects.create(
        code="EMOJI",
        name_ar=text,
        type_code=SemesterType.FIRST,
        academic_year="2025/2026",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 6, 1),
    )
    semester.refresh_from_db()
    assert semester.name_ar == text
