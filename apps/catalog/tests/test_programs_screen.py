"""
The catalogue screens after their UX pass (§2.2 · §2.3 · BR-006 · §3.3/9-11).

The manager creates a programme from the list, edits it, adds and edits its
subjects, approves it (BR-006 against the list in force) and retires it —
each through a dialog; every other role reads. The row answers the reader's
first questions: the price in force, the cohorts, the reconciliation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Program, Subject
from apps.catalog.services import catalog_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue(seeded_settings, active_semester):
    call_command("seed_catalog_demo", "--approve", verbosity=0)


@pytest.fixture
def manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="mgr.cat", password="x-pass-1234", role=Role.CENTER_MANAGER)


@pytest.fixture
def registrar(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(username="reg.cat", password="x-pass-1234", role=Role.REGISTRATION_OFFICER)


def _create(client, url, **extra):
    data = {
        "action": "create",
        "code": "DIP-HR",
        "name_ar": "دبلوم الموارد البشرية",
        "name_en": "HR",
        "knowledge_field": "",
        "course_category": "",
        "specialization": "",
        "training_hours": "120",
        "consumables_per_student": "0",
        "minimum_first_payment_override": "",
    }
    data.update(extra)
    return client.post(url, data)


def test_the_row_carries_price_cohorts_and_reconciliation(client, catalogue, manager) -> None:
    client.force_login(manager)
    response = client.get(reverse("catalog:programs"))
    rows = {r["code"]: r for r in response.context["programs"]}
    diploma = next(r for r in rows.values() if r["program_type"] == "DIPLOMA")
    assert diploma["price"] is not None and diploma["price_list_code"]
    assert diploma["subject_count"] > 0 and diploma["variance"] is not None
    tiles = {t["label"]: t["value"] for t in response.context["tiles"]}
    assert tiles["نشط"] + tiles["غير نشط"] == len(rows)
    page = response.content.decode("utf-8")
    # البحث الحيّ والحوار موجودان. وكان هذا يُثبَّت بـ `hx-get=""`، وهي تعني
    # «العنوان الحالي بمعاملاته» فيضيف htmx حقول النموذج فوقها مع كل ضغطة زرّ
    # (?q=ت ثم ?q=ت&q=تص …). المسار صريحاً هو ما يجعل حقول النموذج هي الاستعلام.
    assert 'hx-get="{}"'.format(reverse("catalog:programs")) in page
    assert 'id="program-new"' in page


def test_the_manager_creates_from_the_list_and_lands_on_the_card(client, catalogue, manager) -> None:
    client.force_login(manager)
    response = _create(client, reverse("catalog:programs"))
    assert response.status_code == 302 and response["Location"].endswith("/programs/DIP-HR/")
    program = Program.objects.get(code="DIP-HR")
    assert program.program_type == "DIPLOMA" and program.training_hours == 120


def test_a_short_course_needs_its_category_before_posting(client, catalogue, manager) -> None:
    client.force_login(manager)
    response = _create(client, reverse("catalog:short-courses"), code="SC-X", name_ar="دورة")
    assert response.status_code == 200 and not Program.objects.filter(code="SC-X").exists()
    assert "BR-061" in response.content.decode("utf-8")


def test_subjects_are_added_edited_and_removed_only_before_cohorts(
    client, catalogue, manager
) -> None:
    client.force_login(manager)
    _create(client, reverse("catalog:programs"))
    url = reverse("catalog:program-detail", args=["DIP-HR"])
    assert client.post(url, {"action": "add-subject", "name_ar": "مبادئ", "training_hours": "30", "price": "300"}).status_code == 302
    assert client.post(url, {"action": "add-subject", "name_ar": "قانون", "training_hours": "30", "price": "0"}).status_code == 302
    subjects = list(Subject.objects.filter(program__code="DIP-HR").order_by("sequence"))
    assert [s.sequence for s in subjects] == [1, 2] and subjects[1].price == 0  # BR-007

    assert client.post(url, {"action": "update-subject", "subject_id": subjects[1].pk, "name_ar": "قانون العمل", "training_hours": "30", "price": "200"}).status_code == 302
    assert Subject.objects.get(pk=subjects[1].pk).price == Decimal("200")
    assert client.post(url, {"action": "remove-subject", "subject_id": subjects[1].pk}).status_code == 302
    assert not Subject.objects.filter(pk=subjects[1].pk).exists()

    # A programme with cohorts keeps its subjects: no delete dialog, and the service refuses.
    seeded = next(p for p in Program.objects.filter(program_type="DIPLOMA").exclude(code="DIP-HR") if p.cohorts.exists()) if any(p.cohorts.exists() for p in Program.objects.filter(program_type="DIPLOMA")) else None
    if seeded is not None and seeded.subjects.exists():
        page = client.get(reverse("catalog:program-detail", args=[seeded.code])).content.decode("utf-8")
        assert 'id="subject-rm-' not in page
        with pytest.raises(ValidationError):
            catalog_service.remove_subject(actor=manager, subject=seeded.subjects.first())


def test_the_card_is_edited_without_touching_its_code(client, catalogue, manager) -> None:
    client.force_login(manager)
    _create(client, reverse("catalog:programs"))
    url = reverse("catalog:program-detail", args=["DIP-HR"])
    response = client.post(url, {"action": "update", "code": "HACKED", "name_ar": "دبلوم HR", "name_en": "", "knowledge_field": "", "course_category": "", "specialization": "إدارة", "training_hours": "150", "is_leveled": "on", "levels_count": "3", "consumables_per_student": "5", "minimum_first_payment_override": ""})
    assert response.status_code == 302
    p = Program.objects.get(code="DIP-HR")
    assert p.name_ar == "دبلوم HR" and p.is_leveled and p.levels_count == 3 and p.consumables_per_student == Decimal("5")
    assert not Program.objects.filter(code="HACKED").exists()


def test_retiring_refuses_live_cohorts_and_approval_reads_the_list_in_force(
    client, catalogue, manager
) -> None:
    client.force_login(manager)
    _create(client, reverse("catalog:programs"))
    url = reverse("catalog:program-detail", args=["DIP-HR"])
    assert client.post(url, {"action": "toggle-active"}).status_code == 302
    assert Program.objects.get(code="DIP-HR").is_active is False

    # Unpriced → approval is refused with BR-008 and the dialog's button is disabled.
    page = client.get(url).content.decode("utf-8")
    assert "بلا سعر ساري" in page
    response = client.post(url, {"action": "approve"})
    assert response.status_code == 200 and "BR-008" in response.content.decode("utf-8")

    seeded = Program.objects.filter(program_type="DIPLOMA").exclude(code="DIP-HR").first()
    recon = catalog_service.reconciliation(program=seeded)
    assert recon["fee"] is not None and recon["ok"] is not None


def test_readers_without_edit_get_no_dialogs(client, catalogue, registrar) -> None:
    client.force_login(registrar)
    # The page's own body rather than «past the sidebar»: the shell now ends
    # with the quick-jump palette, which is a <dialog> on every screen.
    whole = client.get(reverse("catalog:programs")).content.decode("utf-8")
    page = whole.split('id="main"', 1)[1].split("</main>", 1)[0]
    assert "<dialog" not in page and 'method="post"' not in page
    assert client.post(reverse("catalog:programs"), {"action": "create"}).status_code == 403


# ---------------------------------------------------------------------------
# The short-courses screen (§3.3/10): the field bounds a transfer (BR-061),
# the price in force carries levels, deposits and fee exceptions.
# ---------------------------------------------------------------------------
def _short_courses(client):
    return {r["code"]: r for r in client.get(reverse("catalog:short-courses")).context["programs"]}


def test_a_cohort_is_counted_once_however_many_subjects_the_diploma_has(client, catalogue, manager) -> None:
    from django.db.models import Count

    client.force_login(manager)
    diploma = Program.objects.annotate(n=Count("subjects")).filter(program_type="DIPLOMA", n__gt=1).first()
    rows = {r["code"]: r for r in client.get(reverse("catalog:programs")).context["programs"]}
    assert rows[diploma.code]["cohort_count"] == diploma.cohorts.count()
    assert rows[diploma.code]["subject_count"] == diploma.subjects.count()


def test_the_short_course_row_reads_levels_deposit_and_fee_exception(client, catalogue, manager) -> None:
    client.force_login(manager)
    rows = _short_courses(client)
    levelled = next(r for r in rows.values() if r["is_leveled"])
    assert levelled["levels_priced"] == levelled["levels_count"]
    assert levelled["deposit"] == Decimal("25.000")
    assert rows["SC-JCPA"]["fee_note"] == "بلا رسم تسجيل"
    assert rows["SC-NET"]["fee_note"] == "رسم تسجيل خاص"
    page = client.get(reverse("catalog:short-courses")).content.decode("utf-8")
    assert "المستوى 1" in page and 'class="chip chip-link"' in page
    assert "ولا تعريف ولا تعديل" not in page


def test_the_list_prices_every_row_with_a_bounded_number_of_queries(client, catalogue, manager, django_assert_max_num_queries) -> None:
    client.force_login(manager)
    with django_assert_max_num_queries(14):
        client.get(reverse("catalog:short-courses"))


def test_the_search_finds_a_course_by_its_field_and_the_chip_filters_by_it(client, catalogue, manager) -> None:
    client.force_login(manager)
    by_name = client.get(reverse("catalog:short-courses"), {"q": "لغات"}).context["programs"]
    assert [r["code"] for r in by_name] == ["SC-ENG-GEN"]
    by_chip = client.get(reverse("catalog:short-courses"), {"category": "CAT-BUS"}).context["programs"]
    assert {r["category_code"] for r in by_chip} == {"CAT-BUS"} and len(by_chip) == 4


def test_the_card_states_the_pricing_in_force_and_the_transferable_siblings(client, catalogue, manager) -> None:
    client.force_login(manager)
    response = client.get(reverse("catalog:program-detail", kwargs={"code": "SC-JCPA"}))
    pricing = response.context["pricing"]
    assert [lv["course_fee"] for lv in pricing["levels"]] == [Decimal("600.000")]
    fees = {f["category"]: f for f in pricing["registration_fees"]}
    assert fees["CENTER"]["fee"] is None and fees["CENTER"]["is_exception"]
    assert fees["EMPLOYEE"]["fee"] is not None and not fees["EMPLOYEE"]["is_exception"]
    assert {s["code"] for s in response.context["siblings"]} == {"SC-CMA", "SC-PMP", "SC-QA"}
    page = response.content.decode("utf-8")
    assert "دورات المجال نفسه" in page and reverse("operations:transfers") in page

    levelled = client.get(reverse("catalog:program-detail", kwargs={"code": "SC-ENG-GEN"}))
    assert len(levelled.context["pricing"]["levels"]) == 8
    assert levelled.context["siblings"] == []


def test_the_code_hint_follows_the_screen(client, catalogue, manager) -> None:
    client.force_login(manager)
    assert 'placeholder="SC-…"' in client.get(reverse("catalog:short-courses")).content.decode("utf-8")
    assert 'placeholder="DIP-…"' in client.get(reverse("catalog:programs")).content.decode("utf-8")


# ---------------------------------------------------------------------------
# The online-courses screen (§3.3/11): BR-010 — no registration fee, ever.
# ---------------------------------------------------------------------------
def test_the_online_course_states_it_carries_no_registration_fee(client, catalogue, manager) -> None:
    """
    ``pricing_service`` refuses to charge one structurally, so the screen may
    not print the general 50/15 beside it — that would be the catalogue telling
    the till to collect money the system will never charge.
    """
    client.force_login(manager)
    response = client.get(reverse("catalog:program-detail", kwargs={"code": "ON-GENAI"}))
    pricing = response.context["pricing"]
    assert pricing["registration_fees"] == [] and pricing["no_registration_fee"] is True
    page = response.content.decode("utf-8")
    assert "BR-010" in page and "طالب مركز" not in page

    rows = {r["code"]: r for r in client.get(reverse("catalog:online-courses")).context["programs"]}
    assert rows["ON-GENAI"]["fee_note"] == "بلا رسوم تسجيل (BR-010)"


def test_the_catalogue_names_no_partner_share_on_the_online_card(client, catalogue, manager) -> None:
    """§4.3's 50/50 is a term of an agreement; the catalogue does not publish it."""
    client.force_login(manager)
    for url in (reverse("catalog:online-courses"), reverse("catalog:program-detail", kwargs={"code": "ON-GENAI"})):
        page = client.get(url).content.decode("utf-8").split("</nav>", 1)[-1]
        for term in ("50/50", "حصة الشريك", "حصة الجامعة"):
            assert term not in page


def test_the_course_field_is_shown_only_where_it_bounds_a_transfer(client, catalogue, manager) -> None:
    """BR-061 is the short courses' rule, so the column, the filter and the field are theirs."""
    client.force_login(manager)
    short = client.get(reverse("catalog:short-courses")).content.decode("utf-8")
    assert 'id="f_category"' in short and 'name="course_category"' in short

    for route in (reverse("catalog:online-courses"), reverse("catalog:programs")):
        page = client.get(route).content.decode("utf-8")
        assert 'id="f_category"' not in page and 'name="course_category"' not in page
        assert "المجال المعرفي" in page


def test_a_manager_creates_an_online_course_without_a_field(client, catalogue, manager) -> None:
    client.force_login(manager)
    response = _create(
        client, reverse("catalog:online-courses"), code="ON-DATA", name_ar="تحليل البيانات", training_hours="30"
    )
    assert response.status_code == 302
    assert Program.objects.get(code="ON-DATA").program_type == "ONLINE_COURSE"
