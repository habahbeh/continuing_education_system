"""
The participant file's operational half — Sprint 8I.

The participant page used to show one thing: the application form's fields.
Staff open that page to answer a different question — *where does this
person stand?* — and were sent to four other screens to find out. This read
gathers what those screens already show, for one participant, and says what
the next step is.

Three rules, in order of importance:

* **It changes nothing.** Every value is read through the service that owns
  it: enrolments and their balances through ``enrollment_service`` (which
  takes them from ``get_account_state`` — the one place the equation lives),
  clearances through ``clearance_service``, certificates through
  ``certificate_service``. ``next_action`` is a sentence for a person, not a
  rule for the system: nothing downstream reads it.

* **It opens no door.** Each block is gathered only when ``policy`` lets the
  actor onto the screen that shows it anyway. A cashier, whose participant
  view BR-101 narrows to number and name, still gets the enrolment rows and
  balances here — because the enrolments screen already gives them those.
  The personal fields keep going through ``participant_service`` and are not
  touched by this module.

* **It lives here, not in ``people``.** ``people`` imports nothing from
  ``operations``; this module depends on both, so it sits on the side that
  already does.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils.translation import gettext as _

from apps.billing.models import RefundStatus
from apps.billing.services import discount_service, extra_fee_service, refund_service
from apps.cashbox.services import payment_service
from apps.operations.models import CertificateStatus, ClearanceStatus, Enrollment, EnrollmentStatus
from apps.operations.services import (
    certificate_service,
    clearance_service,
    enrollment_service,
    transfer_service,
)
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

ZERO = Decimal("0.000")


def participant_hub(*, actor: Any, participant_number: str, request: Any = None) -> dict[str, Any]:
    """
    Everything the participant file shows beyond the application fields.

    Keys are present only when the actor may see that block, so a template
    that checks for the key is checking the permission.
    """
    policy.require(actor, Screen.STUDENTS, Action.VIEW, request=request)

    hub: dict[str, Any] = {}

    if policy.is_allowed(actor, Screen.ENROLLMENTS, Action.VIEW):
        rows = enrollment_service.list_enrollments(
            actor=actor, participant_number=participant_number, request=request
        )
        hub["enrollments"] = rows  # newest first, as the list screen orders them
        hub["latest"] = rows[0] if rows else None
        hub["totals"] = _totals(rows)

    # The money behind the balance, item by item — what report 5 lists and
    # the demo's file showed. Each list is the screen's own read; the
    # register-level lists take the enrolment code, the receipts take the
    # participant, the transfers carry the participant on every row.
    codes = [row["code"] for row in hub.get("enrollments", [])]
    if policy.is_allowed(actor, Screen.PAYMENTS, Action.VIEW):
        hub["receipts"] = payment_service.list_receipts(
            actor=actor, participant_number=participant_number, request=request
        )
    if codes and policy.is_allowed(actor, Screen.EXTRA_FEES, Action.VIEW):
        hub["extra_fees"] = _per_enrollment(
            extra_fee_service.list_extra_fees, actor, codes, request
        )
    if codes and policy.is_allowed(actor, Screen.DISCOUNTS, Action.VIEW):
        hub["discounts"] = _per_enrollment(discount_service.list_discounts, actor, codes, request)
    if codes and policy.is_allowed(actor, Screen.REFUNDS, Action.VIEW):
        hub["refunds"] = _per_enrollment(refund_service.list_refunds, actor, codes, request)
    if policy.is_allowed(actor, Screen.TRANSFERS, Action.VIEW):
        hub["transfers"] = [
            row
            for row in transfer_service.list_transfers(actor=actor, request=request)
            if row["participant_number"] == participant_number
        ]
    if "totals" in hub:
        _component_totals(hub)

    if policy.is_allowed(actor, Screen.CLEARANCE, Action.VIEW):
        hub["clearances"] = [
            row
            for row in clearance_service.list_clearances(actor=actor, request=request)
            if row["participant_number"] == participant_number
        ]

    if policy.is_allowed(actor, Screen.CERTIFICATES, Action.VIEW):
        hub["certificates"] = [
            row
            for row in certificate_service.list_certificates(actor=actor, request=request)
            if row["participant_number"] == participant_number
        ]
        # The one that stands: a replaced certificate is history, its
        # replacement is the document in the participant's hand.
        hub["certificate_live"] = next(
            (c for c in hub["certificates"] if c["status"] != CertificateStatus.REPLACED),
            hub["certificates"][0] if hub["certificates"] else None,
        )

    if "enrollments" in hub:
        hub["next_action"] = _next_action(
            latest=hub["latest"],
            clearances=hub.get("clearances"),
            certificates=hub.get("certificates"),
        )
    return hub


def latest_enrollments(
    *, actor: Any, participant_numbers: list[str], request: Any = None
) -> dict[str, dict[str, Any]]:
    """
    The newest enrolment of each listed participant — one query for the whole
    list screen, so the quick view can name it without a lookup per row.

    Deliberately NOT the balance: that goes through ``get_account_state`` per
    enrolment, several aggregates each, and two hundred rows of it is not a
    list screen any more. The quick view links to the statement instead.

    Empty for a role the enrolments screen does not admit; the caller renders
    nothing for a participant it has no entry for.
    """
    if not participant_numbers or not policy.is_allowed(actor, Screen.ENROLLMENTS, Action.VIEW):
        return {}
    queryset = (
        Enrollment.objects.filter(participant__participant_number__in=participant_numbers)
        .select_related("participant", "cohort")
        .order_by("participant_id", "-enrolled_on", "-code")
    )
    latest: dict[str, dict[str, Any]] = {}
    for enrollment in queryset:
        number = enrollment.participant.participant_number
        if number in latest:
            continue  # ordered newest first per participant
        latest[number] = {
            "code": enrollment.code,
            "cohort_name": enrollment.cohort.name_ar,
            "enrolled_on": enrollment.enrolled_on,
            "status": enrollment.status,
            "status_display": enrollment.get_status_display(),
            "is_final": enrollment.is_final,
            "is_approved": enrollment.approved_by_id is not None,
        }
    return latest


def _per_enrollment(read: Any, actor: Any, codes: list[str], request: Any) -> list[dict[str, Any]]:
    """One participant has a handful of enrolments; the lists filter by one."""
    rows: list[dict[str, Any]] = []
    for code in codes:
        rows.extend(read(actor=actor, enrollment_code=code, request=request))
    return rows


def _component_totals(hub: dict[str, Any]) -> None:
    """
    The parts the balance is made of, for the totals strip. Informational:
    the balance itself stays what ``get_account_state`` summed per enrolment,
    and these are not fed back into it.
    """
    totals = hub["totals"]
    if "receipts" in hub:
        totals["receipts_count"] = len(hub["receipts"])
    if "extra_fees" in hub:
        totals["extra_fees_total"] = sum((row["amount"] for row in hub["extra_fees"]), ZERO)
    if "refunds" in hub:
        totals["refunds_executed_total"] = sum(
            (row["amount"] for row in hub["refunds"] if row["status"] == RefundStatus.EXECUTED),
            ZERO,
        )


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sums of what each row already carries from ``get_account_state``."""
    due = sum((row["total_due"] for row in rows), ZERO)
    paid = sum((row["total_paid"] for row in rows), ZERO)
    discount = sum((row["total_discount"] for row in rows), ZERO)
    balance = sum((row["balance"] for row in rows), ZERO)
    return {
        "total_due": due,
        "total_paid": paid,
        "total_discount": discount,
        "balance": balance,
        "participant_owes": balance > ZERO,
        "centre_owes": balance < ZERO,
        "is_settled": balance == ZERO,
        "count": len(rows),
    }


def _next_action(
    *,
    latest: dict[str, Any] | None,
    clearances: list[dict[str, Any]] | None,
    certificates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    One sentence about the newest enrolment, in the order the workflow runs.

    Guidance only. It repeats what the screens already enforce — the voucher
    gate (BR-018), the first-payment gate, the clearance's financial step
    (BR-073), the certificate needing a completed clearance (BR-075) — and
    enforces none of it. ``goto`` names the screen the sentence points at;
    the template turns it into a link.

    ``clearances``/``certificates`` are ``None`` when the actor may not see
    them; the sentence then stops at what the actor can act on.
    """
    if latest is None:
        return {
            "tone": "info",
            "text": _(
                "لا تسجيلات لهذا المشارك بعد. الخطوة التالية: تسجيله في دفعة من شاشة التسجيلات."
            ),
            "goto": "enrollments",
        }

    code = latest["code"]
    status = latest["status"]

    if not latest["is_final"]:
        if not latest["voucher_received"]:
            if latest["participant_owes"] and latest["total_paid"] == ZERO:
                return {
                    "tone": "warn",
                    "text": _(
                        "التسجيل %(code)s بانتظار الدفع: تحصيل الدفعة الأولى من الصندوق، "
                        "ثم تسجيل استلام الوصل."
                    )
                    % {"code": code},
                    "goto": "payments",
                }
            return {
                "tone": "warn",
                "text": _(
                    "التسجيل %(code)s مدفوع ولم يُسجَّل استلام الوصل بعد — "
                    "سجّله ليُتاح الاعتماد (BR-018)."
                )
                % {"code": code},
                "goto": "enrollments",
            }
        if not latest["is_approved"]:
            return {
                "tone": "warn",
                "text": _("التسجيل %(code)s استُلم وصله وينتظر اعتماد مدير المركز.")
                % {"code": code},
                "goto": "enrollments",
            }
        if latest["participant_owes"]:
            return {
                "tone": "warn",
                "text": _(
                    "التسجيل %(code)s معتمد ومنتظم، وعليه رصيد متبقٍ %(balance)s — "
                    "متابعة التحصيل قبل التخرج."
                )
                % {"code": code, "balance": latest["balance"]},
                "goto": "account",
                "arg": code,
            }
        return {
            "tone": "ok",
            "text": _(
                "التسجيل %(code)s منتظم ومسدَّد. لا إجراء مطلوب الآن؛ عند انتهاء الدورة يُسجَّل التخرج."
            )
            % {"code": code},
            "goto": "enrollments",
        }

    # A final status. Only the three exits §6.4 names open a clearance
    # (``CLEARANCE_CASE_FOR_STATUS`` is the clearance service's own list); a
    # cancelled or transferred-out enrolment has nothing to clear, and any
    # credit it left is the refund screen's business.
    if status not in clearance_service.CLEARANCE_CASE_FOR_STATUS:
        if latest["centre_owes"]:
            return {
                "tone": "warn",
                "text": _(
                    "التسجيل %(code)s في حالة %(status)s وله رصيد دائن %(credit)s — "
                    "يُردّ من شاشة الاستردادات."
                )
                % {"code": code, "status": latest["status_display"], "credit": -latest["balance"]},
                "goto": "refunds",
            }
        return {
            "tone": "info",
            "text": _("التسجيل %(code)s في حالة %(status)s. لا إجراء مطلوب.")
            % {"code": code, "status": latest["status_display"]},
            "goto": "enrollments",
        }
    if clearances is None:
        return {
            "tone": "info",
            "text": _(
                "التسجيل %(code)s في حالة نهائية (%(status)s). ما بعده — براءة الذمة — على شاشتها."
            )
            % {"code": code, "status": latest["status_display"]},
            "goto": "enrollments",
        }
    live = [
        c
        for c in clearances
        if c["enrollment_code"] == code and c["status"] != ClearanceStatus.CANCELLED
    ]
    if not live:
        return {
            "tone": "warn",
            "text": _(
                "التسجيل %(code)s انتهى (%(status)s) ولا براءة ذمة له بعد — "
                "افتحها من شاشة براءة الذمة."
            )
            % {"code": code, "status": latest["status_display"]},
            "goto": "clearances",
        }
    clearance = live[0]
    if not clearance["is_completed"]:
        return {
            "tone": "warn",
            "text": _("براءة الذمة %(clr)s %(state)s — الخطوة المنتظرة: %(step)s.")
            % {
                "clr": clearance["code"],
                "state": clearance["status_display"],
                "step": clearance["pending_step_name"] or clearance["status_display"],
            },
            "goto": "clearance",
            "arg": clearance["code"],
        }
    if status == EnrollmentStatus.COMPLETED and certificates is not None:
        if not any(c["enrollment_code"] == code for c in certificates):
            return {
                "tone": "warn",
                "text": _(
                    "براءة الذمة مكتملة ولم تُصدر الشهادة بعد — أصدرها من شاشة الشهادات (BR-075)."
                ),
                "goto": "certificates",
            }
        return {
            "tone": "ok",
            "text": _("الملف مكتمل: تخرّج، وبراءة ذمة، وشهادة صادرة."),
            "goto": "certificates",
        }
    return {
        "tone": "ok",
        "text": _("براءة الذمة %(clr)s مكتملة. لا إجراء مطلوب.") % {"clr": clearance["code"]},
        "goto": "clearance",
        "arg": clearance["code"],
    }


__all__ = ["latest_enrollments", "participant_hub"]
