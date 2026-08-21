"""
The partner-money screens — claims, settlements, obligations, agreements.

What these prove is that the screens carry the rules rather than restating
them: an approved claim offers nothing because it is sealed, a settlement
refuses its signature while a balance stands, and the numbers on the claim
detail are the same numbers Sprint 8A pinned — read off the page this time,
not out of a service.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PERIOD_END = date(2026, 12, 20)
FULL = Decimal("270.000")
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def approver(seeded_settings):
    """A second centre manager, so nobody approves their own claim (D-18)."""
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.stl.ui", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def _build(client, cohort_code: str, period_to=PERIOD_END):
    return client.post(
        reverse("settlements:claims"),
        {
            "action": "build",
            "cohort_code": cohort_code,
            "period_from": TERM_START.isoformat(),
            "period_to": period_to.isoformat(),
            "trigger_type": "END_OF_COURSE",
            "trigger_reference_ar": "نهاية الدورة",
        },
        follow=True,
    )


# ---------------------------------------------------------------------------
# Agreements — read only
# ---------------------------------------------------------------------------
def test_the_agreement_detail_shows_every_configurable_term(
    signed_in, manager, percent_agreement
) -> None:
    """
    §3.4 — «النظام يدعم ثلاثة نماذج ولا يثبّت أياً منها في الكود».

    A screen showing only "50%" would hide the exclusions, the discount split
    and the settlement cycle, which are exactly what differs between the
    signed agreements in the client file.
    """
    page = signed_in(manager).get(
        reverse("partners:agreement-detail", args=[percent_agreement.agreement_number])
    )
    agreement = page.context["agreement"]

    assert agreement["percent_rate"] == Decimal("50.0000")
    assert agreement["exclude_registration_fee"] is True
    assert agreement["exclude_deposits"] is True
    assert "discount_split_mode_display" in agreement
    assert "settlement_cycle_display" in agreement
    assert "payout_timing_display" in agreement


def test_no_screen_offers_to_create_an_agreement(signed_in, manager) -> None:
    """
    The matrix grants C E here and no creating service exists.

    Offering a button that leads nowhere would be worse than offering none —
    the gap is recorded in the plan, not papered over on the screen.
    """
    page = signed_in(manager).get(reverse("partners:agreements"))
    body = page.content.decode()
    # The only POST form on the page is the topbar's sign-out.
    assert body.count('<form method="post"') == 1
    assert "/auth/logout/" in body


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------
def test_a_claim_is_built_with_the_sprint_8a_numbers(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    250 tuition shared at 50% is 125, registration excluded — off the screen.

    The same arithmetic ``test_discount_partner_effect`` pins at the service
    layer, read here through the view so the screen is proved not to have
    recomputed anything of its own.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))

    response = _build(signed_in(finance), cohort_with_agreement.code)
    claim = response.context["claim"]

    assert claim["excluded_registration"] == Decimal("20.000")
    assert claim["distribution_base"] == Decimal("250.000")
    assert claim["partner_share"] == Decimal("125.000")
    assert claim["discount_partner_burden"] == Decimal("0.000")


def test_excluded_participants_appear_with_their_reason(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    §5.4 — a withdrawn participant earns the partner nothing, and says so.

    Dropping them from the claim would make it look like a claim nobody had
    checked. BR-045 wants the consideration visible.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    make_paid_enrollment(cohort_with_agreement, index=2, amount=str(FULL), status="WITHDRAWN")

    response = _build(signed_in(finance), cohort_with_agreement.code)
    lines = response.context["claim"]["lines"]

    excluded = [line for line in lines if not line["is_included"]]
    assert len(excluded) == 1
    assert excluded[0]["exclusion_reason"] == "WITHDRAWN"


def test_an_approved_claim_is_shown_sealed_and_offers_nothing(
    signed_in, finance, approver, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    BR-051 · D-12 — approval seals it, and the screen stops offering actions.

    The service refuses an edit either way; a screen still showing buttons
    would be saying something untrue about a signed document.
    """
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    built = _build(signed_in(finance), cohort_with_agreement.code)
    code = built.context["claim"]["code"]
    url = reverse("settlements:claim-detail", args=[code])

    signed_in(approver).post(url, {"action": "approve"}, follow=True)

    page = signed_in(finance).get(url)
    assert page.context["claim"]["is_frozen"] is True
    assert page.context["can_edit"] is False
    assert page.context["claim"]["hash_verifies"] is True

    refused = signed_in(finance).post(url, {"action": "offsets"}, follow=True)
    assert "BR-051" in refused.content.decode()


def test_nobody_approves_the_claim_they_built(
    signed_in, finance, cohort_with_agreement, make_paid_enrollment
) -> None:
    """D-18 — and the finance officer holds no APPROVE here in any case."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    built = _build(signed_in(finance), cohort_with_agreement.code)
    code = built.context["claim"]["code"]

    refused = signed_in(finance).post(
        reverse("settlements:claim-detail", args=[code]), {"action": "approve"}
    )
    assert refused.status_code == 403


def test_a_cohort_without_an_agreement_is_not_offered(
    signed_in, finance, priced_catalog, active_semester
) -> None:
    """A cohort with no partner has no claim to build."""
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    solo = Cohort.objects.create(
        code="CO-NOPARTNER",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة بلا شريك",
        starts_on=TERM_START,
        ends_on=PERIOD_END,
        capacity=25,
    )
    page = signed_in(finance).get(reverse("settlements:claims"))
    offered = dict(page.context["form"].fields["cohort_code"].choices)
    assert solo.code not in offered


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------
def test_the_period_is_derived_from_the_agreement(
    signed_in, finance, percent_agreement, cohort_with_agreement
) -> None:
    """
    §3.1 · تناغم بند 11 — «نهاية كل دورة قصيرة» takes the cohort's end date.

    The operator names the cohort; the system decides the period. Typing a
    period by hand is how two settlements come to overlap.
    """
    response = signed_in(finance).post(
        reverse("settlements:settlements"),
        {
            "action": "open",
            "agreement_number": percent_agreement.agreement_number,
            "code": "STL-UI-1",
            "opens_on": TERM_START.isoformat(),
            "cohort_code": cohort_with_agreement.code,
        },
        follow=True,
    )
    settlement = response.context["settlement"]
    assert settlement["period_to"] == cohort_with_agreement.ends_on


def test_signing_is_refused_while_a_balance_stands(
    signed_in, finance, approver, percent_agreement, cohort_with_agreement, make_paid_enrollment
) -> None:
    """BR-053 — a settlement states that nothing remains outstanding."""
    make_paid_enrollment(cohort_with_agreement, index=1, amount=str(FULL))
    built = _build(signed_in(finance), cohort_with_agreement.code)
    signed_in(approver).post(
        reverse("settlements:claim-detail", args=[built.context["claim"]["code"]]),
        {"action": "approve"},
        follow=True,
    )

    opened = signed_in(finance).post(
        reverse("settlements:settlements"),
        {
            "action": "open",
            "agreement_number": percent_agreement.agreement_number,
            "code": "STL-UI-2",
            "opens_on": TERM_START.isoformat(),
            "cohort_code": cohort_with_agreement.code,
        },
        follow=True,
    )
    url = reverse("settlements:settlement-detail", args=[opened.context["settlement"]["code"]])

    signed_in(finance).post(url, {"action": "attach"}, follow=True)
    refused = signed_in(approver).post(
        url, {"action": "sign", "signed_on": PERIOD_END.isoformat()}, follow=True
    )
    assert "BR-053" in refused.content.decode()

    signed_in(finance).post(url, {"action": "pay", "amount": "125.000"}, follow=True)
    done = signed_in(approver).post(
        url, {"action": "sign", "signed_on": PERIOD_END.isoformat()}, follow=True
    )
    assert done.context["settlement"]["status"] == "SIGNED"


def test_the_finance_officer_cannot_sign_a_settlement(
    signed_in, finance, percent_agreement, cohort_with_agreement
) -> None:
    """§8 — the finance officer prepares and the centre manager approves."""
    opened = signed_in(finance).post(
        reverse("settlements:settlements"),
        {
            "action": "open",
            "agreement_number": percent_agreement.agreement_number,
            "code": "STL-UI-3",
            "opens_on": TERM_START.isoformat(),
            "cohort_code": cohort_with_agreement.code,
        },
        follow=True,
    )
    url = reverse("settlements:settlement-detail", args=[opened.context["settlement"]["code"]])

    refused = signed_in(finance).post(url, {"action": "sign", "signed_on": PERIOD_END.isoformat()})
    assert refused.status_code == 403


def test_an_agreement_with_an_open_cycle_is_not_offered_again(
    signed_in, finance, percent_agreement, cohort_with_agreement
) -> None:
    """One open settlement per agreement, so one claim cannot fall into two."""
    signed_in(finance).post(
        reverse("settlements:settlements"),
        {
            "action": "open",
            "agreement_number": percent_agreement.agreement_number,
            "code": "STL-UI-4",
            "opens_on": TERM_START.isoformat(),
            "cohort_code": cohort_with_agreement.code,
        },
        follow=True,
    )
    page = signed_in(finance).get(reverse("settlements:settlements"))
    offered = dict(page.context["form"].fields["agreement_number"].choices)
    assert percent_agreement.agreement_number not in offered


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------
def test_obligations_became_recordable_in_8c2(signed_in, manager) -> None:
    """
    Sprint 8B-2 left this screen read-only because no creating service existed.

    Sprint 8C-2 built one for §5.6's three manual obligations, so the form is
    now offered — to the roles the matrix grants CREATE, which is what this
    asserts rather than the mere presence of a form tag.
    """
    page = signed_in(manager).get(reverse("settlements:obligations"))

    assert page.status_code == 200
    assert page.context["can_create"] is True
    assert page.context["form"] is not None

    # The absence penalty is NOT offered by hand — BR-057 makes it a formula.
    offered = dict(page.context["form"].fields["obligation_type"].choices)
    assert "TRAINER_ABSENCE_PENALTY" not in offered
    assert "TRAINER_SALARIES" in offered


def test_the_audit_account_still_cannot_record_an_obligation(signed_in, seeded_settings) -> None:
    """Read-only stays read-only for the role that is only ever read-only."""
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.obl", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    page = signed_in(auditor).get(reverse("settlements:obligations"))

    assert page.status_code == 200
    assert page.context["can_create"] is False
    assert page.context["form"] is None


def _record_obligation(client, partner, **extra):
    payload = {
        "action": "record",
        "code": "OBL-UI-1",
        "partner_code": partner.code,
        "obligation_type": "FIELD_TRAINING_EXPENSE",
        "amount": "300.000",
        "occurred_on": TERM_START.isoformat(),
        "statement_reference": "كشف المركز 2026/9",
        "cohort_code": "",
    }
    payload.update(extra)
    return client.post(reverse("settlements:obligations"), payload, follow=True)


def test_an_obligation_is_recorded_through_the_screen(
    signed_in, manager, partner, cohort_with_agreement
) -> None:
    """§5.6 · تناغم بند 7 — the centre's field-training costs, charged back."""
    response = _record_obligation(signed_in(manager), partner)

    assert response.status_code == 200
    row = next(o for o in response.context["obligations"] if o["code"] == "OBL-UI-1")
    assert row["amount"] == Decimal("300.000")
    assert row["statement_reference"] == "كشف المركز 2026/9"


def test_an_obligation_without_a_statement_is_refused(
    signed_in, manager, partner, cohort_with_agreement
) -> None:
    """
    «بموجب كشف من المركز» — the reference is what makes the debt provable.

    A partner who cannot trace the charge back to a document will dispute it
    at settlement time, which is exactly when it is too late.
    """
    response = _record_obligation(signed_in(manager), partner, statement_reference="")

    assert response.status_code == 200
    assert not any(o["code"] == "OBL-UI-1" for o in response.context["obligations"])


def test_an_obligation_recorded_against_a_cohort_is_pinned_to_its_agreement(
    signed_in, manager, partner, cohort_with_agreement
) -> None:
    """
    Q-08 offsets partner-wide by default, but a debt arising under ONE
    contract belongs to it — the same rule the clawback and the refund
    recovery already follow.
    """
    response = _record_obligation(
        signed_in(manager), partner, code="OBL-UI-2", cohort_code=cohort_with_agreement.code
    )

    row = next(o for o in response.context["obligations"] if o["code"] == "OBL-UI-2")
    assert row["cohort_code"] == cohort_with_agreement.code
    assert row["restricted_to"] == cohort_with_agreement.agreement.agreement_number
