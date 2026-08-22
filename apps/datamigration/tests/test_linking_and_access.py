"""
Identity linking, and who is allowed to decide it.

The interesting assertions are about what does NOT happen. No link is made
automatically. The number that carries two different names is flagged and left
alone. The manager who read the workbook in cannot link, and the finance
officer cannot either — the registrar knows the participants, and that is why
the matrix moved ``E`` to them in Sprint 8D-1.

And through all of it, ``participant_number`` keeps its nine-digit CHECK. The
whole archive exists so that constraint never has to bend.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.urls import reverse

from apps.datamigration.models import Finding, HistoricalParticipant
from apps.datamigration.services import linking_service, read_service
from apps.people.constants import Action, Screen
from apps.people.permissions import matrix

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


def _historical(batch, number: str) -> HistoricalParticipant:
    return batch.participants.filter(legacy_number=number).first()


# ---------------------------------------------------------------------------
# Nothing links itself
# ---------------------------------------------------------------------------
def test_archiving_links_nobody(manager, committed_batch) -> None:
    """
    Six archived rows, zero links.

    Auto-linking is not merely absent — there is no code path that would do
    it, and this is the assertion that keeps it that way.
    """
    assert committed_batch.participants.count() == 6
    assert committed_batch.participants.exclude(linked_participant=None).count() == 0


def test_a_number_under_two_names_is_flagged_and_not_resolved(manager, committed_batch) -> None:
    """
    202251004 is «انوار رضوان الاحمد» in one row and «تمارا عامر الشيب» in
    another. Eight numbers in the delivered files behave this way.

    Both rows survive, both are flagged, and neither is merged into the other.
    A matcher would have picked one and been silently wrong about a person.
    """
    rows = committed_batch.participants.filter(legacy_number="202251004")

    assert rows.count() == 2
    assert {row.name_ar for row in rows} == {"انوار رضوان الاحمد", "تمارا عامر الشيب"}
    for row in rows:
        assert Finding.LEGACY_NUMBER_NAME_CONFLICT in row.source_row.findings
        assert row.linked_participant_id is None


def test_the_queue_shows_the_conflict_before_the_decision(
    manager, registrar, committed_batch
) -> None:
    """A reviewer must meet the conflict while choosing, not afterwards."""
    queue = read_service.link_queue(actor=registrar, code=committed_batch.code)
    conflicted = [row for row in queue if row["name_conflict"]]

    assert len(conflicted) == 2
    assert all(row["legacy_number"] == "202251004" for row in conflicted)

    composite = [row for row in queue if row["composite"]]
    assert composite[0]["legacy_alt_number"] == "200920437"


def test_suggestions_come_from_what_the_reviewer_typed(
    registrar, committed_batch, live_participant
) -> None:
    """
    Not from the archive row.

    A ranked "best match" derived from the historical name would be a wrong
    answer wearing a confidence score, and a reviewer would accept it.
    """
    assert read_service.candidate_participants(actor=registrar, query="ان") == []

    found = read_service.candidate_participants(actor=registrar, query="انوار")
    assert (
        live_participant.participant_number,
        f"{live_participant.participant_number} — {live_participant.name_ar}",
    ) in found


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------
def test_the_registrar_links_and_the_act_is_stamped(
    registrar, committed_batch, live_participant
) -> None:
    from apps.core.models import AuditEvent

    historical = _historical(committed_batch, "202251024")
    linking_service.link(
        actor=registrar,
        historical=historical,
        participant=live_participant,
        note_ar="طابق الاسم والرقم القديم مع ملف المشارك",
    )

    historical.refresh_from_db()
    assert historical.linked_participant_id == live_participant.pk
    assert historical.linked_by_id == registrar.pk
    assert historical.linked_at is not None
    assert AuditEvent.objects.filter(
        entity_type="datamigration.HistoricalParticipant",
        reference="202251024",
    ).exists()


def test_a_link_without_a_reason_is_refused(registrar, committed_batch, live_participant) -> None:
    """The note is the only record of WHY, and conflicts make it necessary."""
    historical = _historical(committed_batch, "202251024")

    with pytest.raises(ValidationError, match="مسوّغ"):
        linking_service.link(
            actor=registrar,
            historical=historical,
            participant=live_participant,
            note_ar="   ",
        )

    historical.refresh_from_db()
    assert historical.linked_participant_id is None


def test_a_row_is_not_linked_twice(registrar, committed_batch, live_participant) -> None:
    historical = _historical(committed_batch, "202251024")
    linking_service.link(
        actor=registrar, historical=historical, participant=live_participant, note_ar="ربط"
    )

    with pytest.raises(linking_service.AlreadyLinkedError):
        linking_service.link(
            actor=registrar,
            historical=historical,
            participant=live_participant,
            note_ar="مرة ثانية",
        )


def test_a_mistaken_link_can_be_withdrawn_and_the_withdrawal_is_recorded(
    registrar, committed_batch, live_participant
) -> None:
    """
    A reviewer who cannot correct a mistake will avoid deciding at all.

    Both the link and its withdrawal stay in the audit trail, so the record
    shows a decision that changed rather than a gap where one used to be.
    """
    from apps.core.models import AuditEvent

    historical = _historical(committed_batch, "202251024")
    linking_service.link(
        actor=registrar, historical=historical, participant=live_participant, note_ar="ربط"
    )
    linking_service.unlink(actor=registrar, historical=historical, note_ar="شخص آخر")

    historical.refresh_from_db()
    assert historical.linked_participant_id is None
    assert historical.linked_at is None
    assert (
        AuditEvent.objects.filter(
            entity_type="datamigration.HistoricalParticipant", reference="202251024"
        ).count()
        == 2
    )


def test_an_unidentified_row_has_nobody_to_link(registrar, committed_batch) -> None:
    """
    ``UNIDENTIFIED_NO_USABLE_NUMBER`` rows never became participants at all.

    The guard exists so the caller meets an explanation rather than a bare
    DoesNotExist.
    """
    from apps.datamigration.models import RowState

    row = committed_batch.rows.filter(state=RowState.UNIDENTIFIED).first()

    with pytest.raises(linking_service.UnidentifiedRowError, match="لا يُربط"):
        linking_service.linkable_row(actor=registrar, row_id=row.pk)


def test_linking_copies_no_number_and_no_money(
    registrar, committed_batch, live_participant
) -> None:
    """
    §7 — the link records identity, not data.

    The archive keeps its ten-digit number, the participant keeps their nine,
    and neither learns anything about the other's money.
    """
    historical = _historical(committed_batch, "2022501086")
    before = live_participant.participant_number

    linking_service.link(
        actor=registrar, historical=historical, participant=live_participant, note_ar="ربط"
    )

    live_participant.refresh_from_db()
    historical.refresh_from_db()
    assert live_participant.participant_number == before
    assert len(before) == 9
    assert historical.legacy_number == "2022501086"
    assert len(historical.legacy_number) == 10


# ---------------------------------------------------------------------------
# The production constraint is untouched
# ---------------------------------------------------------------------------
def test_the_nine_digit_participant_number_constraint_still_bites(
    live_participant,
) -> None:
    """
    Asserted, not assumed — this is the constraint the whole archive exists in
    order not to weaken, so the sprint that could have weakened it proves it
    did not.

    Two layers, and both are checked. A ten-digit legacy number is refused by
    the column width (``max_length=9``); a nine-character value that is not
    nine digits is refused by ``people_participant_number_format``. Either
    alone would leave a hole: widening the column would let the regex be the
    only guard, and dropping the regex would let ``20155173-`` through.
    """
    from django.db import DatabaseError, transaction

    from apps.people.models import Participant

    assert Participant._meta.get_field("participant_number").max_length == 9

    # Each refusal gets its own savepoint: the first failed statement poisons
    # the transaction, and without this the second assertion would report a
    # broken transaction rather than a working constraint.
    with pytest.raises(DatabaseError), transaction.atomic():
        Participant.objects.filter(pk=live_participant.pk).update(participant_number="2022501086")

    with pytest.raises(IntegrityError), transaction.atomic():
        Participant.objects.filter(pk=live_participant.pk).update(participant_number="20155173-")

    live_participant.refresh_from_db()
    assert live_participant.participant_number.isdigit()


# ---------------------------------------------------------------------------
# Access — the whole grid
# ---------------------------------------------------------------------------
def test_the_matrix_gives_edit_to_the_registrar_alone(seeded_settings) -> None:
    """
    The cell that changed in this sprint, asserted directly.

    Reading a workbook in and archiving it are the manager's; the identity
    link is the registrar's; finance reads and decides nothing.
    """
    from apps.people.models import Role

    edit_holders = {
        role
        for role in Role.values
        if Action.EDIT in matrix.allowed_actions(role, Screen.MIGRATION)
    }
    assert edit_holders == {Role.REGISTRATION_OFFICER}

    manager_actions = matrix.allowed_actions(Role.CENTER_MANAGER, Screen.MIGRATION)
    assert Action.CREATE in manager_actions
    assert Action.APPROVE in manager_actions
    assert Action.EDIT not in manager_actions

    assert matrix.allowed_actions(Role.FINANCE_OFFICER, Screen.MIGRATION) == frozenset(
        {Action.VIEW}
    )


def test_the_manager_cannot_link(manager, committed_batch, live_participant) -> None:
    from django.core.exceptions import PermissionDenied

    historical = _historical(committed_batch, "202251024")
    with pytest.raises(PermissionDenied):
        linking_service.link(
            actor=manager,
            historical=historical,
            participant=live_participant,
            note_ar="ربط",
        )


def test_the_finance_officer_cannot_link_or_archive(
    finance, committed_batch, live_participant
) -> None:
    """Explicitly: finance holds no identity authority on this screen."""
    from django.core.exceptions import PermissionDenied

    from apps.datamigration.services import archive_service

    with pytest.raises(PermissionDenied):
        linking_service.link(
            actor=finance,
            historical=_historical(committed_batch, "202251024"),
            participant=live_participant,
            note_ar="ربط",
        )
    with pytest.raises(PermissionDenied):
        archive_service.commit(actor=finance, batch=committed_batch)


def test_the_screens_answer_exactly_as_the_matrix_says(client, seeded_settings) -> None:
    """Every role against both screens — nothing crashes, nothing over-grants."""
    from apps.people.models import Role, User

    observed, expected = {}, {}
    for role in Role.values:
        user = User.objects.create_user(
            username=f"grid.dm.{role.lower()}", password=PASSWORD, role=role
        )
        client.force_login(user)
        allowed = 200 if Action.VIEW in matrix.allowed_actions(role, Screen.MIGRATION) else 403
        for name in ("datamigration:batches", "datamigration:links"):
            observed[(role, name)] = client.get(reverse(name)).status_code
            expected[(role, name)] = allowed
        client.logout()

    assert observed == expected


def test_the_cashier_reaches_neither_screen(client, seeded_settings) -> None:
    from apps.people.models import Role, User

    cashier = User.objects.create_user(username="cash.dm", password=PASSWORD, role=Role.CASHIER)
    client.force_login(cashier)
    assert client.get(reverse("datamigration:batches")).status_code == 403
    assert client.get(reverse("datamigration:links")).status_code == 403


def test_the_audit_account_reads_and_writes_nothing(client, seeded_settings) -> None:
    from apps.people.models import Role, User

    auditor = User.objects.create_user(
        username="aud.dm", password=PASSWORD, role=Role.AUDIT_ACCOUNT
    )
    client.force_login(auditor)

    page = client.get(reverse("datamigration:batches"))
    assert page.status_code == 200
    assert page.context["can_import"] is False
    assert page.context["can_commit"] is False
    assert page.context["can_link"] is False

    forced = client.post(
        reverse("datamigration:batches"),
        {"action": "import", "code": "MB-X", "path": "/tmp/x.xlsx", "note_ar": ""},
    )
    assert forced.status_code == 403


def test_the_registrar_sees_the_link_form_and_the_manager_does_not(
    client, registrar, manager, committed_batch
) -> None:
    client.force_login(registrar)
    assert client.get(reverse("datamigration:links")).context["can_link"] is True

    client.force_login(manager)
    page = client.get(reverse("datamigration:links"))
    assert page.context["can_link"] is False
    assert page.context["form"] is None
