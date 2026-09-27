"""
Creating a price list from the screens (Sprint 8L).

``create_price_list``, ``add_price_item`` and ``record_external_approval``
were written, tested and audited — and nothing called them. The register
listed lists and offered no way to make one, so the only road to a first
price was the Django admin, which writes past the service: no audit line, no
frozen-list refusal, no archiving of the list it supersedes.

That gap is not cosmetic. Without a price list nothing in this system can be
charged, so a client installing from an empty database could not reach a
single enrolment without a developer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import (
    CourseCategory,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    ProgramType,
)
from apps.core.models import Semester
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "pricelist-probe-1234"
INDEX = "catalog:pricelists"


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
    category = CourseCategory.objects.create(code="CAT-IT", name_ar="تكنولوجيا المعلومات")
    return Program.objects.create(
        code="SC-NET",
        program_type=ProgramType.SHORT_COURSE,
        name_ar="هندسة الشبكات",
        training_hours=60,
        course_category=category,
        consumables_per_student=0,
    )


def _new_payload(semester: Semester) -> dict[str, Any]:
    return {
        "code": "pl-2026-1",
        "name_ar": "قائمة أسعار الفصل الأول",
        "semester": str(semester.pk),
        "issued_on": "2026-08-01",
        "effective_from": "2026-09-01",
        "proposed_by_text": "مدير المركز",
    }


def test_the_manager_creates_a_list_and_it_is_born_a_draft(
    client: Client, semester: Semester
) -> None:
    """
    BR-008 — a list is never born approved. The form has no status field at
    all, so there is nothing to set: the state is reached by recording the
    president's decision, which leaves evidence behind it.
    """
    manager = _user(Role.CENTER_MANAGER, "pl.mgr")
    client.force_login(manager)

    response = client.post(reverse(INDEX), _new_payload(semester), follow=True)

    assert response.status_code == 200
    price_list = PriceList.objects.get()
    assert price_list.code == "PL-2026-1"
    assert price_list.status == PriceListStatus.DRAFT


def test_the_creation_is_audited_which_the_admin_never_did(
    client: Client, semester: Semester
) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "pl.audit")
    client.force_login(manager)

    client.post(reverse(INDEX), _new_payload(semester), follow=True)

    assert AuditEvent.objects.filter(
        action="CREATE", entity_type="catalog.PriceList", actor=manager
    ).exists()


def test_a_reader_without_create_is_offered_no_form_and_refused_a_post(
    client: Client, semester: Semester
) -> None:
    """§3.4 — the control is not drawn, and the service refuses it anyway."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pl.aud"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")
    assert "قائمة أسعار جديدة" not in body

    assert client.post(reverse(INDEX), _new_payload(semester)).status_code == 403
    assert not PriceList.objects.exists()


def test_a_price_is_added_to_a_draft(
    client: Client, semester: Semester, program: Program
) -> None:
    manager = _user(Role.CENTER_MANAGER, "pl.item")
    client.force_login(manager)
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {
            "action": "item",
            "program": str(program.pk),
            "course_fee": "300.000",
            "deposit_amount": "",
            "notes": "",
        },
        follow=True,
    )

    item = PriceListItem.objects.get()
    assert item.program_id == program.pk
    assert item.course_fee == Decimal("300.000")
    assert item.deposit_amount is None, "an empty deposit is no deposit, not a zero"


def test_a_programme_already_priced_is_not_offered_twice(
    client: Client, semester: Semester, program: Program
) -> None:
    """One item per programme per list; offering it again invites the collision."""
    manager = _user(Role.CENTER_MANAGER, "pl.twice")
    client.force_login(manager)
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()
    url = reverse("catalog:pricelist-detail", args=[price_list.code])
    client.post(
        url,
        {"action": "item", "program": str(program.pk), "course_fee": "300.000"},
        follow=True,
    )

    body = client.get(url).content.decode("utf-8")
    # The PRICE ITEM dialog only. The fee-rule dialog beside it offers every
    # programme on purpose — a rule naming a priced programme is the whole point
    # of «most-specific-wins» (BR-009), so scanning the page as a whole would
    # read that intended offer as this defect.
    dialog = body[body.index('id="price-item-new"') : body.index('id="price-list-edit"')]

    assert f'value="{program.pk}"' not in dialog


def test_recording_the_decision_is_what_approves_the_list(
    client: Client, semester: Semester, program: Program
) -> None:
    """
    D-31 — nobody holds an internal approval right over a price list. The
    president approves outside the system; this records that he did.
    """
    manager = _user(Role.CENTER_MANAGER, "pl.approve")
    client.force_login(manager)
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {
            "action": "approve",
            "approved_by_text": "رئيس جامعة البترا",
            "decision_reference": "قرار 2026/44",
        },
        follow=True,
    )

    price_list.refresh_from_db()
    assert price_list.status == PriceListStatus.APPROVED
    assert price_list.decision_reference == "قرار 2026/44"


def test_an_approval_without_its_evidence_is_refused(
    client: Client, semester: Semester
) -> None:
    """The two fields are the only proof the approval happened."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.noproof"))
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {"action": "approve", "approved_by_text": "", "decision_reference": ""},
        follow=True,
    )

    price_list.refresh_from_db()
    assert price_list.status == PriceListStatus.DRAFT


def test_an_approved_list_offers_no_edit_and_accepts_none(
    client: Client, semester: Semester, program: Program
) -> None:
    """D-14 — an approved list is evidence. The change is a NEW list."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.frozen"))
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()
    url = reverse("catalog:pricelist-detail", args=[price_list.code])
    client.post(
        url,
        {
            "action": "approve",
            "approved_by_text": "رئيس الجامعة",
            "decision_reference": "قرار 1",
        },
        follow=True,
    )

    body = client.get(url).content.decode("utf-8")
    assert "غير قابلة للتعديل" in body
    assert "إضافة سعر برنامج" not in body

    client.post(
        url,
        {"action": "item", "program": str(program.pk), "course_fee": "300.000"},
        follow=True,
    )
    assert not PriceListItem.objects.exists(), "the service refuses it even when posted"


def test_the_empty_state_now_points_at_the_form_it_used_to_deny(
    client: Client, db: Any
) -> None:
    """
    §8 — it said «ولا تُنشأ من هذه الشاشة», which was true and is no longer.
    An empty state that explains without pointing leaves the reader stuck.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.empty"))

    body = client.get(reverse(INDEX)).content.decode("utf-8")

    assert "لا قوائم أسعار معرَّفة بعد" in body
    assert "ولا تُنشأ من هذه الشاشة" not in body
    assert "قائمة أسعار جديدة" in body


def test_the_screens_carry_no_script_of_their_own(client: Client, semester: Semester) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pl.clean"))
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()

    for url in (reverse(INDEX), reverse("catalog:pricelist-detail", args=[price_list.code])):
        body = client.get(url).content.decode("utf-8")
        main = body.split('id="main"', 1)[1].split("</main>", 1)[0]
        assert "<script" not in main, url


def test_the_live_search_replaces_the_query_string_instead_of_growing_it(
    client: Client, semester: Semester
) -> None:
    """
    ``hx-get=""`` means «the current URL, query string and all», and htmx adds
    the form's own fields on top of it. Typing «تصميم» one letter at a time
    therefore pushed

        ?rq=ت&rq=تص&rq=تصم&rq=تصمي&rq=تصميم

    into the address bar — one copy per keystroke, and the back button walked
    back through every one of them. Naming the route explicitly makes the
    form's fields the whole query, which is what the till and the expense
    register have always done.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.hx"))
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    price_list = PriceList.objects.get()
    detail = reverse("catalog:pricelist-detail", args=[price_list.code])

    for url, body in (
        (reverse(INDEX), client.get(reverse(INDEX)).content.decode("utf-8")),
        (detail, client.get(detail).content.decode("utf-8")),
    ):
        assert 'hx-get=""' not in body, f"{url} would accumulate its own query string"
        assert f'hx-get="{url}"' in body


# ---------------------------------------------------------------------------
# Correcting a draft — the gap a mistyped date opened
# ---------------------------------------------------------------------------
def _a_draft(client: Client, semester: Semester) -> PriceList:
    client.post(reverse(INDEX), _new_payload(semester), follow=True)
    return PriceList.objects.get()


def test_a_mistyped_draft_can_be_corrected(client: Client, semester: Semester) -> None:
    """
    A date a month out means no list is in force and nothing can be priced —
    and until this existed there was no way to fix it: no edit screen, no edit
    service, nothing but the table itself. A draft is a document being
    written, not evidence.
    """
    manager = _user(Role.CENTER_MANAGER, "pl.fix")
    client.force_login(manager)
    price_list = _a_draft(client, semester)
    assert price_list.effective_from == date(2026, 9, 1)

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {
            "action": "edit",
            "code": price_list.code,
            "name_ar": "قائمة أسعار الفصل الأول",
            "semester": str(semester.pk),
            "issued_on": "2026-08-01",
            "effective_from": "2026-08-15",
            "proposed_by_text": "مدير المركز",
        },
        follow=True,
    )

    price_list.refresh_from_db()
    assert price_list.effective_from == date(2026, 8, 15)


def test_the_correction_is_audited_with_what_changed(
    client: Client, semester: Semester
) -> None:
    from apps.core.models import AuditEvent

    manager = _user(Role.CENTER_MANAGER, "pl.fix.audit")
    client.force_login(manager)
    price_list = _a_draft(client, semester)

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {
            "action": "edit",
            "code": price_list.code,
            "name_ar": "اسم مصحَّح",
            "semester": str(semester.pk),
            "issued_on": "2026-08-01",
            "effective_from": "2026-09-01",
            "proposed_by_text": "",
        },
        follow=True,
    )

    event = AuditEvent.objects.filter(
        action="UPDATE", entity_type="catalog.PriceList", actor=manager
    ).latest("id")
    assert "name_ar" in str(event.changes)


def test_the_code_is_not_among_the_editable_fields(
    client: Client, semester: Semester
) -> None:
    """It is the reference every other screen names the list by."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.code"))
    price_list = _a_draft(client, semester)

    client.post(
        reverse("catalog:pricelist-detail", args=[price_list.code]),
        {
            "action": "edit",
            "code": "PL-RENAMED",
            "name_ar": "قائمة أسعار الفصل الأول",
            "semester": str(semester.pk),
            "issued_on": "2026-08-01",
            "effective_from": "2026-09-01",
            "proposed_by_text": "",
        },
        follow=True,
    )

    price_list.refresh_from_db()
    assert price_list.code == "PL-2026-1", "renaming the reference is not an edit"


def test_a_price_can_be_taken_off_a_draft(
    client: Client, semester: Semester, program: Program
) -> None:
    """A wrong figure is corrected by removing the row and adding it again."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.rm"))
    price_list = _a_draft(client, semester)
    url = reverse("catalog:pricelist-detail", args=[price_list.code])
    client.post(
        url,
        {"action": "item", "program": str(program.pk), "course_fee": "300.000"},
        follow=True,
    )
    assert PriceListItem.objects.count() == 1

    client.post(url, {"action": "remove-item", "program": program.code}, follow=True)

    assert not PriceListItem.objects.exists()


def test_an_approved_list_refuses_both_the_edit_and_the_removal(
    client: Client, semester: Semester, program: Program
) -> None:
    """D-14 — the change to an approved list is a NEW list, never a correction."""
    client.force_login(_user(Role.CENTER_MANAGER, "pl.frozen2"))
    price_list = _a_draft(client, semester)
    url = reverse("catalog:pricelist-detail", args=[price_list.code])
    client.post(
        url,
        {"action": "item", "program": str(program.pk), "course_fee": "300.000"},
        follow=True,
    )
    client.post(
        url,
        {
            "action": "approve",
            "approved_by_text": "رئيس الجامعة",
            "decision_reference": "قرار 9",
        },
        follow=True,
    )

    client.post(
        url,
        {
            "action": "edit",
            "code": price_list.code,
            "name_ar": "محاولة تعديل",
            "semester": str(semester.pk),
            "issued_on": "2026-08-01",
            "effective_from": "2026-01-01",
            "proposed_by_text": "",
        },
        follow=True,
    )
    client.post(url, {"action": "remove-item", "program": program.code}, follow=True)

    price_list.refresh_from_db()
    assert price_list.name_ar == "قائمة أسعار الفصل الأول"
    assert price_list.effective_from == date(2026, 9, 1)
    assert PriceListItem.objects.count() == 1, "the item survived the removal attempt"

    body = client.get(url).content.decode("utf-8")
    assert "تعديل المسودة" not in body
    assert "غير قابلة للتعديل" in body


def test_a_reader_without_edit_is_offered_neither_act(
    client: Client, semester: Semester, program: Program
) -> None:
    client.force_login(_user(Role.CENTER_MANAGER, "pl.setup"))
    price_list = _a_draft(client, semester)
    url = reverse("catalog:pricelist-detail", args=[price_list.code])
    client.post(
        url,
        {"action": "item", "program": str(program.pk), "course_fee": "300.000"},
        follow=True,
    )

    client.force_login(_user(Role.AUDIT_ACCOUNT, "pl.reader"))
    body = client.get(url).content.decode("utf-8")

    assert "تعديل المسودة" not in body
    assert "إضافة سعر برنامج" not in body
    assert client.post(url, {"action": "remove-item", "program": program.code}).status_code == 403
    assert PriceListItem.objects.count() == 1
