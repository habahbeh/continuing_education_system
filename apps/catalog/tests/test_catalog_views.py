"""
Catalogue screens — access matches the matrix, on the URL not the link.

PERMISSIONS.md §3.3 gives the finance officer read-only access to programmes
and prices (Δ-03: he cannot verify a fee he cannot see) and shuts the cashier
out entirely. These assert that at the URL, because a screen reachable by
typing its address is reachable.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import PriceList, PriceListStatus
from apps.people.constants import Action, Screen
from apps.people.models import Role, User
from apps.people.permissions.matrix import allowed_actions

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"

SCREEN_ROUTES = [
    (Screen.PROGRAMS, "catalog:programs"),
    (Screen.SHORT_COURSES, "catalog:short-courses"),
    (Screen.ONLINE_COURSES, "catalog:online-courses"),
    (Screen.PRICELISTS, "catalog:pricelists"),
]

BUSINESS_ROLES = [
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
]


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def catalog(active_semester, seeded_settings):
    from django.core.management import call_command

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)


@pytest.mark.parametrize(("screen", "route"), SCREEN_ROUTES)
@pytest.mark.parametrize("role", BUSINESS_ROLES)
def test_screen_access_matches_the_matrix(
    client: Client, catalog: PriceList, screen: str, route: str, role: str
) -> None:
    client.force_login(_user(role, f"probe.{screen}.{role.lower()}"[:150]))
    response = client.get(reverse(route))

    expected = Action.VIEW in allowed_actions(role, screen)
    assert response.status_code == (200 if expected else 403), (
        f"{role} on {screen}: matrix says {'allow' if expected else 'deny'}"
    )


def test_cashier_is_shut_out_of_every_catalogue_screen(client: Client, catalog: PriceList) -> None:
    """BR-083 — the cashier sees the till, not the catalogue."""
    client.force_login(_user(Role.CASHIER, "cash.catalog"))
    for _screen, route in SCREEN_ROUTES:
        assert client.get(reverse(route)).status_code == 403


def test_finance_officer_reads_prices(client: Client, catalog: PriceList) -> None:
    """Δ-03 — read-only, because verifying a collected fee needs the list."""
    client.force_login(_user(Role.FINANCE_OFFICER, "fin.catalog"))
    response = client.get(reverse("catalog:pricelists"))
    assert response.status_code == 200
    assert not response.context["can_record_approval"]


def test_manager_can_record_an_approval_but_the_screen_offers_no_approve(
    client: Client, catalog: PriceList
) -> None:
    """
    Row 13 withholds A from everyone — the president approves outside (D-31).

    Recording that decision is an edit, so the manager sees the control and
    the matrix still says nobody holds an internal approval right.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.catalog.view"))
    response = client.get(reverse("catalog:pricelists"))
    assert response.status_code == 200
    assert response.context["can_record_approval"]
    assert Action.APPROVE not in allowed_actions(Role.CENTER_MANAGER, Screen.PRICELISTS)


def test_price_list_detail_shows_the_frozen_notice(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.pl.detail"))
    body = client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).content.decode()
    assert "مجمَّدة" in body
    assert not client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).context[
        "can_edit"
    ]


def test_no_fee_and_zero_fee_read_differently_on_screen(client: Client, catalog: PriceList) -> None:
    """The distinction has to survive all the way to the page."""
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.pl.fees"))
    body = client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).content.decode()
    assert "بلا رسوم" in body


def test_programme_detail_shows_the_subject_total(client: Client, catalog: PriceList) -> None:
    """BR-006 is visible before approval, not only when it blocks one."""
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.prog.detail"))
    body = client.get(reverse("catalog:program-detail", args=["DIP-ID"])).content.decode()
    assert "1650" in body, "the subject total should be shown for reconciliation"


def test_missing_programme_returns_404(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.404"))
    assert client.get(reverse("catalog:program-detail", args=["NOPE"])).status_code == 404


def test_anonymous_visitor_reaches_no_catalogue_screen(client: Client) -> None:
    for _screen, route in SCREEN_ROUTES:
        assert client.get(reverse(route)).status_code in (302, 403)


# ---------------------------------------------------------------------------
# The register after its polish pass: which list is in force, and reading a
# long list by search rather than by scrolling. Every addition is a pure read —
# no service behaviour changed and the screen still writes nothing.
# ---------------------------------------------------------------------------
def test_the_register_names_the_list_in_force_today(client: Client, catalog: PriceList) -> None:
    """
    «أي قائمة تسري اليوم؟» is the reader's first question, and it used to be
    left to be guessed from the ordering. The answer comes from the same pure
    read the pricing engine uses, so the screen cannot say one thing while an
    enrolment is priced by another.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.inforce"))

    response = client.get(reverse("catalog:pricelists"))

    assert response.context["in_force_code"] == catalog.code
    assert "سارية اليوم" in response.content.decode("utf-8")

    detail = client.get(reverse("catalog:pricelist-detail", args=[catalog.code]))
    assert "سارية اليوم" in detail.content.decode("utf-8")


def test_a_list_not_yet_effective_is_not_called_in_force(
    client: Client, catalog: PriceList
) -> None:
    """A list approved for next term is not today's list, and the chip must not say it is."""
    from datetime import timedelta

    catalog.effective_from = catalog.effective_from + timedelta(days=3650)
    catalog.save(update_fields=["effective_from"])
    client.force_login(_user(Role.CENTER_MANAGER, "pl.future"))

    response = client.get(reverse("catalog:pricelists"))

    body = response.content.decode("utf-8")
    assert response.context["in_force_code"] == ""
    # The chip is gone; the sentence explaining that nothing is in force stays.
    assert '<span class="chip ok dot">سارية اليوم</span>' not in body
    assert "ولا قائمة معتمدة سارية اليوم" in body


def test_the_register_narrows_by_search_and_by_status(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pl.search"))
    url = reverse("catalog:pricelists")

    assert len(client.get(url, {"q": catalog.code}).context["price_lists"]) == 1
    assert len(client.get(url, {"q": "لا-يوجد-شيء"}).context["price_lists"]) == 0
    assert len(client.get(url, {"status": PriceListStatus.APPROVED}).context["price_lists"]) == 1
    assert len(client.get(url, {"status": PriceListStatus.DRAFT}).context["price_lists"]) == 0

    narrowed = client.get(url, {"q": "لا-يوجد-شيء"}).content.decode("utf-8")
    assert "لا قائمة تطابق هذه التصفية" in narrowed
    assert "لا قوائم أسعار معرَّفة بعد" not in narrowed, "a filtered result is not an empty register"


def test_the_items_are_read_by_search_not_by_scrolling(client: Client, catalog: PriceList) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pl.items"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    everything = len(client.get(url).context["items"])
    levelled = client.get(url, {"q": "SC-ENG"}).context["items"]

    assert everything > len(levelled) > 0
    assert {row["program_code"] for row in levelled} == {"SC-ENG-GEN"}
    assert "لا بند يطابق البحث" in client.get(url, {"q": "لا-يوجد"}).content.decode("utf-8")


def test_the_list_names_the_active_programmes_it_leaves_unpriced(
    client: Client, catalog: PriceList
) -> None:
    """
    BR-008: a programme with no item on the list in force cannot take an
    enrolment. That gap belongs to the list that caused it, not to the till
    that discovers it.
    """
    from apps.catalog.models import Program, ProgramType

    Program.objects.create(
        code="DIP-UNPRICED",
        program_type=ProgramType.DIPLOMA,
        name_ar="دبلوم بلا سعر",
        training_hours=60,
    )
    client.force_login(_user(Role.CENTER_MANAGER, "pl.unpriced"))

    response = client.get(reverse("catalog:pricelist-detail", args=[catalog.code]))

    assert [p["code"] for p in response.context["unpriced"]] == ["DIP-UNPRICED"]
    page = response.content.decode("utf-8")
    assert "بلا بند على هذه القائمة" in page and "DIP-UNPRICED" in page


def test_every_priced_programme_is_reachable_from_its_row(
    client: Client, catalog: PriceList
) -> None:
    """The programme page links here; the link back was missing."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.links"))

    page = client.get(reverse("catalog:pricelist-detail", args=[catalog.code])).content.decode(
        "utf-8"
    )

    assert reverse("catalog:program-detail", args=["SC-NET"]) in page
    assert reverse("catalog:program-detail", args=["DIP-ID"]) in page


def test_the_price_screens_render_on_a_database_with_settings_only(
    client: Client, seeded_settings: None
) -> None:
    """§8.3 — the screen works before anybody has issued a list."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.bare"))

    response = client.get(reverse("catalog:pricelists"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert response.context["in_force_code"] == ""
    assert "لا قوائم أسعار معرَّفة بعد" in body
    assert "لا قائمة معتمدة سارية اليوم" in body


def test_the_register_leads_with_the_list_in_force_and_filters_by_state(
    client: Client, catalog: PriceList
) -> None:
    """
    The first tile answers «أيّها سارية؟» with a CODE, not a count — a number
    there would be one more thing to translate into the answer. The rest filter,
    and they count the whole register so a tile cannot shrink with the filter it
    opens.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.tiles"))

    response = client.get(reverse("catalog:pricelists"))
    tiles = {t["label"]: t for t in response.context["tiles"]}

    assert tiles["سارية اليوم"]["value"] == catalog.code
    assert tiles["معتمدة"]["value"] == 1
    assert tiles["مسودة"]["url"].endswith("?status=DRAFT")

    narrowed = client.get(reverse("catalog:pricelists"), {"status": PriceListStatus.DRAFT})
    assert len(narrowed.context["price_lists"]) == 0
    assert {t["label"]: t["value"] for t in narrowed.context["tiles"]}["معتمدة"] == 1


def test_the_register_names_the_filter_that_narrowed_it_by_label(
    client: Client, catalog: PriceList
) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pl.banner"))
    url = reverse("catalog:pricelists")

    assert client.get(url).context["active_filters"] == []

    narrowed = client.get(url, {"status": PriceListStatus.APPROVED, "q": "PL"})
    named = dict(narrowed.context["active_filters"])
    body = narrowed.content.decode("utf-8")

    assert named["بحث"] == "PL"
    assert named["الحالة"] == "معتمدة", "the stored code must never be printed"
    # The code may ride in a tile's href; what must never be PRINTED is the
    # enum in place of its label.
    assert "نتائج مصفّاة" in body
    assert "الحالة: APPROVED" not in body


def test_levels_priced_alike_are_one_row_and_a_level_priced_apart_is_its_own(
    client: Client, catalog: PriceList
) -> None:
    """
    English 1–8 at one price printed eight identical rows. Folded, the count of
    items is still stated — and a level priced differently must split back out,
    because that difference is the thing the reader came for.
    """
    from apps.catalog.models import PriceListItem

    client.force_login(_user(Role.CENTER_MANAGER, "pl.levels"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    response = client.get(url)
    rows = {r["program_code"]: r for r in response.context["items"]}

    assert rows["SC-ENG-GEN"]["levels"] == 8
    assert (rows["SC-ENG-GEN"]["level_from"], rows["SC-ENG-GEN"]["level_to"]) == (1, 8)
    assert response.context["item_count"] == PriceListItem.objects.filter(
        price_list=catalog
    ).count()
    assert response.context["program_count"] == len(response.context["items"])
    assert "على" in response.content.decode("utf-8")

    dearer = PriceListItem.objects.filter(price_list=catalog, program__code="SC-ENG-GEN").first()
    assert dearer is not None
    PriceList.objects.filter(pk=catalog.pk).update(status=PriceListStatus.DRAFT)
    PriceListItem.objects.filter(pk=dearer.pk).update(course_fee=dearer.course_fee + 5)

    split = client.get(url).context["items"]
    eng = [r for r in split if r["program_code"] == "SC-ENG-GEN"]
    assert len(eng) == 2 and {r["levels"] for r in eng} == {1, 7}


def test_a_programme_search_keeps_the_general_fee_rules(
    client: Client, catalog: PriceList
) -> None:
    """The specific rule beats the general one (BR-009), so hiding the general
    half of the answer would make the screen say a fee is not charged at all."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.rules"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    all_rules = client.get(url).context["fee_rules"]
    general = [r for r in all_rules if r["program"] is None]
    narrowed = client.get(url, {"rq": "SC-NET"}).context["fee_rules"]

    assert len(general) > 0
    assert len(narrowed) < len(all_rules)
    assert [r for r in narrowed if r["program"] is None] == general
    assert {r["program"].code for r in narrowed if r["program"]} == {"SC-NET"}


def test_the_unpriced_warning_says_whether_it_bites_today(
    client: Client, catalog: PriceList
) -> None:
    """
    An archived or draft list leaving a programme unpriced has no effect today,
    and the warning must not claim otherwise — the sentence used to be written
    as though every list were the one in force.
    """
    from apps.catalog.models import Program, ProgramType

    Program.objects.create(
        code="DIP-NOPRICE",
        program_type=ProgramType.DIPLOMA,
        name_ar="دبلوم بلا سعر",
        training_hours=30,
    )
    client.force_login(_user(Role.CENTER_MANAGER, "pl.bite"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    in_force = client.get(url).content.decode("utf-8")
    assert "وهذه هي القائمة السارية اليوم" in in_force

    PriceList.objects.filter(pk=catalog.pk).update(status=PriceListStatus.ARCHIVED)
    archived = client.get(url).content.decode("utf-8")

    assert "وهذه ليست القائمة السارية اليوم" in archived
    assert "وهذه هي القائمة السارية اليوم" not in archived


def test_the_two_searches_on_the_card_do_not_erase_each_other(
    client: Client, catalog: PriceList
) -> None:
    """Each form carries the other's term, or narrowing one silently widens the other."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.both"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    response = client.get(url, {"q": "SC-ENG", "rq": "SC-NET"})
    body = response.content.decode("utf-8")
    item_form = body.split('id="pl-item-form"', 1)[1].split("</form>", 1)[0]
    rule_form = body.split('id="pl-rule-form"', 1)[1].split("</form>", 1)[0]

    assert {row["program_code"] for row in response.context["items"]} == {"SC-ENG-GEN"}
    assert 'name="rq" value="SC-NET"' in item_form
    assert 'name="q" value="SC-ENG"' in rule_form


def test_a_rule_search_with_no_rule_of_its_own_says_so(
    client: Client, catalog: PriceList
) -> None:
    """
    The general rules always apply, so they stay on screen — and the reader who
    typed a programme has to be told that is what they are looking at.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.general"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    specific = client.get(url, {"rq": "SC-NET"})
    assert specific.context["rules_are_general_only"] is False

    none_of_its_own = client.get(url, {"rq": "لا-يوجد"})
    assert none_of_its_own.context["rules_are_general_only"] is True
    assert none_of_its_own.context["fee_rules"], "the general rules still apply and still show"
    assert "لا قاعدة خاصة بـ" in none_of_its_own.content.decode("utf-8")


def test_the_notes_column_is_drawn_only_where_a_note_exists(
    client: Client, catalog: PriceList
) -> None:
    """A column empty in every row costs width and says nothing."""
    from apps.catalog.models import PriceListItem

    client.force_login(_user(Role.CENTER_MANAGER, "pl.notes"))
    url = reverse("catalog:pricelist-detail", args=[catalog.code])

    bare = client.get(url)
    assert bare.context["has_notes"] is False
    assert "ملاحظات" not in bare.content.decode("utf-8").split("</nav>", 1)[-1]

    PriceList.objects.filter(pk=catalog.pk).update(status=PriceListStatus.DRAFT)
    item = PriceListItem.objects.filter(price_list=catalog).first()
    assert item is not None
    PriceListItem.objects.filter(pk=item.pk).update(notes="سعر متفق عليه مع الشريك")

    with_note = client.get(url)
    assert with_note.context["has_notes"] is True
    assert "سعر متفق عليه مع الشريك" in with_note.content.decode("utf-8")


def test_the_deposit_and_the_policy_that_governs_it_read_as_one_fact(
    client: Client, catalog: PriceList
) -> None:
    """They are never separable — the constraint refuses one without the other."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.deposit"))

    body = client.get(
        reverse("catalog:pricelist-detail", args=[catalog.code])
    ).content.decode("utf-8")

    assert "التأمين وسياسته" in body
    assert "سياسة التأمين" not in body.split("</nav>", 1)[-1], "two columns for one fact"
    deposit_cell = body.split("SC-ENG-GEN", 1)[1]
    assert "تأمين دورة اللغة الإنجليزية العامة" in deposit_cell
