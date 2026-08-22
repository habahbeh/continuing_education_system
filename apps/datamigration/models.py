"""
The historical archive (Q-02 · ADR-013 · D-26 · D-27).

**These tables are not the ledger, and the difference is structural.** The
centre's history lives in twenty-five Excel sheets covering 2015–2026. Of the
443 rows carrying a usable university number, 104 name a receipt and roughly
seventy carry a payment date. Importing the rest as production ``Receipt`` rows
would mean inventing a document number and a date for three payments in four —
fabricating records this system never issued, and then hashing them into an
audit chain that certifies them as real.

So the archive is a separate app with its own tables, forbidden by the static
test A-04 from importing ``billing``, ``cashbox`` or ``settlements``. A
``HistoricalPayment`` is not a payment. It is the centre's own note that
somebody once paid something, kept legible and kept apart. The only gateway
from here to money is an individually reviewed ``OpeningBalance`` (BR-094),
which is Sprint 8D-2 and is not built here.

**Three properties the shape is built around.**

*Nothing is coerced on the way in.* ``MigrationRow.raw_row`` holds every cell
as it was — its text, its type, whether it was a formula. The typed tables are
derived from it and can be derived again. A parsing decision that turns out
wrong costs a re-commit, not a re-read of a file that may by then have changed.

*A row that cannot be identified is kept, not dropped.* 335 cells in the
university-number column are ``#REF!`` and 43 more hold a category word where
a number belongs. Those rows stay as ``MigrationRow`` with the finding
``UNIDENTIFIED_NO_USABLE_NUMBER`` so the count reconciles against the original
sheet — ``read = committed + unidentified + rejected`` — and never become a
``HistoricalParticipant``.

*Identity is decided by a person.* Thirteen legacy numbers appear in more than
one row, which is ordinary — the same student in several cohorts. But eight
carry two different names: 20195073 is both «براءه حسام محمود» and «تمارا عامر
الشيب». No matcher can resolve that, and a matcher that tried would merge two
people silently. ``linked_participant`` is set only by the registrar, on the
review screen, and the act is audited with their name on it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, Money, NameAr, ShortCode


class BatchStatus(models.TextChoices):
    DRAFT = "DRAFT", _("مسودة")
    VALIDATED = "VALIDATED", _("مُدقَّقة")
    COMMITTED = "COMMITTED", _("مؤرشفة")
    SUPERSEDED = "SUPERSEDED", _("مستبدَلة")


class RowState(models.TextChoices):
    RAW = "RAW", _("خام")
    VALID = "VALID", _("صالح للأرشفة")
    UNIDENTIFIED = "UNIDENTIFIED", _("بلا رقم صالح")
    REJECTED = "REJECTED", _("مرفوض")


class Finding(models.TextChoices):
    """
    What validation found in a row, in the vocabulary of the source.

    Each names something the SHEET does, never a correction applied to it. A
    finding of ``SUBJECT_SUM_MISMATCH`` reports that 146 of 163 checked rows
    do not add up; it does not make them add up.
    """

    REF_ERROR = "REF_ERROR", _("خلية #REF! — مرجع محذوف لا قيمة")
    VALUE_ERROR = "VALUE_ERROR", _("خلية #VALUE!")
    UNIDENTIFIED_NO_USABLE_NUMBER = (
        "UNIDENTIFIED_NO_USABLE_NUMBER",
        _("لا رقم جامعي صالح — يُحفظ الصف ولا يُنشأ منه مشارك"),
    )
    LEGACY_NUMBER_COMPOSITE = (
        "LEGACY_NUMBER_COMPOSITE",
        _("رقمان في خلية واحدة — رقم مركز قديم ورقم جامعي"),
    )
    NON_NUMERIC_IDENTITY = ("NON_NUMERIC_IDENTITY", _("كلمة فئة مكان الرقم الجامعي"))
    NAME_MISSING = ("NAME_MISSING", _("صف بلا اسم"))
    SUBJECT_SUM_MISMATCH = ("SUBJECT_SUM_MISMATCH", _("مجموع أعمدة المواد ≠ رسوم الدورة"))
    NEGATIVE_BALANCE = ("NEGATIVE_BALANCE", _("المتبقّي سالب — دفع زائد"))
    OVERPAYMENT = ("OVERPAYMENT", _("المدفوع يتجاوز قيمة الدورة"))
    DATE_UNPARSEABLE = ("DATE_UNPARSEABLE", _("عمود التاريخ يحمل نصاً لا تاريخاً"))
    RECEIPT_REF_COMPOUND = ("RECEIPT_REF_COMPOUND", _("عدة أرقام سندات في خلية واحدة"))
    DUPLICATE_LEGACY_NUMBER = ("DUPLICATE_LEGACY_NUMBER", _("الرقم نفسه في أكثر من صف"))
    LEGACY_NUMBER_NAME_CONFLICT = (
        "LEGACY_NUMBER_NAME_CONFLICT",
        _("الرقم نفسه باسمين مختلفين — لا رَبط آلي"),
    )


class MigrationBatch(models.Model):
    """One workbook, read once."""

    code = ShortCode(unique=True, verbose_name=_("رمز الدفعة"))
    source_filename = models.CharField(max_length=255, verbose_name=_("اسم الملف"))

    #: The idempotency anchor. Re-reading the same bytes produces the same
    #: hash, and ``import_historical`` refuses a second live batch for it —
    #: which is what makes the command safe to run twice by accident.
    source_sha256 = models.CharField(max_length=64, verbose_name=_("بصمة الملف"))

    sheet_count = models.PositiveIntegerField(default=0, verbose_name=_("عدد الأوراق"))
    row_count = models.PositiveIntegerField(default=0, verbose_name=_("عدد الصفوف المقروءة"))

    #: Rows 1 and 2 of every sheet, verbatim. They are not records, so they are
    #: not ``MigrationRow`` — but validation needs them (which columns hold
    #: subjects is decided by whether row 1 prices what row 2 names), and
    #: re-opening the workbook to find out would mean the answer could change
    #: between the dry run and the commit.
    sheet_headers = models.JSONField(default=dict, blank=True, verbose_name=_("رؤوس الأوراق"))

    status = ShortCode(
        choices=BatchStatus.choices, default=BatchStatus.DRAFT, verbose_name=_("الحالة")
    )
    note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة"))

    #: A correction is a NEW batch; the old one is marked and kept. Nothing in
    #: the archive is deleted to make room for a better reading of it.
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name=_("استُبدلت بـ"),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="migration_batches_created",
        verbose_name=_("استوردها"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="migration_batches_validated",
        verbose_name=_("دقّقها"),
    )
    validated_at = models.DateTimeField(null=True, blank=True)

    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="migration_batches_committed",
        verbose_name=_("أرشفها"),
    )
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("دفعة ترحيل")
        verbose_name_plural = _("دفعات الترحيل")
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=BatchStatus.values),
                name="datamigration_batch_status_valid",
            ),
            # A COMMITTED batch names who archived it and when. Without that
            # the status is an assertion nobody signed — the same rule
            # Expense.approved_is_stamped carries.
            models.CheckConstraint(
                condition=~models.Q(status=BatchStatus.COMMITTED)
                | (models.Q(committed_by__isnull=False) & models.Q(committed_at__isnull=False)),
                name="datamigration_batch_committed_is_stamped",
            ),
            # Nothing is archived that was never validated: the dry run is not
            # advisory, it is the step that produces the findings the archiver
            # is agreeing to.
            models.CheckConstraint(
                condition=~models.Q(status=BatchStatus.COMMITTED)
                | models.Q(validated_at__isnull=False),
                name="datamigration_batch_committed_was_validated",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=BatchStatus.SUPERSEDED)
                | models.Q(superseded_by__isnull=False),
                name="datamigration_batch_superseded_names_successor",
            ),
        ]
        indexes = [
            models.Index(fields=["source_sha256"], name="dm_batch_sha_idx"),
            models.Index(fields=["status"], name="dm_batch_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.source_filename}"

    @property
    def is_live(self) -> bool:
        """A batch still standing as the current reading of its file."""
        return self.status != BatchStatus.SUPERSEDED


class MigrationRow(models.Model):
    """
    One spreadsheet row, verbatim.

    ``raw_row`` is the whole point: ``{"C": {"v": "#REF!", "t": "e", "f": false}}``
    — the value as text, its cell type, and whether a formula produced it. No
    number is parsed here and no blank is turned into a zero, because a
    ``#REF!`` silently read as 0 is how a deleted reference becomes a fact.
    """

    batch = models.ForeignKey(
        MigrationBatch, on_delete=models.PROTECT, related_name="rows", verbose_name=_("الدفعة")
    )
    sheet_name = models.CharField(max_length=120, verbose_name=_("الورقة"))
    source_row = models.PositiveIntegerField(verbose_name=_("رقم الصف في الملف"))

    raw_row = models.JSONField(default=dict, verbose_name=_("الخلايا كما وردت"))

    state = ShortCode(choices=RowState.choices, default=RowState.RAW, verbose_name=_("الحالة"))
    findings = models.JSONField(default=list, blank=True, verbose_name=_("الملاحظات"))
    reason_ar = models.CharField(max_length=255, blank=True, verbose_name=_("سبب الرفض"))

    class Meta:
        verbose_name = _("صف مصدر")
        verbose_name_plural = _("صفوف المصدر")
        ordering = ["batch", "sheet_name", "source_row"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=RowState.values),
                name="datamigration_row_state_valid",
            ),
            # THE idempotency key. A second read of the same workbook collides
            # here rather than doubling the archive.
            models.UniqueConstraint(
                fields=["batch", "sheet_name", "source_row"],
                name="datamigration_row_unique_in_batch",
            ),
            # A row set aside says why. "Rejected" with no finding and no
            # reason is a row nobody can argue with later.
            models.CheckConstraint(
                condition=models.Q(state__in=[RowState.RAW, RowState.VALID])
                | ~models.Q(reason_ar=""),
                name="datamigration_row_setaside_has_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "state"], name="dm_row_batch_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sheet_name}!{self.source_row}"

    def cell(self, column: str) -> str:
        """The raw text of one column, or "" — never a coerced value."""
        entry = self.raw_row.get(column) or {}
        return str(entry.get("v") or "")


class HistoricalCohort(models.Model):
    """
    One sheet's header row, kept as LABELS.

    Deliberately not a foreign key to ``catalog.Program`` or
    ``operations.Cohort``. «ادارة الاعمال» in a 2018 sheet and the programme of
    that name in today's catalogue are not established to be the same thing,
    and pretending otherwise would let a historical row inherit a current
    price. Matching them is a human act, and it is not one 8D-1 performs.
    """

    batch = models.ForeignKey(
        MigrationBatch, on_delete=models.PROTECT, related_name="cohorts", verbose_name=_("الدفعة")
    )
    sheet_name = models.CharField(max_length=120, verbose_name=_("الورقة"))

    program_label_ar = models.CharField(max_length=150, blank=True, verbose_name=_("البرنامج"))
    term_label = models.CharField(max_length=120, blank=True, verbose_name=_("الفصل"))

    #: The partner sits in B1 in one workbook, E1 in another and D1 in the
    #: third. Read per sheet, stored as the label it was.
    partner_label_ar = models.CharField(max_length=150, blank=True, verbose_name=_("الشريك"))
    delivery_label = models.CharField(max_length=60, blank=True, verbose_name=_("نمط التقديم"))
    date_label = models.CharField(max_length=120, blank=True, verbose_name=_("التاريخ كما ورد"))

    #: {column letter: {"label": …, "price": …}} — the subject grid as row 1
    #: and row 2 gave it. Kept because the review screen has to show what the
    #: sheet charged for, and because Σ(subjects) ≠ tuition in 146 of 163
    #: checked rows: a reader needs to see the parts to judge the whole.
    subject_prices = models.JSONField(default=dict, blank=True, verbose_name=_("أسعار المواد"))

    class Meta:
        verbose_name = _("دفعة تاريخية")
        verbose_name_plural = _("الدفعات التاريخية")
        ordering = ["batch", "sheet_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "sheet_name"], name="datamigration_cohort_unique_sheet"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.program_label_ar} — {self.term_label}".strip(" —")


class HistoricalParticipant(models.Model):
    """
    A person named in the archive. **Not a ``people.Participant``.**

    ``legacy_number`` carries no format constraint and is not unique, and both
    are decisions rather than omissions. The historical numbers follow the same
    year+type+sequence scheme as production but with a sequence of three to six
    digits, so they run 8, 9, 10 and 11 characters long — and eight cells hold
    TWO numbers joined by a hyphen. Thirteen numbers legitimately appear in
    more than one row, because a student may sit several cohorts.

    ``people_participant_number_format`` — the nine-digit CHECK on production —
    is untouched by any of this. That is the whole reason this column exists
    here instead of there.
    """

    batch = models.ForeignKey(
        MigrationBatch,
        on_delete=models.PROTECT,
        related_name="participants",
        verbose_name=_("الدفعة"),
    )
    source_row = models.OneToOneField(
        MigrationRow,
        on_delete=models.PROTECT,
        related_name="historical_participant",
        verbose_name=_("الصف المصدر"),
    )

    legacy_number = models.CharField(max_length=32, verbose_name=_("الرقم الجامعي القديم"))
    legacy_alt_number = models.CharField(
        max_length=32, blank=True, verbose_name=_("الرقم الثاني في الخلية")
    )

    #: NameAr, but blank is allowed: rows exist with a number and no name, and
    #: dropping them would break the reconciliation against the sheet.
    name_ar = NameAr(blank=True, verbose_name=_("الاسم كما ورد"))
    legacy_category = ShortCode(blank=True, verbose_name=_("الفئة كما وردت"))

    #: Set ONLY by the registrar on the review screen. Never by an importer,
    #: never by a matcher, never in bulk.
    linked_participant = models.ForeignKey(
        "people.Participant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_records",
        verbose_name=_("المشارك المرتبط"),
    )
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_links_made",
        verbose_name=_("ربطه"),
    )
    linked_at = models.DateTimeField(null=True, blank=True)
    link_note_ar = models.CharField(max_length=255, blank=True, verbose_name=_("مسوّغ الربط"))

    class Meta:
        verbose_name = _("مشارك تاريخي")
        verbose_name_plural = _("المشاركون التاريخيون")
        ordering = ["batch", "legacy_number"]
        constraints = [
            # An identified archive row has a number; a row without one stops
            # at MigrationRow with UNIDENTIFIED_NO_USABLE_NUMBER.
            models.CheckConstraint(
                condition=~models.Q(legacy_number=""),
                name="datamigration_historical_participant_has_number",
            ),
            # A link names who made it and when. An unattributed link is an
            # identity decision nobody can be asked about.
            models.CheckConstraint(
                condition=models.Q(linked_participant__isnull=True)
                | (models.Q(linked_by__isnull=False) & models.Q(linked_at__isnull=False)),
                name="datamigration_link_is_stamped",
            ),
        ]
        indexes = [
            models.Index(fields=["legacy_number"], name="dm_hist_legacy_no_idx"),
            models.Index(fields=["name_ar"], name="dm_hist_name_idx"),
            models.Index(fields=["linked_participant"], name="dm_hist_linked_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.legacy_number} — {self.name_ar}".strip(" —")


class HistoricalEnrollment(models.Model):
    """
    One participant on one historical cohort.

    No stored balance, for the reason ``Participant`` has no stored balance: a
    saved total is a number that can disagree with the rows it came from. The
    sheet's own «المبلغ المتبقي» column is kept in ``MigrationRow.raw_row`` as
    evidence of what the sheet claimed, not adopted as truth.
    """

    batch = models.ForeignKey(
        MigrationBatch,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("الدفعة"),
    )
    source_row = models.OneToOneField(
        MigrationRow,
        on_delete=models.PROTECT,
        related_name="historical_enrollment",
        verbose_name=_("الصف المصدر"),
    )
    participant = models.ForeignKey(
        HistoricalParticipant,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("المشارك"),
    )
    cohort = models.ForeignKey(
        HistoricalCohort,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("الدفعة التاريخية"),
    )

    course_value = Money(default=0, verbose_name=_("قيمة الدورة كما وردت"))
    tuition = Money(default=0, verbose_name=_("رسوم الدورة"))
    registration_fee = Money(default=0, verbose_name=_("رسوم التسجيل"))
    collected = Money(default=0, verbose_name=_("المقبوض"))

    legacy_category = ShortCode(blank=True, verbose_name=_("الفئة كما وردت"))
    source_note = models.CharField(max_length=255, blank=True, verbose_name=_("ملاحظة الملف"))
    subject_breakdown = models.JSONField(
        default=dict, blank=True, verbose_name=_("توزيع المواد كما ورد")
    )

    class Meta:
        verbose_name = _("تسجيل تاريخي")
        verbose_name_plural = _("التسجيلات التاريخية")
        ordering = ["batch", "id"]
        constraints = [
            # Amounts as recorded are never negative — a negative BALANCE is
            # ordinary here (21 rows) and is derived, not stored.
            models.CheckConstraint(
                condition=models.Q(course_value__gte=0)
                & models.Q(tuition__gte=0)
                & models.Q(registration_fee__gte=0)
                & models.Q(collected__gte=0),
                name="datamigration_enrollment_amounts_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "cohort"], name="dm_enr_batch_cohort_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.participant.legacy_number} @ {self.cohort.term_label}"

    @property
    def balance(self) -> object:
        """Derived, always. Negative means the sheet recorded an overpayment."""
        return self.course_value - self.collected


class HistoricalPayment(models.Model):
    """
    The sheet's note that money once arrived. **Not a ``cashbox.Receipt``.**

    ``legacy_receipt_ref`` is stored as text because the column holds
    ``2294+2610+2092`` as often as it holds a number, the value ``1`` appears
    thirty-three times, and three rows in four carry nothing at all. There is
    no receipt to reconstruct, which is precisely why none is reconstructed.

    ``parsed_date`` is null far more often than not, and that is reported as a
    finding rather than filled in from the term dates.
    """

    batch = models.ForeignKey(
        MigrationBatch,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("الدفعة"),
    )
    source_row = models.ForeignKey(
        MigrationRow,
        on_delete=models.PROTECT,
        related_name="historical_payments",
        verbose_name=_("الصف المصدر"),
    )
    enrollment = models.ForeignKey(
        HistoricalEnrollment,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("التسجيل"),
    )

    amount = Money(verbose_name=_("المبلغ كما ورد"))
    legacy_receipt_ref = DisplayRef(blank=True, verbose_name=_("مرجع السند كما ورد"))
    legacy_date_text = models.CharField(max_length=120, blank=True, verbose_name=_("التاريخ كنص"))
    parsed_date = models.DateField(null=True, blank=True, verbose_name=_("التاريخ المفهوم"))

    class Meta:
        verbose_name = _("مقبوض تاريخي")
        verbose_name_plural = _("المقبوضات التاريخية")
        ordering = ["batch", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="datamigration_payment_amount_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "enrollment"], name="dm_pay_batch_enr_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.amount} — {self.legacy_receipt_ref or '—'}"


__all__ = [
    "BatchStatus",
    "Finding",
    "HistoricalCohort",
    "HistoricalEnrollment",
    "HistoricalParticipant",
    "HistoricalPayment",
    "MigrationBatch",
    "MigrationRow",
    "RowState",
]
