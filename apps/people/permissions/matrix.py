"""
The allow matrix — transcribed from PERMISSIONS.md §3, one row per screen.

**This file is DERIVED, not authored.** PERMISSIONS.md §3 is the single source
of truth; this module is its machine-readable projection, checked into the
repository so CI does not depend on a documentation tree that lives outside it.

Keeping the two in step:

* ``python manage.py check_permissions_matrix --docs <path-to-docs>`` re-parses
  PERMISSIONS.md §3 and fails on ANY divergence. Run it whenever either side
  changes.
* ``T-282`` fails the build if a Screen or business Role exists in code without
  a cell here — so a new screen cannot be added and quietly left unguarded.

Reading a cell: the string holds the allowed actions, exactly as the document
prints them. ``""`` means an explicit DENY (the document's ``—``). Superscript
footnote markers are dropped; the footnote itself is reproduced as a comment
where it changes the meaning of the cell.

An empty cell is not "unknown" — under BR-080 anything not granted is denied,
so a missing cell and an empty cell have the same effect. They are written out
in full anyway, because a matrix you can read row by row against the document
is the only kind anyone will actually audit.
"""

from __future__ import annotations

from apps.people.constants import BUSINESS_ROLES, Action, Screen
from apps.people.models import Role

#: Column order matches PERMISSIONS.md §3: MGR · REG · FIN · FIM · CSH · AUD
_COLUMNS: tuple[str, ...] = (
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
)

# ---------------------------------------------------------------------------
# PERMISSIONS.md §3 — screen, doc row, then one cell per column above
# ---------------------------------------------------------------------------
_ROWS: tuple[tuple[str, str, tuple[str, str, str, str, str, str]], ...] = (
    # --- §3.1 الرئيسية ----------------------------------------------------
    # ¹ REG sees registration indicators only, never revenue or partner dues.
    # ² BR-083 CSH sees the daily cash total only.
    # ᶠ Q-14 FIM sees financial oversight indicators and clearances awaiting him.
    (Screen.DASHBOARD, "§3.1/1", ("V P", "V", "V", "V", "V", "V P")),
    (Screen.ENROLL_FLOW, "§3.1/2", ("V", "V", "V", "", "", "V")),
    # --- §3.2 المشاركون والتسجيل -----------------------------------------
    # ³ CSH gets a restricted view inside the cash-collection context only.
    # ⁴ BR-018 approval requires the voucher to be recorded.
    # ᵍ Q-14 FIM view is restricted to the clearance-certification context.
    (Screen.STUDENTS, "§3.2/3", ("V C E P", "V C E P", "V P", "V", "V", "V P")),
    (Screen.STUDENT_NEW, "§3.2/4", ("V C", "V C", "", "", "", "V")),
    (Screen.ENROLLMENTS, "§3.2/5", ("V C E A P", "V C E P", "V P", "V", "", "V P")),
    # ⁵ BR-066 REG opens the request; MGR approves. ⁶ FIN edits the fee
    # difference settlement only.
    (Screen.TRANSFERS, "§3.2/6", ("V A P", "V C P", "V E P", "", "", "V P")),
    (Screen.TRANSFER_NEW, "§3.2/7", ("V C", "V C", "", "", "", "V")),
    (Screen.SPECIAL_CASES, "§3.2/8", ("V C E A P", "V C P", "V P", "", "", "V P")),
    # --- §3.3 البرامج والأسعار -------------------------------------------
    # ⁷ BR-006 approval blocked unless subject prices reconcile.
    # ⁸ BR-008 MGR recommends, the President approves (outside the system).
    # ⁹ REG prepares the MoHE draft; MGR submits.
    # ᵗ Q-14 price-list approval is the President's, NOT the finance manager's.
    (Screen.PROGRAMS, "§3.3/9", ("V C E A P", "V P", "V P", "", "", "V P")),
    (Screen.SHORT_COURSES, "§3.3/10", ("V C E A P", "V P", "V P", "", "", "V P")),
    (Screen.ONLINE_COURSES, "§3.3/11", ("V C E A P", "V P", "V P", "", "", "V P")),
    (Screen.COHORTS, "§3.3/12", ("V C E A P", "V P", "V P", "", "", "V P")),
    (Screen.PRICELISTS, "§3.3/13", ("V C E P", "V P", "V P", "", "", "V P")),
    (Screen.MOHE, "§3.3/14", ("V C E A P", "V P", "", "", "", "V P")),
    (Screen.MOHE_SUBMIT, "§3.3/15", ("V C E A P", "V C E", "", "", "", "V")),
    # --- §3.4 الشؤون المالية ---------------------------------------------
    # ¹⁰ BR-018 REG edits voucher_received fields only.
    # ¹¹ BR-025 CSH requests the void, FIN approves it.
    # ¹² BR-081 MGR never takes cash — enforced on UI and API alike.
    # ¹³ BR-027/BR-028 whoever collected does not approve the closing.
    # ¹⁴ CSH sees only his own day and enters the counted cash.
    # ¹⁵ BR-030 approval requires the President's approval reference.
    # ¹⁶ Explicit deny confirmed by the demo audit log (id=17).
    # ¹⁷ BR-034 FIN executes, never approves his own refund.
    # ᵖ Q-14 FIM reads to verify the balance. No C/E/A/X.
    # ᵈ D-29 FIM is an approval role, not an operational one.
    (Screen.PAYMENTS, "§3.4/16", ("V P", "V E P", "V A X P", "V P", "V C P", "V P")),
    (Screen.PAYMENT_NEW, "§3.4/17", ("", "", "V C", "", "V C", "")),
    (Screen.CLOSING, "§3.4/18", ("V A P", "", "V C E A P", "", "V C", "V P")),
    (Screen.DISCOUNTS, "§3.4/19", ("V C E A P", "", "V P", "", "", "V P")),
    (Screen.REFUNDS, "§3.4/20", ("V A P", "", "V C E P", "", "", "V P")),
    (Screen.EXTRA_FEES, "§3.4/21", ("V C E P", "V P", "V C E P", "", "", "V P")),
    (Screen.EXPENSES, "§3.4/22", ("V A P", "", "V C E P", "", "", "V P")),
    # --- §3.5 الشركاء والاتفاقيات ----------------------------------------
    # ¹⁸ BR-042 approval freezes the program/price snapshot.
    # ¹⁹ BR-051 approval freezes the claim; nobody may edit it afterwards.
    # BR-083 CSH sees no partner data at all.
    (Screen.PARTNERS, "§3.5/23", ("V C E P", "", "V P", "", "", "V P")),
    (Screen.AGREEMENTS, "§3.5/24", ("V C E A P", "", "V P", "", "", "V P")),
    (Screen.AGREEMENT_NEW, "§3.5/25", ("V C E", "", "", "", "", "V")),
    (Screen.ENTITLEMENT, "§3.5/26", ("V P", "", "V P", "", "", "V P")),
    (Screen.CLAIMS, "§3.5/27", ("V A P", "", "V C E P", "", "", "V P")),
    (Screen.SETTLEMENTS, "§3.5/28", ("V A P", "", "V C E P", "", "", "V P")),
    (Screen.OBLIGATIONS, "§3.5/29", ("V C E P", "", "V C E P", "", "", "V P")),
    # --- §3.6 الإنهاء والشهادات ------------------------------------------
    # ²⁰ BR-072 MGR owns steps 1 and 3; REG enters step 1 data.
    # ²¹ BR-073/BR-074 step 2 needs TWO certifications: FIN then FIM.
    # ²¹ᵃ Q-14 FIM's A is scoped to ClearanceStep.step_number == 2 only,
    #      and never as the same person who certified first (D-30).
    # ²² BR-075 no certificate without a COMPLETED clearance.
    (Screen.CLEARANCE, "§3.6/30", ("V C E A P", "V E P", "V E A P", "V A P", "", "V P")),
    (Screen.CERTIFICATES, "§3.6/31", ("V C P", "V P", "V P", "", "", "V P")),
    # --- §3.7 التقارير والنظام -------------------------------------------
    # ²³ REG gets reports 4 and 5 only. ²⁴ BR-083 CSH gets none.
    # ³¹ Q-14 FIM gets report 5 only — see REPORT_ACCESS below.
    # ²⁵ Δ-02 user administration belongs to SYSTEM_ADMINISTRATOR.
    # ²⁶ BR-084 nobody edits or deletes an audit row, ever.
    # ²⁷ BR-086 editing a setting creates a new effective-dated value.
    # ²⁸ FIN reads financial settings to verify; he does not change them.
    # ³⁰ BR-094 opening balances: FIN creates, a different reviewer, then MGR.
    (Screen.REPORTS, "§3.7/32", ("V P", "V P", "V P", "V P", "", "V P")),
    (Screen.USERS, "§3.7/33", ("V P", "", "", "", "", "V P")),
    (Screen.AUDIT, "§3.7/34", ("V P", "", "V P", "V P", "", "V P")),
    (Screen.SETTINGS, "§3.7/35", ("V E P", "", "V", "", "", "V P")),
    # ³² Sprint 8D-1 — the migration screen holds two different kinds of act.
    # Reading a workbook and archiving it are the manager's (C then A). Linking
    # an archived name to a living participant is an IDENTITY judgement, so E
    # moves to the registrar, who knows the participants; eight legacy numbers
    # carry two different names and no matcher may resolve them. FIN stays
    # VIEW-only here on purpose: this decision is not a financial one.
    (Screen.MIGRATION, "§3.7/36", ("V C A P", "V E", "V", "", "", "V P")),
    (Screen.OPENING_BALANCES, "§3.7/36أ", ("V A P", "", "V C E P", "", "", "V P")),
)


def _parse(cell: str) -> frozenset[str]:
    actions = frozenset(cell.split())
    unknown = actions - set(Action.values)
    if unknown:  # pragma: no cover - guards a transcription typo
        raise ValueError(f"Unknown action(s) in matrix cell: {sorted(unknown)}")
    return actions


#: (screen, role) -> allowed actions. The engine reads this and nothing else.
ALLOW: dict[tuple[str, str], frozenset[str]] = {
    (screen, role): _parse(cell)
    for screen, _doc_ref, cells in _ROWS
    for role, cell in zip(_COLUMNS, cells, strict=True)
}

#: (screen, role) -> the PERMISSIONS.md row a cell was transcribed from, so a
#: failing matrix test can name the paragraph to go and read.
DOC_REF: dict[tuple[str, str], str] = {
    (screen, role): doc_ref for screen, doc_ref, _cells in _ROWS for role in _COLUMNS
}

# ---------------------------------------------------------------------------
# Sub-screen scope: which of the seven reports each role may open
# PERMISSIONS.md §3.7 footnotes 23 (REG), 24 (CSH), 31 (FIM)
# ---------------------------------------------------------------------------
REPORT_ACCESS: dict[str, frozenset[int]] = {
    Role.CENTER_MANAGER: frozenset({1, 2, 3, 4, 5, 6, 7}),
    Role.REGISTRATION_OFFICER: frozenset({4, 5}),
    Role.FINANCE_OFFICER: frozenset({1, 2, 3, 4, 5, 6, 7}),
    # Q-14: report 5 is the participant statement of account — the one tool the
    # finance manager needs to confirm balance == 0 before certifying. Revenue,
    # net income and partner dues are outside his v1 scope (BR-099).
    Role.FINANCE_MANAGER: frozenset({5}),
    Role.CASHIER: frozenset(),
    Role.AUDIT_ACCOUNT: frozenset({1, 2, 3, 4, 5, 6, 7}),
}

# ---------------------------------------------------------------------------
# SYSTEM_ADMINISTRATOR — a technical role, outside the business matrix
# ---------------------------------------------------------------------------
# Δ-02 and PERMISSIONS.md footnote 25 assign user and role administration to
# the IT department's system administrator. Footnote 29 only *recommends*
# giving it the historical archive; a recommendation is not a decision, so it
# is NOT granted here. Sprint 8 grants it explicitly or does not.
#
# This grant covers user administration and reading the audit trail. Nothing
# financial, nothing operational.
SYSADMIN_ALLOW: dict[str, frozenset[str]] = {
    Screen.USERS: frozenset({Action.VIEW, Action.CREATE, Action.EDIT, Action.PRINT}),
    Screen.AUDIT: frozenset({Action.VIEW, Action.PRINT}),
}


def allowed_actions(role: str, screen: str) -> frozenset[str]:
    """Allow-list lookup only. Deny rules are evaluated before this is reached."""
    if role == Role.SYSTEM_ADMINISTRATOR:
        return SYSADMIN_ALLOW.get(screen, frozenset())
    return ALLOW.get((screen, role), frozenset())


def allowed_reports(role: str) -> frozenset[int]:
    return REPORT_ACCESS.get(role, frozenset())


__all__ = [
    "ALLOW",
    "BUSINESS_ROLES",
    "DOC_REF",
    "REPORT_ACCESS",
    "SYSADMIN_ALLOW",
    "allowed_actions",
    "allowed_reports",
]
