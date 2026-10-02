"""
Sprint 8G — the ministry screens (§3.3/14, §3.3/15).

The logic behind these pages has been tested since Sprint 6; what did not
exist was any way for a person to reach it. This module is about the reach:
who may open each page, which button each role is offered, and whether the
file a screen creates is the file the BR-013 gate then reads.

One thing had to be built before the screens could work at all. BR-016 refuses
to send a file missing the trainer's CV or the entity licence, and until now
every attachment in this repository was made by a test calling
``Attachment.objects.create`` with a hand-computed digest — there was no
supported upload path, so the send button would have been unpressable. The
attachment tests at the end cover that addition.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.operations.models import MoheStatus, MoheSubmission
from apps.operations.services import enrollment_service, mohe_service
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"
SENT_ON = date(2026, 9, 1)
DECIDED_ON = date(2026, 9, 10)
DEADLINE = date(2026, 10, 5)

#: §3.3/14 «V C E A P · V P · — · — · — · V P»
MOHE_READERS = (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT)
MOHE_OUTSIDERS = (Role.FINANCE_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)

CONTENT = {
    "training_axes_ar": "محاور الدورة التدريبية",
    "practical_aspects_ar": "تطبيقات مخبرية",
    "target_audience_ar": "موظفو القطاع العام",
    "trainer_name": "د. سامي العلي",
    "trainer_qualifications": "دكتوراه هندسة شبكات",
    "training_location": "مركز التعليم المستمر",
    "responsible_entity": "جامعة البترا",
}


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 test document body", "application/pdf")


@pytest.fixture
def cohort(make_cohort: Any) -> Any:
    return make_cohort("SC-NET", code="CO-8G")


@pytest.fixture
def draft(manager: User, cohort: Any) -> MoheSubmission:
    return mohe_service.create_submission(actor=manager, cohort=cohort, data=dict(CONTENT))


def _attach_both(actor: User, submission: MoheSubmission) -> None:
    for purpose, name in (("TRAINER_CV", "cv.pdf"), ("ENTITY_LICENSE", "licence.pdf")):
        mohe_service.attach_document(
            actor=actor, submission=submission, purpose=purpose, upload=_pdf(name)
        )


# ---------------------------------------------------------------------------
# Who may open what
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", MOHE_READERS)
def test_the_ministry_list_opens_for_every_role_that_reads_it(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"list.{role.lower()}"))
    assert client.get(reverse("operations:mohe")).status_code == 200


@pytest.mark.parametrize("role", MOHE_OUTSIDERS)
def test_the_ministry_list_is_closed_to_the_financial_roles(
    client: Client, seeded_settings: None, role: str
) -> None:
    """§3.3/14 gives finance, the finance manager and the cashier nothing."""
    client.force_login(_user(role, f"nolist.{role.lower()}"))
    assert client.get(reverse("operations:mohe")).status_code == 403


@pytest.mark.parametrize("role", MOHE_READERS)
def test_the_submission_editor_opens_for_the_roles_holding_view(
    client: Client, priced_catalog: Any, role: str
) -> None:
    client.force_login(_user(role, f"editor.{role.lower()}"))
    assert client.get(reverse("operations:mohe-submit")).status_code == 200


@pytest.mark.parametrize("role", MOHE_OUTSIDERS)
def test_the_submission_editor_is_closed_to_the_financial_roles(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"noeditor.{role.lower()}"))
    assert client.get(reverse("operations:mohe-submit")).status_code == 403


def test_the_auditor_reads_the_editor_and_cannot_post_it(
    client: Client, cohort: Any, seeded_settings: None
) -> None:
    """§3.3/15 gives the audit account ``V`` and withholds ``C``."""
    auditor = _user(Role.AUDIT_ACCOUNT, "aud.8g")
    client.force_login(auditor)

    assert client.get(reverse("operations:mohe-submit")).status_code == 200
    response = client.post(
        reverse("operations:mohe-submit"), {"cohort_code": cohort.code, **CONTENT}
    )
    assert response.status_code == 403
    assert not MoheSubmission.objects.exists()


def test_a_refused_post_leaves_a_denied_attempt(
    client: Client, cohort: Any, seeded_settings: None
) -> None:
    """BR-085 — the refusal is written before anything could roll it back."""
    from apps.core.models import AuditEvent

    auditor = _user(Role.AUDIT_ACCOUNT, "aud.denied.8g")
    client.force_login(auditor)
    client.post(reverse("operations:mohe-submit"), {"cohort_code": cohort.code, **CONTENT})

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=auditor).exists()


def test_both_ministry_screens_are_in_the_menu_for_the_registrar(
    registrar: User, priced_catalog: Any
) -> None:
    from apps.people import nav

    urls = {item["url"] for group in nav.nav_for(registrar) for item in group["items"]}
    assert reverse("operations:mohe") in urls
    assert reverse("operations:mohe-submit") in urls


def test_neither_ministry_screen_is_in_the_cashier_menu(seeded_settings: None) -> None:
    from apps.people import nav

    cashier = _user(Role.CASHIER, "cash.nav.8g")
    urls = {item["url"] for group in nav.nav_for(cashier) for item in group["items"]}
    assert reverse("operations:mohe") not in urls
    assert reverse("operations:mohe-submit") not in urls


# ---------------------------------------------------------------------------
# Opening a file
# ---------------------------------------------------------------------------
def test_the_registrar_opens_a_draft_file_from_the_editor(
    client: Client, registrar: User, cohort: Any
) -> None:
    client.force_login(registrar)
    response = client.post(
        reverse("operations:mohe-submit"),
        {"cohort_code": cohort.code, **CONTENT},
        follow=True,
    )
    assert response.status_code == 200

    submission = MoheSubmission.objects.get(cohort=cohort)
    assert submission.status == MoheStatus.DRAFT
    assert submission.training_axes_ar == CONTENT["training_axes_ar"]
    assert submission.created_by_id == registrar.pk
    assert response.context["submission"]["id"] == submission.pk


def test_a_draft_may_be_saved_with_its_content_incomplete(
    client: Client, registrar: User, cohort: Any
) -> None:
    """
    Deliberate: the file is assembled over days from what different people
    supply. What BR-016 gates is SENDING, not saving.
    """
    client.force_login(registrar)
    client.post(reverse("operations:mohe-submit"), {"cohort_code": cohort.code}, follow=True)

    submission = MoheSubmission.objects.get(cohort=cohort)
    assert submission.status == MoheStatus.DRAFT
    assert submission.trainer_name == ""


def test_a_cohort_that_already_holds_a_file_is_not_offered_again(
    manager: User, cohort: Any, make_cohort: Any
) -> None:
    free = make_cohort("SC-CMA", code="CO-8G-FREE")
    mohe_service.create_submission(actor=manager, cohort=cohort, data=dict(CONTENT))

    offered = [c for c, _label in mohe_service.submittable_cohort_choices(actor=manager)]
    assert cohort.code not in offered
    assert free.code in offered


def test_a_rejected_file_puts_its_cohort_back_on_the_list(
    manager: User, draft: MoheSubmission, cohort: Any
) -> None:
    """
    A rejection is not a dead end.

    ``resubmit`` remains the better route — the new file links back to the
    rejection it answers — but the cohort is offered here again, because
    refusing to let anyone start over would make a rejection final.
    """
    _attach_both(manager, draft)
    mohe_service.submit_to_mohe(actor=manager, submission=draft, submitted_on=SENT_ON)
    mohe_service.record_decision(
        actor=manager,
        submission=draft,
        approved=False,
        decided_on=DECIDED_ON,
        rejection_reason_ar="نقص في محاور التدريب",
    )
    offered = [c for c, _label in mohe_service.submittable_cohort_choices(actor=manager)]
    assert cohort.code in offered, "a rejected file leaves the cohort free again"


# ---------------------------------------------------------------------------
# Attachments — BR-016
# ---------------------------------------------------------------------------
def test_the_detail_page_names_what_is_missing(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    client.force_login(manager)
    response = client.get(reverse("operations:mohe-detail", args=[draft.pk]))

    missing = {m["purpose"] for m in response.context["submission"]["missing_attachments"]}
    assert missing == {"TRAINER_CV", "ENTITY_LICENSE"}
    assert response.context["can_send"] is False
    assert response.context["send_blocked_by_documents"] is True


def test_the_registrar_uploads_a_required_document_from_the_screen(
    client: Client, registrar: User, draft: MoheSubmission
) -> None:
    client.force_login(registrar)
    client.post(
        reverse("operations:mohe-detail", args=[draft.pk]),
        {"action": "attach", "purpose": "TRAINER_CV", "upload": _pdf("cv.pdf")},
        follow=True,
    )
    assert mohe_service.missing_attachments(draft) == ["ENTITY_LICENSE"]


def test_the_send_button_appears_only_once_both_documents_are_in(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    _attach_both(manager, draft)
    client.force_login(manager)
    response = client.get(reverse("operations:mohe-detail", args=[draft.pk]))

    assert response.context["submission"]["missing_attachments"] == []
    assert response.context["can_send"] is True


def test_documents_cannot_be_changed_after_the_file_has_gone(
    manager: User, draft: MoheSubmission
) -> None:
    """The ministry decided on the documents it received."""
    _attach_both(manager, draft)
    mohe_service.submit_to_mohe(actor=manager, submission=draft, submitted_on=SENT_ON)

    with pytest.raises(ValidationError, match="غادر المركز"):
        mohe_service.attach_document(
            actor=manager, submission=draft, purpose="TRAINER_CV", upload=_pdf("new.pdf")
        )


def test_the_cashier_cannot_attach_a_document(draft: MoheSubmission, seeded_settings: None) -> None:
    cashier = _user(Role.CASHIER, "cash.attach.8g")
    with pytest.raises(PermissionDenied):
        mohe_service.attach_document(
            actor=cashier, submission=draft, purpose="TRAINER_CV", upload=_pdf("cv.pdf")
        )


# ---------------------------------------------------------------------------
# Sending — §3.3/15 footnote ⁹: REG drafts, MGR sends
# ---------------------------------------------------------------------------
def test_the_registrar_may_not_send_the_file(
    client: Client, registrar: User, manager: User, draft: MoheSubmission
) -> None:
    _attach_both(manager, draft)
    client.force_login(registrar)

    response = client.post(
        reverse("operations:mohe-detail", args=[draft.pk]),
        {"action": "send", "submitted_on": SENT_ON.isoformat()},
    )
    assert response.status_code == 403
    draft.refresh_from_db()
    assert draft.status == MoheStatus.DRAFT


def test_sending_without_both_documents_is_refused_with_the_rule(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    client.force_login(manager)
    response = client.post(
        reverse("operations:mohe-detail", args=[draft.pk]),
        {"action": "send", "submitted_on": SENT_ON.isoformat()},
        follow=True,
    )
    assert any("BR-016" in str(m) for m in response.context["messages"])
    draft.refresh_from_db()
    assert draft.status == MoheStatus.DRAFT


def test_the_manager_sends_a_complete_file(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    _attach_both(manager, draft)
    client.force_login(manager)
    client.post(
        reverse("operations:mohe-detail", args=[draft.pk]),
        {"action": "send", "submitted_on": SENT_ON.isoformat()},
        follow=True,
    )
    draft.refresh_from_db()
    assert draft.status == MoheStatus.SUBMITTED
    assert draft.submitted_on == SENT_ON


# ---------------------------------------------------------------------------
# The decision — §3.3/14
# ---------------------------------------------------------------------------
@pytest.fixture
def sent(manager: User, draft: MoheSubmission) -> MoheSubmission:
    _attach_both(manager, draft)
    mohe_service.submit_to_mohe(actor=manager, submission=draft, submitted_on=SENT_ON)
    draft.refresh_from_db()
    return draft


def test_approval_from_the_screen_records_the_number_and_the_deadline(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    client.force_login(manager)
    client.post(
        reverse("operations:mohe-detail", args=[sent.pk]),
        {
            "action": "approve",
            "decided_on": DECIDED_ON.isoformat(),
            "mohe_course_number": "MOHE/2026/331",
            "registration_deadline": DEADLINE.isoformat(),
        },
        follow=True,
    )
    sent.refresh_from_db()
    assert sent.status == MoheStatus.APPROVED
    assert sent.mohe_course_number == "MOHE/2026/331"
    assert sent.registration_deadline == DEADLINE
    assert sent.decided_on == DECIDED_ON


def test_approval_without_a_ministry_number_is_refused_before_the_database(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    """C-16 — an approval nobody can produce evidence for."""
    client.force_login(manager)
    response = client.post(
        reverse("operations:mohe-detail", args=[sent.pk]),
        {"action": "approve", "decided_on": DECIDED_ON.isoformat()},
        follow=True,
    )
    assert any("C-16" in str(m) for m in response.context["messages"])
    sent.refresh_from_db()
    assert sent.status == MoheStatus.SUBMITTED


def test_rejection_from_the_screen_stores_the_reason_verbatim(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    reason = "المحاور التدريبية غير كافية، ويلزم بيان الجوانب العملية بالتفصيل."
    client.force_login(manager)
    client.post(
        reverse("operations:mohe-detail", args=[sent.pk]),
        {
            "action": "reject",
            "decided_on": DECIDED_ON.isoformat(),
            "rejection_reason_ar": reason,
        },
        follow=True,
    )
    sent.refresh_from_db()
    assert sent.status == MoheStatus.REJECTED
    assert sent.rejection_reason_ar == reason, "BR-014 — as received, not summarised"


def test_rejection_without_a_reason_is_refused(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    client.force_login(manager)
    response = client.post(
        reverse("operations:mohe-detail", args=[sent.pk]),
        {"action": "reject", "decided_on": DECIDED_ON.isoformat()},
        follow=True,
    )
    assert any("BR-014" in str(m) for m in response.context["messages"])
    sent.refresh_from_db()
    assert sent.status == MoheStatus.SUBMITTED


def test_the_registrar_may_not_record_a_decision(
    client: Client, registrar: User, sent: MoheSubmission
) -> None:
    """§3.3/14 gives the registration officer ``V P`` and no ``A``."""
    client.force_login(registrar)
    response = client.post(
        reverse("operations:mohe-detail", args=[sent.pk]),
        {
            "action": "approve",
            "decided_on": DECIDED_ON.isoformat(),
            "mohe_course_number": "X/1",
        },
    )
    assert response.status_code == 403
    sent.refresh_from_db()
    assert sent.status == MoheStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Resubmission
# ---------------------------------------------------------------------------
@pytest.fixture
def rejected(manager: User, sent: MoheSubmission) -> MoheSubmission:
    mohe_service.record_decision(
        actor=manager,
        submission=sent,
        approved=False,
        decided_on=DECIDED_ON,
        rejection_reason_ar="نقص في بيان الجوانب العملية",
    )
    sent.refresh_from_db()
    return sent


def test_resubmission_opens_a_new_file_and_leaves_the_rejection_readable(
    client: Client, registrar: User, rejected: MoheSubmission
) -> None:
    client.force_login(registrar)
    response = client.post(
        reverse("operations:mohe-detail", args=[rejected.pk]),
        {"action": "resubmit", **CONTENT, "practical_aspects_ar": "تطبيقات مخبرية مفصّلة"},
        follow=True,
    )
    assert response.status_code == 200

    fresh = MoheSubmission.objects.get(resubmission_of=rejected)
    assert fresh.status == MoheStatus.DRAFT
    assert fresh.cohort_id == rejected.cohort_id
    assert fresh.practical_aspects_ar == "تطبيقات مخبرية مفصّلة"
    assert response.context["submission"]["id"] == fresh.pk

    rejected.refresh_from_db()
    assert rejected.status == MoheStatus.REJECTED
    assert rejected.rejection_reason_ar == "نقص في بيان الجوانب العملية"


def test_the_rejected_file_links_forward_to_what_answered_it(
    client: Client, registrar: User, manager: User, rejected: MoheSubmission
) -> None:
    fresh = mohe_service.resubmit(actor=registrar, rejected=rejected, data=dict(CONTENT))

    client.force_login(manager)
    detail = client.get(reverse("operations:mohe-detail", args=[rejected.pk]))
    assert detail.context["submission"]["resubmissions"] == [fresh.pk]

    forward = client.get(reverse("operations:mohe-detail", args=[fresh.pk]))
    assert forward.context["submission"]["resubmission_of"] == rejected.pk


def test_a_file_that_was_not_rejected_offers_no_resubmission(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    client.force_login(manager)
    response = client.get(reverse("operations:mohe-detail", args=[sent.pk]))
    assert response.context["can_resubmit"] is False

    with pytest.raises(ValidationError, match="مرفوض"):
        mohe_service.resubmit(actor=manager, rejected=sent, data=dict(CONTENT))


# ---------------------------------------------------------------------------
# The gate reads the file the screen created — BR-013
# ---------------------------------------------------------------------------
def test_an_approval_recorded_on_screen_is_what_opens_enrolment(
    client: Client,
    manager: User,
    registrar: User,
    cohort: Any,
    make_participant: Any,
    priced_catalog: Any,
) -> None:
    """
    The whole point of the sprint, end to end.

    Every step here goes through a screen — the editor, the upload, the send,
    the decision — and the object the BR-013 gate then finds is the one those
    screens produced.
    """
    from apps.operations.services import enrollment_service

    client.force_login(registrar)
    client.post(
        reverse("operations:mohe-submit"), {"cohort_code": cohort.code, **CONTENT}, follow=True
    )
    submission = MoheSubmission.objects.get(cohort=cohort)

    for purpose, name in (("TRAINER_CV", "cv.pdf"), ("ENTITY_LICENSE", "lic.pdf")):
        client.post(
            reverse("operations:mohe-detail", args=[submission.pk]),
            {"action": "attach", "purpose": purpose, "upload": _pdf(name)},
            follow=True,
        )

    assert not mohe_service.cohort_is_approved(cohort), "not yet — nothing has been sent"

    client.force_login(manager)
    client.post(
        reverse("operations:mohe-detail", args=[submission.pk]),
        {"action": "send", "submitted_on": SENT_ON.isoformat()},
        follow=True,
    )
    client.post(
        reverse("operations:mohe-detail", args=[submission.pk]),
        {
            "action": "approve",
            "decided_on": DECIDED_ON.isoformat(),
            "mohe_course_number": "MOHE/2026/909",
            "registration_deadline": DEADLINE.isoformat(),
        },
        follow=True,
    )

    submission.refresh_from_db()
    assert mohe_service.cohort_is_approved(cohort)
    found = mohe_service.approved_submission_for(cohort)
    assert found is not None and found.pk == submission.pk

    enrollment = enrollment_service.create_enrollment(
        actor=registrar,
        participant=make_participant(1),
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=priced_catalog,
        code="EN-8G-1",
    )
    assert enrollment.pk is not None


def test_the_registrar_records_a_trainee_name_upload_from_the_mohe_screen(
    client: Client,
    registrar: User,
    manager: User,
    cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> None:
    from django.utils import timezone

    from apps.operations.services import enrollment_service

    approve_cohort(cohort, deadline=date(2099, 1, 1))
    enrollment = make_enrollment(cohort, index=81)
    enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
    enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)

    client.force_login(registrar)
    response = client.post(
        reverse("operations:mohe-name-upload", args=[enrollment.code]), follow=True
    )

    enrollment.refresh_from_db()
    assert response.status_code == 200
    assert enrollment.mohe_uploaded_on == timezone.localdate()


# ---------------------------------------------------------------------------
# "Export Uploaded Names" — the CSV of trainees already sent to the ministry
# ---------------------------------------------------------------------------
UPLOADED_ON = date(2026, 9, 12)


@pytest.fixture
def uploaded_and_pending(
    registrar: User,
    manager: User,
    cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> tuple[Any, Any]:
    """Two approved trainees on an approved cohort; only the first is uploaded."""
    from apps.operations.services import enrollment_service

    approve_cohort(cohort, deadline=DEADLINE, course_number="MOHE/2026/77")
    uploaded = make_enrollment(cohort, index=91)
    pending = make_enrollment(cohort, index=92)
    for enrollment in (uploaded, pending):
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    enrollment_service.mark_uploaded_to_mohe(
        actor=registrar, enrollment=uploaded, uploaded_on=UPLOADED_ON
    )
    uploaded.refresh_from_db()
    pending.refresh_from_db()
    return uploaded, pending


def _csv_rows(response: Any) -> list[list[str]]:
    import csv
    import io

    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.parametrize("role", MOHE_READERS)
def test_every_reader_of_the_register_may_download_the_uploaded_names(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"export.{role.lower()}"))
    response = client.get(reverse("operations:mohe-uploaded-export"))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8-sig"
    assert response["Content-Disposition"].startswith('attachment; filename="mohe-uploaded-names-')
    assert response["Content-Disposition"].endswith('.csv"')


@pytest.mark.parametrize("role", MOHE_OUTSIDERS)
def test_the_financial_roles_cannot_download_the_uploaded_names(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The download reads through the same gate as the page: 403, not an empty file."""
    client.force_login(_user(role, f"noexport.{role.lower()}"))
    assert client.get(reverse("operations:mohe-uploaded-export")).status_code == 403


def test_the_export_button_is_on_the_register_for_its_readers(
    client: Client, registrar: User, seeded_settings: None
) -> None:
    """
    One button became three.

    The register used to offer «تصدير الأسماء المرفوعة» alone — the names
    already handed to the ministry, which is the opposite of the list a
    registrar needs. The head now names the three populations and each link
    carries the register's own filters. ``mohe-uploaded-export`` is kept as a
    route for anyone holding its URL, and has its own tests above.
    """
    client.force_login(registrar)
    page = client.get(reverse("operations:mohe")).content.decode()
    base = reverse("operations:mohe-names-export")

    assert "تصدير أسماء المعروض" in page
    assert f'href="{base}?scope=pending"' in page
    assert f'href="{base}?scope=uploaded"' in page
    assert f'href="{base}?scope=all"' in page


def test_the_export_holds_the_uploaded_trainees_only(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    uploaded, pending = uploaded_and_pending

    client.force_login(registrar)
    rows = _csv_rows(client.get(reverse("operations:mohe-uploaded-export")))

    header, *body = rows
    assert len(header) == 8
    assert [row[1] for row in body] == [uploaded.code]
    assert pending.code not in {row[1] for row in body}

    (row,) = body
    assert row == [
        uploaded.participant.name_ar,
        uploaded.code,
        uploaded.cohort.program.name_ar,
        uploaded.cohort.code,
        "MOHE/2026/77",
        uploaded.approved_at.date().isoformat(),
        UPLOADED_ON.isoformat(),
        DEADLINE.isoformat(),
    ]


# ---------------------------------------------------------------------------
# The scoped names download — one cohort, or what the register is showing
# ---------------------------------------------------------------------------
def _names(client: Client, **params: Any) -> list[list[str]]:
    return _csv_rows(client.get(reverse("operations:mohe-names-export"), params))


def test_the_pending_names_are_a_sheet_of_their_own(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    """
    The list a registrar carries to the ministry's own system.

    Written because the only download on the screen was the opposite one: it
    held the names already uploaded, so the names still owed could be had only
    by subtracting one list from another by eye.
    """
    uploaded, pending = uploaded_and_pending

    client.force_login(registrar)
    header, *body = _names(client, scope="pending")

    assert [row[1] for row in body] == [pending.code]
    assert uploaded.code not in {row[1] for row in body}
    # No «تاريخ رفع» column: it would be empty on every line, and no
    # «حالة الرفع»: it would say the same thing on every line.
    assert header == [
        "المتدرب",
        "رمز التسجيل",
        "البرنامج",
        "الدفعة",
        "الرقم الوزاري",
        "تاريخ اعتماد التسجيل",
        "مهلة التسجيل في الوزارة",
    ]


def test_the_whole_file_says_which_names_were_uploaded_and_which_were_not(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    uploaded, pending = uploaded_and_pending

    client.force_login(registrar)
    header, *body = _names(client, scope="all")

    assert {row[1] for row in body} == {uploaded.code, pending.code}
    # «حالة الرفع» sits beside the date it explains, not at the end of the row.
    at = header.index("حالة الرفع")
    assert header[at - 1] == "تاريخ رفع الاسم للوزارة"
    states = {row[1]: row[at] for row in body}
    assert states[uploaded.code] == "مرفوع"
    assert states[pending.code] == "بانتظار الرفع"


def test_the_download_narrows_to_the_cohort_whose_button_was_pressed(
    client: Client,
    registrar: User,
    manager: User,
    uploaded_and_pending: tuple[Any, Any],
    make_cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> None:
    """A second approved cohort must not appear in the first one's sheet."""
    from apps.operations.services import enrollment_service

    uploaded, pending = uploaded_and_pending
    other = make_cohort(code="CO-8M-OTHER")
    approve_cohort(other, deadline=DEADLINE, course_number="MOHE/2026/78")
    outsider = make_enrollment(other, index=93)
    enrollment_service.record_voucher(actor=registrar, enrollment=outsider)
    enrollment_service.approve_enrollment(actor=manager, enrollment=outsider)

    client.force_login(registrar)
    _header, *body = _names(client, scope="all", cohort=pending.cohort.code)

    assert {row[1] for row in body} == {uploaded.code, pending.code}
    assert outsider.code not in {row[1] for row in body}
    response = client.get(
        reverse("operations:mohe-names-export"), {"scope": "all", "cohort": other.code}
    )
    assert f'filename="mohe-names-all-{other.code}-' in response["Content-Disposition"]
    assert [row[1] for row in _csv_rows(response)[1:]] == [outsider.code]


def test_the_journey_back_keeps_the_filter_the_reader_left_behind(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    """
    Going and coming back must cost nothing.

    The file page used to send every reader to the head of the register, so a
    search typed once had to be typed again after each file. The link out
    carries ``q``/``status`` and the link back rebuilds them.
    """
    uploaded, _pending = uploaded_and_pending
    submission = uploaded.cohort.mohe_submissions.first()
    register = reverse("operations:mohe")

    client.force_login(registrar)
    page = client.get(
        reverse("operations:mohe-detail", args=[submission.pk]),
        {"q": "هندسة", "status": "APPROVED", "from": "register"},
    )

    assert page.context["came_from_register"] is True
    kept = "q=%D9%87%D9%86%D8%AF%D8%B3%D8%A9&status=APPROVED"
    assert page.context["back_url"] == f"{register}?{kept}"
    assert "رجوع إلى السجلّ" in page.content.decode()

    # Reached by a bare link instead, the page says «عودة» and goes to the top.
    plain = client.get(reverse("operations:mohe-detail", args=[submission.pk]))
    assert plain.context["came_from_register"] is False
    assert plain.context["back_url"] == register


def test_the_names_are_paged_and_the_pager_keeps_the_way_back(
    client: Client,
    registrar: User,
    manager: User,
    cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> None:
    """
    Fifty-one approved trainees: the page shows fifty and says so.

    ``page_of``'s own size is the window, and the count beside it is the whole
    list — a pager that reported its window would answer «how many are there»
    with «fifty».
    """
    approve_cohort(cohort, deadline=DEADLINE, course_number="MOHE/2026/81")
    for index in range(101, 152):
        enrollment = make_enrollment(cohort, index=index)
        enrollment_service.record_voucher(actor=registrar, enrollment=enrollment)
        enrollment_service.approve_enrollment(actor=manager, enrollment=enrollment)
    submission = cohort.mohe_submissions.first()

    client.force_login(registrar)
    first = client.get(
        reverse("operations:mohe-detail", args=[submission.pk]), {"from": "register"}
    )
    page = first.context["name_page"]

    assert page["total"] == 51
    assert len(page["rows"]) == 50
    assert page["pages"] == 2
    # The pager keeps ``from`` so page two still knows the way back.
    assert "from=register&amp;page=2#names" in first.content.decode()

    second = client.get(
        reverse("operations:mohe-detail", args=[submission.pk]),
        {"from": "register", "page": "2"},
    )
    assert len(second.context["name_page"]["rows"]) == 1
    assert second.context["name_page"]["start"] == 51


def test_a_file_page_with_no_approved_trainee_explains_and_points(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    """§8 — an empty state says why it is empty and where the step is."""
    mohe_service.record_decision(
        actor=manager,
        submission=sent,
        approved=True,
        decided_on=DECIDED_ON,
        mohe_course_number="MOHE/2026/82",
        registration_deadline=DEADLINE,
    )
    client.force_login(manager)

    page = client.get(reverse("operations:mohe-detail", args=[sent.pk])).content.decode()

    assert "لا تسجيل معتمد على هذه الدفعة بعد" in page
    assert f'{reverse("operations:enrollments")}?cohort={sent.cohort.code}' in page
    # And no export button for a sheet that would come out with no rows.
    assert "scope=pending" not in page


def test_an_unknown_scope_falls_back_to_the_whole_file_rather_than_refusing(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    """A hand-edited URL yields the widest honest answer, not a 500."""
    uploaded, pending = uploaded_and_pending

    client.force_login(registrar)
    _header, *body = _names(client, scope="whatever")

    assert {row[1] for row in body} == {uploaded.code, pending.code}


@pytest.mark.parametrize("role", MOHE_OUTSIDERS)
def test_the_financial_roles_cannot_download_the_names_either(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"noscope.{role.lower()}"))
    response = client.get(reverse("operations:mohe-names-export"), {"scope": "all"})
    assert response.status_code == 403


def test_the_register_offers_the_three_scopes_and_a_button_per_cohort(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    """
    Two levels of export: the whole filtered register, and one open file.

    The per-cohort links live inside the row that is open, so the closed
    register draws none of them — the page stays the register, not a wall of
    buttons for every file on it.
    """
    uploaded, _pending = uploaded_and_pending
    code = uploaded.cohort.code
    base = reverse("operations:mohe-names-export")

    client.force_login(registrar)
    register = client.get(reverse("operations:mohe")).content.decode()
    file_page = client.get(
        reverse("operations:mohe-detail", args=[uploaded.cohort.mohe_submissions.first().pk])
    ).content.decode()

    for scope in ("pending", "uploaded", "all"):
        assert f'href="{base}?scope={scope}"' in register
        assert f"cohort={code}&amp;scope={scope}" not in register
        assert f"cohort={code}&amp;scope={scope}" in file_page

    # The button leads to that file's page, at its names. It sits in the
    # actions column, not in «المسجّلون»: its text widened that column to
    # 198px, the widest in the table, for a count of one digit.
    assert "المسجّلون" in register
    assert "from=register#names" in register


def test_the_count_button_shows_the_approved_beside_the_standing(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    """
    Two numbers, because they count two different populations.

    «المسجّلون» counts the standing enrolments and the names below are the
    APPROVED ones. A button labelled with the first that opened a list of the
    second read as a bug, and the page used to apologise for it in prose.
    """
    uploaded, _pending = uploaded_and_pending

    client.force_login(registrar)
    row = next(
        r
        for r in client.get(reverse("operations:mohe")).context["submissions"]
        if r["cohort_code"] == uploaded.cohort.code
    )

    assert row["has_names"] is True
    assert row["approved_count"] == 2
    assert row["pending_upload_count"] == 1


def test_a_file_with_no_approved_enrolment_draws_no_button_to_press(
    client: Client, registrar: User, draft: MoheSubmission
) -> None:
    """§3.4 — a control whose use would lead nowhere is not drawn."""
    client.force_login(registrar)
    response = client.get(reverse("operations:mohe"))

    row = next(
        r for r in response.context["submissions"] if r["cohort_code"] == draft.cohort.code
    )
    assert row["has_names"] is False
    assert f'href="?names={draft.cohort.code}' not in response.content.decode()


def test_the_names_live_on_the_file_page_not_in_the_register(
    client: Client,
    registrar: User,
    manager: User,
    uploaded_and_pending: tuple[Any, Any],
    make_cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> None:
    """
    Fifty trainees belong on a page, not in a row of the register.

    They used to sit in a second card below the register, a fold per cohort:
    with many programmes the reader had to scroll away from the row and find
    the matching fold by its code. Folding them INTO the row was worse — one
    cohort of fifty drowns the register it was meant to explain. So they live
    on the file's own page, where there is width, a pager and a print.
    """
    uploaded, pending = uploaded_and_pending
    other = make_cohort(code="CO-8M-SHUT")
    approve_cohort(other, deadline=DEADLINE, course_number="MOHE/2026/80")
    outsider = make_enrollment(other, index=95)
    enrollment_service.record_voucher(actor=registrar, enrollment=outsider)
    enrollment_service.approve_enrollment(actor=manager, enrollment=outsider)

    client.force_login(registrar)
    register = client.get(reverse("operations:mohe")).content.decode()
    page = client.get(
        reverse("operations:mohe-detail", args=[uploaded.cohort.mohe_submissions.first().pk])
    ).content.decode()

    # No name on the register at all — it stays a register.
    assert "names-box" not in register, "the duplicate card is gone"
    assert pending.participant.name_ar not in register
    # And this file's page carries its own trainees and nobody else's.
    assert pending.participant.name_ar in page
    assert outsider.participant.name_ar not in page
    # The trainee's own page is one click from the name.
    assert reverse("operations:account", args=[pending.code]) in page


def test_the_filtered_register_hands_down_the_slice_it_is_showing(
    client: Client,
    registrar: User,
    manager: User,
    uploaded_and_pending: tuple[Any, Any],
    make_cohort: Any,
    approve_cohort: Any,
    make_enrollment: Any,
) -> None:
    """
    A download that ignored the filter above it would be a quiet lie.

    So the export links carry ``q``/``status``, and the rows obey them.
    """
    from apps.operations.services import enrollment_service

    other = make_cohort(code="CO-8M-FILTER")
    approve_cohort(other, deadline=DEADLINE, course_number="MOHE/2026/79")
    outsider = make_enrollment(other, index=94)
    enrollment_service.record_voucher(actor=registrar, enrollment=outsider)
    enrollment_service.approve_enrollment(actor=manager, enrollment=outsider)

    client.force_login(registrar)
    page = client.get(reverse("operations:mohe"), {"q": other.code}).content.decode()
    assert f"?q={other.code}&amp;scope=pending" in page

    _header, *body = _names(client, scope="all", q=other.code)
    assert [row[1] for row in body] == [outsider.code]


def test_an_uploaded_trainee_who_is_no_longer_active_leaves_the_export(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    from apps.operations.models import Enrollment, EnrollmentStatus

    uploaded, _pending = uploaded_and_pending
    Enrollment.objects.filter(pk=uploaded.pk).update(status=EnrollmentStatus.CANCELLED)

    client.force_login(registrar)
    _header, *body = _csv_rows(client.get(reverse("operations:mohe-uploaded-export")))
    assert body == []


def test_the_export_carries_a_bom_so_excel_reads_the_arabic(
    client: Client, registrar: User, uploaded_and_pending: tuple[Any, Any]
) -> None:
    uploaded, _pending = uploaded_and_pending

    client.force_login(registrar)
    response = client.get(reverse("operations:mohe-uploaded-export"))

    assert response.content.startswith("﻿".encode())
    assert response.content.count("﻿".encode()) == 1, "one BOM, at the very start"
    assert uploaded.participant.name_ar.encode() in response.content
    assert "المتدرب".encode() in response.content


# ---------------------------------------------------------------------------
# A-05 — the screens read projections, never models
# ---------------------------------------------------------------------------
def test_the_ministry_views_name_no_model() -> None:
    """
    ADR-008 in the specific.

    ``tests/test_architecture.py`` already fails the build on any
    ``apps.*.models`` import in a ``views.py``. This is the narrower claim
    that matters for Sprint 8G: the read layer added to ``mohe_service`` is
    what the screens use, so no ministry model name appears in the view file
    even as a local import.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("apps/operations/views.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not [m for m in imported if m.startswith("apps.") and ".models" in m]
    assert "apps.operations.services" in imported, "the screens read through the service layer"


def test_the_read_layer_projects_rather_than_returning_models(manager: User, draft: Any) -> None:
    rows = mohe_service.list_submissions(actor=manager)
    assert rows and all(isinstance(row, dict) for row in rows)

    detail = mohe_service.get_submission(actor=manager, submission_id=draft.pk)
    assert isinstance(detail, dict)
    assert set(detail["content"]) == set(mohe_service.CONTENT_FIELDS)


def test_the_listing_is_filterable_by_status_and_cohort(
    manager: User, draft: MoheSubmission, make_cohort: Any
) -> None:
    other = make_cohort("SC-CMA", code="CO-8G-OTHER")
    mohe_service.create_submission(actor=manager, cohort=other, data=dict(CONTENT))

    assert len(mohe_service.list_submissions(actor=manager)) == 2
    assert len(mohe_service.list_submissions(actor=manager, status="SUBMITTED")) == 0
    found = mohe_service.list_submissions(actor=manager, query="CO-8G-OTHER")
    assert [r["cohort_code"] for r in found] == ["CO-8G-OTHER"]


def test_a_missing_submission_is_a_404(client: Client, manager: User) -> None:
    client.force_login(manager)
    assert client.get(reverse("operations:mohe-detail", args=[99999])).status_code == 404


# ---------------------------------------------------------------------------
# The attachment service — built for BR-016, kept to that
# ---------------------------------------------------------------------------
def test_an_uploaded_document_records_its_own_digest(manager: User, draft: MoheSubmission) -> None:
    import hashlib

    payload = b"%PDF-1.4 the trainer CV"
    mohe_service.attach_document(
        actor=manager,
        submission=draft,
        purpose="TRAINER_CV",
        upload=SimpleUploadedFile("cv.pdf", payload, "application/pdf"),
    )
    stored = mohe_service.get_submission(actor=manager, submission_id=draft.pk)["attachments"]
    assert stored[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert stored[0]["size_bytes"] == len(payload)


def test_an_empty_file_is_refused(manager: User, draft: MoheSubmission) -> None:
    with pytest.raises(ValidationError, match="فارغ"):
        mohe_service.attach_document(
            actor=manager,
            submission=draft,
            purpose="TRAINER_CV",
            upload=SimpleUploadedFile("empty.pdf", b"", "application/pdf"),
        )


def test_a_file_over_the_limit_is_refused(manager: User, draft: MoheSubmission) -> None:
    from apps.core.models.attachment import MAX_ATTACHMENT_BYTES

    oversized = SimpleUploadedFile("big.pdf", b"x" * (MAX_ATTACHMENT_BYTES + 1), "application/pdf")
    with pytest.raises(ValidationError, match="يتجاوز الحد"):
        mohe_service.attach_document(
            actor=manager, submission=draft, purpose="TRAINER_CV", upload=oversized
        )


def test_a_second_upload_for_the_same_purpose_supersedes_the_first(
    manager: User, draft: MoheSubmission
) -> None:
    """
    BR-016 asks whether the CV is attached, not how many were tried.

    The replacement is audited so the fact that it happened survives.
    """
    from apps.core.models import AuditEvent

    mohe_service.attach_document(
        actor=manager, submission=draft, purpose="TRAINER_CV", upload=_pdf("first.pdf")
    )
    mohe_service.attach_document(
        actor=manager, submission=draft, purpose="TRAINER_CV", upload=_pdf("second.pdf")
    )

    stored = mohe_service.get_submission(actor=manager, submission_id=draft.pk)["attachments"]
    assert [a["original_filename"] for a in stored] == ["second.pdf"]

    replacement = AuditEvent.objects.filter(action="UPDATE", entity_type="core.Attachment").first()
    assert replacement is not None
    assert "first.pdf" in str(replacement.changes)


def test_an_unknown_purpose_is_refused(manager: User, draft: MoheSubmission) -> None:
    from apps.core.services import attachment_service

    with pytest.raises(ValidationError, match="غرض مرفق غير معروف"):
        attachment_service.attach(
            actor=manager, target=draft, purpose="NOT_A_PURPOSE", upload=_pdf("x.pdf")
        )


# ---------------------------------------------------------------------------
# The register after its polish pass: the next step belongs to the reader who
# may take it, the deadline counts in Arabic, and the names register stops
# drawing empty tables.
# ---------------------------------------------------------------------------
def test_the_row_offers_an_act_only_to_the_role_that_holds_it(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    """
    The auditor reads this register and holds nothing on MOHE_SUBMIT, so
    «أكمل الملف» promised an act that refuses — a promise then a refusal, and a
    DENIED_ATTEMPT the screen itself invited (BR-085).
    """
    register = reverse("operations:mohe")

    client.force_login(manager)
    manager_page = client.get(register).content.decode("utf-8")
    assert "أكمل الملف" in manager_page

    client.force_login(_user(Role.AUDIT_ACCOUNT, "mohe.aud.act"))
    auditor_page = client.get(register).content.decode("utf-8")

    assert "أكمل الملف" not in auditor_page
    assert "أعد الإرسال" not in auditor_page
    assert "سجّل القرار" not in auditor_page
    # The reader still reaches the file — by the programme's name, which is
    # the link on every row. The old «عرض الملف» button beside it pointed at
    # the same URL, so it spent a column and added no road.
    assert reverse("operations:mohe-detail", args=[draft.pk]) in auditor_page
    assert "عرض الملف" not in auditor_page


def test_the_registrar_is_offered_the_draft_but_not_the_decision(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    """§3.3/14 gives APPROVE to the manager alone; recording a decision is his."""
    register = reverse("operations:mohe")

    client.force_login(manager)
    assert "سجّل القرار" in client.get(register).content.decode("utf-8")

    client.force_login(_user(Role.REGISTRATION_OFFICER, "mohe.reg.act"))
    assert "سجّل القرار" not in client.get(register).content.decode("utf-8")


def test_the_deadline_counts_days_in_arabic(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    """«1 يوماً» and «10 يوماً» are not Arabic; the project has a plural filter."""
    from datetime import timedelta

    from django.utils import timezone

    mohe_service.record_decision(
        actor=manager,
        submission=sent,
        approved=True,
        decided_on=DECIDED_ON,
        mohe_course_number="MOHE/2026/9",
        registration_deadline=timezone.localdate() + timedelta(days=1),
    )
    client.force_login(manager)

    page = client.get(reverse("operations:mohe")).content.decode("utf-8")

    assert "يوم واحد" in page
    assert "1 يوماً" not in page and "يوماً متبقياً" not in page


def test_the_register_counts_every_status_once_and_in_one_place(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    """
    One tally, not two.

    The tiles counted four of the five statuses and a strip of chips below
    them repeated the same figures — and named «مرفوض», which the tiles did
    not. So the one status that demands an act was the one the tiles hid, and
    the reader had two answers to «how many». The fifth tile closes the gap
    and the strip is gone.
    """
    client.force_login(manager)

    response = client.get(reverse("operations:mohe"))
    page = response.content.decode("utf-8")

    assert "توزيع النتائج المعروضة" not in page
    assert "status_counts" not in response.context
    labels = [str(t["label"]) for t in response.context["tiles"]]
    assert labels == ["كل الملفات", "مسودات", "بانتظار الوزارة", "معتمدة", "مرفوضة"]
    assert f'{reverse("operations:mohe")}?status=REJECTED' in page


def test_the_register_searches_without_a_button_and_swaps_its_own_rows(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    client.force_login(manager)

    page = client.get(reverse("operations:mohe")).content.decode("utf-8")

    assert 'id="mohe-results"' in page and 'hx-target="#mohe-results"' in page
    narrowed = client.get(reverse("operations:mohe"), {"q": draft.cohort.code})
    assert [r["cohort_code"] for r in narrowed.context["submissions"]] == [draft.cohort.code]


def test_the_cohort_code_reaches_its_row_in_the_cohorts_register(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    client.force_login(manager)

    page = client.get(reverse("operations:mohe")).content.decode("utf-8")
    link = f'{reverse("operations:cohorts")}?q={draft.cohort.code}'

    assert f'href="{link}"' in page
    found = client.get(reverse("operations:cohorts"), {"q": draft.cohort.code})
    assert [c["code"] for c in found.context["cohorts"]] == [draft.cohort.code]


def test_a_cohort_with_no_approved_enrolment_gets_a_sentence_not_an_empty_table(
    client: Client, manager: User, sent: MoheSubmission
) -> None:
    """
    Three seven-column tables saying nothing filled half the page — and the
    sentence has to explain why «المسجّلون» above is not zero while this is.
    """
    mohe_service.record_decision(
        actor=manager,
        submission=sent,
        approved=True,
        decided_on=DECIDED_ON,
        mohe_course_number="MOHE/2026/10",
        registration_deadline=DEADLINE,
    )
    client.force_login(manager)

    response = client.get(reverse("operations:mohe"))
    page = response.content.decode("utf-8")

    assert response.context["mohe_name_sections"], "no section means this proves nothing"
    assert all(not s["rows"] for s in response.context["mohe_name_sections"])
    # On the register the cell says it briefly — the column is 98px wide —
    # and the file's own page carries the full sentence and the next step.
    assert "لا معتمد بعد" in page
    assert "from=register#names" not in page, "no button to open an empty table (§3.4)"
    row = next(
        r for r in response.context["submissions"] if r["cohort_code"] == sent.cohort.code
    )
    assert row["awaits_approved_enrolment"] is True
    assert row["has_names"] is False
    file_page = client.get(reverse("operations:mohe-detail", args=[sent.pk])).content.decode()
    assert "لا تسجيل معتمد على هذه الدفعة بعد" in file_page


def test_the_register_is_searchable_by_the_programme_on_every_row(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    """
    The programme is printed on every row and was not searchable: a reader
    looking for «هندسة الشبكات» had to know which cohort codes carry it.
    """
    client.force_login(manager)
    url = reverse("operations:mohe")

    by_code = client.get(url, {"q": draft.cohort.code}).context["submissions"]
    by_programme_code = client.get(url, {"q": draft.cohort.program.code}).context["submissions"]
    by_programme_name = client.get(
        url, {"q": draft.cohort.program.name_ar[:6]}
    ).context["submissions"]

    assert [r["cohort_code"] for r in by_code] == [draft.cohort.code]
    assert [r["cohort_code"] for r in by_programme_code] == [draft.cohort.code]
    assert [r["cohort_code"] for r in by_programme_name] == [draft.cohort.code]


# ---------------------------------------------------------------------------
# The open-file screen after its polish pass: a refusal that can be read, and
# an empty state that points somewhere.
# ---------------------------------------------------------------------------
def test_a_refused_save_says_so_and_keeps_what_was_typed(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    """
    The choices are recomputed on every request, so a cohort that gained a file
    while this page was open left the list, the field turned invalid, and the
    page came back with no message — and seven typed fields gone with it.
    """
    client.force_login(manager)

    response = client.post(
        reverse("operations:mohe-submit"),
        {
            "cohort_code": draft.cohort.code,  # already holds `draft`
            "training_axes_ar": "محاور كُتبت بعناية",
            "trainer_name": "د. تجربة",
        },
    )
    body = response.content.decode("utf-8")
    said = [str(m) for m in response.context["messages"]]

    assert response.status_code == 200
    assert said and draft.cohort.code in said[0], "the refusal was silent"
    assert "محاور كُتبت بعناية" in body and "د. تجربة" in body, "the typed content was thrown away"
    assert MoheSubmission.objects.filter(cohort=draft.cohort).count() == 1


def test_a_cohort_code_that_accepts_no_file_is_named_not_ignored(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    client.force_login(manager)
    url = reverse("operations:mohe-submit")

    unknown = client.get(url, {"cohort": "NO-SUCH-COHORT"})
    assert unknown.context["unknown_cohort"] is True
    assert "لا دفعة بالرمز" in unknown.content.decode("utf-8")

    taken = client.get(url, {"cohort": draft.cohort.code})
    assert taken.context["unknown_cohort"] is False
    assert taken.context["existing_file"]["id"] == draft.pk


def test_the_empty_state_points_where_this_reader_may_go(
    client: Client, manager: User, draft: MoheSubmission
) -> None:
    """«لا دفعة تقبل فتح ملف» was a dead end; both ways out are permission-gated."""
    client.force_login(manager)

    response = client.get(reverse("operations:mohe-submit"))
    body = response.content.decode("utf-8")

    assert not response.context["cohorts"], "a cohort is free, so this proves nothing"
    assert f'href="{reverse("operations:mohe")}"' in body
    assert f'href="{reverse("operations:cohorts")}"' in body

    client.force_login(_user(Role.AUDIT_ACCOUNT, "mohe.sub.aud"))
    auditor = client.get(reverse("operations:mohe-submit")).content.decode("utf-8")
    assert f'href="{reverse("operations:mohe")}"' in auditor, "the auditor reads both registers"
