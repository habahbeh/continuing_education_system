"""
Sprint 8H — the transfer screens (§3.2/6, §3.2/7).

The rule engine behind these pages has been tested since Sprint 6 and is not
retested here; ``test_transfer.py`` owns BR-060…BR-066 and the ledger
mechanics. What this module is about is the reach: who may open each page,
which of the three hands is offered which button, and whether the figure the
preview shows is the figure the execution actually charges.

That last one is the reason a small refactor came with the sprint.
``fee_difference_for`` was lifted verbatim out of ``_execute`` so the preview
reads the same implementation rather than restating it — on this particular
number, two copies means telling a participant one thing at the counter and
charging them another.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.operations.models import EnrollmentStatus, Transfer, TransferReason, TransferStatus
from apps.operations.services import transfer_service
from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "probe-password-1234"
TERM_START = date(2026, 9, 20)

#: §3.2/6 «V A P · V C P · V E P · — · — · V P»
TRANSFER_READERS = (
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.AUDIT_ACCOUNT,
)
TRANSFER_OUTSIDERS = (Role.FINANCE_MANAGER, Role.CASHIER)
#: §3.2/7 «V C · V C · — · — · — · V»
REQUEST_READERS = (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT)
REQUEST_OUTSIDERS = (Role.FINANCE_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


@pytest.fixture
def source(make_cohort: Any, approve_cohort: Any) -> Any:
    cohort = make_cohort("SC-NET", code="CO-8H-FROM")
    approve_cohort(cohort, course_number="M-8H-1")
    return cohort


@pytest.fixture
def target(make_cohort: Any, approve_cohort: Any) -> Any:
    """Same category as SC-NET (CAT-IT), so BR-061 is satisfied."""
    cohort = make_cohort("SC-NET", code="CO-8H-TO")
    approve_cohort(cohort, course_number="M-8H-2")
    return cohort


@pytest.fixture
def enrollment(
    source: Any, make_enrollment: Any, charge_and_pay: Any, documented_attendance: Any
) -> Any:
    """A paying participant, one lecture in — inside the BR-062 deadline."""
    enrolled = make_enrollment(source)
    charge_and_pay(enrolled, amount="270.000")
    documented_attendance(enrolled, 1)
    enrolled.refresh_from_db()
    return enrolled


def _request_payload(enrollment: Any, target: Any, **overrides: Any) -> dict[str, Any]:
    payload = {
        "action": "submit",
        "from_enrollment_code": enrollment.code,
        "to_cohort_code": target.code,
        "reason": TransferReason.PARTICIPANT_REQUEST,
        "requested_on": TERM_START.isoformat(),
        "code": "TR-8H-1",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def requested(client: Client, registrar: User, enrollment: Any, target: Any) -> Transfer:
    client.force_login(registrar)
    client.post(reverse("operations:transfer-new"), _request_payload(enrollment, target))
    client.logout()
    return Transfer.objects.get(code="TR-8H-1")


# ---------------------------------------------------------------------------
# Who may open what
# ---------------------------------------------------------------------------
def test_the_row_is_one_line_and_what_left_it_is_a_click_away(
    client: Client, manager: User, requested: Transfer
) -> None:
    """
    Four cells carried two and three lines each — «من» and «إلى» three apiece —
    so the row stood at 79px on a 1440 screen and **183px on 1024**: fewer than
    three requests visible at once on a register meant to be scanned.

    Nothing was deleted. Everything that left the line is in the detail row
    beneath it, in the page and readable to a screen reader before any click.
    """
    import re

    client.force_login(manager)
    html = client.get(reverse("operations:transfers")).content.decode()
    row = html.split(f'>{requested.code}<', 1)[0].rsplit("<tr>", 1)[1] + html.split(
        f'>{requested.code}<', 1
    )[1].split("</tr>", 1)[0]
    visible = re.sub(r"<[^>]+>", " ", row)

    assert "<br>" not in row, "a cell that breaks its line is a cell with two lines"
    assert str(requested.requested_on) not in visible, "the date moved to the detail row"

    detail = html.split(f'id="trd-{requested.code}"', 1)[1].split("</tr>", 1)[0]
    for label in ("تاريخ الطلب", "الرقم الجامعي", "سبب النقل", "المحاضرات عند الطلب"):
        assert label in detail, f"«{label}» left the row without arriving anywhere"


def test_the_detail_row_is_hidden_by_the_markup_and_not_by_a_stylesheet(
    client: Client, manager: User, requested: Transfer
) -> None:
    """
    ``hidden`` rather than a CSS rule: what the markup hides the button shows,
    so the detail works on a page whose stylesheet never arrived.
    """
    client.force_login(manager)
    html = client.get(reverse("operations:transfers")).content.decode()

    opened = html.split(f'id="trd-{requested.code}"', 1)[1].split(">", 1)[0]
    assert "hidden" in opened
    assert f'data-expands="trd-{requested.code}"' in html
    assert 'aria-expanded="false"' in html
    assert "js/ui.js" in html, "the opener is declared but nothing would answer it"


def test_the_spans_are_counted_off_the_header_that_is_drawn(
    client: Client, manager: User, seeded_settings: None
) -> None:
    """
    A hard-coded span goes stale the moment a column moves — and one just did,
    here and on the enrolment register within the same day.
    """
    import re

    client.force_login(manager)
    html = client.get(reverse("operations:transfers")).content.decode()
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]

    assert re.search(rf'colspan="{head.count("<th")}"[^>]*class="empty"', html)


def test_the_register_pages_and_counts_the_whole_result(
    client: Client, manager: User, requested: Transfer
) -> None:
    """
    The list drew every request the centre had ever raised, and its counter
    named the rows on screen. Paged in the view over the list the service
    returns — ``list_transfers`` is read by the detail screen too, so a limit
    pushed into it would answer their question wrongly.
    """
    client.force_login(manager)
    response = client.get(reverse("operations:transfers"))

    page = response.context["page"]
    assert page["total"] == 1
    assert page["number"] == 1
    # A page number is never trusted: it arrives from a URL anyone can edit.
    url = reverse("operations:transfers")
    assert client.get(url, {"page": "abc"}).context["page"]["number"] == 1
    assert client.get(url, {"page": "99"}).context["page"]["number"] == 1


def test_the_search_is_live_and_keeps_its_filters(
    client: Client, manager: User, requested: Transfer
) -> None:
    """The house pattern: the register redraws as it is typed into."""
    client.force_login(manager)
    html = client.get(reverse("operations:transfers")).content.decode()

    assert 'hx-get="' + reverse("operations:transfers") in html
    assert 'hx-target="#tr-results"' in html
    assert 'id="tr-results"' in html
    # The counter and the clear link are replaced out of band, so they must be
    # wrappers that never disappear — what vanishes cannot be swapped.
    assert 'id="tr-count"' in html and 'id="tr-clear"' in html


@pytest.mark.parametrize("role", TRANSFER_READERS)
def test_the_transfers_list_opens_for_every_role_that_reads_it(
    client: Client, priced_catalog: Any, role: str
) -> None:
    client.force_login(_user(role, f"tlist.{role.lower()}"))
    assert client.get(reverse("operations:transfers")).status_code == 200


@pytest.mark.parametrize("role", TRANSFER_OUTSIDERS)
def test_the_transfers_list_is_closed_to_the_finance_manager_and_cashier(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"notlist.{role.lower()}"))
    assert client.get(reverse("operations:transfers")).status_code == 403


@pytest.mark.parametrize("role", REQUEST_READERS)
def test_the_request_form_opens_for_the_roles_holding_view(
    client: Client, priced_catalog: Any, role: str
) -> None:
    client.force_login(_user(role, f"tnew.{role.lower()}"))
    assert client.get(reverse("operations:transfer-new")).status_code == 200


@pytest.mark.parametrize("role", REQUEST_OUTSIDERS)
def test_the_request_form_is_closed_to_finance_and_the_till(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    §3.2/7 gives finance nothing, even though §3.2/6 gives it ``V E P``.

    Finance settles transfers; it does not ask for them.
    """
    client.force_login(_user(role, f"notnew.{role.lower()}"))
    assert client.get(reverse("operations:transfer-new")).status_code == 403


def test_the_auditor_reads_the_request_form_and_cannot_file_it(
    client: Client, enrollment: Any, target: Any
) -> None:
    auditor = _user(Role.AUDIT_ACCOUNT, "aud.8h")
    client.force_login(auditor)

    assert client.get(reverse("operations:transfer-new")).status_code == 200
    response = client.post(reverse("operations:transfer-new"), _request_payload(enrollment, target))
    assert response.status_code == 403
    assert not Transfer.objects.exists()


def test_a_refused_request_leaves_a_denied_attempt(
    client: Client, enrollment: Any, target: Any
) -> None:
    """BR-085 — written before anything could roll it back."""
    from apps.core.models import AuditEvent

    auditor = _user(Role.AUDIT_ACCOUNT, "aud.denied.8h")
    client.force_login(auditor)
    client.post(reverse("operations:transfer-new"), _request_payload(enrollment, target))

    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=auditor).exists()


def test_the_menu_offers_both_roles_the_transfer_list(
    finance: User, registrar: User, priced_catalog: Any
) -> None:
    """§3.2/6 — finance reads the transfers it settles, the registrar opens them."""
    from apps.people import nav

    for user in (finance, registrar):
        urls = {item["url"] for group in nav.nav_for(user) for item in group["items"]}
        assert reverse("operations:transfers") in urls


@pytest.mark.parametrize(
    ("role_fixture", "offered"),
    [("registrar", True), ("finance", False)],
)
def test_the_transfers_screen_offers_the_request_form_to_the_registrar_only(
    client: Client, request: Any, priced_catalog: Any, role_fixture: str, offered: bool
) -> None:
    """
    §3.2/7 — the request form is the registrar's and the manager's; finance
    settles the fee difference and never opens the request.

    Sprint 8H proved this on the menu, where «طلب نقل جديد» had an entry of its
    own. Sprint 8K-1 took that entry out to match the demo sidebar, so the
    distinction now rests entirely on the button on the transfers screen —
    which is where this pair has to hold it.
    """
    client.force_login(request.getfixturevalue(role_fixture))

    body = client.get(reverse("operations:transfers")).content.decode("utf-8")

    assert (f'href="{reverse("operations:transfer-new")}"' in body) is offered


# ---------------------------------------------------------------------------
# The preview — a dry run that writes nothing
# ---------------------------------------------------------------------------
def test_the_preview_shows_the_difference_before_anybody_commits(
    client: Client, registrar: User, enrollment: Any, target: Any
) -> None:
    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"),
        _request_payload(enrollment, target, action="preview"),
    )
    preview = response.context["preview"]

    assert preview["allowed"] is True
    assert preview["evidence"]["same_category"] is True
    assert preview["evidence"]["lectures_attended_at_request"] == 1
    # SC-NET to SC-NET: same price, so the move costs nothing.
    assert preview["money"]["difference"] == Decimal("0.000")
    assert preview["money"]["registration_carried"] == Decimal("20.000")
    assert not Transfer.objects.exists(), "a preview writes nothing"


def test_the_preview_reports_a_refusal_instead_of_raising(
    client: Client,
    registrar: User,
    source: Any,
    make_enrollment: Any,
    target: Any,
    documented_attendance: Any,
) -> None:
    """
    BR-062 with the number in it.

    A screen asking "what would happen?" wants «حضر 5» as its answer, not an
    exception — and the operator needs the count, because it tells them the
    request is not merely refused but permanently out of time.
    """
    late = make_enrollment(source, index=7)
    documented_attendance(late, 5)
    late.refresh_from_db()

    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"), _request_payload(late, target, action="preview")
    )
    preview = response.context["preview"]

    assert preview["allowed"] is False
    assert "BR-062" in preview["refusal"]
    assert "5" in preview["refusal"]
    assert not Transfer.objects.exists()


def test_the_preview_refuses_an_undocumented_attendance_count(
    client: Client, registrar: User, source: Any, make_enrollment: Any, target: Any
) -> None:
    """Q-06 · BR-095 — the rule cannot be judged, which is not the same as refused."""
    undocumented = make_enrollment(source, index=8)

    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"),
        _request_payload(undocumented, target, action="preview"),
    )
    assert response.context["preview"]["allowed"] is False
    assert "BR-095" in response.context["preview"]["refusal"]


def test_the_preview_and_the_execution_read_one_implementation(
    enrollment: Any, target: Any
) -> None:
    """
    The reason ``fee_difference_for`` was extracted.

    ``_execute`` computed this inline. Showing it on a screen meant either
    calling the same function or writing a second one — and a second one is
    how a participant gets quoted one figure and charged another.
    """
    import inspect

    source_text = inspect.getsource(transfer_service._execute)
    assert "fee_difference_for(" in source_text
    assert "new_tuition - old_tuition" not in source_text


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------
def test_the_registrar_files_a_request_and_lands_on_it(
    client: Client, registrar: User, enrollment: Any, target: Any
) -> None:
    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"), _request_payload(enrollment, target), follow=True
    )
    assert response.status_code == 200

    transfer = Transfer.objects.get(code="TR-8H-1")
    assert transfer.status == TransferStatus.PENDING_MANAGER
    assert transfer.from_enrollment_id == enrollment.pk
    assert transfer.to_cohort_id == target.pk
    # WORKFLOWS §5.3 — the evidence is frozen at the request, not read live.
    assert transfer.lectures_attended_at_request == 1
    assert transfer.same_category is True
    assert response.context["transfer"]["code"] == "TR-8H-1"


def test_a_refused_request_shows_the_rule_and_writes_nothing(
    client: Client,
    registrar: User,
    source: Any,
    make_enrollment: Any,
    target: Any,
    documented_attendance: Any,
) -> None:
    late = make_enrollment(source, index=9)
    documented_attendance(late, 5)
    late.refresh_from_db()

    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"), _request_payload(late, target), follow=True
    )
    assert any("BR-062" in str(m) for m in response.context["messages"])
    assert not Transfer.objects.exists()


def test_a_registrar_cannot_grant_themselves_the_category_waiver(
    client: Client, registrar: User, enrollment: Any, make_cohort: Any, approve_cohort: Any
) -> None:
    """
    C-12 — the demo granted this on a dropdown pick with nobody's name on it.

    ``request_transfer`` still trusts whatever ``waiver_by`` it is handed, so
    the screen asks §3.2/6's ``A`` before supplying one. The registrar holds
    ``V C P`` there and is refused, with the refusal on the record.
    """
    from apps.core.models import AuditEvent

    other_field = make_cohort("SC-CMA", code="CO-8H-OTHER")
    approve_cohort(other_field, course_number="M-8H-3")

    client.force_login(registrar)
    response = client.post(
        reverse("operations:transfer-new"),
        _request_payload(
            enrollment,
            other_field,
            reason=TransferReason.CENTER_CANCELLATION,
            grant_category_waiver="on",
            category_waiver_reason_ar="ألغى المركز الدورة",
        ),
    )
    assert response.status_code == 403
    assert not Transfer.objects.exists()
    assert AuditEvent.objects.filter(action="DENIED_ATTEMPT", actor=registrar).exists()


def test_a_waiver_without_a_reason_is_caught_by_the_form(
    client: Client, manager: User, enrollment: Any, make_cohort: Any, approve_cohort: Any
) -> None:
    other_field = make_cohort("SC-CMA", code="CO-8H-NOREASON")
    approve_cohort(other_field, course_number="M-8H-4")

    client.force_login(manager)
    response = client.post(
        reverse("operations:transfer-new"),
        _request_payload(
            enrollment,
            other_field,
            reason=TransferReason.CENTER_CANCELLATION,
            grant_category_waiver="on",
        ),
    )
    assert "category_waiver_reason_ar" in response.context["form"].errors
    assert not Transfer.objects.exists()


def test_the_manager_may_grant_the_waiver_on_a_centre_cancellation(
    client: Client, manager: User, enrollment: Any, make_cohort: Any, approve_cohort: Any
) -> None:
    other_field = make_cohort("SC-CMA", code="CO-8H-WAIVED")
    approve_cohort(other_field, course_number="M-8H-5")

    client.force_login(manager)
    client.post(
        reverse("operations:transfer-new"),
        _request_payload(
            enrollment,
            other_field,
            reason=TransferReason.CENTER_CANCELLATION,
            grant_category_waiver="on",
            category_waiver_reason_ar="ألغى المركز الدورة الأصلية",
        ),
        follow=True,
    )
    transfer = Transfer.objects.get(code="TR-8H-1")
    assert transfer.category_waiver_granted is True
    assert transfer.category_waiver_by_id == manager.pk
    assert transfer.same_category is False


# ---------------------------------------------------------------------------
# The three hands — §3.2/6
# ---------------------------------------------------------------------------
def test_the_manager_recommends_and_finance_cannot(
    client: Client, manager: User, finance: User, requested: Transfer
) -> None:
    client.force_login(finance)
    refused = client.post(
        reverse("operations:transfer-detail", args=[requested.code]), {"action": "recommend"}
    )
    assert refused.status_code == 403
    requested.refresh_from_db()
    assert requested.status == TransferStatus.PENDING_MANAGER

    client.force_login(manager)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "recommend"},
        follow=True,
    )
    requested.refresh_from_db()
    assert requested.status == TransferStatus.PENDING_FINANCE
    assert requested.manager_approved_by_id == manager.pk


def test_finance_executes_and_the_manager_cannot(
    client: Client, manager: User, finance: User, requested: Transfer
) -> None:
    """
    §3.2/6 footnote ⁶ — finance edits the fee-difference settlement.

    The manager holds ``A`` and no ``E`` here, so the person who recommended
    the move is not the person who moves the money.
    """
    client.force_login(manager)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "recommend"},
        follow=True,
    )
    refused = client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "execute", "executed_on": TERM_START.isoformat(), "new_code": "EN-8H-NEW"},
    )
    assert refused.status_code == 403

    client.force_login(finance)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "execute", "executed_on": TERM_START.isoformat(), "new_code": "EN-8H-NEW"},
        follow=True,
    )
    requested.refresh_from_db()
    assert requested.status == TransferStatus.EXECUTED
    assert requested.finance_settled_by_id == finance.pk


def test_executing_out_of_order_is_refused_with_the_rule(
    client: Client, finance: User, requested: Transfer
) -> None:
    """BR-066 — settlement follows the recommendation, never precedes it."""
    client.force_login(finance)
    response = client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "execute", "executed_on": TERM_START.isoformat(), "new_code": "EN-8H-EARLY"},
        follow=True,
    )
    assert any("BR-066" in str(m) for m in response.context["messages"])
    requested.refresh_from_db()
    assert requested.status == TransferStatus.PENDING_MANAGER


def test_rejection_from_the_screen_records_its_reason(
    client: Client, manager: User, requested: Transfer
) -> None:
    client.force_login(manager)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "reject", "reason_ar": "الدفعة الهدف مكتملة العدد"},
        follow=True,
    )
    requested.refresh_from_db()
    assert requested.status == TransferStatus.REJECTED
    assert requested.rejection_reason_ar == "الدفعة الهدف مكتملة العدد"


def test_rejection_without_a_reason_is_refused(
    client: Client, manager: User, requested: Transfer
) -> None:
    client.force_login(manager)
    response = client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "reject", "reason_ar": ""},
        follow=True,
    )
    assert any("سبب الرفض" in str(m) for m in response.context["messages"])
    requested.refresh_from_db()
    assert requested.status == TransferStatus.PENDING_MANAGER


def test_finance_may_reject_nothing(client: Client, finance: User, requested: Transfer) -> None:
    """§3.2/6 gives finance ``E`` and withholds ``A``."""
    client.force_login(finance)
    response = client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "reject", "reason_ar": "لا"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# What the execution did — read through the screen
# ---------------------------------------------------------------------------
def test_an_executed_transfer_moves_the_enrolment_and_shows_the_money(
    client: Client, manager: User, finance: User, requested: Transfer, enrollment: Any
) -> None:
    """
    The service's own tests own the ledger mechanics; this asserts the SCREEN
    reports them, and that the enrolment state matches what those tests fix.
    """
    from apps.operations.models import Enrollment

    client.force_login(manager)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "recommend"},
        follow=True,
    )
    client.force_login(finance)
    response = client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "execute", "executed_on": TERM_START.isoformat(), "new_code": "EN-8H-MOVED"},
        follow=True,
    )

    enrollment.refresh_from_db()
    assert enrollment.status == EnrollmentStatus.TRANSFERRED_OUT
    new = Enrollment.objects.get(code="EN-8H-MOVED")
    assert new.participant_id == enrollment.participant_id
    assert enrollment.transferred_to_id == new.pk

    shown = response.context["transfer"]
    assert shown["status"] == TransferStatus.EXECUTED
    assert shown["to_enrollment_code"] == "EN-8H-MOVED"
    assert shown["transferred_amount"] == Decimal("270.000")
    assert shown["is_closed"] is True


def test_an_executed_transfer_offers_no_further_action(
    client: Client, manager: User, finance: User, requested: Transfer
) -> None:
    client.force_login(manager)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "recommend"},
        follow=True,
    )
    client.force_login(finance)
    client.post(
        reverse("operations:transfer-detail", args=[requested.code]),
        {"action": "execute", "executed_on": TERM_START.isoformat(), "new_code": "EN-8H-DONE"},
        follow=True,
    )

    client.force_login(manager)
    page = client.get(reverse("operations:transfer-detail", args=[requested.code]))
    assert page.context["can_recommend"] is False
    assert page.context["can_reject"] is False
    assert page.context["can_execute"] is False


# ---------------------------------------------------------------------------
# The read layer
# ---------------------------------------------------------------------------
def test_the_source_enrolment_leaves_the_choices_once_a_transfer_is_open(
    manager: User, requested: Transfer, enrollment: Any
) -> None:
    offered = [c for c, _label in transfer_service.transferable_enrollment_choices(actor=manager)]
    assert enrollment.code not in offered


def test_the_lecture_deadline_does_not_silently_remove_a_candidate(
    manager: User, source: Any, make_enrollment: Any, documented_attendance: Any
) -> None:
    """
    BR-062 refuses with a number in the message. Dropping the enrolment from
    the list instead would replace that explanation with an absence.
    """
    late = make_enrollment(source, index=11)
    documented_attendance(late, 5)
    late.refresh_from_db()

    offered = [c for c, _label in transfer_service.transferable_enrollment_choices(actor=manager)]
    assert late.code in offered


def test_only_ministry_approved_short_courses_are_offered_as_destinations(
    manager: User, source: Any, target: Any, make_cohort: Any
) -> None:
    unapproved = make_cohort("SC-CMA", code="CO-8H-NOMOHE")
    diploma = make_cohort("DIP-ID", code="CO-8H-DIP")

    offered = [c for c, _label in transfer_service.destination_cohort_choices(actor=manager)]
    assert target.code in offered
    assert unapproved.code not in offered, "BR-013"
    assert diploma.code not in offered, "BR-060 — short courses only"


def test_the_source_cohort_is_not_offered_as_its_own_destination(
    manager: User, enrollment: Any, source: Any, target: Any
) -> None:
    offered = [
        c
        for c, _label in transfer_service.destination_cohort_choices(
            actor=manager, from_code=enrollment.code
        )
    ]
    assert source.code not in offered
    assert target.code in offered


def test_the_listing_is_filterable(manager: User, requested: Transfer) -> None:
    assert len(transfer_service.list_transfers(actor=manager)) == 1
    assert len(transfer_service.list_transfers(actor=manager, status="EXECUTED")) == 0
    found = transfer_service.list_transfers(actor=manager, query="TR-8H-1")
    assert [r["code"] for r in found] == ["TR-8H-1"]


def test_the_cashier_cannot_read_the_listing(requested: Transfer, seeded_settings: None) -> None:
    cashier = _user(Role.CASHIER, "cash.read.8h")
    with pytest.raises(PermissionDenied):
        transfer_service.list_transfers(actor=cashier)


def test_a_missing_transfer_is_a_404(client: Client, manager: User) -> None:
    client.force_login(manager)
    assert client.get(reverse("operations:transfer-detail", args=["NOPE"])).status_code == 404


def test_preview_requires_view_on_the_request_screen(
    enrollment: Any, target: Any, seeded_settings: None
) -> None:
    cashier = _user(Role.CASHIER, "cash.preview.8h")
    with pytest.raises(PermissionDenied):
        transfer_service.preview_transfer(
            actor=cashier,
            from_enrollment=enrollment,
            to_cohort=target,
            reason=TransferReason.PARTICIPANT_REQUEST,
            as_of=TERM_START,
        )


def test_request_transfer_still_raises_for_a_caller_that_is_not_a_screen(
    manager: User, source: Any, make_enrollment: Any, target: Any, documented_attendance: Any
) -> None:
    """
    ``preview_transfer`` returns refusals; ``request_transfer`` raises them.

    Nothing may come to depend on the preview having been called first.
    """
    late = make_enrollment(source, index=12)
    documented_attendance(late, 5)
    late.refresh_from_db()

    with pytest.raises(ValidationError, match="BR-062"):
        transfer_service.request_transfer(
            actor=manager,
            from_enrollment=late,
            to_cohort=target,
            reason=TransferReason.PARTICIPANT_REQUEST,
            requested_on=TERM_START,
            code="TR-8H-RAW",
        )


# ---------------------------------------------------------------------------
# A-05
# ---------------------------------------------------------------------------
def test_the_transfer_views_read_through_the_service_layer() -> None:
    """ADR-008 — the module-level imports carry no model."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("apps/operations/views.py").read_text(encoding="utf-8"))
    module_level = {
        node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [m for m in module_level if m.startswith("apps.") and ".models" in m]
    assert "apps.operations.services" in module_level


# ---------------------------------------------------------------------------
# The request form — what it may put in front of which hand (Sprint 8L)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("role", "drawn"),
    [
        (Role.CENTER_MANAGER, True),
        (Role.REGISTRATION_OFFICER, False),
        (Role.AUDIT_ACCOUNT, False),
    ],
)
def test_the_waiver_is_drawn_for_the_hand_that_may_grant_it_and_no_other(
    client: Client, seeded_settings: Any, role: str, drawn: bool
) -> None:
    """
    §3.2/6 ``A`` — and the screen was offering it to everybody.

    ``_transfer_inputs`` asks for APPROVE before it supplies a ``waiver_by``,
    so a registrar who ticked the box lost a filled form to a 403 page and
    earned a DENIED_ATTEMPT for pressing a control this screen had put in
    front of them (BR-085). The check has not moved — the test below still
    POSTs the field and still gets its 403 — but the control is no longer
    drawn for a hand that would be refused for using it.
    """
    client.force_login(_user(role, f"waiver.probe.{role.lower()}"))

    body = client.get(reverse("operations:transfer-new")).content.decode("utf-8")

    assert ('name="grant_category_waiver"' in body) is drawn
    assert ('name="category_waiver_reason_ar"' in body) is drawn


def test_the_reason_for_the_waiver_waits_until_the_waiver_is_ticked(
    client: Client, manager: User
) -> None:
    """A field that is required only on a condition is not shown before it."""
    client.force_login(manager)

    body = client.get(reverse("operations:transfer-new")).content.decode("utf-8")

    assert 'id="waiver-why"' in body
    assert 'data-expands="waiver-why"' in body, "the tick is what reveals it (static/js/ui.js)"
    assert '<div id="waiver-why" hidden>' in body


def test_the_destination_list_never_offers_the_cohort_the_participant_is_on(
    client: Client, registrar: User, enrollment: Any, target: Any
) -> None:
    """
    ``destination_cohort_choices`` has always taken ``from_code`` and the view
    was passing none, so the source cohort sat in the list and the only answer
    it could produce was «الدفعة الهدف هي الدفعة نفسها».
    """
    client.force_login(registrar)

    body = client.get(
        f"{reverse('operations:transfer-new')}?enrollment={enrollment.code}"
    ).content.decode("utf-8")

    assert f'value="{target.code}"' in body
    assert f'value="{enrollment.cohort.code}"' not in body


def test_arriving_from_a_row_fills_the_enrolment_in(
    client: Client, registrar: User, enrollment: Any, target: Any
) -> None:
    """The request starts at an enrolment; the operator should not re-find it."""
    client.force_login(registrar)

    body = client.get(
        f"{reverse('operations:transfer-new')}?enrollment={enrollment.code}"
    ).content.decode("utf-8")

    assert f'value="{enrollment.code}" selected' in body


def test_a_link_naming_an_enrolment_that_cannot_move_says_so(
    client: Client, registrar: User, target: Any
) -> None:
    """Silence would leave the reader in front of a form with no idea why."""
    client.force_login(registrar)

    body = client.get(
        f"{reverse('operations:transfer-new')}?enrollment=EN-NOT-A-ROW"
    ).content.decode("utf-8")

    assert "غير قابل للنقل" in body
    assert "BR-060" in body


def test_the_register_offers_the_transfer_only_on_a_row_the_form_would_accept(
    client: Client, registrar: User, enrollment: Any, target: Any
) -> None:
    """
    The row link and the form's own list are filled from one function, so no
    row can offer a link the next screen refuses (BR-085 · §3.4).
    """
    client.force_login(registrar)

    body = client.get(reverse("operations:enrollments")).content.decode("utf-8")

    assert f'{reverse("operations:transfer-new")}?enrollment={enrollment.code}' in body


def test_a_reader_who_may_not_request_gets_no_transfer_link_on_the_row(
    client: Client, cashier: User, enrollment: Any
) -> None:
    """§3.2/7 gives the till nothing here, so the row offers it nothing."""
    client.force_login(cashier)

    body = client.get(reverse("operations:enrollments")).content.decode("utf-8")

    assert "transfers/new/?enrollment=" not in body


def test_an_empty_source_list_explains_itself_instead_of_standing_blank(
    client: Client, registrar: User
) -> None:
    """§8 — an empty control says what would fill it and where that starts."""
    client.force_login(registrar)

    body = client.get(reverse("operations:transfer-new")).content.decode("utf-8")

    assert "لا تسجيل قابل للنقل" in body
    assert reverse("operations:enrollments") in body


def test_a_code_already_used_is_answered_with_a_sentence_not_a_500(
    client: Client, registrar: User, enrollment: Any, target: Any, make_enrollment: Any,
    charge_and_pay: Any, documented_attendance: Any, source: Any
) -> None:
    """
    ``Transfer.code`` is unique and the operator types it, so the second use
    of a code reached the database as an IntegrityError and came back a 500
    with the filled form gone.
    """
    client.force_login(registrar)
    client.post(reverse("operations:transfer-new"), _request_payload(enrollment, target))

    second = make_enrollment(source, index=11)
    charge_and_pay(second, amount="270.000")
    documented_attendance(second, 1)
    second.refresh_from_db()

    response = client.post(
        reverse("operations:transfer-new"),
        _request_payload(second, target),  # the same TR-8H-1
        follow=True,
    )

    assert response.status_code == 200
    assert "مستعمل لطلب آخر" in response.content.decode("utf-8")
    assert Transfer.objects.count() == 1


def test_the_form_screen_carries_no_script_of_its_own(client: Client, manager: User) -> None:
    """§7 — the shared behaviours live in ``static/js/ui.js``."""
    client.force_login(manager)

    body = client.get(reverse("operations:transfer-new")).content.decode("utf-8")
    main = body.split('id="main"', 1)[1].split("</main>", 1)[0]

    assert "<script" not in main
    assert "style=" not in main
