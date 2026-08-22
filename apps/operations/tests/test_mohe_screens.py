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
from apps.operations.services import mohe_service
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
