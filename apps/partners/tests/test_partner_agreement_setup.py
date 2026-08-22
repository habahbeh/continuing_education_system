"""
Sprint 8F — recording a signed partner and a signed agreement.

The three calculation models are the reason this module is long. §3.4 says the
system supports three and hard-codes none of them, and the database enforces
that by refusing an agreement missing the numbers its own model needs. A form
that collected one flat set of fields would therefore fail at the constraint
rather than at the box, so each model is exercised here both ways: complete
and accepted, incomplete and refused with a message naming the field.

The other half is authority. Nothing below grants a role anything — every
check names the matrix cell it reads, and the roles that hold ``V`` without
``C`` are asserted to be refused rather than merely un-linked.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.exceptions import ImmutableRecordError
from apps.partners.forms import AgreementForm
from apps.partners.models import (
    Agreement,
    AgreementStatus,
    CalculationModel,
    DiscountSplitMode,
    Partner,
    PartnerStatus,
    PartnerType,
    PayoutTiming,
    SettlementCycle,
)
from apps.partners.services import partner_service
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "partner-probe-1234"
TERMS: dict[str, Any] = {
    "signed_on": date(2026, 8, 1),
    "valid_from": date(2026, 9, 1),
    "valid_to": date(2027, 8, 31),
}
MODEL_TERMS: dict[str, dict[str, Any]] = {
    CalculationModel.PERCENT: {"percent_rate": Decimal("50.0000")},
    CalculationModel.FIXED_PER_STUDENT: {
        "fixed_amount_per_student": Decimal("195.000"),
        "sell_price": Decimal("250.000"),
    },
    CalculationModel.SERVICE_COMMISSION: {
        "service_name_ar": "تنظيم قاعات",
        "service_price": Decimal("800.000"),
        "commission_amount": Decimal("120.000"),
    },
}


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def manager(seeded_settings: None) -> User:
    return _user(Role.CENTER_MANAGER, "mgr.partner.setup")


@pytest.fixture
def a_partner(manager: User) -> Partner:
    """Created through the service, not the ORM — that is the thing on trial."""
    return partner_service.create_partner(
        actor=manager,
        data={
            "code": "PRT-8F",
            "name_ar": "شركة شريك",
            "partner_type": PartnerType.COMPANY,
        },
    )


def _terms(model: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "agreement_number": f"8F/{model[:4]}",
        "title_ar": "اتفاقية",
        "calculation_model": model,
        "discount_split_mode": DiscountSplitMode.HALF,
        "payout_timing": PayoutTiming.END_OF_COURSE,
        "settlement_cycle": SettlementCycle.END_OF_COURSE,
        **TERMS,
        **MODEL_TERMS[model],
    }
    data.update(overrides)
    return data


def _make(actor: User, partner: Partner, model: str, **overrides: Any) -> Agreement:
    return partner_service.create_agreement(
        actor=actor, partner=partner, data=_terms(model, **overrides)
    )


# ---------------------------------------------------------------------------
# Partners
# ---------------------------------------------------------------------------
def test_a_partner_can_be_recorded_and_is_audited(manager: User) -> None:
    from apps.core.models import AuditEvent

    partner = partner_service.create_partner(
        actor=manager,
        data={
            "code": "PRT-001",
            "name_ar": "شركة المثالية",
            "partner_type": PartnerType.FREELANCE_TRAINER,
            "registry_number": "س.ت 1122",
            "phone": "0790000000",
        },
    )
    assert partner.pk is not None
    assert partner.status == PartnerStatus.ACTIVE, "a new partner is active by default"
    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="partners.Partner", reference="PRT-001"
    ).exists()


def test_only_the_partner_choices_that_are_active_are_offered(manager: User) -> None:
    partner_service.create_partner(
        actor=manager,
        data={"code": "PRT-ON", "name_ar": "قائم", "partner_type": PartnerType.COMPANY},
    )
    partner_service.create_partner(
        actor=manager,
        data={
            "code": "PRT-OFF",
            "name_ar": "سابق",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.FORMER,
        },
    )
    codes = [code for code, _label in partner_service.partner_choices(actor=manager)]
    assert codes == ["PRT-ON"]


# ---------------------------------------------------------------------------
# One agreement per calculation model — §3.4 hard-codes none of them
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model", list(MODEL_TERMS))
def test_an_agreement_can_be_recorded_for_every_calculation_model(
    manager: User, a_partner: Partner, model: str
) -> None:
    agreement = _make(manager, a_partner, model)

    assert agreement.calculation_model == model
    assert agreement.status == AgreementStatus.DRAFT
    for field, value in MODEL_TERMS[model].items():
        assert getattr(agreement, field) == value, field


@pytest.mark.parametrize("model", list(MODEL_TERMS))
def test_the_value_fields_another_model_uses_are_left_empty(
    manager: User, a_partner: Partner, model: str
) -> None:
    """
    A leftover number is a term of the contract to whoever reads the screen.

    The form branches, so ordinarily nothing else is submitted — but a
    resubmitted page or a direct service call can carry the fields of the
    model the user first picked, and «195 per student» beside «50%» is not a
    harmless stray value.
    """
    agreement = _make(
        manager,
        a_partner,
        model,
        # Every value field the OTHER two models use, offered deliberately.
        **{
            field: value
            for other, terms in MODEL_TERMS.items()
            if other != model
            for field, value in terms.items()
        },
    )

    used = set(partner_service.MODEL_FIELDS[model])
    for field in partner_service.VALUE_FIELDS:
        if field in used:
            continue
        assert getattr(agreement, field) in (None, ""), field


# ---------------------------------------------------------------------------
# Incomplete terms are refused by name, not by constraint number
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("model", "drop", "expected_field"),
    [
        (CalculationModel.PERCENT, "percent_rate", "percent_rate"),
        (
            CalculationModel.FIXED_PER_STUDENT,
            "fixed_amount_per_student",
            "fixed_amount_per_student",
        ),
        (CalculationModel.SERVICE_COMMISSION, "commission_amount", "commission_amount"),
        (CalculationModel.FIXED_PER_STUDENT, "sell_price", "sell_price"),
        (CalculationModel.SERVICE_COMMISSION, "service_price", "service_price"),
    ],
)
def test_an_agreement_missing_its_own_number_is_refused_cleanly(
    manager: User, a_partner: Partner, model: str, drop: str, expected_field: str
) -> None:
    data = _terms(model)
    data[drop] = None

    with pytest.raises(ValidationError) as refusal:
        partner_service.create_agreement(actor=manager, partner=a_partner, data=data)

    assert expected_field in refusal.value.message_dict
    assert not Agreement.objects.exists()


def test_a_partner_share_above_the_sell_price_is_refused_before_the_database(
    manager: User, a_partner: Partner
) -> None:
    with pytest.raises(ValidationError) as refusal:
        _make(
            manager,
            a_partner,
            CalculationModel.FIXED_PER_STUDENT,
            fixed_amount_per_student=Decimal("300.000"),
            sell_price=Decimal("250.000"),
        )
    assert "sell_price" in refusal.value.message_dict


def test_a_commission_above_the_service_price_is_refused(manager: User, a_partner: Partner) -> None:
    with pytest.raises(ValidationError) as refusal:
        _make(
            manager,
            a_partner,
            CalculationModel.SERVICE_COMMISSION,
            commission_amount=Decimal("900.000"),
        )
    assert "commission_amount" in refusal.value.message_dict


def test_by_ratio_on_a_per_student_agreement_is_refused_at_the_service(
    manager: User, a_partner: Partner
) -> None:
    """
    C-01 — the demo's live defect, now refused in words before the CHECK.

    BY_RATIO reads the split as a proportion, so on a per-student agreement it
    treated 195 dinars as a percentage and printed a negative university
    share. The constraint has refused it since Sprint 5; what is new is that
    the person entering it is told which box is wrong.
    """
    with pytest.raises(ValidationError) as refusal:
        _make(
            manager,
            a_partner,
            CalculationModel.FIXED_PER_STUDENT,
            discount_split_mode=DiscountSplitMode.BY_RATIO,
        )
    assert "discount_split_mode" in refusal.value.message_dict


def test_validity_must_run_forwards(manager: User, a_partner: Partner) -> None:
    with pytest.raises(ValidationError) as refusal:
        _make(
            manager,
            a_partner,
            CalculationModel.PERCENT,
            valid_from=date(2027, 1, 1),
            valid_to=date(2026, 12, 1),
        )
    assert "valid_to" in refusal.value.message_dict


# ---------------------------------------------------------------------------
# The terms that are neither the model nor the dates
# ---------------------------------------------------------------------------
def test_the_consumables_cap_persists(manager: User, a_partner: Partner) -> None:
    agreement = _make(
        manager,
        a_partner,
        CalculationModel.PERCENT,
        consumables_cap_per_student=Decimal("12.500"),
    )
    agreement.refresh_from_db()
    assert agreement.consumables_cap_per_student == Decimal("12.500")


def test_a_negative_consumables_cap_is_refused(manager: User, a_partner: Partner) -> None:
    with pytest.raises(ValidationError) as refusal:
        _make(
            manager,
            a_partner,
            CalculationModel.PERCENT,
            consumables_cap_per_student=Decimal("-1.000"),
        )
    assert "consumables_cap_per_student" in refusal.value.message_dict


@pytest.mark.parametrize("mode", list(DiscountSplitMode))
def test_every_discount_split_mode_persists(manager: User, a_partner: Partner, mode: str) -> None:
    """BY_RATIO is legal on a percentage agreement, so all three are reachable."""
    agreement = _make(manager, a_partner, CalculationModel.PERCENT, discount_split_mode=mode)
    agreement.refresh_from_db()
    assert agreement.discount_split_mode == mode


def test_the_exclusion_flags_persist_including_the_one_that_is_a_warning(
    manager: User, a_partner: Partner
) -> None:
    """
    Q-01 · BR-092 — excluding deposits is the default and including them is a
    deliberate contract term, so the flag has to be storable in both states.
    """
    agreement = _make(
        manager,
        a_partner,
        CalculationModel.PERCENT,
        exclude_registration_fee=False,
        exclude_deposits=False,
        exclude_consumables=True,
    )
    agreement.refresh_from_db()
    assert agreement.exclude_registration_fee is False
    assert agreement.exclude_deposits is False
    assert agreement.exclude_consumables is True


def test_the_timing_terms_persist(manager: User, a_partner: Partner) -> None:
    agreement = _make(
        manager,
        a_partner,
        CalculationModel.FIXED_PER_STUDENT,
        payout_timing=PayoutTiming.ADVANCE,
        settlement_cycle=SettlementCycle.EVERY_4_MONTHS,
        name_list_due_days=7,
        entitlement_rule_ar="يستحق الشريك عن كل طالب أكمل المادة الأولى.",
    )
    agreement.refresh_from_db()
    assert agreement.payout_timing == PayoutTiming.ADVANCE
    assert agreement.settlement_cycle == SettlementCycle.EVERY_4_MONTHS
    assert agreement.name_list_due_days == 7
    assert agreement.entitlement_rule_ar.startswith("يستحق")


# ---------------------------------------------------------------------------
# Activation and supersession
# ---------------------------------------------------------------------------
def test_a_draft_is_not_offered_to_a_cohort_until_it_is_activated(
    manager: User, a_partner: Partner
) -> None:
    agreement = _make(manager, a_partner, CalculationModel.PERCENT)
    assert partner_service.agreement_choices(actor=manager) == []

    partner_service.activate_agreement(actor=manager, agreement=agreement)
    assert [n for n, _label in partner_service.agreement_choices(actor=manager)] == [
        agreement.agreement_number
    ]


def test_an_active_agreement_cannot_be_activated_again(manager: User, a_partner: Partner) -> None:
    agreement = _make(manager, a_partner, CalculationModel.PERCENT)
    partner_service.activate_agreement(actor=manager, agreement=agreement)

    with pytest.raises(ImmutableRecordError):
        partner_service.activate_agreement(actor=manager, agreement=agreement)


def test_supersession_ends_the_old_and_leaves_both_readable(
    manager: User, a_partner: Partner
) -> None:
    from apps.core.models import AuditEvent

    original = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/OLD")
    partner_service.activate_agreement(actor=manager, agreement=original)

    appendix = _make(
        manager,
        a_partner,
        CalculationModel.PERCENT,
        agreement_number="8F/NEW",
        percent_rate=Decimal("60.0000"),
        supersedes=original,
    )
    # Activating the appendix is what ends the contract it replaces — one act,
    # so there is no instant with two live agreements and none with zero.
    partner_service.activate_agreement(actor=manager, agreement=appendix)

    original.refresh_from_db()
    appendix.refresh_from_db()
    assert original.status == AgreementStatus.TERMINATED
    assert appendix.status == AgreementStatus.ACTIVE
    assert appendix.supersedes_id == original.pk

    # Both readable, and the old one still carries the terms it was signed with.
    rows = {r["agreement_number"]: r for r in partner_service.list_agreements(actor=manager)}
    assert set(rows) == {"8F/OLD", "8F/NEW"}
    assert rows["8F/OLD"]["percent_rate"] == Decimal("50.0000")
    assert rows["8F/NEW"]["supersedes"] == "8F/OLD"

    assert AuditEvent.objects.filter(
        action="UPDATE", entity_type="partners.Agreement", reference="8F/OLD"
    ).exists()


def test_supersession_refuses_an_agreement_of_another_partner(
    manager: User, a_partner: Partner
) -> None:
    other = partner_service.create_partner(
        actor=manager,
        data={"code": "PRT-OTH", "name_ar": "شريك آخر", "partner_type": PartnerType.COMPANY},
    )
    theirs = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/THEIRS")
    partner_service.activate_agreement(actor=manager, agreement=theirs)

    mine = _make(manager, other, CalculationModel.PERCENT, agreement_number="8F/MINE")
    partner_service.activate_agreement(actor=manager, agreement=mine)

    with pytest.raises(ValidationError, match="شريكاً آخر"):
        partner_service.supersede_agreement(actor=manager, previous=theirs, successor=mine)


def test_a_draft_cannot_end_a_live_agreement(manager: User, a_partner: Partner) -> None:
    """Ending the contract in force in favour of a draft leaves nobody covered."""
    live = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/LIVE")
    partner_service.activate_agreement(actor=manager, agreement=live)
    draft = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/DRAFT")

    with pytest.raises(ValidationError):
        partner_service.supersede_agreement(actor=manager, previous=live, successor=draft)

    live.refresh_from_db()
    assert live.status == AgreementStatus.ACTIVE


def test_an_agreement_that_is_not_live_cannot_be_superseded(
    manager: User, a_partner: Partner
) -> None:
    first = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/A")
    second = _make(manager, a_partner, CalculationModel.PERCENT, agreement_number="8F/B")
    partner_service.activate_agreement(actor=manager, agreement=second)

    with pytest.raises(ValidationError):
        partner_service.supersede_agreement(actor=manager, previous=first, successor=second)


# ---------------------------------------------------------------------------
# Authority — §3.5/23 «V C E P», §3.5/24 «V C E A P», §3.5/25 «V C E»
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "role", [Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT, Role.REGISTRATION_OFFICER, Role.CASHIER]
)
def test_no_role_but_the_manager_may_record_a_partner(seeded_settings: None, role: str) -> None:
    actor = _user(role, f"probe.partner.{role.lower()}")
    with pytest.raises(PermissionDenied):
        partner_service.create_partner(
            actor=actor,
            data={"code": "X", "name_ar": "س", "partner_type": PartnerType.COMPANY},
        )
    assert not Partner.objects.exists()


@pytest.mark.parametrize(
    "role", [Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT, Role.REGISTRATION_OFFICER, Role.CASHIER]
)
def test_no_role_but_the_manager_may_record_an_agreement(a_partner: Partner, role: str) -> None:
    actor = _user(role, f"probe.agreement.{role.lower()}")
    with pytest.raises(PermissionDenied):
        _make(actor, a_partner, CalculationModel.PERCENT)
    assert not Agreement.objects.exists()


def test_a_refused_attempt_leaves_a_denied_attempt_row(a_partner: Partner) -> None:
    """BR-085 — the refusal survives, because it is written before any rollback."""
    from apps.core.models import AuditEvent

    finance = _user(Role.FINANCE_OFFICER, "fin.denied.probe")
    with pytest.raises(PermissionDenied):
        _make(finance, a_partner, CalculationModel.PERCENT)

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=finance).exists()


def test_the_audit_account_may_read_the_editor_and_not_submit_it(
    a_partner: Partner, seeded_settings: None
) -> None:
    """
    §3.5/25 gives the audit account ``V`` and withholds ``C``.

    That is the whole shape of the audit role — sees everything, changes
    nothing — so the editor opening for them is correct and the service
    refusing them is also correct.
    """
    from apps.people.constants import Action, Screen
    from apps.people.permissions import policy

    auditor = _user(Role.AUDIT_ACCOUNT, "aud.editor.probe")
    assert policy.is_allowed(auditor, Screen.AGREEMENT_NEW, Action.VIEW)
    assert not policy.is_allowed(auditor, Screen.AGREEMENT_NEW, Action.CREATE)

    with pytest.raises(PermissionDenied):
        _make(auditor, a_partner, CalculationModel.PERCENT)


def test_activation_needs_approve_which_the_finance_officer_lacks(
    manager: User, a_partner: Partner
) -> None:
    agreement = _make(manager, a_partner, CalculationModel.PERCENT)
    finance = _user(Role.FINANCE_OFFICER, "fin.activate.probe")

    with pytest.raises(PermissionDenied):
        partner_service.activate_agreement(actor=finance, agreement=agreement)

    agreement.refresh_from_db()
    assert agreement.status == AgreementStatus.DRAFT


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------
def test_the_partner_form_opens_for_the_manager_and_records(client: Client, manager: User) -> None:
    client.force_login(manager)
    assert client.get(reverse("partners:partner-new")).status_code == 200

    response = client.post(
        reverse("partners:partner-new"),
        {
            "code": "PRT-UI",
            "name_ar": "شريك من الشاشة",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.ACTIVE,
        },
        follow=True,
    )
    assert response.status_code == 200
    assert Partner.objects.filter(code="PRT-UI").exists()


@pytest.mark.parametrize("role", [Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT, Role.CASHIER])
def test_the_partner_form_is_closed_to_roles_without_create(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"ui.partner.{role.lower()}"))
    assert client.get(reverse("partners:partner-new")).status_code == 403


def test_the_agreement_editor_opens_for_the_auditor_and_refuses_their_post(
    client: Client, a_partner: Partner, seeded_settings: None
) -> None:
    auditor = _user(Role.AUDIT_ACCOUNT, "aud.ui.probe")
    client.force_login(auditor)

    assert client.get(reverse("partners:agreement-new")).status_code == 200
    assert client.post(reverse("partners:agreement-new"), _post_terms(a_partner)).status_code == 403
    assert not Agreement.objects.exists()


def _post_terms(partner: Partner, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "partner_code": partner.code,
        "agreement_number": "UI/001",
        "title_ar": "اتفاقية من الشاشة",
        "signed_on": "2026-08-01",
        "valid_from": "2026-09-01",
        "valid_to": "2027-08-31",
        "calculation_model": CalculationModel.PERCENT,
        "percent_rate": "50.0000",
        "discount_split_mode": DiscountSplitMode.HALF,
        "payout_timing": PayoutTiming.END_OF_COURSE,
        "settlement_cycle": SettlementCycle.END_OF_COURSE,
        "exclude_registration_fee": "on",
        "exclude_deposits": "on",
    }
    data.update(overrides)
    return data


def test_the_agreement_editor_records_a_draft_and_the_page_offers_activation(
    client: Client, manager: User, a_partner: Partner
) -> None:
    client.force_login(manager)
    response = client.post(reverse("partners:agreement-new"), _post_terms(a_partner), follow=True)
    assert response.status_code == 200

    agreement = Agreement.objects.get(agreement_number="UI/001")
    assert agreement.status == AgreementStatus.DRAFT
    assert response.context["can_activate"] is True

    client.post(reverse("partners:agreement-activate", args=[agreement.agreement_number]))
    agreement.refresh_from_db()
    assert agreement.status == AgreementStatus.ACTIVE


def test_the_editor_refuses_an_incomplete_model_with_a_field_error(
    client: Client, manager: User, a_partner: Partner
) -> None:
    """The branch, proven: a percentage form submitted as a per-student one."""
    client.force_login(manager)
    response = client.post(
        reverse("partners:agreement-new"),
        _post_terms(
            a_partner, calculation_model=CalculationModel.FIXED_PER_STUDENT, percent_rate=""
        ),
    )
    assert response.status_code == 200
    assert "fixed_amount_per_student" in response.context["form"].errors
    assert not Agreement.objects.exists()


def test_activation_from_the_screen_is_refused_for_a_role_without_approve(
    client: Client, manager: User, a_partner: Partner
) -> None:
    agreement = _make(manager, a_partner, CalculationModel.PERCENT)
    client.force_login(_user(Role.FINANCE_OFFICER, "fin.ui.activate"))

    response = client.post(
        reverse("partners:agreement-activate", args=[agreement.agreement_number])
    )
    assert response.status_code == 403
    agreement.refresh_from_db()
    assert agreement.status == AgreementStatus.DRAFT


# ---------------------------------------------------------------------------
# The form's own shape
# ---------------------------------------------------------------------------
def test_every_form_field_is_rendered_by_exactly_one_group() -> None:
    """
    A field missing from ``GROUPS`` is a field the screen never draws — it
    would be silently absent, submit as empty, and be blamed on the service.
    """
    grouped = [name for _title, _model, names in AgreementForm.GROUPS for name in names]
    assert sorted(grouped) == sorted(AgreementForm.base_fields)
    assert len(grouped) == len(set(grouped)), "a field appears in two groups"


def test_the_model_specific_groups_match_what_the_service_stores() -> None:
    """The screen must not offer a box whose value the service would discard."""
    shown = {model: set(names) for _title, model, names in AgreementForm.GROUPS if model}
    assert shown == {model: set(fields) for model, fields in partner_service.MODEL_FIELDS.items()}


def test_activating_an_unknown_agreement_is_a_404_not_a_crash(
    client: Client, manager: User
) -> None:
    client.force_login(manager)
    assert client.post(reverse("partners:agreement-activate", args=["9999/1"])).status_code == 404


def test_activating_a_live_agreement_from_the_screen_reports_the_refusal(
    client: Client, manager: User, a_partner: Partner
) -> None:
    """
    A business refusal is a message on the page, not a 500.

    ``activate_agreement`` raises ``ImmutableRecordError`` for anything that
    is not a draft (D-15). The screen has to survive it — the manager pressing
    a stale button on a page someone else already activated is ordinary, not
    exceptional.
    """
    agreement = _make(manager, a_partner, CalculationModel.PERCENT)
    partner_service.activate_agreement(actor=manager, agreement=agreement)

    client.force_login(manager)
    response = client.post(
        reverse("partners:agreement-activate", args=[agreement.agreement_number]), follow=True
    )
    assert response.status_code == 200
    assert any("D-15" in str(m) for m in response.context["messages"])
