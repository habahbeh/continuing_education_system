"""
Issuing certificates (WORKFLOWS §7, BR-075 … BR-079).

🐞 **The demo's loophole.** Certificate ``2026000002`` was issued as a
replacement with neither a clearance nor an original certificate to replace —
a complete way around BR-075, reachable by ticking one box. C-08 now makes
that shape unstorable, and ``issue_replacement`` refuses it before the
database has to.

Three things this module deliberately does NOT do:

* **compute a grade.** BR-078 is explicit that there is no grades module and
  no exams entity. The grade is entered by whoever issues the certificate,
  validated against the ``certificate_grades`` SETTING rather than a CHECK
  constraint, because the vocabulary belongs to the centre (Q-31 precedent).
* **renumber on reprint.** A reprint is the same certificate on new paper;
  a new number would put two documents into the world claiming to be one.
* **cover migrated historical certificates.** BR-089 gives those a separate
  counter scope, and that is Sprint 8's.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.audit_service import write_audit
from apps.core.services.numbering_service import ensure_sequence, next_number
from apps.core.services.settings_service import get_setting
from apps.operations.models import (
    Certificate,
    CertificateStatus,
    Clearance,
    ClearanceStatus,
    GradeSource,
)
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ENTITY = "operations.Certificate"

#: BR-076 — ten digits: the year, then a six-digit sequence within it.
CERTIFICATE_SCOPE = "certificate"
SEQUENCE_PADDING = 6

#: BR-078 — the vocabulary lives in data, not here.
GRADES_KEY = "certificate_grades"

#: BR-038 — the replacement fee, itself a setting since Sprint 1.
REPLACEMENT_FEE_KEY = "certificate_replacement_fee"


class ClearanceRequiredError(ValidationError):
    """BR-075 / D-22 — no certificate without a COMPLETED clearance."""


class OriginalCertificateRequiredError(ValidationError):
    """C-08 — the demo's replacement loophole."""


class GradeRequiredError(ValidationError):
    """BR-078 — the system does not compute one, so somebody must state it."""


class ReplacementFeeNotCollectedError(ValidationError):
    """BR-038 — the replacement fee is charged and collected before reissue."""


def available_grades(*, as_of: date) -> list[tuple[str, str]]:
    """(code, Arabic label) pairs, read from the setting (BR-078)."""
    raw = get_setting(GRADES_KEY, as_of=as_of, default=None)
    if not raw:
        return []
    try:
        pairs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [(str(code), str(label)) for code, label in pairs]


def completed_clearance_for(enrollment: Any) -> Clearance | None:
    """
    BR-075 — the one thing that authorises a certificate to exist.

    ``operations_clearance_one_live_per_enrollment`` guarantees at most one
    live clearance per enrolment, so this cannot silently choose between two.
    """
    return Clearance.objects.filter(enrollment=enrollment, status=ClearanceStatus.COMPLETED).first()


def _partition(issued_on: date) -> str:
    return f"{issued_on:%Y}"


def issue_certificate(
    *,
    actor: Any,
    enrollment: Any,
    grade: str,
    issued_on: date,
    duration_text: str = "",
    training_hours: int | None = None,
    request: Any = None,
) -> Certificate:
    """
    WORKFLOWS §7.2 K1 — issue, if and only if the clearance is complete.

    The permission check and the BR-075 gate both run BEFORE any transaction
    opens, so a refusal's denied-attempt row survives the raise (BR-100). This
    is the refusal the demo could not make at all.
    """
    policy.require(actor, Screen.CERTIFICATES, Action.CREATE, request=request)

    clearance = completed_clearance_for(enrollment)
    if clearance is None:
        write_audit(
            action="DENIED_ATTEMPT",
            entity_type=ENTITY,
            reference=enrollment.code,
            summary_ar=f"محاولة إصدار شهادة بلا براءة ذمة مكتملة — {enrollment.code}",
            actor=actor,
            denial_rule="D-22",
            request=request,
        )
        raise ClearanceRequiredError(
            f"لا شهادة بلا براءة ذمة مكتملة للتسجيل {enrollment.code} (BR-075 · D-22)."
        )

    grade = (grade or "").strip()
    if not grade:
        raise GradeRequiredError("التقدير إلزامي — النظام لا يحتسبه (BR-078).")

    valid = {code for code, _label in available_grades(as_of=issued_on)}
    if valid and grade not in valid:
        raise GradeRequiredError(
            f"تقدير غير معرَّف: {grade}. التقديرات المتاحة تُدار من الإعدادات ({GRADES_KEY}) — BR-078."
        )

    return _issue(
        actor=actor,
        enrollment=enrollment,
        clearance=clearance,
        grade=grade,
        issued_on=issued_on,
        duration_text=duration_text,
        training_hours=training_hours,
        request=request,
    )


@transaction.atomic
def _issue(
    *,
    actor: Any,
    enrollment: Any,
    clearance: Clearance,
    grade: str,
    issued_on: date,
    duration_text: str,
    training_hours: int | None,
    request: Any,
) -> Certificate:
    partition = _partition(issued_on)
    ensure_sequence(CERTIFICATE_SCOPE, partition, padding=SEQUENCE_PADDING)
    number = next_number(CERTIFICATE_SCOPE, partition, prefix=partition, padding=SEQUENCE_PADDING)

    certificate = Certificate.objects.create(
        certificate_number=number,
        participant=enrollment.participant,
        enrollment=enrollment,
        clearance=clearance,
        # ADR-012 — the catalogue will move on; the document must not.
        program_name_snapshot=enrollment.cohort.program.name_ar,
        duration_text=duration_text
        or f"{enrollment.cohort.starts_on} — {enrollment.cohort.ends_on}",
        training_hours=training_hours,
        grade=grade,
        grade_source=GradeSource.MANUAL,
        issued_on=issued_on,
        issued_by=actor,
        status=CertificateStatus.ISSUED,
    )

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(certificate.pk),
        reference=number,
        summary_ar=f"إصدار شهادة {number} — {enrollment.participant.name_ar}",
        actor=actor,
        changes={
            "event": "ISSUE",
            "certificate_number": number,
            "clearance": clearance.code,
            "grade": grade,
            "program": certificate.program_name_snapshot,
        },
        request=request,
    )
    return certificate


def replacement_fee_is_collected(*, enrollment: Any, as_of: date) -> Any:
    """
    BR-038 — find the collected replacement fee, or None.

    Returns the charge line so the certificate can LINK to it: "was the fee
    collected" then has one answer that survives a second replacement years
    later, rather than a guess about which extra fee on the account meant this.
    """
    from apps.billing.models import ChargeLine, ChargeType
    from apps.billing.services.account_service import ZERO, outstanding_for_line

    lines = ChargeLine.objects.filter(
        enrollment=enrollment,
        charge_type=ChargeType.EXTRA_FEE,
        voided=False,
        replacement_certificates__isnull=True,
    ).order_by("charged_on", "id")

    for line in lines:
        if outstanding_for_line(line) <= ZERO:
            return line
    return None


def issue_replacement(
    *,
    actor: Any,
    original: Certificate,
    issued_on: date,
    request: Any = None,
) -> Certificate:
    """
    WORKFLOWS §7.2 K3 — a replacement for a lost certificate (BR-038).

    Two conditions, and the demo enforced neither: there must be an ORIGINAL
    (C-08), and the 15-dinar fee must actually have been collected — not
    merely charged. A replacement issued against an unpaid fee is a document
    given away.
    """
    policy.require(actor, Screen.CERTIFICATES, Action.CREATE, request=request)

    if original.status == CertificateStatus.REPLACED:
        raise OriginalCertificateRequiredError(
            f"الشهادة {original.certificate_number} مُستبدَلة سلفاً — البدل يصدر عن الشهادة القائمة."
        )

    fee_line = replacement_fee_is_collected(enrollment=original.enrollment, as_of=issued_on)
    if fee_line is None:
        expected = get_setting(REPLACEMENT_FEE_KEY, as_of=issued_on, default=None)
        raise ReplacementFeeNotCollectedError(
            f"لم يُقبض رسم بدل الفاقد ({expected}) — لا يصدر البدل قبل القبض (BR-038)."
        )

    return _issue_replacement(
        actor=actor,
        original=original,
        fee_line=fee_line,
        issued_on=issued_on,
        request=request,
    )


@transaction.atomic
def _issue_replacement(
    *, actor: Any, original: Certificate, fee_line: Any, issued_on: date, request: Any
) -> Certificate:
    partition = _partition(issued_on)
    ensure_sequence(CERTIFICATE_SCOPE, partition, padding=SEQUENCE_PADDING)
    number = next_number(CERTIFICATE_SCOPE, partition, prefix=partition, padding=SEQUENCE_PADDING)

    replacement = Certificate.objects.create(
        certificate_number=number,
        participant=original.participant,
        enrollment=original.enrollment,
        # C-08 — a replacement carries no clearance of its own; the ORIGINAL
        # is what proves the clearance happened.
        clearance=None,
        program_name_snapshot=original.program_name_snapshot,
        duration_text=original.duration_text,
        training_hours=original.training_hours,
        grade=original.grade,
        grade_source=original.grade_source,
        issued_on=issued_on,
        issued_by=actor,
        is_replacement=True,
        replaces=original,
        replacement_fee_line=fee_line,
        status=CertificateStatus.ISSUED,
    )

    original.status = CertificateStatus.REPLACED
    original.save(update_fields=["status"])

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(replacement.pk),
        reference=number,
        summary_ar=f"إصدار بدل فاقد {number} بدلاً من {original.certificate_number}",
        actor=actor,
        changes={
            "event": "ISSUE",
            "certificate_number": number,
            "replaces": original.certificate_number,
            "fee_line": str(fee_line.pk),
            "fee_amount": str(fee_line.gross_amount),
        },
        request=request,
    )
    return replacement


def deliver(
    *, actor: Any, certificate: Certificate, delivered_on: date, request: Any = None
) -> Certificate:
    """WORKFLOWS §7.2 K2 — handed over, which is clearance step 3."""
    policy.require(actor, Screen.CERTIFICATES, Action.CREATE, request=request)

    if certificate.status != CertificateStatus.ISSUED:
        raise ValidationError("لا تُسلَّم إلا شهادة صادرة.")
    return _deliver(
        actor=actor, certificate=certificate, delivered_on=delivered_on, request=request
    )


@transaction.atomic
def _deliver(
    *, actor: Any, certificate: Certificate, delivered_on: date, request: Any
) -> Certificate:
    certificate.status = CertificateStatus.DELIVERED
    certificate.delivered_on = delivered_on
    certificate.save(update_fields=["status", "delivered_on"])

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(certificate.pk),
        reference=certificate.certificate_number,
        summary_ar=f"تسليم الشهادة {certificate.certificate_number}",
        actor=actor,
        changes={"delivered_on": delivered_on.isoformat()},
        request=request,
    )
    return certificate


def record_reprint(*, actor: Any, certificate: Certificate, request: Any = None) -> Certificate:
    """
    WORKFLOWS §7.5 — a reprint is audited and changes NOTHING.

    Same number, same row. A reprint that renumbered would put two documents
    into the world each claiming to be the certificate.
    """
    policy.require(actor, Screen.CERTIFICATES, Action.PRINT, request=request)

    write_audit(
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=str(certificate.pk),
        reference=certificate.certificate_number,
        summary_ar=f"إعادة طباعة الشهادة {certificate.certificate_number}",
        actor=actor,
        changes={"event": "PRINT", "renumbered": False},
        request=request,
    )
    return certificate


def list_certificates(*, actor: Any, query: str = "", request: Any = None) -> list[dict[str, Any]]:
    """Certificates as rows, replacements shown against what they replace."""
    from apps.core.display import person_name, text_of

    policy.require(actor, Screen.CERTIFICATES, Action.VIEW, request=request)

    queryset = Certificate.objects.select_related(
        "participant", "enrollment", "clearance", "replaces", "issued_by"
    )
    if query:
        queryset = queryset.filter(certificate_number__icontains=query) | queryset.filter(
            participant__name_ar__icontains=query
        )

    return [
        {
            "certificate_number": c.certificate_number,
            "participant_name": c.participant.name_ar,
            "participant_number": c.participant.participant_number,
            "enrollment_code": c.enrollment.code,
            "program_name": c.program_name_snapshot,
            "duration_text": c.duration_text,
            "training_hours": c.training_hours,
            "grade": c.grade,
            "issued_on": c.issued_on,
            "issued_by": person_name(c.issued_by),
            "status": c.status,
            "status_display": c.get_status_display(),
            "delivered_on": c.delivered_on,
            "is_replacement": c.is_replacement,
            "replaces": text_of(c.replaces, "certificate_number"),
            "clearance_code": text_of(c.clearance, "code"),
        }
        for c in queryset.order_by("-issued_on", "-certificate_number")
    ]


def certificate_instance(*, actor: Any, number: str, request: Any = None) -> Certificate:
    """The Certificate object, for handing back into this module (A-05)."""
    policy.require(actor, Screen.CERTIFICATES, Action.VIEW, request=request)
    return Certificate.objects.select_related("enrollment", "participant").get(
        certificate_number=number
    )


def issuable_enrollment_choices(*, actor: Any, request: Any = None) -> list[tuple[str, str]]:
    """
    Enrolments a certificate may be issued for — BR-075, enforced as a LIST.

    «لا شهادة بلا براءة ذمة». A participant whose clearance is blocked simply
    does not appear here, which is what the demo's own guidance promised
    («المحجوبون لا يظهرون») and what its code never actually did. The service
    refuses the forged request too; this spares the honest user the trip.
    """
    from apps.operations.models import Enrollment

    policy.require(actor, Screen.CERTIFICATES, Action.CREATE, request=request)

    cleared = set(
        Clearance.objects.filter(status=ClearanceStatus.COMPLETED).values_list(
            "enrollment_id", flat=True
        )
    )
    already = set(
        Certificate.objects.filter(is_replacement=False).values_list("enrollment_id", flat=True)
    )
    return [
        (e.code, f"{e.code} — {e.participant.name_ar}")
        for e in Enrollment.objects.select_related("participant")
        .filter(pk__in=cleared)
        .order_by("-enrolled_on")
        if e.pk not in already
    ]


def certificate_document(*, actor: Any, number: str, request: Any = None) -> dict[str, Any]:
    """
    Everything the printed certificate shows (§7).

    ⚠️ Built from the REQUIREMENTS, not from the centre's blank certificate —
    which was not in the client folder. The chrome carries the marker that
    says so.

    §7 lists exactly what the document contains: participant name, course
    name, course duration, hours, GRADE, number, date. All seven are on the
    ``Certificate`` row already, snapshotted at issue (ADR-012), so printing
    one issued years ago shows the programme as it was named THEN.

    The signature and both stamps are applied BY HAND — §7 says so — which is
    why what prints is their labelled empty space. The system does not draw a
    stamp it has no authority to apply.

    ``clearance`` is carried through so the printed document can name the
    clearance that authorised it (BR-075). A replacement carries none of its
    own by design (C-08); it names the ORIGINAL, which is what proves the
    clearance happened.
    """
    from apps.core.services import document_settings

    policy.require(actor, Screen.CERTIFICATES, Action.VIEW, request=request)

    rows = list_certificates(actor=actor, request=request)
    detail = next((r for r in rows if r["certificate_number"] == number), None)
    if detail is None:
        raise Certificate.DoesNotExist(number)

    as_of = timezone.now().date()
    detail["chrome"] = document_settings.chrome(as_of=as_of)
    detail["labels"] = document_settings.certificate_labels(as_of=as_of)
    return detail


__all__ = [
    "CERTIFICATE_SCOPE",
    "GRADES_KEY",
    "ClearanceRequiredError",
    "GradeRequiredError",
    "OriginalCertificateRequiredError",
    "ReplacementFeeNotCollectedError",
    "available_grades",
    "certificate_document",
    "certificate_instance",
    "completed_clearance_for",
    "deliver",
    "issuable_enrollment_choices",
    "issue_certificate",
    "issue_replacement",
    "list_certificates",
    "record_reprint",
    "replacement_fee_is_collected",
]
