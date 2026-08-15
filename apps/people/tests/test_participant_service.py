"""
Participant service — BR-003, BR-004, BR-005, BR-101, T-286.

The database owns the hard constraints; this layer owns the readable Arabic
message, the audit record, and the field projection that keeps a cashier from
seeing an identity document.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.models import AuditEvent, EffectiveSetting, Semester
from apps.people.models import Participant, ParticipantCategory, Role, User
from apps.people.services import participant_service as svc

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def registrar(seeded_settings: None) -> User:
    return User.objects.create_user(
        username="reg.one",
        password=PASSWORD,
        role=Role.REGISTRATION_OFFICER,
        full_name_ar="موظف التسجيل",
    )


@pytest.fixture
def cashier(seeded_settings: None) -> User:
    return User.objects.create_user(username="cash.one", password=PASSWORD, role=Role.CASHIER)


# ---------------------------------------------------------------------------
# Creation, numbering and the audit record (T-286, BR-001)
# ---------------------------------------------------------------------------
def test_create_allocates_a_number_and_audits_it(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant = svc.create_participant(actor=registrar, data=participant_data)

    assert participant.participant_number == "202610001"

    event = AuditEvent.objects.filter(action="CREATE", entity_type=svc.ENTITY).first()
    assert event is not None and event.changes is not None
    assert event.reference == "202610001", "T-286: the generated number must be the reference"
    assert event.changes["sequence_scope"] == "participant"
    assert event.changes["sequence_partition"] == "20261"
    assert event.changes["semester"] == active_semester.code
    assert event.actor_id == registrar.pk


def test_centre_participant_gets_type_five(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_data["category"] = ParticipantCategory.CENTER
    participant = svc.create_participant(actor=registrar, data=participant_data)
    assert participant.participant_number.startswith("20265")


def test_pledge_timestamp_is_stamped_on_creation(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    """BR-003 — the flag alone is not evidence; the moment is."""
    participant = svc.create_participant(actor=registrar, data=participant_data)
    assert participant.no_refund_pledge_at is not None

    event = AuditEvent.objects.filter(action="CREATE").first()
    assert event is not None and event.changes is not None
    assert event.changes["no_refund_pledge_at"]


# ---------------------------------------------------------------------------
# BR-003 / BR-004 — refusals with a readable reason
# ---------------------------------------------------------------------------
def test_br003_creation_without_the_pledge_is_refused(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_data["no_refund_pledge_accepted"] = False
    with pytest.raises(ValidationError) as exc:
        svc.create_participant(actor=registrar, data=participant_data)
    assert "no_refund_pledge_accepted" in exc.value.message_dict
    assert not Participant.objects.exists()


def test_br004_exemption_without_approval_reference_is_refused(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_data.update({"category": ParticipantCategory.EMPLOYEE, "is_exempt": True})
    with pytest.raises(ValidationError) as exc:
        svc.create_participant(actor=registrar, data=participant_data)
    assert "exemption_approval_ref" in exc.value.message_dict


def test_br004_exemption_with_approval_reference_is_recorded(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_data.update(
        {
            "category": ParticipantCategory.EMPLOYEE,
            "is_exempt": True,
            "exemption_approval_ref": "ر.ج/2026/118",
        }
    )
    svc.create_participant(actor=registrar, data=participant_data)
    event = AuditEvent.objects.filter(action="CREATE").first()
    assert event is not None and event.changes is not None
    assert event.changes["exemption_approval_ref"] == "ر.ج/2026/118"


def test_a_failed_creation_consumes_no_participant_number(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    """A rejected application must not burn a number out of the sequence."""
    bad = dict(participant_data, no_refund_pledge_accepted=False)
    with pytest.raises(ValidationError):
        svc.create_participant(actor=registrar, data=bad)

    good = svc.create_participant(actor=registrar, data=participant_data)
    assert good.participant_number == "202610001"


# ---------------------------------------------------------------------------
# BR-005 — duplicate identity documents warn, and the override is recorded
# ---------------------------------------------------------------------------
def test_br005_duplicate_identity_requires_a_reason(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    svc.create_participant(actor=registrar, data=participant_data)

    second = dict(participant_data, name_ar="شخص آخر بنفس الرقم الوطني")
    with pytest.raises(ValidationError) as exc:
        svc.create_participant(actor=registrar, data=second)
    assert "202610001" in str(exc.value), "the warning must show the matching record"


def test_br005_override_is_permitted_and_audited_with_its_reason(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    svc.create_participant(actor=registrar, data=participant_data)
    second = dict(participant_data, name_ar="توأم بنفس وثيقة الهوية")

    created = svc.create_participant(
        actor=registrar,
        data=second,
        duplicate_override_reason="توأمان بوثيقة صادرة بالخطأ — مراجعة أحوال مدنية",
    )
    assert created.participant_number == "202610002"

    override = AuditEvent.objects.filter(summary_ar__contains="تجاوز تحذير").first()
    assert override is not None and override.changes is not None
    assert "مراجعة أحوال مدنية" in override.changes["reason"]


def test_br005_block_mode_refuses_outright(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    """The same rule tightened by a setting, not by a migration."""
    svc.create_participant(actor=registrar, data=participant_data)
    EffectiveSetting.objects.filter(key=svc.UNIQUENESS_MODE_KEY).update(value="BLOCK")

    with pytest.raises(PermissionDenied):
        svc.create_participant(
            actor=registrar,
            data=dict(participant_data, name_ar="محاولة تحت وضع المنع"),
            duplicate_override_reason="سبب لا يكفي في وضع المنع",
        )
    assert AuditEvent.objects.filter(denial_rule="BR-005").exists()


# ---------------------------------------------------------------------------
# Q-31 — values validated against the reference lists
# ---------------------------------------------------------------------------
def test_unknown_qualification_is_refused(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant_data["qualification"] = "NOT_ON_THE_LIST"
    with pytest.raises(ValidationError) as exc:
        svc.create_participant(actor=registrar, data=participant_data)
    assert "qualification" in exc.value.message_dict


def test_qualification_list_is_data_not_code(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    """
    Q-31 — the client's answer changes a setting, not a migration.

    Adding a level makes it immediately valid, with no schema change.
    """
    from apps.people.services import reference_data

    EffectiveSetting.objects.filter(key=reference_data.QUALIFICATION_KEY).update(
        value='[["POSTDOC","ما بعد الدكتوراه"]]'
    )
    participant_data["qualification"] = "POSTDOC"
    created = svc.create_participant(actor=registrar, data=participant_data)
    assert created.qualification == "POSTDOC"


# ---------------------------------------------------------------------------
# BR-101 — the projection
# ---------------------------------------------------------------------------
def test_br101_cashier_sees_only_name_and_number(
    registrar: User, cashier: User, active_semester: Semester, participant_data: dict
) -> None:
    svc.create_participant(actor=registrar, data=participant_data)

    row = svc.get_participant(actor=cashier, participant_number="202610001")

    assert set(row) == {"participant_number", "name_ar"}
    for forbidden in ("phone", "id_document_number", "city", "email", "date_of_birth"):
        assert forbidden not in row, f"cashier can see {forbidden} — BR-101 breached"


def test_br101_finance_manager_is_restricted_too(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    svc.create_participant(actor=registrar, data=participant_data)
    manager = User.objects.create_user(
        username="fim.one", password=PASSWORD, role=Role.FINANCE_MANAGER
    )
    row = svc.get_participant(actor=manager, participant_number="202610001")
    assert set(row) == {"participant_number", "name_ar"}


def test_br101_registrar_sees_the_full_record(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    svc.create_participant(actor=registrar, data=participant_data)
    row = svc.get_participant(actor=registrar, participant_number="202610001")
    assert "phone" in row and "id_document_number" in row


def test_br101_restricted_search_cannot_probe_hidden_fields(
    registrar: User, cashier: User, active_semester: Semester, participant_data: dict
) -> None:
    """
    A search box over fields you may not read is a way to read them.

    The cashier can look a participant up by number; searching by phone must
    not confirm or deny that a phone number exists.
    """
    svc.create_participant(actor=registrar, data=participant_data)

    by_phone = svc.list_participants(actor=cashier, query="0791234567")
    assert by_phone == [], "the cashier probed a field BR-101 hides"

    by_number = svc.list_participants(actor=cashier, query="202610001")
    assert len(by_number) == 1


# ---------------------------------------------------------------------------
# Permissions on the service itself
# ---------------------------------------------------------------------------
def test_cashier_cannot_create_a_participant(
    cashier: User, active_semester: Semester, participant_data: dict
) -> None:
    with pytest.raises(PermissionDenied):
        svc.create_participant(actor=cashier, data=participant_data)
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT").exists()


def test_finance_officer_cannot_create_a_participant(
    seeded_settings: None, active_semester: Semester, participant_data: dict
) -> None:
    """PERMISSIONS §3.2 row 4 gives student-new to MGR and REG only."""
    finance = User.objects.create_user(
        username="fin.one", password=PASSWORD, role=Role.FINANCE_OFFICER
    )
    with pytest.raises(PermissionDenied):
        svc.create_participant(actor=finance, data=participant_data)


def test_update_records_before_and_after(
    registrar: User, active_semester: Semester, participant_data: dict
) -> None:
    participant = svc.create_participant(actor=registrar, data=participant_data)

    svc.update_participant(actor=registrar, participant=participant, data={"phone": "0799999999"})

    event = AuditEvent.objects.filter(action="UPDATE").first()
    assert event is not None and event.changes is not None
    assert event.changes["phone"] == {"from": "0791234567", "to": "0799999999"}


def test_audit_account_cannot_update_a_participant(
    registrar: User, seeded_settings: None, active_semester: Semester, participant_data: dict
) -> None:
    """D-02 — read-only means read-only, on every path."""
    participant = svc.create_participant(actor=registrar, data=participant_data)
    auditor = User.objects.create_user(
        username="aud.one", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    with pytest.raises(PermissionDenied):
        svc.update_participant(actor=auditor, participant=participant, data={"phone": "0700000000"})
