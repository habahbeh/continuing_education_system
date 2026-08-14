"""Academic semester (DATA_MODEL §3.1)."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import NameAr, ShortCode


class SemesterType(models.IntegerChoices):
    """
    Semester type code.

    This code becomes the 5th digit of the participant number (BR-001):
    1 = first, 2 = second, 3 = summer. Code 5 is reserved for centre
    participants (BR-002) and is deliberately NOT a semester type.
    """

    FIRST = 1, _("الفصل الأول")
    SECOND = 2, _("الفصل الثاني")
    SUMMER = 3, _("الفصل الصيفي")


class Semester(models.Model):
    code = ShortCode(unique=True, verbose_name=_("الرمز"))
    name_ar = NameAr(verbose_name=_("اسم الفصل"))
    type_code = models.PositiveSmallIntegerField(
        choices=SemesterType.choices, verbose_name=_("رمز النوع")
    )
    academic_year = models.CharField(max_length=9, verbose_name=_("السنة الدراسية"))
    starts_on = models.DateField(verbose_name=_("يبدأ في"))
    ends_on = models.DateField(verbose_name=_("ينتهي في"))
    is_active = models.BooleanField(default=False, verbose_name=_("الفصل الحالي"))

    # Generated column trick: MySQL has no partial indexes, so we build a
    # column that is NULL unless the row is active, then make it unique.
    # NULLs do not participate in uniqueness, so at most one row can be active
    # (DATA_MODEL §11.3).
    active_flag = models.BooleanField(null=True, editable=False, default=None)

    class Meta:
        verbose_name = _("فصل دراسي")
        verbose_name_plural = _("الفصول الدراسية")
        ordering = ["-starts_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gt=models.F("starts_on")),
                name="core_semester_ends_after_starts",
            ),
            models.CheckConstraint(
                condition=models.Q(type_code__in=[1, 2, 3]),
                name="core_semester_type_code_valid",
            ),
            models.UniqueConstraint(
                fields=["active_flag"],
                name="core_semester_single_active",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active"], name="core_semester_active_idx"),
            models.Index(fields=["starts_on", "ends_on"], name="core_semester_range_idx"),
        ]

    def __str__(self) -> str:
        return self.name_ar

    def save(self, *args: object, **kwargs: object) -> None:
        # Keep the generated column in sync with the business flag.
        self.active_flag = True if self.is_active else None
        super().save(*args, **kwargs)  # type: ignore[arg-type]
