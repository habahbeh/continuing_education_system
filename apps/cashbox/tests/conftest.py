"""Shared fixtures for the billing and cashbox tests."""

from __future__ import annotations

from datetime import date

import pytest

PASSWORD = "probe-password-1234"


@pytest.fixture
def priced_catalog(active_semester, seeded_settings):
    """The Sprint 3 demo catalogue with its price list approved."""
    from django.core.management import call_command

    from apps.catalog.models import PriceList, PriceListStatus

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


@pytest.fixture
def cashier(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="cash.bill",
        password=PASSWORD,
        role=Role.CASHIER,
        full_name_ar="أمين الصندوق",
    )


@pytest.fixture
def finance(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fin.bill",
        password=PASSWORD,
        role=Role.FINANCE_OFFICER,
        full_name_ar="الموظف المالي",
    )


@pytest.fixture
def registrar(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="reg.bill", password=PASSWORD, role=Role.REGISTRATION_OFFICER
    )


@pytest.fixture
def participant(registrar, active_semester, participant_data):
    from apps.people.services import participant_service

    return participant_service.create_participant(actor=registrar, data=participant_data)


@pytest.fixture
def cash_method(db):
    from apps.cashbox.models import PaymentMethod

    return PaymentMethod.objects.create(code="CASH", name_ar="نقداً")


def _cohort(program_code: str, active_semester, level=None):
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    program = Program.objects.get(code=program_code)
    return Cohort.objects.create(
        code=f"CO-{program_code}-{level or 0}",
        program=program,
        semester=active_semester,
        level=level,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )


@pytest.fixture
def make_enrollment(priced_catalog, participant, active_semester):
    """
    Build an enrolment with its charge lines from the Sprint 3 resolver.

    Returns (enrollment, quote) so a test can assert against the very numbers
    the pricing layer produced.
    """
    from apps.billing.services import charge_service
    from apps.catalog.models import Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Enrollment

    def _make(program_code: str = "SC-NET", level=None, actor=None, category="UNIVERSITY"):
        from apps.people.models import Role, User

        actor = actor or User.objects.create_user(
            username=f"mgr.{program_code}.{level or 0}",
            password=PASSWORD,
            role=Role.CENTER_MANAGER,
        )
        program = Program.objects.get(code=program_code)
        cohort = _cohort(program_code, active_semester, level=level)

        quote = pricing_service.resolve_price(
            program=program,
            participant_category=category,
            as_of=date(2026, 9, 20),
            level=level,
        )
        enrollment = Enrollment.objects.create(
            code=f"EN-{program_code}-{level or 0}",
            participant=participant,
            cohort=cohort,
            enrolled_on=date(2026, 9, 20),
            price_list=priced_catalog,
        )
        charge_service.charge_lines_from_quote(
            actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
        )
        return enrollment, quote

    return _make
