"""
Sprint 8F-0 — a fresh database reaches a paid enrolment with no demo fixture.

The audit before this sprint found the catalogue to be the first thing a real
installation cannot build. ``Subject``, ``RegistrationFeeRule``,
``CourseCategory``, ``KnowledgeField``, ``DepositPolicy`` and ``PriceListItem``
had no reachable creation path at all, and ``Program`` and ``PriceList`` had
services with no caller. The only way to a priced catalogue was
``seed_catalog_demo`` — whose own docstring says its rows are invented, and
which the client cannot use to open a real centre.

This module is the proof that the gap is closed. It starts from an empty
database and walks the installer's actual path — ``createsuperuser``,
``seed_settings``, ``seed_semester``, then the Django admin — to a participant
who is enrolled, charged and has paid. Every catalogue row is entered through
an admin FORM over HTTP, not through the ORM, because a form that rejects the
data it exists to collect is exactly the failure a model-level test cannot see.

``seed_catalog_demo`` is sabotaged for the duration. A proof of independence
that merely omits the fixture proves nothing about a fixture some other module
may have pulled in first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

PASSWORD = "install-password-1234"
ISSUED_ON = date(2026, 8, 1)
EFFECTIVE_FROM = date(2026, 9, 1)
TERM_START = date(2026, 9, 20)
TERM_END = date(2026, 12, 20)


@pytest.fixture
def no_demo_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``seed_catalog_demo`` fail loudly if anything in here reaches it."""
    from apps.catalog.management.commands import seed_catalog_demo

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "seed_catalog_demo ran during the fresh-install proof — the point "
            "of this module is that a real installation never needs it."
        )

    monkeypatch.setattr(seed_catalog_demo.Command, "handle", _refuse)


def _add_url(model: str) -> str:
    return reverse(f"admin:catalog_{model}_add")


def _submit(client: Client, url: str, data: dict[str, Any]) -> None:
    """POST an admin form and fail with its errors rather than with a 200."""
    response = client.post(url, data, follow=True)
    assert response.status_code == 200
    context_form = response.context.get("adminform") if response.context else None
    if context_form is not None:
        errors = getattr(context_form.form, "errors", None)
        assert not errors, f"{url} refused the form: {errors.as_json()}"
    assert "errorlist" not in response.content.decode("utf-8"), f"{url} reported an error"


def _take_cohort_through_the_ministry(cohort: Any, *, manager: Any, registrar: Any) -> None:
    """BR-016's two documents, then the submission and the decision."""
    import hashlib

    from django.contrib.contenttypes.models import ContentType
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.models import Attachment
    from apps.core.models.attachment import AttachmentPurpose
    from apps.operations.models import MoheSubmission
    from apps.operations.services import mohe_service

    submission = mohe_service.create_submission(
        actor=registrar,
        cohort=cohort,
        data={"training_axes_ar": "محاور الدورة", "target_audience_ar": "الفئة المستهدفة"},
    )
    content_type = ContentType.objects.get_for_model(MoheSubmission)
    for purpose in (AttachmentPurpose.TRAINER_CV, AttachmentPurpose.ENTITY_LICENSE):
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
            uploaded_by=registrar,
        )
    mohe_service.submit_to_mohe(actor=manager, submission=submission, submitted_on=date(2026, 9, 1))
    mohe_service.record_decision(
        actor=manager,
        submission=submission,
        approved=True,
        decided_on=date(2026, 9, 10),
        mohe_course_number="MOHE-FRESH-1",
        registration_deadline=date(2026, 10, 5),
    )


# ---------------------------------------------------------------------------
# The installer's path, end to end
# ---------------------------------------------------------------------------
def install_fresh_system(client: Client) -> dict[str, Any]:
    """
    Everything an installer does before the first participant exists.

    Returned as a dict rather than asserted here so more than one test can
    stand on the same install without either owning it. Sprint 8F's partner
    proof is the second caller: it needs a priced catalogue to have anything
    for a partner to earn a share OF, and rebuilding one beside this would be
    two install paths to keep true.
    """
    from apps.catalog.models import (
        CourseCategory,
        KnowledgeField,
        PriceList,
        PriceListStatus,
        Program,
    )
    from apps.catalog.services import pricing_service
    from apps.core.models import Semester
    from apps.people.models import Role, User
    from apps.people.services import user_service

    # -- 1. the two commands the README runbook names -----------------------
    call_command("seed_settings", verbosity=0)
    call_command(
        "seed_semester",
        "--code",
        "2026-1",
        "--type",
        "1",
        "--academic-year",
        "2026/2027",
        "--starts",
        "2026-09-01",
        "--ends",
        "2027-01-15",
        verbosity=0,
    )

    # -- 2. createsuperuser, which now asks for the role --------------------
    # Δ-02 makes the ROLE the authority and T-165 refuses a bare superuser the
    # user admin on purpose. ``REQUIRED_FIELDS`` carrying ``role`` is what
    # keeps that refusal from locking the first installer out of their own
    # system.
    sysadmin = User.objects.create_superuser(
        username="installer",
        password=PASSWORD,
        email="installer@petra.edu.jo",
        role=Role.SYSTEM_ADMINISTRATOR,
    )
    assert "role" in User.REQUIRED_FIELDS

    # -- 3. the system administrator creates the centre manager -------------
    manager = user_service.create_user(
        actor=sysadmin,
        username="centre.manager",
        password=PASSWORD,
        full_name_ar="مدير مركز التعليم المستمر",
        role=Role.CENTER_MANAGER,
    )
    # Django's own admin gate. Set from the user admin's "حالة الموظف" box;
    # the permission matrix decides what the manager may then DO in there.
    manager.is_staff = True
    manager.save(update_fields=["is_staff"])

    assert client.login(username="centre.manager", password=PASSWORD)

    # -- 4. the catalogue, entered through admin forms ----------------------
    _submit(
        client,
        _add_url("coursecategory"),
        {"code": "CAT-IT", "name_ar": "تكنولوجيا المعلومات", "is_active": "on"},
    )
    _submit(
        client,
        _add_url("knowledgefield"),
        {"code": "KF-IT", "name_ar": "تكنولوجيا المعلومات", "is_active": "on"},
    )

    category = CourseCategory.objects.get(code="CAT-IT")
    knowledge_field = KnowledgeField.objects.get(code="KF-IT")

    _submit(
        client,
        _add_url("program"),
        {
            "code": "SC-NET",
            "program_type": "SHORT_COURSE",
            "name_ar": "هندسة الشبكات",
            "name_en": "Network Engineering",
            "training_hours": "60",
            "knowledge_field": str(knowledge_field.pk),
            "specialization": "",
            "course_category": str(category.pk),
            "levels_count": "",
            "consumables_per_student": "0.000",
            "minimum_first_payment_override": "",
            "is_active": "on",
            # The subjects inline. A short course has none; a diploma's rows
            # would go here, beside the fee they have to add up to (BR-006).
            "subjects-TOTAL_FORMS": "0",
            "subjects-INITIAL_FORMS": "0",
            "subjects-MIN_NUM_FORMS": "0",
            "subjects-MAX_NUM_FORMS": "1000",
        },
    )
    program = Program.objects.get(code="SC-NET")

    semester = Semester.objects.get(is_active=True)
    _submit(
        client,
        _add_url("pricelist"),
        {
            "code": "PL-2026-1",
            "name_ar": "قائمة أسعار الفصل الأول 2026/2027",
            "semester": str(semester.pk),
            "issued_on": ISSUED_ON.isoformat(),
            "effective_from": EFFECTIVE_FROM.isoformat(),
            "proposed_by_text": "مدير المركز",
            # The president's decision, recorded as evidence while the list is
            # still a draft. The action below is what acts on it (D-31).
            "approved_by_text": "رئيس جامعة البترا",
            "decision_reference": "قرار 2026/44",
        },
    )
    price_list = PriceList.objects.get(code="PL-2026-1")
    assert price_list.status == PriceListStatus.DRAFT, "a new list is never born approved"

    _submit(
        client,
        _add_url("pricelistitem"),
        {
            "price_list": str(price_list.pk),
            "program": str(program.pk),
            "level": "",
            "course_fee": "250.000",
            "deposit_amount": "",
            "deposit_policy": "",
            "notes": "",
        },
    )
    _submit(
        client,
        _add_url("registrationfeerule"),
        {
            "price_list": str(price_list.pk),
            "program": "",
            "participant_category": "UNIVERSITY",
            "fee": "15.000",
            "exception_note_ar": "القاعدة العامة — BR-009",
        },
    )

    # -- 5. approval, through the action and therefore through the service --
    changelist = reverse("admin:catalog_pricelist_changelist")
    response = client.post(
        changelist,
        {
            "action": "record_president_approval",
            "_selected_action": [str(price_list.pk)],
        },
        follow=True,
    )
    assert response.status_code == 200

    price_list.refresh_from_db()
    assert price_list.status == PriceListStatus.APPROVED
    assert price_list.approved_at is not None
    assert price_list.approved_semester_key == semester.pk

    # -- 6. the catalogue now prices a course -------------------------------
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=TERM_START
    )
    assert quote.course_fee == Decimal("250.000")
    assert quote.registration_fee == Decimal("15.000")

    registrar = user_service.create_user(
        actor=sysadmin,
        username="registrar.one",
        password=PASSWORD,
        full_name_ar="موظفة التسجيل",
        role=Role.REGISTRATION_OFFICER,
    )
    cashier = user_service.create_user(
        actor=sysadmin,
        username="cashier.one",
        password=PASSWORD,
        full_name_ar="أمين الصندوق",
        role=Role.CASHIER,
    )
    finance = user_service.create_user(
        actor=sysadmin,
        username="finance.one",
        password=PASSWORD,
        full_name_ar="موظف الشؤون المالية",
        role=Role.FINANCE_OFFICER,
    )
    return {
        "sysadmin": sysadmin,
        "manager": manager,
        "registrar": registrar,
        "cashier": cashier,
        "finance": finance,
        "semester": semester,
        "program": program,
        "price_list": price_list,
        "quote": quote,
    }


def test_a_fresh_database_reaches_a_paid_enrolment_without_the_demo_seed(
    client: Client, no_demo_seed: None
) -> None:
    from apps.billing.services import account_service, charge_service
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service
    from apps.operations.models import Cohort
    from apps.operations.services import enrollment_service
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service

    installed = install_fresh_system(client)
    manager, registrar, cashier = (
        installed["manager"],
        installed["registrar"],
        installed["cashier"],
    )
    program, semester, price_list = (
        installed["program"],
        installed["semester"],
        installed["price_list"],
    )
    quote = installed["quote"]

    # -- 7. a cohort, a participant, an enrolment, a charge, a payment ------
    cohort = Cohort.objects.create(
        code="CO-NET-1",
        program=program,
        semester=semester,
        name_ar="دفعة هندسة الشبكات",
        starts_on=TERM_START,
        ends_on=TERM_END,
        capacity=25,
    )
    # BR-013 — no enrolment onto a cohort the ministry has not approved. The
    # MOHE screens already exist, so this part of a fresh install was never
    # blocked; it is walked here because leaving it out would prove a path
    # nobody can actually take.
    _take_cohort_through_the_ministry(cohort, manager=manager, registrar=registrar)
    participant = participant_service.create_participant(
        actor=registrar,
        data={
            "category": ParticipantCategory.UNIVERSITY,
            "name_ar": "ليان محمد سعيد النجار",
            "id_document_type": IdDocumentType.NATIONAL_ID,
            "id_document_number": "9982011111",
            "registered_on": TERM_START,
            "no_refund_pledge_accepted": True,
        },
    )
    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=participant,
        cohort=cohort,
        enrolled_on=TERM_START,
        price_list=price_list,
        code="EN-FRESH-1",
    )
    charge_service.charge_lines_from_quote(
        actor=registrar, enrollment=enrollment, quote=quote, charged_on=TERM_START
    )
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("265.000"),
        payment_method=PaymentMethod.objects.create(code="CASH", name_ar="نقداً"),
        received_on=TERM_START,
    )

    state = account_service.get_account_state(enrollment)
    assert state.total_due == Decimal("265.000")
    assert state.total_paid == Decimal("265.000")
    assert state.balance == Decimal("0.000")


# ---------------------------------------------------------------------------
# Sprint 8F — the partner side of the same fresh database
# ---------------------------------------------------------------------------
def test_a_fresh_database_reaches_a_partner_claim_without_any_fixture(
    client: Client, no_demo_seed: None
) -> None:
    """
    The blocker Sprint 8F was opened for.

    Before it, ``Partner`` and ``Agreement`` existed only where a test fixture
    made them, which put claims, settlements, obligations and every partner
    figure in the reports out of reach of a real installation. This walks the
    whole thing from an empty database: partner recorded, agreement
    transcribed and activated, cohort placed under it, participant enrolled
    and paying — and then the ENTITLEMENT and CLAIM services reading terms
    that no fixture supplied.

    Nothing here imports a partner fixture. ``tests/conftest.py`` does make
    ``partner`` and ``percent_agreement`` available in this package, which is
    exactly why the last assertion counts the rows: proving independence by
    not typing a name proves nothing when the name is in scope.
    """
    from datetime import date
    from decimal import Decimal

    from apps.billing.services import charge_service
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service
    from apps.operations.models import Cohort
    from apps.operations.services import enrollment_service
    from apps.partners.models import Agreement, AgreementStatus, Partner, PartnerType
    from apps.partners.services import partner_service
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service
    from apps.settlements.services import claim_service, entitlement_service

    installed = install_fresh_system(client)
    manager, registrar, cashier, finance = (
        installed["manager"],
        installed["registrar"],
        installed["cashier"],
        installed["finance"],
    )
    program, semester, price_list, quote = (
        installed["program"],
        installed["semester"],
        installed["price_list"],
        installed["quote"],
    )

    # -- the partner, recorded from the contract ----------------------------
    partner = partner_service.create_partner(
        actor=manager,
        data={
            "code": "PRT-FRESH",
            "name_ar": "شركة تناغم للتدريب",
            "partner_type": PartnerType.COMPANY,
            "registry_number": "س.ت 45211",
        },
    )

    # -- the agreement, transcribed then made live --------------------------
    agreement = partner_service.create_agreement(
        actor=manager,
        partner=partner,
        data={
            "agreement_number": "2026/71",
            "title_ar": "اتفاقية تدريب نسبية",
            "signed_on": date(2026, 8, 1),
            "valid_from": date(2026, 9, 1),
            "valid_to": date(2027, 8, 31),
            "calculation_model": "PERCENT",
            "percent_rate": Decimal("50.0000"),
            "exclude_registration_fee": True,
            "exclude_deposits": True,
        },
    )
    assert agreement.status == AgreementStatus.DRAFT, "a signed agreement is not born live"
    partner_service.activate_agreement(actor=manager, agreement=agreement)
    agreement.refresh_from_db()
    assert agreement.status == AgreementStatus.ACTIVE

    # Only now does the cohort form offer it (BR-041) — and only for a date
    # inside its window, which Sprint 8F-1 added. The whole scenario runs on
    # TERM_START, so that is the date asked about.
    assert ("2026/71", "2026/71 — شركة تناغم للتدريب") in partner_service.agreement_choices(
        actor=manager, as_of=TERM_START
    )

    # -- a paying participant under that agreement --------------------------
    cohort = Cohort.objects.create(
        code="CO-PRT-FRESH",
        program=program,
        semester=semester,
        name_ar="دفعة شراكة",
        starts_on=TERM_START,
        ends_on=TERM_END,
        capacity=25,
        agreement=agreement,
    )
    _take_cohort_through_the_ministry(cohort, manager=manager, registrar=registrar)

    participant = participant_service.create_participant(
        actor=registrar,
        data={
            "category": ParticipantCategory.UNIVERSITY,
            "name_ar": "رهف عمر خالد الزعبي",
            "id_document_type": IdDocumentType.NATIONAL_ID,
            "id_document_number": "9982022222",
            "registered_on": TERM_START,
            "no_refund_pledge_accepted": True,
        },
    )
    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=participant,
        cohort=cohort,
        enrolled_on=TERM_START,
        price_list=price_list,
        code="EN-PRT-FRESH",
    )
    charge_service.charge_lines_from_quote(
        actor=registrar, enrollment=enrollment, quote=quote, charged_on=TERM_START
    )
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=Decimal("265.000"),
        payment_method=PaymentMethod.objects.create(code="CASH", name_ar="نقداً"),
        received_on=TERM_START,
    )

    # -- entitlement reads the terms the manager typed ----------------------
    verdict = entitlement_service.evaluate_eligibility(enrollment, as_of=TERM_START)
    assert verdict.is_eligible

    # 265 collected = 15 registration (excluded by the agreement) + 250 tuition.
    base = entitlement_service.shareable_collected(
        enrollment, agreement=agreement, as_of=TERM_START
    )
    assert base == Decimal("250.000")
    assert entitlement_service.partner_share_for(
        agreement=agreement, base=base, student_count=1
    ) == Decimal("125.000")

    # -- and a claim is drawn against them ----------------------------------
    claim = claim_service.build_claim(
        actor=finance,
        agreement=agreement,
        cohort=cohort,
        period_from=TERM_START,
        period_to=TERM_END,
        trigger_type="END_OF_COURSE",
    )
    assert claim.partner_share == Decimal("125.000")
    assert claim.model_snapshot == "PERCENT"
    assert claim.rate_snapshot == Decimal("50.0000")

    # -- no fixture put anything here ---------------------------------------
    assert list(Partner.objects.values_list("code", flat=True)) == ["PRT-FRESH"]
    assert list(Agreement.objects.values_list("agreement_number", flat=True)) == ["2026/71"]
