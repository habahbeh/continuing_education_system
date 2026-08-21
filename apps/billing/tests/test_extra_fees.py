"""
Additional fees, and the ledger line each one carries (BR-037 … BR-040, §5.5).

``ExtraFee.charge_line`` was a foreign key nobody filled. These tests pin the
pairing: the fee is the business record, the line is what the participant pays
and the allocator sees, and neither exists without the other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.billing.models import ChargeType, ExtraFeeType
from apps.billing.services import extra_fee_service
from apps.billing.services.account_service import get_account_state

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.fees", password=PASSWORD, role=Role.CENTER_MANAGER
    )


def test_the_repeat_subject_fee_uses_its_seeded_amount(manager, make_enrollment) -> None:
    """BR-037 — 75 dinars, from the setting rather than a literal."""
    enrollment, _ = make_enrollment()

    fee = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.SUBJECT_REPEAT,
        charged_on=TERM_START,
        subject_name="مبادئ المحاسبة",
    )

    assert fee.amount == Decimal("75.000")
    assert fee.charge_line is not None
    assert fee.charge_line.charge_type == ChargeType.EXTRA_FEE
    assert "مبادئ المحاسبة" in fee.charge_line.description_ar


def test_the_repeat_fee_is_shared_and_the_certificate_fee_is_not(manager, make_enrollment) -> None:
    """
    §5.5 — «إعادة مادة 75: 50% لكل طرف» · «بدل فاقد شهادة 15: للمركز».

    The repeat fee is marked shareable rather than halved here: a 50%
    agreement already halves it through the distribution base, and halving it
    twice would be the same double-count this sprint removes.
    """
    enrollment, _ = make_enrollment()

    repeat = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.SUBJECT_REPEAT,
        charged_on=TERM_START,
    )
    replacement = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.CERTIFICATE_REPLACEMENT,
        charged_on=TERM_START,
    )

    assert repeat.charge_line is not None
    assert replacement.charge_line is not None
    assert repeat.is_partner_shareable is True
    assert repeat.charge_line.is_partner_shareable is True
    assert replacement.amount == Decimal("15.000")
    assert replacement.is_partner_shareable is False
    assert replacement.charge_line.is_partner_shareable is False


def test_an_international_exam_fee_needs_the_participants_prior_agreement(
    manager, make_enrollment
) -> None:
    """
    BR-040 · §5.5 — «الامتحانات الدولية خارج الرسوم إلا باتفاق مسبق مع الطالب».

    تناغم clause 3 says the same from the partner's side: the course fee does
    not include international exam costs unless agreed with the student in
    advance.
    """
    enrollment, _ = make_enrollment()

    with pytest.raises(extra_fee_service.PriorAgreementRequiredError):
        extra_fee_service.charge_extra_fee(
            actor=manager,
            enrollment=enrollment,
            fee_type=ExtraFeeType.INTERNATIONAL_EXAM,
            charged_on=TERM_START,
            amount=Decimal("120.000"),
        )

    fee = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.INTERNATIONAL_EXAM,
        charged_on=TERM_START,
        amount=Decimal("120.000"),
        prior_agreement_with_participant=True,
    )
    assert fee.prior_agreement_with_participant is True
    assert fee.is_partner_shareable is False


def test_an_exam_fee_with_no_amount_is_refused(manager, make_enrollment) -> None:
    """There is no seeded default for an international exam — it varies."""
    enrollment, _ = make_enrollment()

    with pytest.raises(extra_fee_service.FeeAmountUnknownError):
        extra_fee_service.charge_extra_fee(
            actor=manager,
            enrollment=enrollment,
            fee_type=ExtraFeeType.INTERNATIONAL_EXAM,
            charged_on=TERM_START,
            prior_agreement_with_participant=True,
        )


def test_an_other_fee_must_state_whether_it_is_shared(manager, make_enrollment) -> None:
    """
    A fee nobody classified would otherwise inherit a silent default.

    Whether the partner shares it changes what they are paid, so it is a
    decision someone makes rather than one that falls out of a dictionary.
    """
    enrollment, _ = make_enrollment()

    with pytest.raises(ValidationError):
        extra_fee_service.charge_extra_fee(
            actor=manager,
            enrollment=enrollment,
            fee_type=ExtraFeeType.OTHER,
            charged_on=TERM_START,
            amount=Decimal("30.000"),
        )

    fee = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.OTHER,
        charged_on=TERM_START,
        amount=Decimal("30.000"),
        is_partner_shareable=False,
    )
    assert fee.is_partner_shareable is False


def test_the_fee_raises_what_the_participant_owes(manager, make_enrollment) -> None:
    """The pairing is only real if the ledger moved."""
    enrollment, _ = make_enrollment()
    before = get_account_state(enrollment).total_due

    extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.SUBJECT_REPEAT,
        charged_on=TERM_START,
    )

    assert get_account_state(enrollment).total_due == before + Decimal("75.000")


def test_a_replacement_fee_is_findable_for_the_certificate_service(
    manager, make_enrollment
) -> None:
    """BR-038 — the link certificate reissue checks before handing out paper."""
    enrollment, _ = make_enrollment()
    assert extra_fee_service.replacement_fee_for(enrollment) is None

    fee = extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type=ExtraFeeType.CERTIFICATE_REPLACEMENT,
        charged_on=TERM_START,
    )

    assert extra_fee_service.replacement_fee_for(enrollment) == fee


def test_the_registration_officer_cannot_charge_a_fee(registrar, make_enrollment) -> None:
    """§8 — data entry is not the same authority as charging money."""
    enrollment, _ = make_enrollment()

    with pytest.raises(PermissionDenied):
        extra_fee_service.charge_extra_fee(
            actor=registrar,
            enrollment=enrollment,
            fee_type=ExtraFeeType.SUBJECT_REPEAT,
            charged_on=TERM_START,
        )


def test_an_unknown_fee_type_is_refused(manager, make_enrollment) -> None:
    enrollment, _ = make_enrollment()

    with pytest.raises(ValidationError):
        extra_fee_service.charge_extra_fee(
            actor=manager,
            enrollment=enrollment,
            fee_type="LATE_NIGHT_SURCHARGE",
            charged_on=TERM_START,
            amount=Decimal("10.000"),
        )
