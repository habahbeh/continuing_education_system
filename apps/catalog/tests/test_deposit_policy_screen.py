"""
Deposit policies, and the deposit that could not be entered at all.

Found by walking a fresh install as a trainee would. Three defects, one root:

1. ``DepositPolicy`` had no screen. The pricing engine read it, every price
   item may point at one, and the only road to a row was the Django admin —
   which writes straight to the table, leaving no audit line behind the wording
   that a forfeited deposit is later defended with.
2. The price-item form carried the AMOUNT and **no policy field at all**, so
   ``C-26`` (amount and policy are set together or neither is) refused every
   deposit anyone typed. No programme in a fresh install could be priced with a
   deposit, by any road but the admin.
3. Half a deposit — the amount filled, the policy forgotten — met the database
   constraint rather than a field error, which is a 500 where a red label
   belongs.

BR-096 sits underneath all three: a programme with no policy carries no deposit
LINE, which is not the same claim as a deposit of zero.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import (
    CourseCategory,
    DepositPolicy,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
)
from apps.core.models import Semester
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "deposit-probe-1234"
INDEX = "catalog:deposit-policies"


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def _payload(**over: Any) -> dict[str, Any]:
    payload = {
        "action": "create",
        "code": "dep-eng",
        "name_ar": "تأمين دورة اللغة",
        "is_required": "on",
        "refund_trigger": "on_centre_cancellation",
        "forfeit_on": ["DISMISSED"],
        "forfeit_other": "",
        "allows_partial_deduction": "on",
        "claim_deadline_days": "",
        "notes_ar": "",
        "is_active": "on",
    }
    payload.update(over)
    return payload


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


# -- the screen that did not exist --------------------------------------------
def test_the_manager_writes_a_policy_from_the_screen(client: Client) -> None:
    manager = _user(Role.CENTER_MANAGER, "dp.mgr")
    client.force_login(manager)

    response = client.post(reverse(INDEX), _payload(), follow=True)

    assert response.status_code == 200
    row = DepositPolicy.objects.get()
    assert row.code == "DEP-ENG", "the code is upper-cased — it is what price items point at"
    assert row.refund_trigger == "ON_CENTRE_CANCELLATION"
    assert row.forfeit_on == ["DISMISSED"]
    assert row.is_required


def test_the_write_is_audited_which_the_admin_road_never_was(client: Client) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "dp.audit")
    client.force_login(manager)

    client.post(reverse(INDEX), _payload(), follow=True)

    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="catalog.DepositPolicy", actor=manager
    ).exists()


def test_the_twelve_statuses_are_offered_as_checkboxes(client: Client) -> None:
    """The typo-free path: a status the workflow reaches is picked, never typed."""
    from apps.operations.models import EnrollmentStatus

    client.force_login(_user(Role.CENTER_MANAGER, "dp.states"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert f'value="{EnrollmentStatus.DISMISSED}"' in body
    assert f'value="{EnrollmentStatus.WITHDRAWN}"' in body


def test_a_forfeit_value_outside_the_twelve_can_be_entered(client: Client) -> None:
    """
    The ⚠⚠ of the 2026-09-26 review, and it was a defect of my own making.

    The field was built as a CLOSED list of enrolment statuses, on the reasoning
    that a status typed by hand never matches anything. The proof that the
    reasoning was wrong about THIS field was already in the database: the
    client's documented policy (``DEP-ENG-GEN``, 2026-08-15) forfeits on
    ``CONFIRMED`` — the moment the participant confirmed, not a stored state, and
    not one of the twelve. So the screen could not express the single policy the
    client had actually specified, and Q-30 is open precisely to prevent that.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.open"))

    client.post(reverse(INDEX), _payload(forfeit_on=[], forfeit_other="confirmed"), follow=True)

    assert DepositPolicy.objects.get().forfeit_on == ["CONFIRMED"]


def test_the_checkboxes_and_the_free_field_become_one_list(client: Client) -> None:
    """Two controls, one stored answer — merged by the form, not by a caller."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.merge"))

    client.post(
        reverse(INDEX),
        _payload(forfeit_on=["DISMISSED"], forfeit_other="CONFIRMED، ON_NO_SHOW"),
        follow=True,
    )

    assert DepositPolicy.objects.get().forfeit_on == ["DISMISSED", "CONFIRMED", "ON_NO_SHOW"]


def test_a_free_value_that_is_not_a_code_is_refused(client: Client) -> None:
    """
    Q-30 keeps the VOCABULARY open, not the shape: a forfeit value is compared,
    so a sentence there is a clause that can never fire.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.shape"))

    response = client.post(
        reverse(INDEX), _payload(forfeit_other="عند إلغاء المركز للدورة"), follow=True
    )

    assert not DepositPolicy.objects.exists()
    assert "ليست رمز حالة" in response.content.decode("utf-8")


def test_editing_a_policy_does_not_silently_drop_its_unknown_values(
    client: Client,
) -> None:
    """
    The trap this fix had to avoid: a policy holding ``CONFIRMED`` opened for
    editing and saved unchanged would lose it, because the checkboxes have
    nowhere to draw it. The row splits its stored list so the free field carries
    exactly what the boxes cannot.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.keep"))
    client.post(
        reverse(INDEX),
        _payload(forfeit_on=["DISMISSED"], forfeit_other="CONFIRMED"),
        follow=True,
    )

    body = client.get(reverse(INDEX)).content.decode("utf-8")
    assert 'value="CONFIRMED"' in body, "the editor cannot show what it must preserve"

    # And saving it back, exactly as the dialog presents it, keeps both.
    client.post(
        reverse(INDEX),
        {
            "action": "edit",
            "code": "DEP-ENG",
            "name_ar": "تأمين دورة اللغة",
            "is_required": "on",
            "refund_trigger": "ON_CENTRE_CANCELLATION",
            "forfeit_on": ["DISMISSED"],
            "forfeit_other": "CONFIRMED",
            "allows_partial_deduction": "on",
            "claim_deadline_days": "",
            "notes_ar": "",
            "is_active": "on",
        },
        follow=True,
    )

    assert DepositPolicy.objects.get().forfeit_on == ["DISMISSED", "CONFIRMED"]


def test_the_free_field_is_offered_what_the_centre_already_says(client: Client) -> None:
    """
    Q-30 forbids a constraint, so what stops ``CONFIRMED`` and ``ON_CONFIRM``
    living side by side is showing the writer what is already stored.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.seen"))
    client.post(reverse(INDEX), _payload(forfeit_other="CONFIRMED"), follow=True)

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert 'id="forfeit-seen"' in body
    assert 'list="forfeit-seen"' in body, "the suggestions are not attached to the field"


def test_the_refund_trigger_stays_free_text_as_q30_asked(client: Client) -> None:
    """
    The client's instruction of 2026-08-15: these values stay configurable. A
    trigger the code has never heard of must be storable, or a fifth trigger
    becomes a migration.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.q30"))

    client.post(
        reverse(INDEX), _payload(refund_trigger="on_a_trigger_not_yet_invented"), follow=True
    )

    assert DepositPolicy.objects.get().refund_trigger == "ON_A_TRIGGER_NOT_YET_INVENTED"


def test_an_empty_forfeit_list_is_printed_as_a_claim_not_left_blank(client: Client) -> None:
    """«يُسترد دائماً» is a statement; an empty cell says nothing."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.always"))
    client.post(reverse(INDEX), _payload(forfeit_on=[]), follow=True)

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert DepositPolicy.objects.get().forfeit_on == []
    assert "يُسترد دائماً" in body


def test_a_policy_is_stood_down_and_never_deleted(client: Client) -> None:
    """Price items point at it; deleting it leaves a deposit with no terms."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.off"))
    client.post(reverse(INDEX), _payload(), follow=True)

    client.post(reverse(INDEX), {"action": "toggle", "code": "DEP-ENG"}, follow=True)

    assert DepositPolicy.objects.count() == 1
    assert not DepositPolicy.objects.get().is_active


def test_the_empty_state_says_what_its_absence_costs(client: Client) -> None:
    """§8 — an empty state explains AND points."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.empty"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert "لا سياسة تأمين معرَّفة بعد" in body
    assert "C-26" in body, "the screen does not say why the absence blocks a deposit"


# -- C-26: the deposit that could not be entered ------------------------------
def test_a_deposit_can_now_be_priced_at_all(
    client: Client, draft: PriceList, program: Program
) -> None:
    """
    The defect that mattered most. The price-item form had an amount field and
    no policy field, so C-26 refused every deposit — and no programme on a fresh
    install could carry one by any road but the Django admin.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.pair"))
    client.post(reverse(INDEX), _payload(), follow=True)
    policy_row = DepositPolicy.objects.get()

    client.post(
        reverse("catalog:pricelist-detail", args=[draft.code]),
        {
            "action": "item",
            "program": str(program.pk),
            "course_fee": "300.000",
            "deposit_amount": "50.000",
            "deposit_policy": str(policy_row.pk),
            "notes": "",
        },
        follow=True,
    )

    item = PriceListItem.objects.get()
    assert str(item.deposit_amount) == "50.000"
    assert item.deposit_policy_id == policy_row.pk, "C-26's other half was not stored"


def test_half_a_deposit_is_a_field_error_not_a_database_refusal(
    client: Client, draft: PriceList, program: Program
) -> None:
    """
    Forgetting the policy beside the amount is an ordinary typing mistake. Met at
    the database it is a 500 or a bare page-level message; met here it is a red
    label on the field the reader must fix.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.half"))

    response = client.post(
        reverse("catalog:pricelist-detail", args=[draft.code]),
        {
            "action": "item",
            "program": str(program.pk),
            "course_fee": "300.000",
            "deposit_amount": "50.000",
            "deposit_policy": "",
            "notes": "",
        },
    )

    assert response.status_code == 200, "C-26 reached the database instead of the form"
    assert not PriceListItem.objects.exists()
    assert "يحتاج سياسةً تحكمه" in response.content.decode("utf-8")


def test_a_policy_with_no_amount_is_refused_too(
    client: Client, draft: PriceList, program: Program
) -> None:
    """The other half of C-26: a policy governing nothing does nothing."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.other"))
    client.post(reverse(INDEX), _payload(), follow=True)

    response = client.post(
        reverse("catalog:pricelist-detail", args=[draft.code]),
        {
            "action": "item",
            "program": str(program.pk),
            "course_fee": "300.000",
            "deposit_amount": "",
            "deposit_policy": str(DepositPolicy.objects.get().pk),
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert not PriceListItem.objects.exists()
    assert "بلا مبلغ لا تفعل شيئاً" in response.content.decode("utf-8")


def test_no_deposit_at_all_stays_available_and_stays_not_a_zero(
    client: Client, draft: PriceList, program: Program
) -> None:
    """
    BR-096 — the common case is a programme with NO deposit, and adding the
    policy field must not have made the deposit compulsory. NULL, never 0.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.none"))

    client.post(
        reverse("catalog:pricelist-detail", args=[draft.code]),
        {
            "action": "item",
            "program": str(program.pk),
            "course_fee": "300.000",
            "deposit_amount": "",
            "deposit_policy": "",
            "notes": "",
        },
        follow=True,
    )

    item = PriceListItem.objects.get()
    assert item.deposit_amount is None, "«no deposit» became a deposit of zero"
    assert item.deposit_policy_id is None


def test_a_stood_down_policy_is_not_offered_to_a_new_item(
    client: Client, draft: PriceList
) -> None:
    """
    History for the items that already carry it, not a choice for a new one —
    which is what §3.4 asks: a control whose use is refused is not drawn.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.hidden"))
    client.post(reverse(INDEX), _payload(), follow=True)
    client.post(reverse(INDEX), {"action": "toggle", "code": "DEP-ENG"}, follow=True)

    body = client.get(reverse("catalog:pricelist-detail", args=[draft.code])).content.decode(
        "utf-8"
    )

    assert "تأمين دورة اللغة" not in body


# -- who may, and who may not -------------------------------------------------
def test_the_registrar_reads_and_writes_nothing(client: Client) -> None:
    """§3.3/13 — REG holds ``V P`` on price lists; a deposit is a term of price."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "dp.reg"))

    response = client.get(reverse(INDEX))
    assert response.status_code == 200
    assert "إضافة سياسة" not in response.content.decode("utf-8")

    assert client.post(reverse(INDEX), _payload()).status_code == 403
    assert not DepositPolicy.objects.exists()


def test_the_audit_account_writes_nothing_ever(client: Client) -> None:
    """D-02."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "dp.aud"))

    assert client.get(reverse(INDEX)).status_code == 200
    assert client.post(reverse(INDEX), _payload()).status_code == 403
    assert not DepositPolicy.objects.exists()


def test_the_cashier_has_no_business_here_at_all(client: Client) -> None:
    """Row 13 gives CSH nothing on price lists — not even a read."""
    client.force_login(_user(Role.CASHIER, "dp.cash"))

    assert client.get(reverse(INDEX)).status_code == 403


def test_the_screen_does_not_refuse_a_reader_before_they_type(client: Client) -> None:
    """The bound-but-empty form bug, guarded here as on every screen this sprint."""
    client.force_login(_user(Role.CENTER_MANAGER, "dp.shut"))

    response = client.get(reverse(INDEX))

    assert not response.context["form"].errors
    assert "err-summary" not in response.content.decode("utf-8")


def test_the_screen_is_reachable_from_the_navigation(client: Client) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "dp.nav"))

    body = client.get(reverse("catalog:pricelists")).content.decode("utf-8")

    assert reverse(INDEX) in body, "the sidebar does not offer the deposit policies screen"


# -- what the screen review of 2026-09-26 found --------------------------------
def test_the_three_write_only_fields_are_shown_after_they_are_saved(
    client: Client,
) -> None:
    """
    ``is_taxable``, ``allows_partial_deduction`` and ``notes_ar`` were entered
    and then displayed NOWHERE — so nobody could check what they had typed, and
    with no edit path they were write-once-forever. A tax decision (Q-27) that
    cannot be read back is a tax decision nobody can audit.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.shown"))
    client.post(
        reverse(INDEX),
        _payload(is_taxable="on", allows_partial_deduction="", notes_ar="غير مسترد بعد التأكيد"),
        follow=True,
    )

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert "خاضع للضريبة" in body
    assert "يسمح بحسم جزئي" in body
    assert "غير مسترد بعد التأكيد" in body
    # In a details row that opens in place, the way the enrolments register does
    # it — not in a column that would push the table past its width.
    assert 'data-expands="dep-DEP-ENG"' in body


def test_a_policy_can_be_corrected_in_place(client: Client) -> None:
    """
    A mistyped deadline or a trigger spelt two ways had no correction path from
    any screen. D-14 freezes an approved price LIST; it says nothing about the
    terms a deposit is held on, and a row nobody may fix gets worked around by
    adding a second one.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.edit"))
    client.post(reverse(INDEX), _payload(claim_deadline_days="30"), follow=True)

    client.post(
        reverse(INDEX),
        {
            "action": "edit",
            "code": "DEP-ENG",
            "name_ar": "تأمين دورة اللغة — معدّل",
            "is_required": "on",
            "refund_trigger": "on_course_cancelled",
            "forfeit_on": ["WITHDRAWN"],
            "allows_partial_deduction": "on",
            "claim_deadline_days": "45",
            "notes_ar": "صحّحت المهلة",
            "is_active": "on",
        },
        follow=True,
    )

    row = DepositPolicy.objects.get()
    assert row.code == "DEP-ENG", "the code must not be editable — price items point at it"
    assert row.name_ar == "تأمين دورة اللغة — معدّل"
    assert row.refund_trigger == "ON_COURSE_CANCELLED"
    assert row.claim_deadline_days == 45
    assert row.forfeit_on == ["WITHDRAWN"]


def test_the_edit_is_audited_field_by_field(client: Client) -> None:
    """The wording of a deposit's terms is what a forfeiture is defended with."""
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "dp.editaudit")
    client.force_login(manager)
    client.post(reverse(INDEX), _payload(), follow=True)

    client.post(
        reverse(INDEX),
        {
            "action": "edit",
            "code": "DEP-ENG",
            "name_ar": "تأمين معدّل",
            "refund_trigger": "ON_CENTRE_CANCELLATION",
            "allows_partial_deduction": "on",
            "claim_deadline_days": "",
            "notes_ar": "",
            "is_active": "on",
        },
        follow=True,
    )

    event = AuditEvent.objects.filter(
        action="UPDATE", entity_type="catalog.DepositPolicy", actor=manager
    ).latest("id")
    assert "name_ar" in (event.changes or {}), f"the change was not recorded: {event.changes}"


def test_a_reader_without_edit_is_offered_no_edit_control(client: Client) -> None:
    """§3.4 — the act is not drawn, and the service refuses it anyway."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "dp.noedit"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert "تعديل السياسة" not in body
    assert client.post(reverse(INDEX), {"action": "edit", "code": "X"}).status_code == 403


def test_standing_a_policy_down_asks_first(client: Client) -> None:
    """
    It takes the policy out of the pricing choices immediately, so one click is
    not enough — and the dialog says how many price items stand on it.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.confirm"))
    client.post(reverse(INDEX), _payload(), follow=True)

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert 'id="dep-off-DEP-ENG"' in body, "the toggle fires with no confirmation"
    assert "تخرج فوراً من خيارات بنود الأسعار الجديدة" in body


def test_the_filter_can_be_cancelled_from_inside_the_swapped_region(
    client: Client,
) -> None:
    """
    The «إلغاء التصفية» link sat OUTSIDE ``#dep-rows`` while the live search
    swaps only that region — so after typing it never appeared, and the reader
    could not get back to the whole list without editing the URL.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.cancel"))
    client.post(reverse(INDEX), _payload(), follow=True)

    body = client.get(reverse(INDEX), {"q": "ZZZ"}).content.decode("utf-8")
    region = body[body.index('id="dep-rows"') :]

    assert "إلغاء التصفية" in region, "the way out of a filter is outside the swapped region"


def test_the_cards_count_the_whole_set_not_the_filtered_one(client: Client) -> None:
    """
    A card counted off the narrowed rows would answer «how many are active» with
    «the ones you can see», and pressing it would change its own number.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.tiles"))
    client.post(reverse(INDEX), _payload(), follow=True)
    client.post(reverse(INDEX), _payload(code="dep-two", name_ar="تأمين ثانٍ"), follow=True)
    client.post(reverse(INDEX), {"action": "toggle", "code": "DEP-TWO"}, follow=True)

    response = client.get(reverse(INDEX), {"q": "DEP-ENG"})
    tiles = {str(tile["label"]): tile["count"] for tile in response.context["tiles"]}

    assert tiles["نشطة"] == 1
    assert tiles["مُعطَّلة"] == 1, f"the cards followed the filter: {tiles}"


def test_the_forfeit_statuses_are_a_named_group_not_a_label_pointing_nowhere(
    client: Client,
) -> None:
    """
    ``CheckboxSelectMultiple`` has no ``id_for_label``, so the shared partial
    drew ``<label for="">`` — a name attached to nothing, and no group name for a
    screen reader. A set of checkboxes is named by ``<legend>``.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.group"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert 'for=""' not in body, "a label points at nothing"
    assert "<legend>" in body
    assert 'class="check-list"' in body, "the checkboxes keep inheriting the full field width"


def test_the_price_item_dialog_points_at_this_screen(
    client: Client, draft: PriceList
) -> None:
    """
    §8 — an empty state explains AND points. The deposit field names a policy
    from a list defined on another screen, and said so without saying where.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dp.route"))

    body = client.get(
        reverse("catalog:pricelist-detail", args=[draft.code])
    ).content.decode("utf-8")

    assert reverse(INDEX) in body
