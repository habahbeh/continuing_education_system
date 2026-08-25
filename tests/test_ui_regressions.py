"""
Sprint 8I — the defects the browser pass found, each pinned by a test.

Nothing speculative lives here. Every test below corresponds to something
that was actually broken when a person opened the page: a button with
invisible text, a screen that returned 500, a developer's note printed above
a participant's details. They are grouped by the kind of mistake rather than
by app, because the kinds are what will recur.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.people.models import Role, User

pytestmark = pytest.mark.django_db

PASSWORD = "ui-probe-1234"
CSS_SOURCE = Path("static/src/input.css")
TEMPLATE_ROOTS = (Path("templates"), Path("apps"))


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


# ---------------------------------------------------------------------------
# The audit screen returned 500 for every role that could open it
# ---------------------------------------------------------------------------
def test_the_audit_screen_renders_an_event_that_has_no_actor(
    client: Client, seeded_settings: None
) -> None:
    """
    The worst find of the pass, and the most ordinary cause.

    ``{{ e.actor.full_name_ar|default:e.actor.username|default:"—" }}`` looks
    defensive and is not: Django silences a failed lookup in the VALUE of a
    filter expression but resolves its ARGUMENT eagerly, so
    ``e.actor.username`` on a null actor raised VariableDoesNotExist and took
    the whole page with it.

    A null actor is not exotic. Every anonymous DENIED_ATTEMPT has one — and
    one of those is written the first time anybody hits a protected URL while
    logged out, which on the QA database was 47 of 132 rows.
    """
    from apps.core.services.audit_service import write_audit

    write_audit(
        action="DENIED_ATTEMPT",
        entity_type="partners",
        summary_ar="محاولة مرفوضة — لم يتم تسجيل الدخول (Q-12)",
        actor=None,
        # core_audit_denied_requires_rule — a refusal names the rule it enforced.
        denial_rule="Q-12",
    )

    client.force_login(_user(Role.AUDIT_ACCOUNT, "aud.audit.screen"))
    response = client.get(reverse("people:audit"))

    assert response.status_code == 200
    assert "النظام" in response.content.decode("utf-8"), "an actor-less row names the system"


# ---------------------------------------------------------------------------
# A developer's note was printed to the user
# ---------------------------------------------------------------------------
def test_no_template_opens_a_hash_comment_it_does_not_close_on_the_same_line() -> None:
    """
    Django's ``{# #}`` is SINGLE-LINE. A two-line one comments out its first
    line and RENDERS the rest, which is how a note about BR-101 came to sit
    above the participant's details on screen. ``{% comment %}`` spans lines;
    the hash form does not.
    """
    offenders: list[str] = []
    for root in TEMPLATE_ROOTS:
        for path in root.rglob("*.html"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{path}:{number}")

    assert not offenders, (
        "A hash comment must close on its own line or the rest renders as text. "
        "Use {% comment %} instead. Offenders: " + ", ".join(offenders)
    )


def test_the_participant_page_prints_no_developer_note(
    client: Client, seeded_settings: None, active_semester: object, participant_data: dict
) -> None:
    from apps.people.services import participant_service

    registrar = _user(Role.REGISTRATION_OFFICER, "reg.detail.probe")
    participant = participant_service.create_participant(actor=registrar, data=participant_data)

    client.force_login(registrar)
    body = client.get(
        reverse("people:participant-detail", args=[participant.participant_number])
    ).content.decode("utf-8")

    assert "BR-101" not in body
    assert "the service projected" not in body


# ---------------------------------------------------------------------------
# Raw storage codes were shown to an Arabic-speaking user
# ---------------------------------------------------------------------------
def test_the_participant_page_reads_in_arabic_not_in_column_values(
    seeded_settings: None, active_semester: object, participant_data: dict
) -> None:
    """
    ``UNIVERSITY``, ``NATIONAL_ID`` and ``True`` are how the columns store it.
    The model already carries the Arabic for each in its ``choices``.
    """
    from apps.people.services import participant_service

    registrar = _user(Role.REGISTRATION_OFFICER, "reg.display.probe")
    participant = participant_service.create_participant(actor=registrar, data=participant_data)

    shown = {
        key: value
        for key, _label, value in participant_service.get_participant_display(
            actor=registrar, participant_number=participant.participant_number
        )
    }
    assert shown["category"] == "طالب جامعة / خرّيج"
    assert shown["id_document_type"] == "رقم وطني"
    assert shown["no_refund_pledge_accepted"] == "نعم"


# ---------------------------------------------------------------------------
# Date fields that were plain text boxes
# ---------------------------------------------------------------------------
def test_every_date_field_on_a_form_offers_a_date_picker() -> None:
    """
    Thirty-odd date fields across the system carry
    ``widget=forms.DateInput({"type": "date"})``; the three on the participant
    form did not, so the first screen a registrar uses asked them to type an
    ISO date by hand with no picker and no hint of the format.
    """
    import django.forms as django_forms

    from apps.people.participant_forms import ParticipantEditForm, ParticipantForm

    # Asserted on the RENDERED input, not on `widget.attrs`: Django consumes
    # the `type` key into `input_type` and leaves `attrs` empty, so checking
    # the dict reports every field as broken including the fixed ones. The
    # first version of this test did exactly that.
    offenders: list[str] = []
    for form_class in (ParticipantForm, ParticipantEditForm):
        form = form_class()
        for name, field in form_class.base_fields.items():
            is_date = isinstance(field, django_forms.DateField)
            if is_date and 'type="date"' not in str(form[name]):
                offenders.append(f"{form_class.__name__}.{name}")

    assert not offenders, "date fields rendering as plain text: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# Styling that made controls invisible
# ---------------------------------------------------------------------------
def test_the_button_class_sets_its_own_text_colour() -> None:
    """
    ``.btn2`` had a white background and no colour, so inside ``.topbar`` —
    white on maroon — it inherited white and the logout button rendered as an
    empty white box.
    """
    source = CSS_SOURCE.read_text(encoding="utf-8")
    rule = next(line for line in source.splitlines() if line.strip().startswith(".btn2  "))
    assert "text-" in rule, "a button on a coloured bar must not inherit its text colour"


def test_form_controls_laid_out_with_calc_row_are_styled() -> None:
    """
    ``.calc-row`` started as a read-only calculation line and ten templates
    reused it for FORMS — the login page among them, which shipped with a
    password box a user could not see.
    """
    source = CSS_SOURCE.read_text(encoding="utf-8")
    assert ".calc-row input" in source
    assert ".calc-row select" in source
    assert ".calc-row textarea" in source


def test_action_buttons_in_a_table_row_sit_side_by_side() -> None:
    """
    A button that POSTs needs its own form and its own CSRF token, and a form
    is a block element — so every action cell in the system stacked its
    buttons vertically down the row. Twenty-one templates share the pattern,
    which is why the fix is one rule in the stylesheet rather than twenty-one
    wrappers. Found in the Sprint 8I browser pass, fixed in 8I-1.
    """
    source = CSS_SOURCE.read_text(encoding="utf-8")
    assert ".tbl td > form" in source
    rule = next(line for line in source.splitlines() if line.strip().startswith(".tbl td > form{"))
    assert "inline" in rule


@pytest.mark.parametrize("tag", ["success", "warning", "error"])
def test_every_django_message_tag_has_a_colour(tag: str) -> None:
    """
    ``_messages.html`` prints ``message.tags`` straight into the class, and
    Django's vocabulary is success/error/warning/debug. Only ``info`` happened
    to collide with a rule that existed, so a refusal and a confirmation
    rendered in the same neutral grey.
    """
    source = CSS_SOURCE.read_text(encoding="utf-8")
    assert f".note.{tag}" in source


# ---------------------------------------------------------------------------
# The bare framework error pages
# ---------------------------------------------------------------------------
def test_a_refused_page_answers_in_arabic_with_a_way_out(
    client: Client, seeded_settings: None
) -> None:
    """
    Without ``403.html`` Django answers PermissionDenied with a bare English
    ``<h1>403 Forbidden</h1>`` — no branding, no navigation, no link back.
    Q-12 expires a session after 30 minutes, and ``policy.require`` cannot
    tell an expired session from a refusal, so that page was also what a user
    met after lunch.
    """
    client.force_login(_user(Role.CASHIER, "cash.403.probe"))
    response = client.get(reverse("partners:partners"))

    assert response.status_code == 403
    body = response.content.decode("utf-8")
    assert "لا صلاحية للوصول" in body
    assert "403 Forbidden" not in body


def test_the_refusal_page_offers_an_anonymous_user_the_way_to_log_in(
    client: Client, seeded_settings: None
) -> None:
    response = client.get(reverse("operations:dashboard"))
    assert response.status_code == 403

    body = response.content.decode("utf-8")
    assert reverse("people:login") in body


# ---------------------------------------------------------------------------
# Two menu entries lit at once
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/partners/", "/partners/"),
        # Sprint 8K-1 took «شريك جديد» out of the menu for demo parity; the
        # partners screen still offers it as a guarded button. With no entry of
        # its own, /partners/new/ now belongs to its parent, like any detail page.
        ("/partners/new/", "/partners/"),
        ("/partners/PRT-1/", "/partners/"),
        ("/operations/mohe/", "/operations/mohe/"),
        ("/operations/mohe/submit/", "/operations/mohe/submit/"),
        ("/operations/mohe/7/", "/operations/mohe/"),
    ],
)
def test_exactly_one_menu_entry_is_marked_for_a_path(
    seeded_settings: None, path: str, expected: str
) -> None:
    """
    Marking on the screen alone lit two entries at once: ``operations:mohe``
    and ``operations:mohe-submit`` sit in one section of the menu, one path
    beneath the other. Longest matching prefix gives the child
    its own page and the parent the detail pages beneath it.
    """
    from apps.people import nav

    manager = _user(Role.CENTER_MANAGER, f"mgr.nav.{abs(hash(path)) % 10000}")
    groups = nav.mark_active(nav.nav_for(manager), path)
    active = [item["url"] for group in groups for item in group["items"] if item["is_active"]]

    assert active == [expected]


# ---------------------------------------------------------------------------
# The till could not be used on a fresh install
# ---------------------------------------------------------------------------
def test_a_payment_method_can_be_created_without_a_fixture() -> None:
    """
    ``PaymentMethod`` is a reference table by design (DATA_MODEL §8.7) and had
    no admin, no service and no seed — so on a fresh database the payment
    screen offered an empty dropdown and no money could ever be taken.
    """
    from django.contrib import admin as django_admin

    from apps.cashbox.models import PaymentMethod

    assert PaymentMethod in django_admin.site._registry


def test_a_payment_method_cannot_be_deleted_from_the_admin(rf: object) -> None:
    """Every Receipt holds a PROTECT key to its method; is_active retires one."""
    from django.contrib import admin as django_admin

    from apps.cashbox.models import PaymentMethod

    model_admin = django_admin.site._registry[PaymentMethod]
    assert not model_admin.has_delete_permission(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The sidebar — navigation polish
# ---------------------------------------------------------------------------
NAV_PARTIAL = Path("templates/partials/_nav.html")

#: Copied from the demo's stylesheet by Sprint 8K-1 and defined nowhere here.
NAV_DEAD_CLASSES = ["vflow", "vstep", "vtitle", "vmeta", "vbox", "mini-title", "stack-list"]


def test_the_sidebar_announces_its_groups_as_named_lists(
    client: Client, seeded_settings: None
) -> None:
    """
    Forty entries in seven groups. As a bare run of links a screen reader had
    to be walked through all forty to find out which section it was in, and the
    group heading was a ``div`` it never announced.

    Now every group is a named region over a real list: the heading can be
    jumped to, and the list says how many entries are under it.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "nav.groups"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    nav = body.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]

    headings = re.findall(r'<h2 class="nav-group-title" id="(nav-group-\d+)">', nav)
    labelled = re.findall(r'aria-labelledby="(nav-group-\d+)"', nav)

    assert headings, "the sidebar draws no group headings"
    assert headings == labelled, "a group points at a heading that is not there"
    assert len(re.findall(r"<ul>", nav)) == len(headings)
    assert nav.count('role="group"') == len(headings)


def test_the_open_screen_is_marked_by_more_than_its_colour(
    client: Client, seeded_settings: None
) -> None:
    """
    Exactly one entry is current, and it says so four ways: the ``active``
    class carries a ground and a rule on the leading edge, the weight lifts,
    and ``aria-current="page"`` tells anyone not looking at the colour.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "nav.active"))

    body = client.get(reverse("operations:enrollments")).content.decode("utf-8")
    nav = body.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]

    active = re.findall(r'<a class="nav-item active"[^>]*aria-current="page"', nav)
    assert len(active) == 1, f"{len(active)} entries claim to be the open screen"
    # …and the class is not decorative: the stylesheet gives it a weight and a
    # rule, not only a background.
    rule = CSS_SOURCE.read_text(encoding="utf-8").split(".nav-item.active", 1)[1].split("}", 1)[0]
    assert "font-extrabold" in rule
    assert "border-inline-start-color" in rule


def test_the_sidebar_still_filters_by_permission_after_the_polish(
    client: Client, seeded_settings: None
) -> None:
    """
    Presentation changed; the gate did not. The cashier's menu is short because
    BR-083 keeps them out of partner data and the reports, not because a
    template hid anything.
    """
    import re

    client.force_login(_user(Role.CASHIER, "nav.cashier"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    nav = body.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]

    assert "الشركاء المتعاقدون" not in nav
    assert "التقارير" not in nav
    assert "استيفاء دفعة" in nav
    # Every entry drawn is still a real address.
    for href in re.findall(r'<a class="nav-item[^"]*" href="([^"]+)"', nav):
        assert href.startswith("/"), href


def test_the_phone_drawer_survived_the_polish(client: Client, seeded_settings: None) -> None:
    """
    The sidebar is a drawer below ``md``. Its close button, the toggle that
    opens it and the scrim behind it all have to stay, and the row has to stay
    comfortable to hit with a thumb rather than a mouse pointer.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "nav.drawer"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")

    assert 'class="nav-close-row"' in body
    assert 'x-ref="navClose"' in body
    assert 'class="nav-toggle"' in body
    assert 'class="nav-scrim"' in body
    # The drawer slides on a logical axis, so it opens from the right in RTL.
    assert '[dir="rtl"] .sidebar' in css
    # A 40px row is a mouse target; the phone gets a taller one.
    phone = css.split("@media (max-width: 767px)", 1)[1].split("\n  }", 1)[0]
    assert ".nav-item" in phone and "py-3" in phone


def test_the_sidebar_polish_added_no_icon_font_and_no_dead_class() -> None:
    """
    No icon system exists in this project — one inline SVG for the hamburger is
    not one. Forty hand-authored glyphs are a content project, and a CDN is
    refused outright by A-07, so the hierarchy is carried by weight, spacing
    and rules instead.
    """
    source = NAV_PARTIAL.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")

    assert "http://" not in source and "https://" not in source
    assert "<script" not in source
    assert "style=" not in source
    assert "<i " not in source, "no icon-font element crept in"
    for dead in NAV_DEAD_CLASSES:
        assert f".{dead}" not in css, f"{dead} now exists — drop it from the dead list"
        assert dead not in source, f"the sidebar uses the demo-only class «{dead}»"


def test_the_sidebar_reads_as_sections_not_as_one_long_column() -> None:
    """
    Every entry was ``font-bold`` under a ``font-black`` heading: two adjacent
    weights, so nothing separated a heading from an entry and forty links read
    as one column. Weight now does the separating, a hairline divides the
    groups, and the heading stays put while its own section scrolls.
    """
    import re

    css = CSS_SOURCE.read_text(encoding="utf-8")

    def declarations(selector: str) -> str:
        """Every block for this selector, joined.

        A selector can appear more than once — ``.nav-item`` is redeclared
        inside the phone media query — and the phone one comes first in the
        file, so taking the first match reads the wrong rule.
        """
        pattern = re.escape(selector) + r"\s*\{(.*?)\}"
        blocks = re.findall(pattern, css, re.S)
        assert blocks, f"no rule found for {selector}"
        return "\n".join(blocks)

    # The entry sits at 500 under a heading at 900: two weights apart, not one.
    assert "font-medium" in declarations(".nav-item")
    assert "font-black" in declarations(".nav-group-title")
    # A hairline between groups, and a heading that stays while its own
    # section scrolls under it.
    assert "border-t" in declarations(".nav-group + .nav-group")
    assert "sticky" in declarations(".nav-group-title")
