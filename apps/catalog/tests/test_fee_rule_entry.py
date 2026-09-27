"""
Registration fee rules, enterable from a screen at last (BR-009).

Found by walking a fresh install as a trainee would: categories, fields,
programmes, a priced list, a ministry-approved cohort, a participant — and
then the first enrolment died with a 500 reading «لا توجد قاعدة رسم تسجيل
للفئة UNIVERSITY على القائمة PL-2026-1».

Three separate defects sat behind that one traceback:

1. ``RegistrationFeeRule`` was read by the pricing engine on EVERY enrolment
   and could be written from nowhere but the Django admin. The detail screen
   printed the rules and offered no way to make one.
2. The list had already been approved, and D-14 freezes an approved list — so
   the rule could never be added and the term could never price an enrolment.
   Nothing warned before the approval that the list was structurally unusable.
3. The pricing engine's refusals escaped ``_create_enrollment`` uncaught, so a
   registrar met a 500 and lost the typed form instead of a message naming
   what was missing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import (
    CourseCategory,
    PriceList,
    PriceListStatus,
    Program,
    ProgramType,
    RegistrationFeeRule,
)
from apps.core.models import Semester
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "fee-rule-probe-1234"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def semester(db: Any) -> Semester:
    return Semester.objects.create(
        code="2026-1",
        name_ar="الفصل الأول",
        type_code=1,
        academic_year="2026/2027",
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 1, 15),
        is_active=True,
    )


@pytest.fixture
def program(db: Any) -> Program:
    return Program.objects.create(
        code="SC-NET",
        program_type=ProgramType.SHORT_COURSE,
        name_ar="هندسة الشبكات",
        training_hours=60,
        course_category=CourseCategory.objects.create(code="CAT-IT", name_ar="تكنولوجيا المعلومات"),
        consumables_per_student=0,
    )


@pytest.fixture
def online(db: Any) -> Program:
    """BR-010 — never carries a registration fee, so never offered a rule."""
    return Program.objects.create(
        code="OL-XL",
        program_type=ProgramType.ONLINE_COURSE,
        name_ar="إكسل المتقدّم",
        training_hours=12,
        consumables_per_student=0,
    )


@pytest.fixture
def draft(semester: Semester) -> PriceList:
    return PriceList.objects.create(
        code="PL-2026-1",
        name_ar="قائمة أسعار الفصل الأول",
        semester=semester,
        issued_on=date(2026, 8, 1),
        effective_from=date(2026, 9, 1),
        proposed_by_text="مدير المركز",
        status=PriceListStatus.DRAFT,
    )


def _detail(price_list: PriceList) -> str:
    return reverse("catalog:pricelist-detail", args=[price_list.code])


def _rule_payload(**over: Any) -> dict[str, Any]:
    payload = {
        "action": "fee-rule",
        "program": "",
        "participant_category": "UNIVERSITY",
        "fee": "20.000",
        "exception_note_ar": "",
    }
    payload.update(over)
    return payload


# -- the act that did not exist ------------------------------------------------
def test_the_manager_adds_a_general_rule_from_the_screen(
    client: Client, draft: PriceList
) -> None:
    """The whole gap: a rule the admin alone could write, written from a screen."""
    manager = _user(Role.CENTER_MANAGER, "fr.mgr")
    client.force_login(manager)

    response = client.post(_detail(draft), _rule_payload(), follow=True)

    assert response.status_code == 200
    rule = RegistrationFeeRule.objects.get()
    assert rule.price_list_id == draft.pk
    assert rule.program_id is None, "an empty scope is the GENERAL rule, not a missing one"
    assert rule.participant_category == "UNIVERSITY"
    assert str(rule.fee) == "20.000"


def test_a_programme_rule_beats_the_general_one_the_screen_wrote(
    client: Client, draft: PriceList, program: Program
) -> None:
    """
    BR-009 — most-specific-wins, and both rows must be enterable for that to
    mean anything. The resolver is what decides; this checks the screen can
    give it the two rows to decide between.
    """
    from apps.catalog.services import pricing_service

    client.force_login(_user(Role.CENTER_MANAGER, "fr.specific"))
    client.post(_detail(draft), _rule_payload(fee="20.000"), follow=True)
    client.post(
        _detail(draft),
        _rule_payload(program=str(program.pk), fee="5.000"),
        follow=True,
    )

    fee, _note = pricing_service.resolve_registration_fee(
        program=program, participant_category="UNIVERSITY", price_list=draft
    )
    assert str(fee) == "5.000", "the programme rule did not beat the general one"


def test_the_write_is_audited_which_the_admin_road_never_was(
    client: Client, draft: PriceList
) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "fr.audit")
    client.force_login(manager)

    client.post(_detail(draft), _rule_payload(), follow=True)

    assert AuditEvent.objects.filter(
        entity_type="catalog.PriceList", reference=draft.code, actor=manager
    ).exists()


# -- the absence that is not a zero -------------------------------------------
def test_a_waived_fee_is_stored_as_nothing_not_as_zero(
    client: Client, draft: PriceList
) -> None:
    """
    BR-009 · T-098 — JCPA and PMP charge NO registration fee, which is a
    different fact from charging zero. ``{% if fee %}`` cannot tell them apart,
    so the column is stored NULL and printed as its own chip.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "fr.null"))

    client.post(
        _detail(draft),
        _rule_payload(fee="", exception_note_ar="دورة JCPA بلا رسم تسجيل"),
        follow=True,
    )

    rule = RegistrationFeeRule.objects.get()
    assert rule.fee is None, "a waived fee became a zero — a different claim entirely"
    assert rule.exception_note_ar


def test_a_waived_fee_without_a_reason_is_refused(client: Client, draft: PriceList) -> None:
    """«بلا رسوم» with nothing beside it is the row nobody can explain later."""
    client.force_login(_user(Role.CENTER_MANAGER, "fr.why"))

    response = client.post(_detail(draft), _rule_payload(fee="", exception_note_ar=""))

    assert response.status_code == 200
    assert not RegistrationFeeRule.objects.exists()
    assert "سبباً مكتوباً" in response.content.decode("utf-8")


def test_the_screen_prints_a_waived_fee_as_words_not_as_a_number(
    client: Client, draft: PriceList
) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "fr.print"))
    client.post(
        _detail(draft), _rule_payload(fee="", exception_note_ar="بلا رسم موثّق"), follow=True
    )

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "بلا رسوم تسجيل" in body


# -- correcting a draft --------------------------------------------------------
def test_a_mistyped_rule_can_be_removed_from_a_draft(client: Client, draft: PriceList) -> None:
    """A draft is a document being written; a document being written is correctable."""
    client.force_login(_user(Role.CENTER_MANAGER, "fr.del"))
    client.post(_detail(draft), _rule_payload(), follow=True)
    rule = RegistrationFeeRule.objects.get()

    client.post(_detail(draft), {"action": "remove-rule", "rule": str(rule.pk)}, follow=True)

    assert not RegistrationFeeRule.objects.exists()


def test_a_second_rule_for_the_same_scope_and_category_is_refused(
    client: Client, draft: PriceList
) -> None:
    """
    C-27 — two rules for one (list, scope, category) would leave the resolver
    choosing arbitrarily between them, which is how a fee changes by itself.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "fr.dup"))
    client.post(_detail(draft), _rule_payload(fee="20.000"), follow=True)

    response = client.post(_detail(draft), _rule_payload(fee="99.000"), follow=True)

    assert RegistrationFeeRule.objects.count() == 1
    assert "سلفاً" in response.content.decode("utf-8")


# -- D-14, on both sides -------------------------------------------------------
def test_an_approved_list_is_offered_no_rule_form_and_refuses_the_post(
    client: Client, draft: PriceList
) -> None:
    """
    §3.4 and D-14 together: the act is not drawn on a frozen list, and the
    service refuses it even when the POST arrives anyway.
    """
    draft.status = PriceListStatus.APPROVED
    draft.save(update_fields=["status"])
    client.force_login(_user(Role.CENTER_MANAGER, "fr.frozen"))

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "إضافة قاعدة رسم" not in body

    response = client.post(_detail(draft), _rule_payload(), follow=True)
    assert not RegistrationFeeRule.objects.exists()
    assert "غير قابلة للتعديل" in response.content.decode("utf-8")


def test_a_reader_without_edit_is_offered_nothing_and_refused_the_post(
    client: Client, draft: PriceList
) -> None:
    client.force_login(_user(Role.AUDIT_ACCOUNT, "fr.aud"))

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "إضافة قاعدة رسم" not in body

    assert client.post(_detail(draft), _rule_payload()).status_code == 403
    assert not RegistrationFeeRule.objects.exists()


# -- the warning that would have prevented the dead end -----------------------
def test_the_screen_names_the_categories_the_list_cannot_price_yet(
    client: Client, draft: PriceList
) -> None:
    """
    The finding itself: a draft with no rule prices nothing, and the approval
    freezes it. So the gap is named on the screen BEFORE the decision — this is
    the sentence whose absence cost the walk a whole price list.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "fr.warn"))

    body = client.get(_detail(draft)).content.decode("utf-8")

    assert "فئات لا تستطيع هذه القائمة تسعيرها بعد" in body
    assert "لا قاعدة رسم تسجيل واحدة على هذه القائمة" in body, "the approval dialog is silent"


def test_the_warning_goes_away_once_every_category_is_covered(
    client: Client, draft: PriceList
) -> None:
    from apps.people.constants import PARTICIPANT_CATEGORY_CHOICES

    client.force_login(_user(Role.CENTER_MANAGER, "fr.covered"))
    for category, _label in PARTICIPANT_CATEGORY_CHOICES:
        client.post(
            _detail(draft), _rule_payload(participant_category=category, fee="20.000"), follow=True
        )

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "فئات لا تستطيع هذه القائمة تسعيرها بعد" not in body


def test_an_approved_list_shows_no_gap_warning_it_cannot_act_on(
    client: Client, draft: PriceList
) -> None:
    """A warning about something D-14 forbids fixing is noise, not guidance."""
    draft.status = PriceListStatus.APPROVED
    draft.save(update_fields=["status"])
    client.force_login(_user(Role.CENTER_MANAGER, "fr.frozenwarn"))

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "فئات لا تستطيع هذه القائمة تسعيرها بعد" not in body


# -- BR-010 --------------------------------------------------------------------
def test_online_courses_are_not_offered_a_rule_scope_at_all(
    client: Client, draft: PriceList, program: Program, online: Program
) -> None:
    """
    BR-010 is structural, not an exception row: an online course carries no
    registration fee whatever any rule says, so a rule naming one would be a
    row the resolver never reads.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "fr.online"))

    body = client.get(_detail(draft)).content.decode("utf-8")
    # The rule dialog only — the PRICE item form beside it does offer online
    # courses, which carry a course fee like any other programme.
    dialog = body[body.index('id="fee-rule-new"') : body.index('id="price-list-approve"')]

    assert f'value="{program.pk}"' in dialog
    assert f'value="{online.pk}"' not in dialog


def test_the_general_scope_is_named_rather_than_left_blank(
    client: Client, draft: PriceList
) -> None:
    """«كل البرامج» and «لم أختر بعد» look identical blank and mean opposites."""
    client.force_login(_user(Role.CENTER_MANAGER, "fr.scope"))

    body = client.get(_detail(draft)).content.decode("utf-8")
    assert "القاعدة العامة لكل البرامج" in body


def test_the_new_dialog_does_not_greet_the_reader_already_open(
    client: Client, draft: PriceList
) -> None:
    """
    The bug class this sprint has now met three times: a form bound with an
    empty ``request.POST`` reports every required field missing, ``errors`` is
    true before a character is typed, and ``x-init`` opens the dialog on sight.

    ``tests/test_ui_regressions.py`` guards the five register screens; this
    screen takes a code in its URL so it cannot join that parametrisation, and
    is guarded here instead.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "fr.shut"))

    response = client.get(_detail(draft))
    page = response.content.decode("utf-8")

    assert 'id="fee-rule-new"' in page, "the dialog is not drawn at all — check the fixture"
    assert "x-init" not in page, "a dialog opens itself on a plain read"
    assert not response.context["rule_form"].errors


def test_a_frozen_list_says_why_there_is_no_button_and_where_to_go(
    client: Client, draft: PriceList
) -> None:
    """
    The confusion this caused in use, and it is a §8 failure not a bug.

    The rules card explained what a missing rule COSTS and said nothing about
    why this particular list would not accept one — so a reader looking for
    «إضافة قاعدة رسم» on an approved list hunted for a button that D-14 forbids
    drawing, and concluded the screen was broken. An empty state explains AND
    points: the absence has a reason (the list is evidence) and an exit (issue a
    new list), and both belong on the screen rather than in someone's head.
    """
    draft.status = PriceListStatus.APPROVED
    draft.save(update_fields=["status"])
    client.force_login(_user(Role.CENTER_MANAGER, "fr.whynobutton"))

    body = client.get(_detail(draft)).content.decode("utf-8")

    assert "إضافة قاعدة رسم" not in body, "D-14 — the act is not drawn on a frozen list"
    assert "ولا تُضاف قاعدة إلى هذه القائمة" in body, "the absence is unexplained"
    assert "D-14" in body
    assert reverse("catalog:pricelists") in body, "no route to the only way forward"


def test_a_draft_still_offers_the_button_and_not_the_explanation(
    client: Client, draft: PriceList
) -> None:
    """The other half: on a draft the act is there, so the excuse must not be."""
    client.force_login(_user(Role.CENTER_MANAGER, "fr.draftbutton"))

    body = client.get(_detail(draft)).content.decode("utf-8")

    assert "إضافة قاعدة رسم" in body
    assert "ولا تُضاف قاعدة إلى هذه القائمة" not in body
