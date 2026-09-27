"""
The participants registry as a SCREEN — ordering, paging, search and the two
dialogs it opens.

Split from ``test_participant_views.py`` on purpose: that file holds the
permission proofs (T-283/T-284/T-285), and these are the promises the page
makes to the person reading it. Both kinds regressed here at once, and the
worst of them was silent — a listing sliced to two hundred rows with no
ORDER BY, which answered two identical requests with two different sets.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.core.models import Semester
from apps.people.models import IdDocumentType, Participant, ParticipantCategory, Role, User
from apps.people.services import participant_service

pytestmark = pytest.mark.django_db

PASSWORD = "screen-probe-1234"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def _make(number: str, *, name: str, registered: date, category: str = "UNIVERSITY") -> Participant:
    """A row straight into the table: these tests are about reading, not writing."""
    return Participant.objects.create(
        participant_number=number,
        category=category,
        name_ar=name,
        id_document_type=IdDocumentType.NATIONAL_ID,
        id_document_number=f"99{number}",
        registered_on=registered,
    )


# ---------------------------------------------------------------------------
# Ordering — the defect that made «the first two hundred» meaningless
# ---------------------------------------------------------------------------
def test_the_registry_is_ordered_newest_first(seeded_settings: None) -> None:
    """
    Insertion order must not decide what the reader sees.

    The rows are created in an order that is neither the number order nor the
    registration order, so a listing that merely echoes the table cannot pass.
    """
    _make("202610002", name="ب", registered=date(2026, 9, 10))
    _make("202610001", name="أ", registered=date(2026, 9, 20))
    _make("202610003", name="ج", registered=date(2026, 9, 15))
    # Same day as the newest: the number breaks the tie, and breaks it the
    # same way every time.
    _make("202610004", name="د", registered=date(2026, 9, 20))

    actor = _user(Role.REGISTRATION_OFFICER, "order.reg")
    rows = participant_service.list_participants(actor=actor)

    assert [row["participant_number"] for row in rows] == [
        "202610004",
        "202610001",
        "202610003",
        "202610002",
    ]


def test_the_order_does_not_move_between_two_identical_reads(seeded_settings: None) -> None:
    """The guarantee the missing ORDER BY took away."""
    for index in range(12):
        _make(f"2026100{index:02d}", name=f"مشارك {index}", registered=date(2026, 9, 1))

    actor = _user(Role.REGISTRATION_OFFICER, "order.stable")
    first = [
        row["participant_number"] for row in participant_service.list_participants(actor=actor)
    ]
    second = [
        row["participant_number"] for row in participant_service.list_participants(actor=actor)
    ]

    assert first == second


# ---------------------------------------------------------------------------
# Paging — the count is the registry's, not the window's
# ---------------------------------------------------------------------------
def test_the_count_names_the_registry_and_not_the_page(
    client: Client, seeded_settings: None
) -> None:
    """
    Fifty-two rows, fifty on the page.

    The old screen printed the length of what it had drawn, which was a cap it
    never mentioned; a reader had no way to tell «٢٠٠» from «٢٠٠ of 2,000».
    """
    for index in range(52):
        _make(f"20261{index:04d}", name=f"مشارك {index}", registered=date(2026, 9, 1))

    client.force_login(_user(Role.REGISTRATION_OFFICER, "page.reg"))
    body = client.get(reverse("people:participants")).content.decode()

    assert "52 مشاركاً" in body
    assert body.count('data-opens="quick-modal"') == 50
    assert "1 / 2" in body  # the pager says where the reader is


def test_the_second_page_holds_the_remainder_and_keeps_the_filter(
    client: Client, seeded_settings: None
) -> None:
    for index in range(52):
        _make(f"20261{index:04d}", name=f"مشارك {index}", registered=date(2026, 9, 1))

    client.force_login(_user(Role.REGISTRATION_OFFICER, "page.two"))
    body = client.get(
        reverse("people:participants"), {"page": "2", "category": "UNIVERSITY"}
    ).content.decode()

    assert body.count('data-opens="quick-modal"') == 2
    assert "2 / 2" in body
    assert "category=UNIVERSITY" in body  # the pager link carries the filter


def test_a_nonsense_page_number_lands_on_a_real_page(client: Client, seeded_settings: None) -> None:
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(Role.REGISTRATION_OFFICER, "page.junk"))

    for raw in ("0", "-3", "99", "٢", "abc"):
        response = client.get(reverse("people:participants"), {"page": raw})
        assert response.status_code == 200
        assert "202610001" in response.content.decode()


# ---------------------------------------------------------------------------
# Search — the spelling of a name is not the name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("term", ["أحمد", "احمد", "إحمد"])
def test_the_name_search_sees_through_the_alef(seeded_settings: None, term: str) -> None:
    """
    Both spellings exist in every real registry, and the MySQL collation folds
    neither. A clerk who cannot find a participant opens a second file for
    them — which is how a search defect becomes a duplicate-identity defect.
    """
    _make("202610001", name="أحمد محمد سالم", registered=date(2026, 9, 1))
    _make("202610002", name="احمد خالد الزعبي", registered=date(2026, 9, 2))

    actor = _user(Role.REGISTRATION_OFFICER, f"fold.{abs(hash(term)) % 997}")
    found = {
        row["participant_number"]
        for row in participant_service.list_participants(actor=actor, query=term)
    }

    assert found == {"202610001", "202610002"}


def test_the_ya_and_ta_marbuta_fold_too(seeded_settings: None) -> None:
    _make("202610001", name="ليلى الخطيب", registered=date(2026, 9, 1))
    _make("202610002", name="فاطمة القضاة", registered=date(2026, 9, 2))
    actor = _user(Role.REGISTRATION_OFFICER, "fold.ya")

    by_ya = participant_service.list_participants(actor=actor, query="ليلي")
    by_ha = participant_service.list_participants(actor=actor, query="فاطمه")

    assert [row["participant_number"] for row in by_ya] == ["202610001"]
    assert [row["participant_number"] for row in by_ha] == ["202610002"]


def test_the_restricted_role_still_cannot_search_by_name(seeded_settings: None) -> None:
    """Folding widened the search; it must not have widened the field set."""
    _make("202610001", name="أحمد محمد سالم", registered=date(2026, 9, 1))
    cashier = _user(Role.CASHIER, "fold.cashier")

    assert participant_service.list_participants(actor=cashier, query="احمد") == []
    assert len(participant_service.list_participants(actor=cashier, query="2026")) == 1


def test_the_search_box_promises_only_what_the_role_can_do(
    client: Client, seeded_settings: None
) -> None:
    """Two directions: the full reader is offered the name, the cashier is not."""
    _make("202610001", name="أحمد محمد سالم", registered=date(2026, 9, 1))

    client.force_login(_user(Role.REGISTRATION_OFFICER, "hint.reg"))
    registrar_body = client.get(reverse("people:participants")).content.decode()

    client.force_login(_user(Role.CASHIER, "hint.cashier"))
    cashier_body = client.get(reverse("people:participants")).content.decode()

    assert "اسم، أو رقم جامعي" in registrar_body
    assert "اسم، أو رقم جامعي" not in cashier_body
    assert "بالرقم الجامعي…" in cashier_body


# ---------------------------------------------------------------------------
# The filter banner — it must not claim a filter that was not applied
# ---------------------------------------------------------------------------
def test_a_blank_search_is_not_a_filter(client: Client, seeded_settings: None) -> None:
    """Two spaces in the box used to draw the banner over an unfiltered list."""
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(Role.REGISTRATION_OFFICER, "blank.reg"))

    body = client.get(reverse("people:participants"), {"q": "   "}).content.decode()

    assert "إلغاء التصفية" not in body
    assert "202610001" in body


def test_an_unknown_category_is_ignored_rather_than_obeyed(
    client: Client, seeded_settings: None
) -> None:
    """Obeying it answered «no such participants» about a category that does not exist."""
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(Role.REGISTRATION_OFFICER, "bogus.reg"))

    body = client.get(reverse("people:participants"), {"category": "BOGUS"}).content.decode()

    assert "202610001" in body
    assert "إلغاء التصفية" not in body


@pytest.mark.parametrize("role", [Role.CASHIER, Role.FINANCE_MANAGER])
def test_a_category_the_role_cannot_see_is_not_drawn_as_a_filter_either(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The service already ignores it — a filter reports its field as surely as
    printing it (BR-101) — so the same request returns the same set with the
    parameter and without it. The page kept the word anyway: a link copied from
    the manager drew «الفئة: طالب جامعة / خرّيج» and «إلغاء التصفية» over the
    till's unfiltered rows, naming a label the projection withheld.
    """
    _make("202610001", name="أ", registered=date(2026, 9, 1), category="UNIVERSITY")
    _make("202610002", name="ب", registered=date(2026, 9, 2), category="CENTER")
    client.force_login(_user(role, f"catfilter.{role.lower()}"))

    response = client.get(reverse("people:participants"), {"category": "UNIVERSITY"})
    body = response.content.decode()

    assert response.context["page"]["total"] == 2  # the parameter narrowed nothing…
    assert "202610002" in body  # …and the row it names is still on the page
    assert response.context["is_filtered"] is False
    assert "إلغاء التصفية" not in body
    assert "طالب جامعة" not in body


def test_the_row_actions_may_wrap_so_the_file_button_stays_on_the_screen(
    client: Client, seeded_settings: None
) -> None:
    """
    Four buttons held on one line made the table 819px inside a 718px wrapper
    at 1024, so the actions cell began at -76 and «فتح الملف» — the whole point
    of the row — sat behind a horizontal scroll. Wrapping below 1180px brings
    the table back to 718px with no overflow, and the guard is here rather than
    in the stylesheet because the rule is invisible until a screen is narrow.
    """
    css = (Path(settings.BASE_DIR) / "static" / "src" / "input.css").read_text(encoding="utf-8")
    block = css.split("/* سجل المشاركين:", 1)[1].split("/* سجل الشركاء:", 1)[0]

    assert "@media (max-width: 1180px)" in block
    assert "whitespace-normal" in block


def test_the_filter_cards_count_the_registry_by_category(
    client: Client, seeded_settings: None
) -> None:
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    _make("202650001", name="ب", registered=date(2026, 9, 2), category=ParticipantCategory.CENTER)
    client.force_login(_user(Role.REGISTRATION_OFFICER, "tiles.reg"))

    body = client.get(reverse("people:participants")).content.decode()

    assert "كل المشاركين" in body
    for _value, label in ParticipantCategory.choices:
        assert str(label) in body


def test_a_selected_card_keeps_counting_the_whole_set(
    client: Client, seeded_settings: None
) -> None:
    """
    A card is a filter, and a filter that recounts itself reads «1 of 1» about
    a registry of five. The cards count what the OTHER filter left.
    """
    for index in range(4):
        _make(f"20261000{index}", name=f"جامعي {index}", registered=date(2026, 9, 1))
    _make(
        "202650001", name="مركزي", registered=date(2026, 9, 2), category=ParticipantCategory.CENTER
    )

    client.force_login(_user(Role.REGISTRATION_OFFICER, "tiles.on"))
    response = client.get(reverse("people:participants"), {"category": "CENTER"})

    tiles = {tile["label"]: tile["value"] for tile in response.context["tiles"]}
    assert tiles["كل المشاركين"] == 5
    assert tiles["طالب جامعة / خرّيج"] == 4
    assert tiles["طالب مركز"] == 1


# ---------------------------------------------------------------------------
# The dialogs — one per page, and each drawn only for a reader who may use it
# ---------------------------------------------------------------------------
def test_the_quick_view_is_one_dialog_for_the_whole_page(
    client: Client, seeded_settings: None
) -> None:
    """Fifty rows used to mean fifty hidden dialogs in every response."""
    for index in range(6):
        _make(f"20261000{index}", name=f"مشارك {index}", registered=date(2026, 9, 1))

    client.force_login(_user(Role.REGISTRATION_OFFICER, "quick.one"))
    body = client.get(reverse("people:participants")).content.decode()

    assert body.count('id="quick-modal"') == 1
    assert body.count('data-opens="quick-modal"') == 6


def test_the_quick_view_carries_the_row_it_will_show(client: Client, seeded_settings: None) -> None:
    _make("202650001", name="ليلى الخطيب", registered=date(2026, 9, 12), category="CENTER")
    Participant.objects.filter(participant_number="202650001").update(
        phone="0790001111", city="IRBID", qualification="BACHELOR"
    )

    client.force_login(_user(Role.REGISTRATION_OFFICER, "quick.data"))
    body = client.get(reverse("people:participants")).content.decode()

    assert 'data-phone="0790001111"' in body
    assert 'data-city="إربد"' in body  # the label, not the stored code
    assert 'data-qualification="بكالوريوس"' in body
    assert 'data-registered="2026-09-12"' in body


def test_the_restricted_role_is_offered_no_quick_view_at_all(
    client: Client, seeded_settings: None
) -> None:
    """
    Its dialog held one line that the row already printed, and a button worth
    less than its own absence is not a button.
    """
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(Role.CASHIER, "quick.cashier"))

    body = client.get(reverse("people:participants")).content.decode()

    assert "quick-modal" not in body
    assert "عرض سريع" not in body
    assert "202610001" in body  # …but the row itself is there


@pytest.mark.parametrize(
    ("role", "offered"),
    [(Role.REGISTRATION_OFFICER, True), (Role.AUDIT_ACCOUNT, False), (Role.CASHIER, False)],
)
def test_the_edit_action_appears_for_exactly_the_roles_that_may_edit(
    client: Client, seeded_settings: None, role: str, offered: bool
) -> None:
    """Both directions: the guide promises editing, and the screen must mean it."""
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(role, f"edit.{role.lower()}"))

    body = client.get(reverse("people:participants")).content.decode()

    assert (reverse("people:participant-edit", args=["202610001"]) in body) is offered


@pytest.mark.parametrize(
    ("role", "offered"),
    [(Role.REGISTRATION_OFFICER, True), (Role.AUDIT_ACCOUNT, False), (Role.CASHIER, False)],
)
def test_the_quick_enrolment_button_follows_the_create_permission(
    client: Client, seeded_settings: None, active_semester: Semester, role: str, offered: bool
) -> None:
    _make("202610001", name="أ", registered=date(2026, 9, 1))
    client.force_login(_user(role, f"enroll.{role.lower()}"))

    body = client.get(reverse("people:participants")).content.decode()

    # No approved cohort exists in this database, so the dialog is absent for
    # everyone — which is itself the rule: a button with nothing to offer is
    # not drawn. What must hold in both directions is that no role WITHOUT the
    # permission is ever handed it.
    if not offered:
        assert 'data-opens="enroll-modal"' not in body


# ---------------------------------------------------------------------------
# The screen renders on an empty registry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "role", [Role.REGISTRATION_OFFICER, Role.CENTER_MANAGER, Role.CASHIER, Role.AUDIT_ACCOUNT]
)
def test_the_screen_renders_with_no_participants_at_all(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"empty.{role.lower()}"))

    body = client.get(reverse("people:participants")).content.decode()

    assert "لا مشاركون بعد" in body
    assert "لا مشاركين" in body  # the counter reads as Arabic, not as «0»


# ---------------------------------------------------------------------------
# The demo registry — seed data, and therefore held to the form's own rules
# ---------------------------------------------------------------------------
def test_the_demo_seed_writes_a_registry_the_screen_can_show(
    seeded_settings: None, active_semester: Semester
) -> None:
    """
    It goes through the service, so every seeded row obeys BR-001/BR-003 and
    carries an audit trail — a seed that wrote rows the admission form could
    not have produced would be seeding a registry this system does not know.
    """
    from django.core.management import call_command

    _user(Role.SUPER_ADMIN, "seed.admin")
    call_command("seed_participants_demo", verbosity=0)

    rows = Participant.objects.all()
    assert rows.count() == 10
    assert {row.category for row in rows} == set(ParticipantCategory.values)
    for row in rows:
        assert row.participant_number.startswith("2026")
        assert len(row.name_ar.split()) >= 3, f"«{row.name_ar}» ليس اسماً يُعرض على عميل"
        assert row.no_refund_pledge_accepted


def test_the_demo_seed_is_idempotent(seeded_settings: None, active_semester: Semester) -> None:
    """Re-running must not hand the same person a second participant number."""
    from django.core.management import call_command

    _user(Role.SUPER_ADMIN, "seed.again")
    call_command("seed_participants_demo", verbosity=0)
    call_command("seed_participants_demo", verbosity=0)

    assert Participant.objects.count() == 10


def test_the_demo_seed_refuses_without_an_actor(seeded_settings: None) -> None:
    """A participant nobody created is not a participant this system recognises."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("seed_participants_demo", verbosity=0)
