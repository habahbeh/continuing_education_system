"""Fixtures for the operations lifecycle tests."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pytest

PASSWORD = "probe-password-1234"
TERM_START = date(2026, 9, 20)
TERM_END = date(2026, 12, 20)
DEADLINE = date(2026, 10, 5)


@pytest.fixture
def priced_catalog(active_semester, seeded_settings):
    from django.core.management import call_command

    from apps.catalog.models import PriceList, PriceListStatus

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


def _user(username: str, role: str):
    from apps.people.models import User

    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def registrar(seeded_settings):
    from apps.people.models import Role

    return _user("reg.ops", Role.REGISTRATION_OFFICER)


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role

    return _user("mgr.ops", Role.CENTER_MANAGER)


@pytest.fixture
def finance(seeded_settings):
    from apps.people.models import Role

    return _user("fin.ops", Role.FINANCE_OFFICER)


@pytest.fixture
def cashier(seeded_settings):
    from apps.people.models import Role

    return _user("cash.ops", Role.CASHIER)


@pytest.fixture
def cash_method(db):
    from apps.cashbox.models import PaymentMethod

    return PaymentMethod.objects.create(code="CASH", name_ar="نقداً")


@pytest.fixture
def make_cohort(priced_catalog, active_semester):
    """A cohort on any seeded programme — unapproved until told otherwise."""
    from apps.catalog.models import Program
    from apps.operations.models import Cohort

    def _make(program_code: str = "SC-NET", code: str | None = None, **overrides):
        program = Program.objects.get(code=program_code)
        fields = {
            "code": code or f"CO-{program_code}",
            "program": program,
            "semester": active_semester,
            "name_ar": f"دفعة {program.name_ar}",
            "starts_on": TERM_START,
            "ends_on": TERM_END,
            "capacity": 25,
        }
        fields.update(overrides)
        return Cohort.objects.create(**fields)

    return _make


@pytest.fixture
def attach_required_documents():
    """BR-016 — the two PDFs a submission cannot be sent without."""
    from django.contrib.contenttypes.models import ContentType
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.models import Attachment
    from apps.core.models.attachment import AttachmentPurpose
    from apps.operations.models import MoheSubmission

    def _attach(submission, actor, purposes=None):
        purposes = purposes or [
            AttachmentPurpose.TRAINER_CV,
            AttachmentPurpose.ENTITY_LICENSE,
        ]
        content_type = ContentType.objects.get_for_model(MoheSubmission)
        for purpose in purposes:
            payload = f"%PDF-1.4 {purpose}".encode()
            Attachment.objects.create(
                content_type=content_type,
                object_id=str(submission.pk),
                purpose=purpose,
                file=SimpleUploadedFile(f"{purpose}.pdf", payload, "application/pdf"),
                original_filename=f"{purpose}.pdf",
                mime_type="application/pdf",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                uploaded_by=actor,
            )

    return _attach


@pytest.fixture
def approve_cohort(manager, registrar, attach_required_documents):
    """Take a cohort all the way through the ministry, the way a user would."""
    from apps.operations.services import mohe_service

    def _approve(cohort, *, deadline: date | None = DEADLINE, course_number="MOHE-1"):
        submission = mohe_service.create_submission(
            actor=registrar,
            cohort=cohort,
            data={"training_axes_ar": "محاور", "target_audience_ar": "الفئة"},
        )
        attach_required_documents(submission, registrar)
        mohe_service.submit_to_mohe(
            actor=manager, submission=submission, submitted_on=date(2026, 9, 1)
        )
        return mohe_service.record_decision(
            actor=manager,
            submission=submission,
            approved=True,
            decided_on=date(2026, 9, 10),
            mohe_course_number=course_number,
            registration_deadline=deadline,
        )

    return _approve


@pytest.fixture
def make_participant(registrar, active_semester):
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service

    def _make(index: int = 1):
        return participant_service.create_participant(
            actor=registrar,
            data={
                "category": ParticipantCategory.UNIVERSITY,
                "name_ar": f"مشارك تشغيلي {index} الرباعي",
                "id_document_type": IdDocumentType.NATIONAL_ID,
                "id_document_number": f"88800{index:05d}",
                "registered_on": TERM_START,
                "no_refund_pledge_accepted": True,
            },
        )

    return _make


@pytest.fixture
def make_enrollment(priced_catalog, registrar, make_participant):
    """An enrolment created through the service, so BR-013 is exercised."""
    from apps.operations.services import enrollment_service

    def _make(cohort, index: int = 1, participant=None, actor=None):
        return enrollment_service.create_enrollment(
            actor=actor or registrar,
            participant=participant or make_participant(index),
            cohort=cohort,
            enrolled_on=TERM_START,
            price_list=priced_catalog,
            code=f"EN-OPS-{index}",
        )

    return _make


@pytest.fixture
def charge_and_pay(priced_catalog, registrar, cashier, cash_method):
    """Raise the quote's charge lines and take money against them."""
    from apps.billing.services import charge_service
    from apps.cashbox.services import payment_service
    from apps.catalog.services import pricing_service

    def _do(enrollment, amount: str | None = "270.000", received_on: date = TERM_START):
        quote = pricing_service.resolve_price(
            program=enrollment.cohort.program,
            participant_category=enrollment.participant.category,
            as_of=received_on,
            level=enrollment.cohort.level,
        )
        charge_service.charge_lines_from_quote(
            actor=registrar, enrollment=enrollment, quote=quote, charged_on=received_on
        )
        if amount:
            payment_service.take_payment(
                actor=cashier,
                enrollment=enrollment,
                amount=Decimal(amount),
                payment_method=cash_method,
                received_on=received_on,
            )
        return quote

    return _do


@pytest.fixture
def documented_attendance(manager):
    """Q-06 / BR-095 — a counter with its source, verifier and timestamp."""
    from apps.operations.services import enrollment_service

    def _record(enrollment, lectures: int, ref: str = "كشف المدرب 2026/09"):
        return enrollment_service.record_attendance(
            actor=manager,
            enrollment=enrollment,
            lectures_attended=lectures,
            record_ref=ref,
        )

    return _record
