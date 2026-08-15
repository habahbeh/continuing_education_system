"""
Creating charge lines — where the tax triple is computed (BR-098, Q-05).

**Q-25 is unanswered, and this module is where that matters.**

``default_tax_rate`` is seeded NULL, meaning "the rate is not known yet" —
which is a different statement from "there is no tax". A taxable line whose
rate is unknown cannot be priced, so this raises ``TaxRateNotConfigured``
rather than computing zero. The database backs that up: constraint C-28 makes
a taxable line without a captured rate unstorable, so even a caller who
bypassed this module could not write the silent zero.

The alternative — treating tax as zero until someone asks — produces receipts
that are quietly wrong, in a system whose whole purpose is to be auditable.
A loud failure costs a conversation; a silent zero costs a reconciliation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.billing.models import ChargeLine, ChargeType
from apps.core.exceptions import TaxRateNotConfigured
from apps.core.money import round_money
from apps.core.services.audit_service import write_audit
from apps.core.services.settings_service import get_setting

ENTITY = "billing.ChargeLine"

TAX_RATE_KEY = "default_tax_rate"


def effective_tax_rate(*, as_of: date) -> Decimal | None:
    """
    The tax rate in force, or None when it has never been configured (Q-25).

    None is the seeded state and means UNKNOWN. Callers must not read it as
    zero — the whole point of BR-098 is that those two are different.
    """
    raw = get_setting(TAX_RATE_KEY, as_of=as_of, default=None)
    return Decimal(str(raw)) if raw is not None else None


def compute_tax(*, net: Decimal, is_taxable: bool, as_of: date) -> tuple[Decimal, Decimal | None]:
    """
    Return ``(tax_amount, rate_snapshot)`` for a line.

    Raises TaxRateNotConfigured when the line is taxable and no rate exists.
    """
    if not is_taxable:
        return Decimal("0.000"), None

    rate = effective_tax_rate(as_of=as_of)
    if rate is None:
        raise TaxRateNotConfigured(
            "لا يمكن إنشاء بند خاضع للضريبة — نسبة الضريبة غير محدَّدة بعد "
            f"(الإعداد {TAX_RATE_KEY} فارغ · Q-25). "
            "الاحتساب صفراً ممنوع: «غير محدَّدة» تختلف عن «لا ضريبة»."
        )

    tax = round_money(net * rate / Decimal("100"))
    return tax, rate


@transaction.atomic
def create_charge_line(
    *,
    actor: Any,
    enrollment: Any,
    charge_type: str,
    description_ar: str,
    net_amount: Decimal,
    charged_on: date,
    is_taxable: bool = False,
    is_partner_shareable: bool = True,
    subject: Any = None,
    deposit_policy_snapshot: dict[str, Any] | None = None,
    request: Any = None,
) -> ChargeLine:
    """
    Create one charge line, computing the tax triple.

    ``is_revenue`` is not a parameter: it is decided by the charge type and
    enforced both ways by C-21. A deposit is a liability the university owes
    back; letting a caller mark one as revenue would inflate income reports
    and every partner's share at once.
    """
    tax_amount, rate_snapshot = compute_tax(net=net_amount, is_taxable=is_taxable, as_of=charged_on)
    is_deposit = charge_type == ChargeType.DEPOSIT

    if is_deposit and not deposit_policy_snapshot:
        raise ValueError(
            "بند التأمين يجب أن يحمل لقطة سياسته (C-26 · BR-096) — "
            "وإلا صارت شروط الاسترداد مجهولة بعد تغيير السياسة."
        )

    line = ChargeLine(
        enrollment=enrollment,
        charge_type=charge_type,
        description_ar=description_ar,
        net_amount=net_amount,
        is_taxable=is_taxable,
        tax_rate_snapshot=rate_snapshot,
        tax_amount=tax_amount,
        gross_amount=net_amount + tax_amount,
        charged_on=charged_on,
        subject=subject,
        # BR-092 — a deposit is never revenue, and never shareable by default.
        is_revenue=not is_deposit,
        is_partner_shareable=False if is_deposit else is_partner_shareable,
        deposit_policy_snapshot=deposit_policy_snapshot,
    )
    line.full_clean(exclude=["enrollment"])
    line.save()

    write_audit(
        action="CREATE",
        entity_type=ENTITY,
        entity_id=str(line.pk),
        reference=enrollment.code,
        summary_ar=f"بند رسم — {description_ar} ({line.gross_amount})",
        actor=actor,
        changes={
            "charge_type": charge_type,
            "net": str(net_amount),
            "tax": str(tax_amount),
            "tax_rate_snapshot": str(rate_snapshot) if rate_snapshot is not None else None,
            "gross": str(line.gross_amount),
            "is_revenue": line.is_revenue,
        },
        request=request,
    )
    return line


def charge_lines_from_quote(
    *, actor: Any, enrollment: Any, quote: Any, charged_on: date, request: Any = None
) -> list[ChargeLine]:
    """
    Turn a Sprint 3 ``PriceQuote`` into charge lines (BR-012).

    The quote's values are COPIED, so re-issuing a price list later cannot move
    this enrolment's numbers. Three lines at most, and each only when the quote
    says so:

    * registration — omitted entirely when the quote says no fee applies. A
      zero-value line would read as "we charged nothing", which is a different
      claim from "no fee is due" (BR-009, BR-010).
    * tuition — always.
    * deposit — only where the programme has a policy (BR-096), carrying a
      snapshot of that policy so the refund terms survive later edits.

    Taxability is left False pending Q-26: which fee types are taxable is the
    client's answer, and guessing it would be the silent assumption this whole
    module exists to avoid.
    """
    from apps.catalog.models import DepositPolicy

    lines: list[ChargeLine] = []

    if quote.charges_registration_fee:
        lines.append(
            create_charge_line(
                actor=actor,
                enrollment=enrollment,
                charge_type=ChargeType.REGISTRATION,
                description_ar="رسم تسجيل",
                net_amount=quote.registration_fee,
                charged_on=charged_on,
                # BR-009 — registration fees are outside the partner's base.
                is_partner_shareable=False,
                request=request,
            )
        )

    lines.append(
        create_charge_line(
            actor=actor,
            enrollment=enrollment,
            charge_type=ChargeType.TUITION,
            description_ar="رسوم دراسية",
            net_amount=quote.course_fee,
            charged_on=charged_on,
            request=request,
        )
    )

    if quote.has_deposit:
        policy = DepositPolicy.objects.get(pk=quote.deposit_policy_id)
        lines.append(
            create_charge_line(
                actor=actor,
                enrollment=enrollment,
                charge_type=ChargeType.DEPOSIT,
                description_ar=f"تأمين — {policy.name_ar}",
                net_amount=quote.deposit_amount,
                charged_on=charged_on,
                deposit_policy_snapshot=snapshot_of(policy),
                request=request,
            )
        )

    return lines


def snapshot_of(policy: Any) -> dict[str, Any]:
    """
    Freeze a deposit policy onto the charge line (ADR-012, BR-096).

    Copied rather than referenced because the policy is client-owned data that
    may be edited or disabled. Years later the question is not "what does the
    policy say now?" but "what did this participant agree to?".
    """
    return {
        "code": policy.code,
        "name_ar": policy.name_ar,
        "is_required": policy.is_required,
        "refund_trigger": policy.refund_trigger,
        "forfeit_on": list(policy.forfeit_on or []),
        "is_taxable": policy.is_taxable,
        "allows_partial_deduction": policy.allows_partial_deduction,
        "claim_deadline_days": policy.claim_deadline_days,
    }


__all__ = [
    "TAX_RATE_KEY",
    "charge_lines_from_quote",
    "compute_tax",
    "create_charge_line",
    "effective_tax_rate",
    "snapshot_of",
]
