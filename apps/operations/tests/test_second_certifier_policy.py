"""
Who signs the financial step second — a policy, not a literal (BR-074 · Q-14).

§6.4 names «المحاسب ثم المدير المالي», while §8 lists five roles without the
finance manager among them. The contradiction is inside the requirements
document, and the implementation followed §6.4 because that is the text of the
procedure. Making the role a SETTING means the centre can resolve it without a
code change — while the part that makes this a control at all, the second
signature belonging to a DIFFERENT PERSON, stays a database constraint that no
setting can relax.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied

from apps.core.models import AuditEvent
from apps.core.services.settings_service import get_setting
from apps.operations.models import ClearanceCaseType
from apps.operations.services import clearance_service

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fim.policy", password=PASSWORD, role=Role.FINANCE_MANAGER
    )


@pytest.fixture
def second_finance_officer(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin2.policy", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def settled(make_cohort, approve_cohort, make_enrollment, charge_and_pay):
    def _make(index: int = 1, code: str = "CO-POL"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount="270.000")
        return enrollment

    return _make


def _point_policy_at(role: str) -> None:
    """Change the policy the way an administrator would — dated, not patched."""
    from apps.core.models import EffectiveSetting, SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    key = clearance_service.SECOND_CERTIFIER_ROLE_KEY
    if EffectiveSetting.objects.filter(key=key).exists():
        close_setting(key, effective_to=date(2026, 1, 1))
    set_setting(
        key,
        role,
        value_type=SettingValueType.STRING,
        effective_from=date(2026, 1, 2),
        note="اختبار — تحويل صلاحية المصادقة الثانية",
    )


def _to_first_signature(manager, finance, enrollment, code="CLR-POL"):
    clearance = clearance_service.open_clearance(
        actor=manager,
        enrollment=enrollment,
        case_type=ClearanceCaseType.GRADUATION,
        opened_on=TERM_START,
        code=code,
    )
    clearance_service.complete_custody_step(
        actor=manager,
        clearance=clearance,
        custody_items=[{"name_ar": "هوية المركز", "returned": True}],
    )
    clearance_service.certify_finance_step(actor=finance, clearance=clearance)
    return clearance


def test_the_seeded_default_is_the_finance_manager(seeded_settings) -> None:
    """§6.4 names them, so that is what ships."""
    assert (
        get_setting(clearance_service.SECOND_CERTIFIER_ROLE_KEY, as_of=TERM_START)
        == "FINANCE_MANAGER"
    )
    assert clearance_service.second_certifier_role(as_of=TERM_START) == "FINANCE_MANAGER"


def test_the_default_role_can_countersign(settled, manager, finance, finance_manager) -> None:
    clearance = _to_first_signature(manager, finance, settled())

    step = clearance_service.second_certify_finance_step(actor=finance_manager, clearance=clearance)

    assert step.is_done is True
    assert step.second_certified_by == finance_manager


def test_another_role_is_refused_and_the_refusal_names_the_setting(
    settled, manager, finance, second_finance_officer
) -> None:
    """A refusal that names its rule is a refusal someone can act on."""
    clearance = _to_first_signature(manager, finance, settled())

    with pytest.raises(clearance_service.SecondCertifierRoleError) as excinfo:
        clearance_service.second_certify_finance_step(
            actor=second_finance_officer, clearance=clearance
        )

    assert clearance_service.SECOND_CERTIFIER_ROLE_KEY in str(excinfo.value)
    assert (
        AuditEvent.objects.filter(action="DENIED_ATTEMPT", denial_rule="BR-074")
        .filter(changes__required_role="FINANCE_MANAGER")
        .exists()
    )


def test_changing_the_setting_moves_the_authority(
    settled, manager, finance, second_finance_officer, finance_manager
) -> None:
    """
    The centre's answer to Q-14 becomes a dated setting, not a deployment.

    Pointing the policy at FINANCE_OFFICER lets a second finance officer sign
    — and locks the finance manager out, because the setting names ONE role
    rather than a floor to rise above.
    """
    _point_policy_at("FINANCE_OFFICER")
    assert clearance_service.second_certifier_role(as_of=TERM_START) == "FINANCE_OFFICER"

    clearance = _to_first_signature(manager, finance, settled())
    step = clearance_service.second_certify_finance_step(
        actor=second_finance_officer, clearance=clearance
    )
    assert step.is_done is True

    other = _to_first_signature(manager, finance, settled(index=2, code="CO-POL2"), code="CLR-P2")
    with pytest.raises(clearance_service.SecondCertifierRoleError):
        clearance_service.second_certify_finance_step(actor=finance_manager, clearance=other)


def test_the_different_person_rule_is_not_configurable(
    settled, manager, finance, second_finance_officer
) -> None:
    """
    D-30 · C-30 — the control the setting must never be able to switch off.

    With the policy pointed at FINANCE_OFFICER, the officer who signed first
    now HOLDS the required role — and is still refused. One role check and one
    person check are two different controls, and only the first is a question
    about the org chart.
    """
    _point_policy_at("FINANCE_OFFICER")

    clearance = _to_first_signature(manager, finance, settled())

    with pytest.raises((PermissionDenied, Exception)) as excinfo:
        clearance_service.second_certify_finance_step(actor=finance, clearance=clearance)

    assert "FINANCE_OFFICER" not in str(excinfo.value) or "نفس" in str(excinfo.value)

    # Someone else holding the same role still can.
    step = clearance_service.second_certify_finance_step(
        actor=second_finance_officer, clearance=clearance
    )
    assert step.is_done is True
