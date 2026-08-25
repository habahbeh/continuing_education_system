"""
Sprint 8I-1 — the last things checked before the system is shown to a client.

Two questions only. Does the front door open onto something a client should
see? And can the clearance be walked to a certificate THROUGH THE SCREENS
rather than through the services underneath them — which is what a demo
actually does, and which no test had ever done.

``tests/test_operational_readiness.py`` flow F already proves the service
chain. This module proves the buttons.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "demo-probe-1234"
TERM_START = date(2026, 9, 20)
TERM_END = date(2026, 12, 20)


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


# ---------------------------------------------------------------------------
# The front door
# ---------------------------------------------------------------------------
def test_the_root_url_sends_a_signed_in_user_to_the_dashboard(
    client: Client, seeded_settings: None
) -> None:
    """
    ``/`` was the health check — Django's version, MySQL's version and whether
    STRICT_ALL_TABLES was set. That is a page for whoever deploys the system,
    and it was the first thing a client saw AND the page every login landed
    on.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mgr.root.probe"))
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("operations:dashboard")


def test_the_root_url_sends_a_stranger_to_the_login_page(client: Client) -> None:
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("people:login")


def test_the_health_check_keeps_its_own_address(client: Client) -> None:
    """Moved, not deleted — whoever deploys the system still needs it."""
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert "MySQL" in response.content.decode("utf-8")


def test_a_successful_login_lands_on_the_dashboard(client: Client, seeded_settings: None) -> None:
    _user(Role.REGISTRATION_OFFICER, "reg.login.probe")
    response = client.post(
        reverse("people:login"), {"username": "reg.login.probe", "password": PASSWORD}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("operations:dashboard")


# ---------------------------------------------------------------------------
# Clearance to certificate, driven through the screens
# ---------------------------------------------------------------------------
@pytest.fixture
def staff(seeded_settings: None) -> dict[str, User]:
    return {
        "manager": _user(Role.CENTER_MANAGER, "mgr.demo"),
        "registrar": _user(Role.REGISTRATION_OFFICER, "reg.demo"),
        "finance": _user(Role.FINANCE_OFFICER, "fin.demo"),
        "finance_manager": _user(Role.FINANCE_MANAGER, "fim.demo"),
        "cashier": _user(Role.CASHIER, "cash.demo"),
    }


@pytest.fixture
def settled_enrollment(
    staff: dict[str, User], priced_catalog: Any, active_semester: Any, cash_method: Any
) -> Any:
    """A graduate who owes nothing — the state a clearance starts from."""
    from apps.billing.services import account_service, charge_service
    from apps.cashbox.services import payment_service
    from apps.catalog.models import Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.models import IdDocumentType, ParticipantCategory
    from apps.people.services import participant_service

    participant = participant_service.create_participant(
        actor=staff["registrar"],
        data={
            "category": ParticipantCategory.UNIVERSITY,
            "name_ar": "ليان علاء الدين تيسير حمدان",
            "id_document_type": IdDocumentType.NATIONAL_ID,
            "id_document_number": "9998887771",
            "registered_on": TERM_START,
            "no_refund_pledge_accepted": True,
        },
    )
    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-DEMO",
        program=program,
        semester=active_semester,
        name_ar="دفعة العرض",
        starts_on=TERM_START,
        ends_on=TERM_END,
        capacity=25,
    )
    enrollment = Enrollment.objects.create(
        code="EN-DEMO",
        participant=participant,
        cohort=cohort,
        enrolled_on=TERM_START,
        price_list=priced_catalog,
        status="COMPLETED",
    )
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=TERM_START
    )
    charge_service.charge_lines_from_quote(
        actor=staff["registrar"], enrollment=enrollment, quote=quote, charged_on=TERM_START
    )
    payment_service.take_payment(
        actor=staff["cashier"],
        enrollment=enrollment,
        amount=account_service.get_account_state(enrollment).total_due,
        payment_method=cash_method,
        received_on=TERM_START,
    )
    assert account_service.get_account_state(enrollment).balance == Decimal("0.000")
    return enrollment


def test_the_clearance_reaches_a_certificate_through_the_screens(
    client: Client, staff: dict[str, User], settled_enrollment: Any
) -> None:
    """
    Every step is a POST to a real URL, by the role the matrix gives it.

    Four acts and then a fifth: WORKFLOWS §6.3 C5 makes closing its own
    deliberate step, because it re-reads the balance rather than trusting the
    one certified earlier. Sprint 8E mistook that for a defect; it is the
    control, and this test walks it as a user must.
    """
    from apps.operations.models import Certificate, Clearance

    detail = None

    # 1 — the centre manager opens the clearance from the clearance screen.
    client.force_login(staff["manager"])
    client.post(
        reverse("operations:clearances"),
        {
            "action": "open",
            "enrollment_code": settled_enrollment.code,
            "case_type": "GRADUATION",
            "opened_on": TERM_END.isoformat(),
            "code": "CLR-DEMO",
        },
        follow=True,
    )
    clearance = Clearance.objects.get(code="CLR-DEMO")
    detail = reverse("operations:clearance-detail", args=[clearance.code])

    # 2 — custody, the centre's own step.
    client.post(detail, {"action": "custody", "items": "هوية المركز\nبطاقة المواصلات"}, follow=True)

    # 3 — finance certifies, and a SECOND finance signature follows (BR-074).
    client.force_login(staff["finance"])
    client.post(detail, {"action": "certify"}, follow=True)
    client.force_login(staff["finance_manager"])
    client.post(detail, {"action": "second-certify"}, follow=True)

    # 4 — handover, back with the centre, naming who actually took it.
    client.force_login(staff["manager"])
    client.post(
        detail,
        {"action": "handover", "participant_ack_name": "ليان علاء الدين تيسير حمدان"},
        follow=True,
    )

    # 5 — closing re-reads the balance (§6.3 C5).
    response = client.post(detail, {"action": "close"}, follow=True)
    assert response.status_code == 200

    clearance.refresh_from_db()
    assert clearance.status == "COMPLETED", "every step reachable from the screen"

    # 6 — and only now does the certificate screen offer this enrolment.
    issued = client.post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": settled_enrollment.code,
            "grade": "EXCELLENT",
            "issued_on": TERM_END.isoformat(),
        },
        follow=True,
    )
    assert issued.status_code == 200

    certificate = Certificate.objects.get(enrollment=settled_enrollment)
    assert certificate.certificate_number
    assert certificate.grade == "EXCELLENT"


def test_the_certificate_screen_refuses_an_enrolment_with_no_clearance(
    client: Client, staff: dict[str, User], settled_enrollment: Any
) -> None:
    """BR-075 — no certificate without a completed clearance."""
    from apps.operations.models import Certificate

    client.force_login(staff["manager"])
    client.post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": settled_enrollment.code,
            "grade": "EXCELLENT",
            "issued_on": TERM_END.isoformat(),
        },
        follow=True,
    )
    assert not Certificate.objects.filter(enrollment=settled_enrollment).exists()


# ---------------------------------------------------------------------------
# Sprint 8K — demo parity routes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "route",
    [
        "operations:enroll-flow",
        "operations:special-cases",
        "settlements:entitlement",
        "people:settings",
        "people:coverage",
        "people:future",
    ],
)
def test_sprint_8k_demo_parity_pages_open_for_the_manager(
    client: Client, seeded_settings: None, route: str
) -> None:
    """Every demo-sidebar promise added in 8K-1 has a real guarded page."""
    client.force_login(_user(Role.CENTER_MANAGER, f"mgr.8k.{route.replace(':', '.')}"))

    response = client.get(reverse(route))

    assert response.status_code == 200


# The guide — Sprint 8K-2
# ---------------------------------------------------------------------------
#: Every screen the guide offers a button to, and the screen permission that
#: earns it. Nine screens across eight stages: stage 5 offers two.
GUIDE_STAGE_SCREENS = [
    ("people:participant-new", "student-new"),
    ("operations:enrollments", "enrollments"),
    ("cashbox:payment-new", "payment-new"),
    ("cashbox:payments", "payments"),
    ("operations:transfers", "transfers"),
    ("operations:special-cases", "special-cases"),
    ("cashbox:closing", "closing"),
    ("operations:clearances", "clearance"),
    ("operations:certificates", "certificates"),
]

#: §3.1/2 — who may read the guide at all.
GUIDE_READERS = [
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.AUDIT_ACCOUNT,
]


def _stage_button(url: str) -> str:
    """The stage button, as the template draws it — not merely a link to it.

    The sidebar links most of these screens too, so a bare ``href in body``
    test would pass on the menu alone and prove nothing about the guide.
    """
    return f'<a class="btn2 sm" href="{url}">'


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.CENTER_MANAGER, True),
        (Role.REGISTRATION_OFFICER, True),
        (Role.FINANCE_OFFICER, True),
        (Role.AUDIT_ACCOUNT, True),
        (Role.FINANCE_MANAGER, False),
        (Role.CASHIER, False),
    ],
)
def test_the_guide_opens_for_exactly_the_roles_the_matrix_allows(
    client: Client, seeded_settings: None, role: str, allowed: bool
) -> None:
    """§3.1/2 — the finance manager and the cashier hold no VIEW on ENROLL_FLOW."""
    client.force_login(_user(role, f"guide.open.{role.lower()}"))

    response = client.get(reverse("operations:enroll-flow"))

    assert response.status_code == (200 if allowed else 403)


def test_the_guide_refuses_an_anonymous_visitor(client: Client, seeded_settings: None) -> None:
    """Fail-closed: the guide names screens and rules, and is not public."""
    assert client.get(reverse("operations:enroll-flow")).status_code == 403


@pytest.mark.parametrize("role", GUIDE_READERS)
def test_the_guide_explains_every_stage_to_every_reader(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    Filtering the buttons must not filter the teaching. A registrar who may
    not open the till still has to know that a payment happens, who takes it
    and what it unblocks — otherwise the guide stops being a guide for
    exactly the people who most need one.
    """
    client.force_login(_user(role, f"guide.copy.{role.lower()}"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")

    for stage_title in (
        "طلب التحاق جديد",
        "التسجيل ومتابعته",
        "استيفاء دفعة",
        "الدفعات وسندات القبض",
        "النقل أو الحالة الخاصة",
        "الإقفال اليومي",
        "براءة الذمة",
        "الشهادة",
    ):
        assert stage_title in body, f"{role} is not told about «{stage_title}»"


def test_the_guide_shows_the_registrar_the_admission_form_and_not_the_till(
    client: Client, seeded_settings: None
) -> None:
    """One reader, spelled out: what the registrar is and is not offered."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "guide.reg.named"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")

    assert _stage_button(reverse("people:participant-new")) in body
    assert _stage_button(reverse("operations:enrollments")) in body
    assert _stage_button(reverse("operations:certificates")) in body
    # D-06 — the registrar takes no cash, so neither till screen is offered.
    assert _stage_button(reverse("cashbox:payment-new")) not in body
    assert _stage_button(reverse("cashbox:closing")) not in body


@pytest.mark.parametrize("role", GUIDE_READERS)
def test_the_guided_flow_never_offers_a_step_the_reader_may_not_open(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The guide sends a new employee to the screen for each stage, and the first
    version linked every stage for everyone who could open the page. Four roles
    can, and none of them may open all nine screens: §3.4/17 keeps the manager,
    the registrar and the audit account out of cash collection — D-01 makes
    BR-081 an explicit deny rather than a missing grant — and §3.2/4 keeps the
    finance officer out of the admission form. Following the guide as written
    therefore ended in a refusal and a DENIED_ATTEMPT row (BR-085).

    A stage the reader may not VIEW keeps its explanation and loses its button.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"guide.{role.lower()}"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")

    for route, screen in GUIDE_STAGE_SCREENS:
        may_open = Action.VIEW in allowed_actions(role, screen)
        offered = _stage_button(reverse(route)) in body
        assert offered is may_open, (
            f"{role} {'may' if may_open else 'may NOT'} open {screen}, "
            f"but the guide {'offers' if offered else 'omits'} its button"
        )
