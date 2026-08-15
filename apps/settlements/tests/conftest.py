"""Fixtures for the partner and settlement tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

PASSWORD = "probe-password-1234"
TERM_START = date(2026, 9, 20)


@pytest.fixture
def priced_catalog(active_semester, seeded_settings):
    from django.core.management import call_command

    from apps.catalog.models import PriceList, PriceListStatus

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


@pytest.fixture
def finance(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.partner", password=PASSWORD, role=Role.FINANCE_OFFICER
    )


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="mgr.partner", password=PASSWORD, role=Role.CENTER_MANAGER
    )


@pytest.fixture
def cashier(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="cash.partner", password=PASSWORD, role=Role.CASHIER)


@pytest.fixture
def cash_method(db):
    from apps.cashbox.models import PaymentMethod

    return PaymentMethod.objects.create(code="CASH", name_ar="نقداً")


@pytest.fixture
def partner(db):
    from apps.partners.models import Partner, PartnerType

    return Partner.objects.create(
        code="PRT-001", name_ar="شركة تدريب مثال", partner_type=PartnerType.COMPANY
    )


@pytest.fixture
def percent_agreement(partner):
    """50/50 on the net base — the ordinary shape."""
    from apps.partners.models import Agreement, AgreementStatus, CalculationModel

    return Agreement.objects.create(
        agreement_number="2026/14",
        partner=partner,
        title_ar="اتفاقية نسبية",
        signed_on=date(2026, 8, 1),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 8, 31),
        calculation_model=CalculationModel.PERCENT,
        percent_rate=Decimal("50.0000"),
        status=AgreementStatus.ACTIVE,
    )


@pytest.fixture
def advance_agreement(partner):
    """CFM's shape: 195 per student, paid BEFORE the course runs (Q-09)."""
    from apps.partners.models import (
        Agreement,
        AgreementStatus,
        CalculationModel,
        PayoutTiming,
    )

    return Agreement.objects.create(
        agreement_number="2026/18",
        partner=partner,
        title_ar="اتفاقية صرف مقدّم",
        signed_on=date(2026, 8, 1),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 8, 31),
        calculation_model=CalculationModel.FIXED_PER_STUDENT,
        fixed_amount_per_student=Decimal("195.000"),
        sell_price=Decimal("250.000"),
        payout_timing=PayoutTiming.ADVANCE,
        name_list_due_days=7,
        status=AgreementStatus.ACTIVE,
    )


@pytest.fixture
def cohort_with_agreement(priced_catalog, active_semester, percent_agreement):
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    return Cohort.objects.create(
        code="CO-PRT-1",
        program=Program.objects.get(code="SC-NET"),
        semester=active_semester,
        name_ar="دفعة شراكة",
        starts_on=TERM_START,
        ends_on=date(2026, 12, 20),
        capacity=25,
        agreement=percent_agreement,
    )


@pytest.fixture
def make_paid_enrollment(priced_catalog, cashier, cash_method, active_semester):
    """
    An enrolled, paying participant on a cohort.

    Network engineering, university category: 20 registration (excluded from
    the partner base) + 250 tuition (shared).
    """
    from apps.billing.services import charge_service
    from apps.cashbox.services import payment_service
    from apps.catalog.models import Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Enrollment
    from apps.people.models import IdDocumentType, ParticipantCategory, Role, User
    from apps.people.services import participant_service

    registrar = User.objects.create_user(
        username="reg.partner", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )

    def _make(cohort, index: int = 1, amount: str = "270.000", status: str | None = None):
        participant = participant_service.create_participant(
            actor=registrar,
            data={
                "category": ParticipantCategory.UNIVERSITY,
                "name_ar": f"مشارك رقم {index} الرباعي",
                "id_document_type": IdDocumentType.NATIONAL_ID,
                "id_document_number": f"99900{index:05d}",
                "registered_on": TERM_START,
                "no_refund_pledge_accepted": True,
            },
        )
        quote = pricing_service.resolve_price(
            program=Program.objects.get(code=cohort.program.code),
            participant_category="UNIVERSITY",
            as_of=TERM_START,
        )
        enrollment = Enrollment.objects.create(
            code=f"EN-PRT-{index}",
            participant=participant,
            cohort=cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
            status=status or "ACTIVE",
        )
        charge_service.charge_lines_from_quote(
            actor=registrar, enrollment=enrollment, quote=quote, charged_on=TERM_START
        )
        if amount:
            payment_service.take_payment(
                actor=cashier,
                enrollment=enrollment,
                amount=Decimal(amount),
                payment_method=cash_method,
                received_on=TERM_START,
            )
        return enrollment

    return _make
