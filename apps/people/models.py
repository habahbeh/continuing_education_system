"""
Custom user model (DATA_MODEL §4.1).

Built in Sprint 1 because changing AUTH_USER_MODEL after the first migration
is painful. Sprint 1 delivered the model only. Sprint 2A adds the sixth
business role and the local-authentication state, per the resolved decisions:

* Q-12 — local accounts in v1. No SSO/AD/LDAP/SAML/2FA. The lockout counters
  below are account state, not a permission engine; the engine lives in
  apps/people/permissions/.
* Q-14 — FINANCE_MANAGER is an internal role with a deliberately narrow scope
  (BR-099). It is NOT a stronger FINANCE_OFFICER and inherits nothing.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import DisplayRef, NameAr, NameEn, ShortCode


class Role(models.TextChoices):
    """
    The five documented business roles, plus two roles resolved after the demo.

    PERMISSIONS.md §6 Δ-02: user and role administration is NOT a centre
    manager function; it belongs to a dedicated system administrator held by
    the IT department.

    PERMISSIONS.md §6 Δ-13 (Q-14): FINANCE_MANAGER is the internal role that
    performs the SECOND certification of the financial clearance step after
    the finance officer (BR-074). Its scope is approval and oversight only —
    see BR-099 and deny rule D-29.

    Not roles, deliberately (Q-14):
    * University President — an external approver represented by a mandatory
      text reference plus a mandatory attachment (D-31). No account, no role,
      no PENDING_APPROVAL state waiting for a login that will never happen.
    * "Accountant" — a job title in form CS Fm 7.18 Rev A, not a role code.
      The first clearance certification is performed by FINANCE_OFFICER.
    """

    CENTER_MANAGER = "CENTER_MANAGER", _("مدير المركز")
    REGISTRATION_OFFICER = "REGISTRATION_OFFICER", _("موظف التسجيل")
    FINANCE_OFFICER = "FINANCE_OFFICER", _("الموظف المالي")
    FINANCE_MANAGER = "FINANCE_MANAGER", _("المدير المالي")
    CASHIER = "CASHIER", _("الصندوق")
    AUDIT_ACCOUNT = "AUDIT_ACCOUNT", _("حساب التدقيق")
    SYSTEM_ADMINISTRATOR = "SYSTEM_ADMINISTRATOR", _("مدير النظام")


class User(AbstractUser):
    full_name_ar = NameAr(blank=True, verbose_name=_("الاسم بالعربية"))
    role = ShortCode(
        choices=Role.choices,
        blank=True,
        verbose_name=_("الدور"),
        help_text=_("الدور يأتي من المصادقة — لا واجهة تغيّره على الجلسة (D-20)"),
    )
    department = models.CharField(max_length=150, blank=True, verbose_name=_("الدائرة"))

    # ``createsuperuser`` asks for the role (Sprint 8F-0). Not a schema change
    # — ``REQUIRED_FIELDS`` only tells the command what to prompt for.
    #
    # Without it a fresh install ends in a deadlock: the first account is made
    # by ``createsuperuser``, which leaves ``role`` blank, and T-165 refuses a
    # bare superuser the user admin on purpose (Δ-02 names the ROLE as the
    # authority). Nobody could then create the first real user. Prompting here
    # is the smallest fix that does not weaken that refusal — the installer
    # states the role instead of the system inferring one from a flag.
    REQUIRED_FIELDS = ["email", "role"]

    # --- Local authentication state (Q-12) --------------------------------
    # Counters, not policy: the thresholds themselves are EffectiveSettings
    # (`login_max_failed_attempts`, `session_idle_timeout_minutes`) because a
    # number in the code is a business constant in disguise (BR-086).
    failed_login_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("عدد المحاولات الفاشلة"),
    )
    locked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("تاريخ القفل"),
        help_text=_("يُقفل الحساب بعد تجاوز حد المحاولات الفاشلة (Q-12)"),
    )
    lock_reason = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("سبب القفل"),
    )

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined,misc]
        verbose_name = _("مستخدم")  # type: ignore[assignment]
        verbose_name_plural = _("المستخدمون")  # type: ignore[assignment]
        constraints = [
            # Derived from Role.values, not a second hand-written list. The
            # duplicate list is exactly how a seventh role gets added to the
            # choices and forgotten in the constraint — which MySQL would then
            # reject at runtime while Django considered it valid (T-270).
            #
            # Django freezes this list into the migration file at the moment
            # the migration is written. That is correct: a migration is a
            # historical record. The benefit of deriving is that changing Role
            # now PRODUCES a migration instead of silently diverging.
            models.CheckConstraint(
                condition=models.Q(role="") | models.Q(role__in=Role.values),
                name="people_user_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name_ar or self.get_username()

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None


class ParticipantCategory(models.TextChoices):
    """
    The three participant categories (DATA_MODEL §4.2, GLOSSARY §1).

    CENTER is the one that changes the participant number: its type digit is
    fixed at 5 regardless of the semester (BR-002).
    """

    UNIVERSITY = "UNIVERSITY", _("طالب جامعة / خرّيج")
    CENTER = "CENTER", _("طالب مركز")
    EMPLOYEE = "EMPLOYEE", _("موظف الجامعة")


class IdDocumentType(models.TextChoices):
    NATIONAL_ID = "NATIONAL_ID", _("رقم وطني")
    PASSPORT = "PASSPORT", _("جواز سفر")


class Gender(models.TextChoices):
    MALE = "MALE", _("ذكر")
    FEMALE = "FEMALE", _("أنثى")


class Participant(models.Model):
    """
    A centre participant (DATA_MODEL §4.2).

    Deliberately absent, and not by oversight:

    * **No money field of any kind.** The balance is a Sprint 4 function
      (``get_account_state()``) computed from charge lines and allocations —
      never a stored column, because a stored balance is a number that can
      disagree with the ledger that produced it.
    * **No ``source_batch``.** Cancelled by Q-02: historical participants live
      in the isolated archive tables, linked only through
      ``HistoricalParticipant.linked_participant``, with no financial effect.
    * **No enrolment.** Linking a participant to a cohort is
      ``operations.Enrollment`` with its twelve states — Sprint 6. The "new
      application" screen creates a Participant and nothing else
      (DATA_MODEL §4.2, checklist §5.2).

    ``qualification`` and ``city`` carry no choices and no CHECK constraint on
    purpose: their permitted values are still open (Q-31). SPEC §6 names their
    counts — six levels, twelve governorates — but never lists them. They are
    validated against effective-dated reference lists instead, so answering
    Q-31 is data entry rather than a data migration.
    """

    participant_number = models.CharField(
        max_length=9,
        unique=True,
        editable=False,
        verbose_name=_("الرقم الجامعي"),
        help_text=_("يُولَّد آلياً: السنة(4) + النوع(1) + التسلسل(4) — BR-001"),
    )
    category = ShortCode(choices=ParticipantCategory.choices, verbose_name=_("الفئة"))

    name_ar = NameAr(verbose_name=_("الاسم رباعياً بالعربية"))
    name_en = NameEn(blank=True, verbose_name=_("الاسم بالإنجليزية"))

    id_document_type = ShortCode(
        choices=IdDocumentType.choices,
        default=IdDocumentType.NATIONAL_ID,
        verbose_name=_("نوع وثيقة الهوية"),
    )
    id_document_number = models.CharField(max_length=32, verbose_name=_("رقم وثيقة الهوية"))

    nationality = models.CharField(max_length=60, blank=True, verbose_name=_("الجنسية"))
    gender = ShortCode(choices=Gender.choices, blank=True, verbose_name=_("الجنس"))
    date_of_birth = models.DateField(null=True, blank=True, verbose_name=_("تاريخ الميلاد"))

    # No choices / no CHECK — pending Q-31. See the class docstring.
    qualification = ShortCode(blank=True, verbose_name=_("المؤهل العلمي"))
    city = models.CharField(max_length=60, blank=True, verbose_name=_("المدينة"))

    phone = models.CharField(max_length=32, blank=True, verbose_name=_("الهاتف"))
    po_box = models.CharField(max_length=32, blank=True, verbose_name=_("صندوق البريد"))
    email = models.EmailField(blank=True, verbose_name=_("البريد الإلكتروني"))
    employer = models.CharField(max_length=150, blank=True, verbose_name=_("جهة العمل"))

    registered_on = models.DateField(verbose_name=_("تاريخ التسجيل"))

    # BR-003 — the pledge is contractual evidence cited when a refund is
    # refused (BR-033), so the timestamp matters as much as the flag.
    no_refund_pledge_accepted = models.BooleanField(
        default=False, verbose_name=_("الإقرار بالتعهّد بعدم الاسترداد")
    )
    no_refund_pledge_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("وقت قبول التعهّد")
    )

    # BR-004 — an exemption without the president's written approval is an
    # unexplained waiver of revenue.
    is_exempt = models.BooleanField(default=False, verbose_name=_("معفى"))
    exemption_approval_ref = DisplayRef(blank=True, verbose_name=_("رقم موافقة رئيس الجامعة"))
    exemption_approval_date = models.DateField(
        null=True, blank=True, verbose_name=_("تاريخ الموافقة")
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("أُنشئ في"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("عُدِّل في"))

    class Meta:
        verbose_name = _("مشارك")
        verbose_name_plural = _("المشاركون")
        ordering = ["-registered_on", "participant_number"]
        constraints = [
            # C-19 · BR-001 — exactly nine digits. MySQL 8.0.4+ supports REGEXP
            # in CHECK; this is the last line of defence behind the numbering
            # service, not a substitute for it.
            models.CheckConstraint(
                condition=models.Q(participant_number__regex=r"^[0-9]{9}$"),
                name="people_participant_number_format",
            ),
            # BR-003 — accepting the pledge without recording WHEN destroys the
            # evidence the pledge exists to provide.
            models.CheckConstraint(
                condition=models.Q(no_refund_pledge_accepted=False)
                | models.Q(no_refund_pledge_at__isnull=False),
                name="people_participant_pledge_timestamped",
            ),
            # C-14 · BR-004 — no exemption without an approval reference.
            models.CheckConstraint(
                condition=models.Q(is_exempt=False) | ~models.Q(exemption_approval_ref=""),
                name="people_participant_exemption_has_ref",
            ),
            # Closed vocabularies, enforced in the database for the same reason
            # people_user_role_valid is: choices alone are Python-side only.
            models.CheckConstraint(
                condition=models.Q(category__in=ParticipantCategory.values),
                name="people_participant_category_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(id_document_type__in=IdDocumentType.values),
                name="people_participant_id_doc_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["name_ar"], name="people_participant_name_idx"),
            models.Index(fields=["phone"], name="people_participant_phone_idx"),
            models.Index(fields=["category"], name="people_participant_cat_idx"),
            models.Index(fields=["id_document_number"], name="people_participant_iddoc_idx"),
        ]
        # NOTE: unique(id_document_type, id_document_number) is deliberately
        # NOT here. DATA_MODEL §4.2 defers it until the Sprint 8 archive data
        # is cleaned; enabling it now would block that archiving. BR-005 is
        # enforced as a service-level warning instead, driven by the
        # `identity_document_uniqueness_mode` setting.

    def __str__(self) -> str:
        return f"{self.participant_number} — {self.name_ar}"
