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

import re
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


# The registration-and-payment guide
# ---------------------------------------------------------------------------
#: The eight steps the client approved, in the demo's own order, transcribed
#: from its js/pages-core.js rather than from the view — so a step renamed,
#: reordered or dropped fails against the demo and not against a copy of the
#: page's own opinion.
DEMO_EIGHT_STEPS = [
    "الإعلان عن البرنامج المعتمد",
    "الطالب يقدّم طلب التحاق",
    "تنسيب مدير المركز للدائرة المالية",
    "الطالب يدفع في الدائرة المالية",
    "الطالب يستلم سند القبض",
    "يعود للمركز ويقدّم الوصل",
    "المركز يعتمد التسجيل",
    "رفع اسم المتدرب لنظام الوزارة",
]

#: Each step's button and the screen that earns it. Step 6 points at the
#: enrolments screen, not the demo's payments: ``voucher_received`` lives on
#: the enrolment here, and a map that names the wrong door is worse than none.
GUIDE_STAGE_SCREENS = [
    ("operations:mohe", "mohe"),
    ("people:participant-new", "student-new"),
    ("operations:enrollments", "enrollments"),
    ("cashbox:payment-new", "payment-new"),
    ("cashbox:payments", "payments"),
    ("operations:mohe-submit", "mohe-submit"),
]

#: The paths that open after approval — a separate section, not a ninth step.
GUIDE_LATER_SCREENS = [
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


def _primary_section(body: str) -> str:
    """Only the eight. The later-paths section must not answer for them."""
    return body.split("الخطوات الثماني للتسجيل والدفع", 1)[1].split("بعد اعتماد التسجيل", 1)[0]


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
def test_the_guide_carries_the_eight_steps_the_client_approved(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The promise the client signed off is a registration-and-payment map of
    eight steps. This page was once rebuilt around a broader participant
    lifecycle, which is a different screen wearing the same name.

    All eight, in the demo's order, for every reader — and numbered 1…8 so the
    sequence is read rather than inferred.
    """
    import re

    client.force_login(_user(role, f"guide.eight.{role.lower()}"))

    primary = _primary_section(
        client.get(reverse("operations:enroll-flow")).content.decode("utf-8")
    )
    drawn = re.findall(r'<span class="step-num"[^>]*>(\d+)</span>\s*<h3>([^<]+)</h3>', primary)

    assert [title.strip() for _n, title in drawn] == DEMO_EIGHT_STEPS
    assert [n for n, _t in drawn] == [str(i) for i in range(1, 9)]


@pytest.mark.parametrize("role", GUIDE_READERS)
def test_the_later_paths_are_a_section_of_their_own_not_a_ninth_step(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    Transfers, special cases, the closing, the clearance and the certificate
    are real and belong on the page — underneath, labelled, and drawn as cards
    rather than as numbered steps, so nobody reads them as part of the eight.
    """
    client.force_login(_user(role, f"guide.later.{role.lower()}"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")
    later = body.split("بعد اعتماد التسجيل", 1)[1]

    assert "مسارات لاحقة عند الحاجة" in later
    for title in ("النقل بين الدورات", "الحالات الخاصة", "براءة الذمة", "الشهادة"):
        assert title in later
        assert title not in _primary_section(body), f"«{title}» leaked into the eight"
    # Cards, not steps: the eight own the numbered treatment.
    assert 'class="act-card' in later
    assert 'class="step-num"' not in later


@pytest.mark.parametrize("role", GUIDE_READERS)
def test_the_guide_explains_every_step_to_every_reader(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    Filtering the buttons must not filter the teaching. A registrar who may not
    open the till still has to know that a payment happens, who takes it and
    what it unblocks — otherwise the guide stops guiding exactly the people who
    most need one.
    """
    client.force_login(_user(role, f"guide.copy.{role.lower()}"))

    primary = _primary_section(
        client.get(reverse("operations:enroll-flow")).content.decode("utf-8")
    )

    for step in DEMO_EIGHT_STEPS:
        assert step in primary, f"{role} is not told about «{step}»"


def test_the_payment_step_still_carries_the_minimum_first_payment_rule(
    client: Client, seeded_settings: None
) -> None:
    """
    BR-020 is the rule a cashier meets first, and the reason a payment is
    refused at the counter. The claim has to stay exact: diplomas only, the
    first payment only, the minimum read from an effective-dated setting, and a
    per-programme override that beats it (Q-15).
    """
    client.force_login(_user(Role.CENTER_MANAGER, "guide.br020"))

    primary = _primary_section(
        client.get(reverse("operations:enroll-flow")).content.decode("utf-8")
    )
    step = primary.split("الطالب يدفع في الدائرة المالية", 1)[1]

    assert "BR-020" in step
    assert "diploma_minimum_first_payment" in step
    assert "دبلوم" in step
    assert "الدفعة الأولى" in step
    assert "Q-15" in step
    # …and the rule that keeps the manager out of the till.
    assert "BR-081" in step


def test_the_guide_shows_the_registrar_the_admission_form_and_not_the_till(
    client: Client, seeded_settings: None
) -> None:
    """One reader, spelled out: what the registrar is and is not offered."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "guide.reg.named"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")

    assert _stage_button(reverse("people:participant-new")) in body
    assert _stage_button(reverse("operations:enrollments")) in body
    # D-06 — the registrar takes no cash, so the till is named and not opened.
    assert _stage_button(reverse("cashbox:payment-new")) not in body


@pytest.mark.parametrize("role", GUIDE_READERS)
def test_the_guided_flow_never_offers_a_step_the_reader_may_not_open(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The guide sends a new employee to the screen for each step, and an early
    version linked every step for everyone who could open the page. Four roles
    can, and none may open all of them: §3.4/17 keeps the manager, the
    registrar and the audit account out of cash collection — D-01 makes BR-081
    an explicit deny rather than a missing grant — and §3.3/14 keeps the
    finance officer out of the ministry file.

    A step the reader may not open keeps its explanation and loses its button.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"guide.{role.lower()}"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")

    for route, screen in GUIDE_STAGE_SCREENS + GUIDE_LATER_SCREENS:
        may_open = Action.VIEW in allowed_actions(role, screen)
        offered = _stage_button(reverse(route)) in body or (
            f'<a class="act-card" href="{reverse(route)}">' in body
        )
        assert offered is may_open, (
            f"{role} {'may' if may_open else 'may NOT'} open {screen}, "
            f"but the guide {'offers' if offered else 'omits'} its button"
        )


# ---------------------------------------------------------------------------
# The four screens the demo had and the project did not — Sprint 8K-3
# ---------------------------------------------------------------------------
#: route → (screen that guards it, the screens it offers buttons to)
GUIDED_SCREENS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "operations:special-cases": (
        "special-cases",
        [
            ("operations:enrollments", "enrollments"),
            ("operations:transfers", "transfers"),
            ("operations:clearances", "clearance"),
        ],
    ),
    "settlements:entitlement": (
        "entitlement",
        [
            ("partners:agreements", "agreements"),
            ("settlements:claims", "claims"),
            ("settlements:settlements", "settlements"),
            ("settlements:obligations", "obligations"),
            ("settlements:absences", "obligations"),
        ],
    ),
    "people:settings": ("settings", []),
    "people:future": ("settings", []),
}

ALL_ROLES = [
    Role.CENTER_MANAGER,
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
    Role.AUDIT_ACCOUNT,
]

#: Classes the first 8K-1 draft copied from the demo's stylesheet. None of them
#: is defined in this project's CSS, so anything using one renders unstyled.
DEAD_DEMO_CLASSES = ["vflow", "vstep", "vtitle", "vmeta", "vbox", "mini-title", "stack-list"]

GUIDED_TEMPLATES = [
    "templates/operations/special_cases.html",
    "templates/settlements/entitlement.html",
    "templates/core/settings.html",
    "templates/core/future.html",
    "templates/operations/enroll_flow.html",
]


@pytest.mark.parametrize("route", sorted(GUIDED_SCREENS))
@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_guided_screens_open_for_exactly_the_roles_the_matrix_allows(
    client: Client, seeded_settings: None, route: str, role: str
) -> None:
    """Hiding a menu entry is courtesy; this is the control (BR-080)."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    screen, _links = GUIDED_SCREENS[route]
    may_view = Action.VIEW in allowed_actions(role, screen)
    client.force_login(_user(role, f"g3.{role.lower()}.{route.replace(':', '.')}"))

    response = client.get(reverse(route))

    assert response.status_code == (200 if may_view else 403)


@pytest.mark.parametrize("route", sorted(GUIDED_SCREENS))
def test_the_guided_screens_refuse_an_anonymous_visitor(
    client: Client, seeded_settings: None, route: str
) -> None:
    """Fail-closed: none of the four is public, however read-only it is."""
    assert client.get(reverse(route)).status_code == 403


@pytest.mark.parametrize("route", sorted(GUIDED_SCREENS))
@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_guided_screens_offer_no_button_the_reader_may_not_follow(
    client: Client, seeded_settings: None, route: str, role: str
) -> None:
    """
    Every button on these pages leaves for another screen, and a button the
    reader may not follow ends in a refusal plus a DENIED_ATTEMPT row (BR-085).
    The gate is asserted in both directions so a matrix change cannot silently
    turn a working button into a trap — or hide one that should be there.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    screen, links = GUIDED_SCREENS[route]
    if Action.VIEW not in allowed_actions(role, screen):
        pytest.skip("cannot open the page at all")
    client.force_login(_user(role, f"g3b.{role.lower()}.{route.replace(':', '.')}"))

    body = client.get(reverse(route)).content.decode("utf-8")

    for target_route, target_screen in links:
        may_follow = Action.VIEW in allowed_actions(role, target_screen)
        offered = f'<a class="btn2 sm" href="{reverse(target_route)}">' in body
        assert offered is may_follow, (
            f"{role} {'may' if may_follow else 'may NOT'} open {target_screen}, "
            f"but {route} {'offers' if offered else 'omits'} its button"
        )


@pytest.mark.parametrize("template", GUIDED_TEMPLATES)
def test_the_guided_screens_use_no_class_this_project_never_defined(template: str) -> None:
    """
    8K-1 built these pages with markup lifted from the demo's stylesheet, so
    ``vflow``/``vstep``/``vtitle``/``vbox`` and friends rendered as bare divs
    here. The project has its own stepper; a class that exists in neither
    ``input.css`` nor the built sheet is a silent styling hole.
    """
    from pathlib import Path

    source = Path(template).read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    for dead in DEAD_DEMO_CLASSES:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from DEAD_DEMO_CLASSES"
        assert dead not in source, f"{template} still uses the demo-only class «{dead}»"


@pytest.mark.parametrize(
    ("route", "role", "phrase"),
    [
        ("operations:special-cases", Role.CENTER_MANAGER, "صفحة إرشادية"),
        ("settlements:entitlement", Role.FINANCE_OFFICER, "صفحة دليل"),
        ("people:settings", Role.FINANCE_OFFICER, "قراءة فقط"),
        ("people:future", Role.CENTER_MANAGER, "هذه ليست نواقص"),
    ],
)
def test_each_guided_screen_says_out_loud_what_it_is(
    client: Client, seeded_settings: None, route: str, role: str, phrase: str
) -> None:
    """A read-only page that does not say so reads as a broken functional one."""
    client.force_login(_user(role, f"g3c.{role.lower()}.{route.replace(':', '.')}"))

    body = client.get(reverse(route)).content.decode("utf-8")

    assert phrase in body


# ---------------------------------------------------------------------------
# The delivery-review page — Sprint 8K-4
# ---------------------------------------------------------------------------
#: The demo's sidebar, flattened from ``const NAV`` in its own js/app.js and
#: written out here independently of the view. That independence is the point:
#: a row reordered, dropped or invented in ``_COVERAGE_ROWS`` fails against
#: this list rather than against a copy of itself.
DEMO_SIDEBAR_ORDER = [
    "لوحة المؤشرات",
    "مسار التسجيل والدفع",
    "المشاركون",
    "طلب التحاق جديد",
    "التسجيلات",
    "النقل بين الدورات",
    "الحالات الخاصة",
    "الدبلومات التدريبية",
    "الدورات القصيرة",
    "الدورات الأونلاين",
    "الدفعات المُشغّلة",
    "قوائم الأسعار المؤرّخة",
    "اعتماد الوزارة",
    "الدفعات وسندات القبض",
    "استيفاء دفعة",
    "الإقفال اليومي",
    "الخصومات",
    "الاستردادات",
    "الرسوم الإضافية",
    "المصروفات",
    "الشركاء المتعاقدون",
    "الاتفاقيات",
    "محرّر اتفاقية",
    "استحقاق الشركاء",
    "المطالبات",
    "المخالصات",
    "التزامات الشركاء",
    "براءة الذمة",
    "الشهادات",
    "التقارير",
    "المستخدمون والصلاحيات",
    "سجل التدقيق",
    "الإعدادات",
    "ترحيل البيانات",
    "مصفوفة تغطية المتطلبات",
    "النطاق المستقبلي",
]

COVERAGE_ROW = re.compile(
    r'<td class="num">\d+</td>\s*<td>([^<]+)</td>\s*<td dir="ltr">([^<]+)</td>\s*'
    r"<td>([^<]+)</td>\s*"
    r'<td><span class="chip ([a-z]*) dot">([^<]+)</span>'
)


def _coverage_rows(client: Client, role: str = Role.CENTER_MANAGER) -> list[tuple[str, ...]]:
    """The parity table's rows, parsed out of the page the reviewer sees.

    Scoped to the matrix card: the sidebar carries the very same Arabic labels,
    so a plain ``label in body`` would pass on the menu and prove nothing.
    """
    client.force_login(_user(role, f"cov.{role.lower()}"))
    body = client.get(reverse("people:coverage")).content.decode("utf-8")
    table = body.split("المصفوفة — بترتيب قائمة الديمو", 1)[1]
    table = table.split("شاشات في النظام خارج", 1)[0]
    return COVERAGE_ROW.findall(table)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.CENTER_MANAGER, True),
        (Role.FINANCE_OFFICER, True),
        (Role.AUDIT_ACCOUNT, True),
        (Role.REGISTRATION_OFFICER, False),
        (Role.FINANCE_MANAGER, False),
        (Role.CASHIER, False),
    ],
)
def test_the_coverage_matrix_opens_for_exactly_the_roles_the_matrix_allows(
    client: Client, seeded_settings: None, role: str, allowed: bool
) -> None:
    """It is guarded by §3.7/35 like the other review screens."""
    client.force_login(_user(role, f"cov.open.{role.lower()}"))

    response = client.get(reverse("people:coverage"))

    assert response.status_code == (200 if allowed else 403)


def test_the_coverage_matrix_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """It names every screen the system has; it is not a public document."""
    assert client.get(reverse("people:coverage")).status_code == 403


def test_the_coverage_matrix_lists_every_demo_sidebar_item_in_the_demo_order(
    client: Client, seeded_settings: None
) -> None:
    """The whole promise of the page: nothing dropped, nothing reordered."""
    listed = [row[0] for row in _coverage_rows(client)]

    assert listed == DEMO_SIDEBAR_ORDER


def test_the_coverage_matrix_separates_project_extras_from_demo_parity(
    client: Client, seeded_settings: None
) -> None:
    """
    Screens the demo never showed sit in their own table. Folding them into the
    parity count would inflate it with things nobody asked to see covered.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "cov.extras"))

    body = client.get(reverse("people:coverage")).content.decode("utf-8")
    parity, extras = body.split("شاشات في النظام خارج", 1)

    assert "إضافة في النظام الحقيقي" in extras
    assert "إضافة في النظام الحقيقي" not in parity.split("المصفوفة — بترتيب", 1)[1]


def test_the_coverage_matrix_does_not_call_every_row_done(
    client: Client, seeded_settings: None
) -> None:
    """
    A page that marks all 36 «منفذ» is a page nobody can trust. Three rows are
    guided pages and one waits on a client decision, and the table has to show
    that difference rather than round it up.
    """
    statuses = [row[4] for row in _coverage_rows(client)]

    assert len(set(statuses)) >= 3, f"only {set(statuses)} used — the page rounds up"
    assert statuses.count("منفذ") < len(statuses)
    assert "إرشادي" in statuses
    assert "بانتظار قرار" in statuses


def test_the_coverage_matrix_gives_each_status_its_own_chip(
    client: Client, seeded_settings: None
) -> None:
    """Status has to be readable at a glance, not by reading every note."""
    rows = _coverage_rows(client)
    pairs = {status: chip for _demo, _path, _kind, chip, status in rows}

    assert len(set(pairs.values())) == len(pairs), f"two statuses share a chip: {pairs}"
    assert pairs["منفذ"] == "ok"
    assert pairs["إرشادي"] == "brand"
    assert pairs["بانتظار قرار"] == "warn"


def test_every_route_the_coverage_matrix_names_still_reverses() -> None:
    """
    The page prints an address for each row, so a renamed route would leave a
    claim on screen that no longer resolves. ``reverse`` here names the offender.
    """
    from apps.people.views import _COVERAGE_ROWS, _EXTRA_ROWS

    for _demo, route, _kind, _status, _note in _COVERAGE_ROWS:
        assert reverse(route), route
    for _screen, route, _tag, _note in _EXTRA_ROWS:
        assert reverse(route), route


def test_the_coverage_matrix_uses_no_class_this_project_never_defined() -> None:
    """8K-1 built this page with ``tbl compact``, which is defined nowhere here."""
    from pathlib import Path

    source = Path("templates/core/coverage.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    for dead in [*DEAD_DEMO_CLASSES, "compact", "mono"]:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from the dead list"
        assert dead not in source, f"coverage.html still uses the demo-only class «{dead}»"


# ---------------------------------------------------------------------------
# The shared guided-help block — Sprint 8K-5
# ---------------------------------------------------------------------------
#: (route, guidance key, screen that guards the route) for every screen the
#: sprint asked to be taught. Written out here rather than derived from the
#: registry, so a template that quietly loses its tag is caught.
GUIDED_HELP_SCREENS = [
    ("operations:dashboard", "dashboard", "dashboard"),
    ("people:participants", "participants", "students"),
    ("people:participant-new", "participant-new", "student-new"),
    ("operations:enrollments", "enrollments", "enrollments"),
    ("operations:enroll-flow", "enroll-flow", "enroll-flow"),
    ("cashbox:payments", "payments", "payments"),
    ("cashbox:payment-new", "payment-new", "payment-new"),
    ("cashbox:closing", "cashbox-closing", "closing"),
    # Sprint 8I-1 — the discounts register joined the taught set with its
    # own polish slice.
    ("billing:discounts", "discounts", "discounts"),
    ("billing:refunds", "refunds", "refunds"),
    ("billing:extra-fees", "extra-fees", "extra-fees"),
    ("expenses:expenses", "expenses", "expenses"),
    ("operations:transfers", "transfers", "transfers"),
    ("operations:transfer-new", "transfer-new", "transfer-new"),
    ("operations:special-cases", "special-cases", "special-cases"),
    ("catalog:programs", "programs", "programs"),
    ("catalog:short-courses", "short-courses", "short-courses"),
    ("catalog:online-courses", "online-courses", "online-courses"),
    # Sprint 8I-1 — the six polished screens joined the taught set. The two
    # detail pages need an object to open and are proved next to their own
    # fixtures in ``tests/test_ui_regressions.py``.
    ("catalog:pricelists", "pricelists", "pricelists"),
    ("operations:cohorts", "cohorts", "cohorts"),
    ("operations:mohe", "mohe", "mohe"),
    ("operations:mohe-submit", "mohe-submit", "mohe-submit"),
    ("operations:clearances", "clearance", "clearance"),
    ("operations:certificates", "certificates", "certificates"),
    ("partners:partners", "partners", "partners"),
    ("partners:agreements", "agreements", "agreements"),
    ("partners:agreement-new", "agreement-new", "agreement-new"),
    ("settlements:entitlement", "entitlement", "entitlement"),
    ("settlements:claims", "claims", "claims"),
    ("settlements:settlements", "settlements", "settlements"),
    ("settlements:obligations", "obligations", "obligations"),
    ("reporting:reports", "reports", "reports"),
    ("people:settings", "settings", "settings"),
    ("people:coverage", "coverage", "settings"),
    ("people:future", "future", "settings"),
]


def _a_role_that_may_open(screen: str) -> str:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in ALL_ROLES:
        if Action.VIEW in allowed_actions(role, screen):
            return role
    raise AssertionError(f"no role may VIEW {screen}")


@pytest.mark.parametrize(("route", "key", "screen"), GUIDED_HELP_SCREENS)
def test_the_guided_help_reaches_every_screen_it_was_asked_to(
    client: Client, seeded_settings: None, route: str, key: str, screen: str
) -> None:
    """
    The demo taught on every screen; the real system now does too. A template
    that loses ``{% guided_help %}`` leaves its readers with a correct form and
    no idea which button is theirs, and that is what this catches.
    """
    from apps.people.guidance import GUIDES

    role = _a_role_that_may_open(screen)
    client.force_login(_user(role, f"gh.{key}.{role.lower()}"))

    response = client.get(reverse(route))

    assert response.status_code == 200, f"{route} did not open for {role}"
    body = response.content.decode("utf-8")
    assert str(GUIDES[key].what) in body, f"{route} renders no guided help"
    assert "من يستخدمها" in body


@pytest.mark.parametrize(("route", "key", "screen"), GUIDED_HELP_SCREENS)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_guided_help_never_points_a_reader_at_a_refusal(
    client: Client, seeded_settings: None, route: str, key: str, screen: str, role: str
) -> None:
    """
    The prose is for everyone and the links are not. A next-step link the reader
    may not follow ends in a refusal and a DENIED_ATTEMPT row (BR-085) for doing
    exactly what the help said — so the gate is asserted in both directions.
    """
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    if Action.VIEW not in allowed_actions(role, screen):
        pytest.skip("cannot open the page at all")
    guide = GUIDES[key]
    if not guide.links:
        pytest.skip("this screen offers no next step")
    client.force_login(_user(role, f"ghl.{key}.{role.lower()}"))

    body = client.get(reverse(route)).content.decode("utf-8")
    help_block = body.split("من يستخدمها", 1)[1].split("</div>", 2)[0]

    for target_screen, target_route, label in guide.links:
        may_follow = Action.VIEW in allowed_actions(role, target_screen)
        offered = f'<a href="{reverse(target_route)}">{label}</a>' in help_block
        assert offered is may_follow, (
            f"{role} {'may' if may_follow else 'may NOT'} open {target_screen}, "
            f"but the help on {route} {'offers' if offered else 'omits'} «{label}»"
        )


def test_the_guided_help_explains_itself_to_readers_who_get_no_links(
    client: Client, seeded_settings: None
) -> None:
    """
    Filtering the links must not filter the teaching. The registrar may not open
    the till (D-06), and still has to learn from the enrolments screen that a
    payment comes next and who takes it.
    """
    from apps.people.guidance import GUIDES

    client.force_login(_user(Role.REGISTRATION_OFFICER, "gh.reg.noLinks"))

    body = client.get(reverse("operations:enrollments")).content.decode("utf-8")

    assert str(GUIDES["enrollments"].what) in body
    assert str(GUIDES["enrollments"].after) in body
    # …but the till itself is not offered.
    assert f'<a href="{reverse("cashbox:payment-new")}">' not in body


def test_the_guided_help_partial_uses_no_class_this_project_never_defined() -> None:
    """One partial on twenty-three screens: a dead class here is a hole on all."""
    from pathlib import Path

    source = Path("templates/partials/_guided_help.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    for dead in [*DEAD_DEMO_CLASSES, "compact", "mono"]:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from the dead list"
        assert dead not in source, f"the guided-help partial uses «{dead}»"
    for used in ["note", "hint", "chip"]:
        assert f".{used}" in css, f"the partial leans on «{used}», which CSS must define"


def test_the_guided_help_stays_calm() -> None:
    """
    Colour is for the moment something is actually refused, not for explaining
    that a rule exists. The block is neutral; a screen that needs amber says so
    on its own body, where the refusal happens.
    """
    from pathlib import Path

    source = Path("templates/partials/_guided_help.html").read_text(encoding="utf-8")

    assert "note warn" not in source
    assert "note danger" not in source


# ---------------------------------------------------------------------------
# Delivery gate — Sprint 8K-6
# ---------------------------------------------------------------------------
#: Every screen a client demo actually walks through, and the screen permission
#: that guards it. One table, checked against every role and against nobody at
#: all, so «it worked when I clicked it» is not the evidence we ship on.
DELIVERY_ROUTES = [
    ("operations:dashboard", "dashboard"),
    ("operations:enroll-flow", "enroll-flow"),
    ("people:participants", "students"),
    ("people:participant-new", "student-new"),
    ("operations:enrollments", "enrollments"),
    ("operations:transfers", "transfers"),
    ("operations:special-cases", "special-cases"),
    ("catalog:programs", "programs"),
    ("catalog:pricelists", "pricelists"),
    ("operations:mohe", "mohe"),
    ("cashbox:payments", "payments"),
    ("cashbox:payment-new", "payment-new"),
    ("cashbox:closing", "closing"),
    ("partners:partners", "partners"),
    ("partners:agreements", "agreements"),
    ("settlements:entitlement", "entitlement"),
    ("settlements:claims", "claims"),
    ("settlements:settlements", "settlements"),
    ("settlements:obligations", "obligations"),
    ("operations:clearances", "clearance"),
    ("operations:certificates", "certificates"),
    ("reporting:reports", "reports"),
    ("people:settings", "settings"),
    ("people:coverage", "settings"),
    ("people:future", "settings"),
]


@pytest.mark.parametrize(("route", "screen"), DELIVERY_ROUTES)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_demo_screen_answers_exactly_the_roles_the_matrix_allows(
    client: Client, seeded_settings: None, route: str, screen: str, role: str
) -> None:
    """
    The whole delivery, one assertion: 200 where the matrix grants VIEW and 403
    where it does not — on a database seeded with settings and nothing else, so
    an empty screen still has to render rather than fall over on no rows.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    may_view = Action.VIEW in allowed_actions(role, screen)
    client.force_login(_user(role, f"gate.{role.lower()}.{route.replace(':', '.')}"))

    response = client.get(reverse(route))

    assert response.status_code == (200 if may_view else 403), (
        f"{role} on {route}: matrix says VIEW={may_view}, screen said {response.status_code}"
    )


@pytest.mark.parametrize(("route", "screen"), DELIVERY_ROUTES)
def test_no_demo_screen_opens_for_a_visitor_who_never_signed_in(
    client: Client, seeded_settings: None, route: str, screen: str
) -> None:
    """Fail-closed, on every screen the demo shows — Q-12, and no exceptions."""
    assert client.get(reverse(route)).status_code == 403


def test_the_project_adds_screens_the_demo_never_had_and_says_so(
    client: Client, seeded_settings: None
) -> None:
    """
    Parity is not the whole delivery: four screens exist that the demo never
    showed. They are real, they are reachable, and the coverage matrix lists
    them apart from the parity table so the 36 cannot be quietly inflated.
    """
    from apps.people.views import _COVERAGE_ROWS, _EXTRA_ROWS

    demo_labels = {row[0] for row in _COVERAGE_ROWS}
    additions = {row[0] for row in _EXTRA_ROWS if "إضافة" in str(row[2])}

    assert additions == {
        "نموذج الإرسال للوزارة",
        "الأرصدة الافتتاحية",
        "غيابات المدربين",
        "ربط السجلات التاريخية",
    }
    assert not (additions & demo_labels), "an addition is being counted as demo parity"

    client.force_login(_user(Role.CENTER_MANAGER, "gate.additions"))
    body = client.get(reverse("people:coverage")).content.decode("utf-8")
    for addition in additions:
        assert addition in body


def test_the_project_navigation_still_carries_every_demo_sidebar_item() -> None:
    """
    The sidebar is what the client recognises. Checked against the same list
    the coverage matrix is checked against, so nav and matrix cannot drift
    apart without one of them failing.
    """
    from apps.people import nav

    labels = [str(item.label) for group in nav.NAV for item in group.items]

    for demo_item in DEMO_SIDEBAR_ORDER:
        assert demo_item in labels, f"«{demo_item}» left the sidebar"
    assert [label for label in labels if label in DEMO_SIDEBAR_ORDER] == DEMO_SIDEBAR_ORDER, (
        "the sidebar no longer follows the demo's order"
    )


def test_the_readme_names_no_screen_the_system_does_not_have() -> None:
    """
    The runbook is read aloud in front of a client, so a renamed screen there
    is a stumble in the demo itself. Sprint 8K-1 renamed «تسجيل اتفاقية موقّعة»
    to «محرّر اتفاقية» and took «شريك جديد» and «طلب نقل جديد» out of the
    sidebar, and the runbook went on naming all three for five commits.

    Every Arabic name the README quotes in backticks must be a live menu entry.
    """
    from pathlib import Path

    from apps.people import nav

    labels = {str(item.label) for group in nav.NAV for item in group.items}
    readme = Path("README.md").read_text(encoding="utf-8")
    quoted = set(re.findall(r"`([؀-ۿ][^`]*)`", readme))

    unknown = sorted(name for name in quoted if name not in labels)
    assert not unknown, f"README names screens that are not in the menu: {unknown}"


def test_the_readme_no_longer_calls_the_built_screens_missing() -> None:
    """
    §2.4 listed «الحالات الخاصة · شاشة الإعدادات · شاشة الاستحقاق» as
    «غير منفَّذة». All three were built in 8K-3, and a README that still calls
    them absent talks the delivery down in front of the client.
    """
    from pathlib import Path

    readme = Path("README.md").read_text(encoding="utf-8")

    assert "غير منفَّذة" not in readme
    for built in ("الحالات الخاصة", "استحقاق الشركاء", "الإعدادات"):
        assert built in readme, f"«{built}» is built and the README should say what it is"
    # …and it must not oversell them either.
    assert "لا إدخال منها" in readme
    assert "لا تحتسب مبلغاً" in readme
    assert "لا تعديل" in readme


# ---------------------------------------------------------------------------
# The dashboard — page polish phase
# ---------------------------------------------------------------------------
#: Every counter the dashboard can draw, and the screen whose permission earns
#: it. A number appears when its screen does, and never otherwise.
DASHBOARD_METRICS = [
    ("أرصدة غير مسوّاة", "enrollments"),
    ("التسجيلات", "enrollments"),
    ("بانتظار الوصل", "enrollments"),
    ("الدفعات المُشغّلة", "cohorts"),
    ("سندات اليوم", "payments"),
    ("سندات لم تدخل إقفالاً", "payments"),
    ("إقفالات لم تُعتمد", "closing"),
    ("طلبات نقل قائمة", "transfers"),
    ("براءات ذمة قائمة", "clearance"),
    ("مطالبات بانتظار الاعتماد", "claims"),
]

#: The "start here" buttons, and the screen each opens.
DASHBOARD_ACTIONS = [
    ("people:participant-new", "student-new"),
    ("cashbox:payment-new", "payment-new"),
    ("operations:transfer-new", "transfer-new"),
]


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_opens_for_every_business_role(
    client: Client, seeded_settings: None, role: str
) -> None:
    """§3.1/1 grants VIEW to all six. It is the screen every login lands on."""
    client.force_login(_user(role, f"dash.open.{role.lower()}"))

    assert client.get(reverse("operations:dashboard")).status_code == 200


def test_the_dashboard_refuses_the_system_administrator_on_purpose(
    client: Client, seeded_settings: None
) -> None:
    """
    Δ-02 — the system administrator is a technical role outside the business
    matrix: users and the audit trail, nothing financial and nothing
    operational. So the dashboard refuses them, and ``is_superuser`` does not
    change that. The role is the authority, not the Django flag (T-165).

    Pinned because it reads like a bug the first time somebody meets it.
    """
    from apps.people.models import Role

    admin = _user(Role.SYSTEM_ADMINISTRATOR, "dash.sysadmin")
    admin.is_superuser = True
    admin.save(update_fields=["is_superuser"])
    client.force_login(admin)

    assert client.get(reverse("operations:dashboard")).status_code == 403


def test_the_dashboard_refuses_an_anonymous_visitor(client: Client, seeded_settings: None) -> None:
    """Fail-closed on the first screen after login, like every other."""
    assert client.get(reverse("operations:dashboard")).status_code == 403


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_counts_only_screens_the_reader_may_open(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    A counter is a promise that the rows behind it can be reached — every card
    is a link to them. Showing one for a screen the reader may not open both
    leaks a number and sends them into a refusal (BR-085).

    Asserted in both directions: absent when forbidden, and the reader's own
    screens still counted when allowed.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"dash.metrics.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    for label, screen in DASHBOARD_METRICS:
        if Action.VIEW in allowed_actions(role, screen):
            continue
        assert label not in body, f"{role} may not open {screen} but is shown «{label}»"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_offers_no_action_the_reader_may_not_take(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    «ابدأ من هنا» is the one place on the screen that starts work rather than
    reporting it. D-01 keeps the manager out of «استيفاء دفعة» — BR-081 is an
    explicit deny, not a missing grant — so the manager's dashboard must not
    offer it however senior the account is.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"dash.acts.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    for route, screen in DASHBOARD_ACTIONS:
        may = Action.VIEW in allowed_actions(role, screen)
        offered = f'href="{reverse(route)}"' in body
        assert offered is may, (
            f"{role} {'may' if may else 'may NOT'} open {screen}, "
            f"but the dashboard {'offers' if offered else 'omits'} its button"
        )


def test_the_dashboard_shows_the_manager_no_till_button(
    client: Client, seeded_settings: None
) -> None:
    """BR-081 · D-01 spelled out, because it is the one that surprises people."""
    client.force_login(_user(Role.CENTER_MANAGER, "dash.mgr.notill"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    assert f'href="{reverse("people:participant-new")}"' in body
    assert f'href="{reverse("cashbox:payment-new")}"' not in body


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_renders_on_a_database_with_no_work_in_it(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    Seeded settings and nothing else — no enrolment, no receipt, no cohort.
    Every counter is zero and every queue is empty, and the screen still has to
    say something useful rather than fall over or show a wall of noughts.
    """
    client.force_login(_user(role, f"dash.empty.{role.lower()}"))

    response = client.get(reverse("operations:dashboard"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    # A queue with nothing in it draws no line at all.
    for label, _screen in DASHBOARD_METRICS:
        if label in ("أرصدة غير مسوّاة", "التسجيلات", "الدفعات المُشغّلة", "سندات اليوم"):
            continue
        assert label not in body, f"empty queue «{label}» drew a line anyway"
    assert "لا شيء ينتظر قراراً" in body or "لا عدّادات ضمن صلاحيات دورك" in body


def test_the_dashboard_keeps_its_responsive_scaffolding() -> None:
    """
    One column on a phone, two on a wide screen, and nothing hand-rolled.

    The queue is a few short lines and used to stretch the whole width, which
    left a gap nobody reads. From ``lg`` it sits beside the counters instead.
    Below that it stacks: splitting a 700px viewport two ways would give the
    queue a 240px column, which is worse than stacking, not better.
    """
    from pathlib import Path

    source = Path("templates/operations/dashboard.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        return css.split(selector, 1)[1].split("}", 1)[0]

    # The counters keep their own grid, defined once.
    assert 'class="kpi-grid"' in source
    assert "sm:grid-cols-2" in rule(".kpi-grid")
    assert "xl:grid-cols-4" in rule(".kpi-grid")

    # Two columns, and only from lg — never below it.
    assert 'class="dash-cols"' in source
    assert 'class="dash-main"' in source
    assert 'class="dash-side"' in source
    assert "lg:grid-cols-3" in rule(".dash-cols")
    assert "lg:col-span-2" in rule(".dash-main ")
    for selector in (".dash-cols", ".dash-main ", ".dash-side"):
        assert "md:" not in rule(selector), f"{selector} splits before lg"

    # Grid children need an explicit zero minimum or long content bursts the
    # column instead of scrolling inside it.
    assert "min-w-0" in rule(".dash-main ")
    assert "min-w-0" in rule(".dash-side")

    # Four counters squeezed into two-thirds of the width are unreadable.
    assert "xl:grid-cols-2" in rule(".dash-main .kpi-grid")

    # The action zone is a card grid now, and it collapses the same way:
    # one column on a phone, two on a tablet, three on a desktop.
    assert 'class="act-grid"' in source
    assert "sm:grid-cols-2" in rule(".act-grid")
    assert "lg:grid-cols-3" in rule(".act-grid")

    # The workflow strip wraps rather than scrolling sideways, and each stop
    # keeps a floor width so six of them never shred into slivers.
    assert 'class="flow"' in source
    assert "flex-wrap" in rule(".flow")
    assert "9rem" in rule(".flow li")


def test_the_dashboard_layout_classes_survived_the_css_build() -> None:
    """
    Tailwind drops a component class no template mentions. Adding the rule and
    rebuilding BEFORE the markup existed purged all four of these once already,
    so the built sheet — not the source — is what this asserts.
    """
    from pathlib import Path

    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for name in (
        "dash-cols",
        "dash-main",
        "dash-side",
        "sec-title",
        "bar",
        "chart-row",
        "act-grid",
        "act-card",
        "flow",
    ):
        assert f".{name}" in built, f"«{name}» is not in the built stylesheet — rebuild CSS"


def test_the_dashboard_uses_no_class_this_project_never_defined() -> None:
    """The polish phase may not reintroduce the demo's stylesheet."""
    from pathlib import Path

    source = Path("templates/operations/dashboard.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    for dead in [*DEAD_DEMO_CLASSES, "compact", "mono"]:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from the dead list"
        assert dead not in source, f"dashboard.html uses the demo-only class «{dead}»"
    assert "style=" not in source, "inline styles were removed in 8J-5 and do not come back"


# ---------------------------------------------------------------------------
# The dashboard's bars — page polish phase
# ---------------------------------------------------------------------------
@pytest.fixture
def mixed_enrollments(settled_enrollment: Any) -> Any:
    """
    Two enrolments in two different states.

    One is not enough any more: a distribution of a single category is a
    restatement of the counter beside it, so the view suppresses it. A chart
    needs something to compare against before it can be tested.
    """
    from apps.operations.models import Cohort, Enrollment

    # Q-19 — one enrolment per participant per cohort, so the second one needs
    # a cohort of its own rather than a second seat in the first.
    second = Cohort.objects.create(
        code="CO-DEMO-2",
        program=settled_enrollment.cohort.program,
        semester=settled_enrollment.cohort.semester,
        name_ar="دفعة العرض الثانية",
        starts_on=settled_enrollment.cohort.starts_on,
        ends_on=settled_enrollment.cohort.ends_on,
        capacity=25,
    )
    Enrollment.objects.create(
        code="EN-DEMO-2",
        participant=settled_enrollment.participant,
        cohort=second,
        enrolled_on=settled_enrollment.enrolled_on,
        price_list=settled_enrollment.price_list,
        status="ACTIVE",
    )
    return settled_enrollment


def test_the_dashboard_bar_never_overflows_or_divides_by_zero() -> None:
    """
    A bar is N cells with the first K filled, and K is integer arithmetic all
    the way down — A-01b keeps ``float()`` out of application code and a bar has
    no business being where it creeps back in.

    Three edges matter: a scale of zero must not raise, a value above its scale
    must clamp instead of spilling, and one-in-forty must still light a cell
    rather than rounding away to an empty strip beside a full one.
    """
    from apps.operations.views import DASHBOARD_BAR_SEGMENTS as N
    from apps.operations.views import _bar

    assert _bar(0, 0) == [False] * N
    assert _bar(5, 0) == [False] * N
    assert _bar(0, 10) == [False] * N
    assert sum(_bar(1, 40)) == 1
    assert sum(_bar(40, 40)) == N
    assert sum(_bar(41, 40)) == N, "a bar spilled past its own width"
    assert sum(_bar(20, 40)) == N // 2


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_distribution_chart_appears_only_for_readers_of_the_enrolments_screen(
    client: Client, seeded_settings: None, mixed_enrollments: Any, role: str
) -> None:
    """
    The chart is a grouping of the enrolment rows, so it carries exactly the
    permission those rows do. A role that may not open «التسجيلات» may not read
    their shape either — a distribution is still the data, counted.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    may_read = Action.VIEW in allowed_actions(role, "enrollments")
    client.force_login(_user(role, f"dash.chart.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    assert ("توزيع التسجيلات حسب الحالة" in body) is may_read


def test_the_distribution_chart_counts_the_same_enrolments_the_counter_does(
    client: Client, seeded_settings: None, mixed_enrollments: Any
) -> None:
    """
    No invented numbers: the chart groups the rows the view already fetched, so
    its bars must add up to the «التسجيلات» counter beside them. If the two ever
    disagree, one of them is making something up — and this says which.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dash.chart.total"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    counter = re.search(
        r'<div class="label">التسجيلات</div>\s*<div class="value num">(\d+)</div>', body
    )
    assert counter, "the enrolments counter is not on the page to compare against"

    chart = body.split("توزيع التسجيلات حسب الحالة", 1)[1].split("ما يحتاج متابعة", 1)[0]
    bars = [int(v) for v in re.findall(r'<span class="num">(\d+)</span>', chart)]

    assert bars, "the chart drew no rows"
    assert sum(bars) == int(counter.group(1))


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_draws_no_chart_when_there_is_nothing_to_chart(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    Settings seeded and nothing else. A chart of nothing is not a chart, so the
    card is absent rather than present and empty — the counters already say
    zero, and three empty boxes are a worse answer than one.
    """
    client.force_login(_user(role, f"dash.nochart.{role.lower()}"))

    response = client.get(reverse("operations:dashboard"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "توزيع التسجيلات حسب الحالة" not in body
    assert 'class="bar' not in body


def test_the_dashboard_charts_need_no_script_and_no_inline_style() -> None:
    """
    The bar is cells with a class, not a width. That is the whole reason there
    is no charting library here, no CDN, and nothing for A-07 to catch — and
    why the fill survives a printer, which computed widths often do not.
    """
    from pathlib import Path

    source = Path("templates/operations/dashboard.html").read_text(encoding="utf-8")

    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    # The fill is expressed as a class the stylesheet owns.
    assert 'class="bar' in source
    assert 'class="{% if on %}on{% endif %}"' in source


# ---------------------------------------------------------------------------
# The command-center sections — page polish phase
# ---------------------------------------------------------------------------
#: The six stops of the workflow strip, and the screen each one opens.
DASHBOARD_FLOW = [
    ("طلب التحاق", "people:participant-new", "student-new"),
    ("التسجيل", "operations:enrollments", "enrollments"),
    ("استيفاء دفعة", "cashbox:payment-new", "payment-new"),
    ("الإقفال اليومي", "cashbox:closing", "closing"),
    ("براءة الذمة", "operations:clearances", "clearance"),
    ("الشهادة", "operations:certificates", "certificates"),
]


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_workflow_strip_shows_every_stop_to_everyone(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The strip is the reason this page is worth opening on a quiet morning: a
    centre with four enrolments has the same process as one with four hundred,
    and the map does not shrink with the numbers.

    So every stop is named for every reader — the shape of the work is not a
    privilege. Only the links are filtered.
    """
    client.force_login(_user(role, f"dash.flow.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    for label, _route, _screen in DASHBOARD_FLOW:
        assert label in body, f"{role} is not shown the «{label}» stop"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_workflow_strip_links_only_the_stops_the_reader_may_open(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    A stop the reader may not open keeps its place and loses its link. The
    manager's «استيفاء دفعة» is the case that proves it: D-01 makes BR-081 an
    explicit deny, so the map still shows the centre takes money — it just
    does not hand the manager a door into the till.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"dash.flowlink.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    strip = body.split('<ol class="flow">', 1)[1].split("</ol>", 1)[0]

    for label, route, screen in DASHBOARD_FLOW:
        may = Action.VIEW in allowed_actions(role, screen)
        linked = f'<a href="{reverse(route)}">{label}</a>' in strip
        assert linked is may, (
            f"{role} {'may' if may else 'may NOT'} open {screen}, "
            f"but «{label}» is {'linked' if linked else 'unlinked'}"
        )


def test_the_manager_sees_the_till_stop_named_but_not_linked(
    client: Client, seeded_settings: None
) -> None:
    """BR-081 · D-01, spelled out on the strip as well as on the buttons."""
    client.force_login(_user(Role.CENTER_MANAGER, "dash.flow.mgr"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    strip = body.split('<ol class="flow">', 1)[1].split("</ol>", 1)[0]

    assert "استيفاء دفعة" in strip
    assert f'<a href="{reverse("cashbox:payment-new")}">' not in strip


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_dashboard_says_what_this_reader_may_reach(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    One line at the top naming the reader's own territory, read off the matrix
    rather than off the role name — so it cannot claim more than the sidebar
    will actually open, whatever the permissions become later.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"dash.scope.{role.lower()}"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    header = body.split('<p class="sub">', 1)[-1].split("</p>", 1)[0]

    may_cash = Action.VIEW in allowed_actions(role, "payments") or (
        Action.VIEW in allowed_actions(role, "closing")
    )
    may_partners = Action.VIEW in allowed_actions(role, "partners") or (
        Action.VIEW in allowed_actions(role, "claims")
    )
    assert ("الصندوق" in header) is may_cash
    assert ("الشركاء" in header) is may_partners


def test_a_single_category_is_not_drawn_as_a_distribution(
    client: Client, seeded_settings: None, settled_enrollment: Any
) -> None:
    """
    One enrolment in one state used to draw a bar at 100% beside a counter
    reading 1 — a chart that restates the number next to it and makes the page
    look like a toy. A comparison needs two things to compare.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dash.onecat"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    assert "التسجيلات" in body, "the counter itself still belongs on the page"
    assert "توزيع التسجيلات حسب الحالة" not in body


def test_the_dashboard_is_still_worth_opening_with_no_data_at_all(
    client: Client, seeded_settings: None
) -> None:
    """
    Settings seeded and nothing else — no enrolment, no receipt, no cohort.

    The page must still carry substance, because this is what a new centre sees
    on its first morning and what a demo opens on. Structure comes from the
    process and the reader's own permissions, neither of which is a count.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "dash.brandnew"))

    response = client.get(reverse("operations:dashboard"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "مسار العمل" in body, "the workflow map is what fills a quiet morning"
    assert "صلاحياتك تغطّي" in body
    assert "ابدأ من هنا" in body
    assert "حالة المركز اليوم" in body
    # A zero reads as a statement, not as a blank card.
    assert "لا يوجد حالياً" in body
    # …and no chart pretends to describe nothing.
    assert 'class="bar' not in body


def test_the_dashboard_action_cards_stay_permission_gated(
    client: Client, seeded_settings: None
) -> None:
    """
    The buttons became cards with a line of explanation. That is presentation;
    the gate behind them is the same one, and the manager still gets no till.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in ALL_ROLES:
        client.force_login(_user(role, f"dash.cards.{role.lower()}"))
        body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
        cards = re.findall(r'<a class="act-card[^"]*" href="([^"]+)">', body)

        for route, screen in DASHBOARD_ACTIONS:
            may = Action.VIEW in allowed_actions(role, screen)
            assert (reverse(route) in cards) is may, f"{role} · {screen}"


def test_the_guide_page_uses_no_class_this_project_never_defined() -> None:
    """The eight steps ride the project's own stepper, not the demo's."""
    from pathlib import Path

    source = Path("templates/operations/enroll_flow.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    for dead in [*DEAD_DEMO_CLASSES, "compact", "mono"]:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from the dead list"
        assert dead not in source, f"enroll_flow.html uses the demo-only class «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source


def test_the_guide_page_keeps_its_responsive_scaffolding() -> None:
    """
    The eight are stacked cards, which read the same on a phone as on a desk.
    The later paths are a grid that collapses: one column, two, then three.
    """
    from pathlib import Path

    source = Path("templates/operations/enroll_flow.html").read_text(encoding="utf-8")
    css = Path("static/src/input.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        return css.split(selector, 1)[1].split("}", 1)[0]

    assert 'class="step"' in source and 'class="step-body"' in source
    assert 'class="act-grid"' in source
    assert "sm:grid-cols-2" in rule(".act-grid")
    assert "lg:grid-cols-3" in rule(".act-grid")
    # A later path the reader may not open keeps its card and loses its link.
    assert "act-card is-off" in source
    assert ".act-card.is-off" in css


def test_a_later_path_the_reader_cannot_open_is_shown_without_a_link(
    client: Client, seeded_settings: None
) -> None:
    """
    The registrar may not open the daily closing (§3.4/18 grants them nothing
    there), so the card stays — the closing is part of how the centre works —
    and it is drawn as a plain card rather than a door that would refuse them.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "guide.later.off"))

    body = client.get(reverse("operations:enroll-flow")).content.decode("utf-8")
    later = body.split("بعد اعتماد التسجيل", 1)[1]

    assert "الإقفال اليومي" in later
    assert f'href="{reverse("cashbox:closing")}"' not in later
    assert "act-card is-off" in later
