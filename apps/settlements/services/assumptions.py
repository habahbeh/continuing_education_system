"""
The four working assumptions behind Sprint 5 — and how each stays reversible.

⚠️ **None of these is a settled client decision.** Each is a professional
assumption recorded so that work could proceed, and each is expressed as DATA
so that the client's real answer costs a setting change rather than a
migration. Nothing here is a rule in code.

============  ==========================================  ==================
Question      Assumption                                  Reversal
============  ==========================================  ==================
Q-28          partner base = net, before tax              ``partner_base_mode``
Q-08          offsets at PARTNER level                    ``partner_offset_scope``
                                                          + per-obligation pin
Q-09          advance payouts create CLAWBACK obligations  agreement payout_timing
Q-16          overdue after 30 days with a balance         ``payment_overdue_days``
============  ==========================================  ==================

**Q-28 deserves the loudest warning.** At a 16% rate the difference between
net and gross is roughly 800 dinars on every 10,000 of partner claims, it
accrues across every cohort and every term, and claims end in SIGNED
settlements. It is free to change today and expensive once signatures exist —
so ``base_mode_snapshot`` is stamped on each claim, making the basis used
visible on the document rather than implied by whatever the setting says later.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from apps.core.services.settings_service import get_setting

#: Q-28 — "NET" (before tax) or "GROSS" (after). Seeded NET in Sprint 1.
BASE_MODE_KEY = "partner_base_mode"
BASE_MODE_NET = "NET"
BASE_MODE_GROSS = "GROSS"

#: Q-08 — "PARTNER" (any claim of that partner) or "AGREEMENT" (same
#: agreement only). An individual obligation may always be pinned tighter.
OFFSET_SCOPE_KEY = "partner_offset_scope"
OFFSET_SCOPE_PARTNER = "PARTNER"
OFFSET_SCOPE_AGREEMENT = "AGREEMENT"

#: Q-16 — days since the last payment before an enrolment counts as overdue.
OVERDUE_DAYS_KEY = "payment_overdue_days"


def partner_base_mode(*, as_of: date) -> str:
    """
    ⚠️ ASSUMPTION (Q-28) — which amount the partner shares.

    NET is the recommended professional assumption: tax is collected for the
    treasury, so splitting it would hand the partner money that was never the
    centre's, and charge them a tax burden that is not theirs (BR-093).

    Switching the setting to GROSS changes every future claim with no schema
    change, because ChargeLine already stores net AND gross.
    """
    return str(get_setting(BASE_MODE_KEY, as_of=as_of, default=BASE_MODE_NET)).upper()


def offset_scope(*, as_of: date) -> str:
    """
    ⚠️ ASSUMPTION (Q-08) — how far an offset may reach.

    PARTNER by default: a refund arising under one agreement may be recovered
    from a claim under another, for the same partner. That matches how the
    demo actually behaved and how an ongoing commercial relationship usually
    works — but it may contradict a specific contract, which is what
    ``PartnerObligation.restricted_to_agreement`` exists for.
    """
    return str(get_setting(OFFSET_SCOPE_KEY, as_of=as_of, default=OFFSET_SCOPE_PARTNER)).upper()


def overdue_days(*, as_of: date) -> int:
    """⚠️ ASSUMPTION (Q-16) — the payment grace period, in days."""
    return int(get_setting(OVERDUE_DAYS_KEY, as_of=as_of, default=30))


def net_ratio(charge_line: Any) -> Decimal:
    """
    The fraction of a gross amount that is net, for scaling a payment.

    A partial payment against a taxed line carries its tax proportionally, so
    the partner's base moves with it rather than being credited the whole
    payment.
    """
    if charge_line.gross_amount == 0:
        return Decimal("0")
    return charge_line.net_amount / charge_line.gross_amount


__all__ = [
    "BASE_MODE_GROSS",
    "BASE_MODE_KEY",
    "BASE_MODE_NET",
    "OFFSET_SCOPE_AGREEMENT",
    "OFFSET_SCOPE_KEY",
    "OFFSET_SCOPE_PARTNER",
    "OVERDUE_DAYS_KEY",
    "net_ratio",
    "offset_scope",
    "overdue_days",
    "partner_base_mode",
]
