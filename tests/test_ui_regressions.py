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


# ---------------------------------------------------------------------------
# The sidebar's icons — navigation polish
# ---------------------------------------------------------------------------
NAV_SPRITE = Path("templates/partials/_nav_icons.html")


def _symbols() -> set[str]:
    import re

    return set(re.findall(r'id="i-([a-z-]+)"', NAV_SPRITE.read_text(encoding="utf-8")))


def test_every_nav_entry_names_a_symbol_the_sprite_actually_defines() -> None:
    """
    A ``<use>`` pointing at a symbol that is not there renders nothing at all —
    silently, with no error anywhere. So the two lists are compared directly:
    every icon the tree names must exist, and the fallback must exist too.
    """
    from apps.people.nav import NAV, NavItem

    defined = _symbols()
    named = {item.icon for group in NAV for item in group.items}
    fallback = NavItem.__dataclass_fields__["icon"].default

    assert not (named - defined), f"nav names symbols the sprite lacks: {sorted(named - defined)}"
    assert fallback in defined, "the fallback icon is not in the sprite"
    assert not (defined - named - {fallback}), (
        f"sprite carries symbols nothing uses: {sorted(defined - named - {fallback})}"
    )


def test_an_entry_added_without_an_icon_still_draws_one() -> None:
    """
    The field defaults to a real symbol rather than to an empty string, so a
    future entry that forgets one gets a neutral mark instead of a hole in a
    column where every sibling has a glyph.
    """
    from apps.people.constants import Screen
    from apps.people.nav import NavItem

    item = NavItem(Screen.DASHBOARD, "operations:dashboard", "بلا رمز")

    assert item.icon in _symbols()


def test_the_icons_are_decorative_and_the_label_is_still_the_name(
    client: Client, seeded_settings: None
) -> None:
    """
    The glyph is ``aria-hidden`` and the text stays: a screen reader announces
    «التسجيلات», never «التسجيلات صورة». Nothing about the open screen depends
    on an icon either — colour, ground, rule, weight and ``aria-current`` all
    still carry it, so the sidebar works identically with images off.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "nav.icons.a11y"))

    body = client.get(reverse("operations:enrollments")).content.decode("utf-8")
    nav = body.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]

    items = re.findall(r'<a class="nav-item[^>]*>(.*?)</a>', nav, re.S)
    assert items
    for entry in items:
        assert 'class="nav-ico"' in entry
        assert 'aria-hidden="true"' in entry
        assert re.search(r"<span>[^<]+</span>", entry), "an entry lost its text label"
    # Every reference resolves against the sprite drawn just above them.
    for name in re.findall(r'<use href="#i-([a-z-]+)"/>', nav):
        assert f'id="i-{name}"' in nav, f"«{name}» is referenced and never defined"


def test_the_sprite_is_defined_once_not_once_per_entry(
    client: Client, seeded_settings: None
) -> None:
    """Forty definitions per render would be the cost the sprite exists to avoid."""
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "nav.icons.once"))

    body = client.get(reverse("operations:dashboard")).content.decode("utf-8")

    assert len(re.findall(r'<svg class="nav-sprite"', body)) == 1
    assert len(re.findall(r"<symbol id=", body)) == len(_symbols())


def test_the_icons_brought_in_no_font_no_package_and_no_cdn() -> None:
    """
    A-07 refuses an external resource outright, and an icon font would have
    been a second one: a webfont request, a ligature vocabulary and a glyph
    that reads as a letter to anything not styling it. Local SVG has none of
    those problems.
    """
    sprite = NAV_SPRITE.read_text(encoding="utf-8")
    partial = NAV_PARTIAL.read_text(encoding="utf-8")

    for source in (sprite, partial):
        assert "http://" not in source and "https://" not in source
        assert "<script" not in source
        assert "font-awesome" not in source.lower()
        assert "bootstrap-icons" not in source.lower()
    # No emoji standing in for a glyph.
    assert not any(ord(ch) > 0x2100 for ch in partial), "a non-Arabic symbol crept into the nav"


def test_the_icon_set_speaks_one_visual_language() -> None:
    """
    One stroke width, one cap, one join, one box — set on the wrapping ``svg``
    in the template so a symbol cannot quietly disagree with its siblings. And
    every symbol is drawn in strokes, so it takes the colour of the row it sits
    in rather than carrying its own.
    """
    import re

    sprite = NAV_SPRITE.read_text(encoding="utf-8")
    partial = NAV_PARTIAL.read_text(encoding="utf-8")

    assert 'stroke-width="1.6"' in partial
    assert 'stroke="currentColor"' in partial
    assert 'stroke-linecap="round"' in partial and 'stroke-linejoin="round"' in partial

    boxes = set(re.findall(r'<symbol id="i-[a-z-]+" viewBox="([^"]+)"', sprite))
    assert boxes == {"0 0 20 20"}, f"symbols disagree about their box: {boxes}"
    # A filled shape would ignore currentColor and stay dark on the open row.
    assert 'fill="' not in sprite, "a symbol carries its own fill"


# ---------------------------------------------------------------------------
# The admission form — page polish
# ---------------------------------------------------------------------------
ADMISSION_TEMPLATE = Path("templates/people/participant_new.html")


def _admission(client: Client, role: str, tag: str) -> str:
    client.force_login(_user(role, f"adm.{tag}.{role.lower()}"))
    response = client.get(reverse("people:participant-new"))
    assert response.status_code == 200
    return response.content.decode("utf-8")


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.CENTER_MANAGER, True),
        (Role.REGISTRATION_OFFICER, True),
        (Role.AUDIT_ACCOUNT, True),
        (Role.FINANCE_OFFICER, False),
        (Role.FINANCE_MANAGER, False),
        (Role.CASHIER, False),
    ],
)
def test_the_admission_form_opens_for_exactly_the_roles_the_matrix_allows(
    client: Client, seeded_settings: None, role: str, allowed: bool
) -> None:
    """§3.2/4 — the audit account may read the form and may not submit it."""
    client.force_login(_user(role, f"adm.open.{role.lower()}"))

    response = client.get(reverse("people:participant-new"))

    assert response.status_code == (200 if allowed else 403)


def test_the_admission_form_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """Fail-closed: it collects identity documents and is not public."""
    assert client.get(reverse("people:participant-new")).status_code == 403


def test_every_form_field_is_drawn_exactly_once_across_the_sections(
    client: Client, seeded_settings: None
) -> None:
    """
    Twenty fields in one flat grid became five named cards. Grouping is the
    whole risk of that change: a field left out of the map would vanish from
    the page silently, and one listed twice would be submitted twice.

    So the rendered page is compared against the form itself, not against the
    map — the map cannot vouch for itself.
    """
    import re

    from apps.people.participant_forms import ParticipantForm

    body = _admission(client, Role.REGISTRATION_OFFICER, "fields")
    drawn = re.findall(r'<label for="id_([a-z_]+)"', body)

    assert sorted(drawn) == sorted(ParticipantForm().fields)
    assert len(drawn) == len(set(drawn)), "a field is rendered twice"


def test_the_section_map_covers_the_form_and_invents_nothing() -> None:
    """A field added to the form later must be placed, not quietly dropped."""
    from apps.people.participant_forms import ParticipantForm
    from apps.people.views import PARTICIPANT_FORM_SECTIONS

    placed = [name for names in PARTICIPANT_FORM_SECTIONS.values() for name in names]

    assert sorted(placed) == sorted(ParticipantForm().fields)
    assert len(placed) == len(set(placed))


def test_the_admission_form_draws_its_five_named_sections(
    client: Client, seeded_settings: None
) -> None:
    """Numbered and titled, so the form reads as stages rather than a wall."""
    import re

    body = _admission(client, Role.CENTER_MANAGER, "sections")
    sections = re.findall(r'<span class="step-num"[^>]*>(\d+)</span>\s*<h2>([^<]+)</h2>', body)

    assert [n for n, _t in sections] == ["1", "2", "3", "4", "5"]
    assert "الفئة" in sections[0][1]
    assert "الإعفاء" in sections[4][1] and "التعهّد" in sections[4][1]


def test_the_admission_form_never_shows_a_number_before_it_is_earned(
    client: Client, seeded_settings: None
) -> None:
    """
    The demo printed a participant number into a read-only box before saving.
    Here the number is drawn from a locked counter inside the save transaction
    (BR-001), so a number shown beforehand would be a guess that the record
    then contradicts. The page says where it comes from and shows none.
    """
    import re

    body = _admission(client, Role.REGISTRATION_OFFICER, "nonumber")

    assert not re.search(r"\b20\d{7}\b", body), "a participant number was previewed"
    assert 'name="participant_number"' not in body
    assert "BR-001" in body
    assert "الفصل الدراسي النشط" in body


def test_the_admission_form_explains_the_fields_that_trip_people_up(
    client: Client, seeded_settings: None
) -> None:
    """
    Three fields have no obvious meaning to a new employee, and all three were
    rendered bare: the exemption pair, which needs the president's approval
    reference before ``clean()`` will pass; and the duplicate override, which
    belongs to BR-005's WARN mode and should stay empty otherwise.
    """
    body = _admission(client, Role.REGISTRATION_OFFICER, "help")

    assert "رقم موافقة رئيس الجامعة" in body
    assert "إلزامي متى فُعِّل الإعفاء" in body
    assert "BR-005" in body
    assert "لا يُحفظ الطلب بدونه" in body


def test_the_help_copy_changed_nothing_about_how_the_form_behaves() -> None:
    """
    ``help_text`` is copy. The field set, which of them are required, the widget
    types and ``clean()`` are the behaviour, and none of them moved — this is
    what separates a polish from a change to the form.
    """
    from apps.people.participant_forms import ParticipantEditForm, ParticipantForm

    form = ParticipantForm()

    assert len(form.fields) == 20
    assert sorted(n for n, f in form.fields.items() if f.required) == [
        "category",
        "id_document_number",
        "id_document_type",
        "name_ar",
        "registered_on",
    ]
    # The pledge is enforced in clean(), not by `required` on the widget.
    bound = ParticipantForm(data={})
    assert not bound.is_valid()
    assert "no_refund_pledge_accepted" in bound.errors
    # Editing still skips the pledge it already has on record (BR-003).
    assert issubclass(ParticipantEditForm, ParticipantForm)


def test_the_shared_form_partial_still_renders_whole_forms_untouched(
    client: Client, seeded_settings: None
) -> None:
    """
    ``_form.html`` gained an opt-in ``only`` subset, and twenty-seven templates
    include it without one. Absent the variable it must behave exactly as it
    did — this checks a screen that passes nothing and expects every field.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "adm.partial.whole"))

    body = client.get(reverse("partners:partner-new")).content.decode("utf-8")

    assert re.findall(r'<label for="id_[a-z_]+"', body), "the whole-form path drew nothing"
    assert 'class="grid2"' in body


def test_the_admission_form_added_no_dead_class_and_no_dependency() -> None:
    """No CSS was needed for this page; every class it uses already existed."""
    import re

    source = ADMISSION_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "form-section", "split3"]:
        assert dead not in source, f"the admission form uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    # Every class it draws with is defined; the page needed no new CSS.
    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    assert not [c for c in used if f".{c}" not in css]
    # Inputs stay full width and the sections stack on a phone.
    assert "sm:grid-cols-2" in css.split(".grid2", 1)[1].split("}", 1)[0]


# ---------------------------------------------------------------------------
# The participant registry — page polish
# ---------------------------------------------------------------------------
REGISTRY_TEMPLATE = Path("templates/people/participants.html")

#: Fields BR-101 keeps from the cashier and the finance manager. None of them
#: may appear in the page they are served, in any form.
RESTRICTED_AWAY = (
    "id_document_number",
    "date_of_birth",
    "nationality",
    "email",
    "employer",
    "po_box",
)


@pytest.fixture
def three_participants(seeded_settings: None) -> None:
    """One of each category, so counts and filters have something to sort."""
    from datetime import date

    from apps.people.models import IdDocumentType, Participant, ParticipantCategory

    for i, category in enumerate(
        (ParticipantCategory.UNIVERSITY, ParticipantCategory.CENTER, ParticipantCategory.EMPLOYEE)
    ):
        Participant.objects.create(
            participant_number=f"20269000{i}",
            category=category,
            name_ar=f"مشارك الاختبار {i}",
            id_document_type=IdDocumentType.NATIONAL_ID,
            id_document_number=f"88800{i}",
            phone=f"07900000{i}",
            registered_on=date(2026, 1, 1),
        )


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.CENTER_MANAGER, True),
        (Role.REGISTRATION_OFFICER, True),
        (Role.FINANCE_OFFICER, True),
        (Role.FINANCE_MANAGER, True),
        (Role.CASHIER, True),
        (Role.AUDIT_ACCOUNT, True),
    ],
)
def test_the_registry_opens_for_every_role_the_matrix_grants_view(
    client: Client, seeded_settings: None, role: str, allowed: bool
) -> None:
    """§3.2/3 grants VIEW to all six — narrowed by field, not by door."""
    client.force_login(_user(role, f"reg.open.{role.lower()}"))

    assert client.get(reverse("people:participants")).status_code == (200 if allowed else 403)


def test_the_registry_refuses_an_anonymous_visitor(client: Client, seeded_settings: None) -> None:
    """Fail-closed: it lists people, and it is not public."""
    assert client.get(reverse("people:participants")).status_code == 403


@pytest.mark.parametrize("role", [Role.CASHIER, Role.FINANCE_MANAGER])
def test_a_restricted_role_is_served_no_field_it_may_not_see(
    client: Client, three_participants: None, role: str
) -> None:
    """
    BR-101 · T-283 — the narrowing is in the projection, so the forbidden field
    is absent from the response body rather than hidden in the page. The polish
    added a category label and a count row; neither may reopen what the service
    closed.
    """
    client.force_login(_user(role, f"reg.priv.{role.lower()}"))

    body = client.get(reverse("people:participants")).content.decode("utf-8")

    for field in RESTRICTED_AWAY:
        assert field not in body, f"{role} was served «{field}»"
    assert "88800" not in body, "an identity document number reached the page"
    assert "0790000" not in body, "a phone number reached the page"
    # …and the row still carries what they ARE allowed: number and name.
    assert "202690000" in body
    assert "مشارك الاختبار 0" in body


@pytest.mark.parametrize("role", [Role.CASHIER, Role.FINANCE_MANAGER])
def test_a_restricted_role_is_offered_no_filter_on_a_field_it_cannot_see(
    client: Client, three_participants: None, role: str
) -> None:
    """
    A filter on a hidden field turns the screen into an oracle for it — which
    is exactly why ``list_participants`` narrows the SEARCH for these roles.
    The category filter and the category counts follow the same rule here: they
    are drawn only for a reader who already sees the field.
    """
    client.force_login(_user(role, f"reg.filter.{role.lower()}"))

    body = client.get(reverse("people:participants")).content.decode("utf-8")

    assert 'id="category"' not in body
    assert "الفئة" not in body
    for code in ("UNIVERSITY", "CENTER", "EMPLOYEE"):
        assert code not in body


def test_the_registry_names_the_category_instead_of_printing_its_code(
    client: Client, three_participants: None
) -> None:
    """
    The column was rendering the stored value, so a client reading the registry
    saw «UNIVERSITY» where the label says «طالب جامعة / خرّيج». The label is
    attached in the view, over rows that already carry the field.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "reg.labels"))

    body = client.get(reverse("people:participants")).content.decode("utf-8")

    assert not re.search(r"<td[^>]*>[^<]*(UNIVERSITY|CENTER|EMPLOYEE)", body), (
        "a stored code is being printed in a table cell"
    )
    assert "طالب جامعة / خرّيج" in body
    assert "طالب مركز" in body


def test_the_category_counts_describe_the_rows_on_screen(
    client: Client, three_participants: None
) -> None:
    """
    The listing is capped, so a registry-wide total would be a number nobody
    can check against the page. The chips count what is drawn — and they follow
    the filter, which is what makes them verifiable by eye.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "reg.counts"))

    def chips(url: str) -> dict[str, int]:
        body = client.get(url).content.decode("utf-8")
        return {
            label.strip(): int(n)
            for label, n in re.findall(
                r'<span class="chip">([^<:]+): <span class="num">(\d+)', body
            )
        }

    assert sum(chips(reverse("people:participants")).values()) == 3
    filtered = chips(reverse("people:participants") + "?category=CENTER")
    assert sum(filtered.values()) == 1
    assert "طالب مركز" in filtered


def test_the_registry_empty_row_spans_the_table_this_role_actually_gets(
    client: Client, seeded_settings: None
) -> None:
    """
    The empty row was hard-coded to five columns while the cashier's table has
    three, so the message overhung its own table. The span is counted from the
    permitted columns now.
    """
    import re

    for role, expected in ((Role.CENTER_MANAGER, "5"), (Role.CASHIER, "3")):
        client.force_login(_user(role, f"reg.span.{role.lower()}"))
        body = client.get(reverse("people:participants")).content.decode("utf-8")
        assert re.search(rf'colspan="{expected}"', body), f"{role} empty row spans wrongly"


def test_the_registry_offers_the_admission_form_only_where_it_is_allowed(
    client: Client, seeded_settings: None
) -> None:
    """
    §3.2/4 — creating is the manager's and the registrar's; the rest read. The
    audit account is the case worth naming: it holds VIEW on the admission form
    and not CREATE, so the sidebar links it and this page must not offer it.

    Which is why the assertion is scoped past ``</nav>``. A bare ``href in
    body`` is answered by the menu and proves nothing about the page — it
    passed for every role until the audit account, where the two disagree.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.CASHIER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"reg.create.{role.lower()}"))
        body = client.get(reverse("people:participants")).content.decode("utf-8")
        page = body.split("</nav>", 1)[-1]
        may = Action.CREATE in allowed_actions(role, "student-new")
        offered = f'class="btn2 primary" href="{reverse("people:participant-new")}"' in page or (
            f'class="btn2 primary empty-act" href="{reverse("people:participant-new")}"' in page
        )
        assert offered is may, role


def test_the_registry_shows_which_filters_are_active(
    client: Client, three_participants: None
) -> None:
    """A filtered registry that looks unfiltered is how «where did they go?» starts."""
    client.force_login(_user(Role.CENTER_MANAGER, "reg.active"))

    plain = client.get(reverse("people:participants")).content.decode("utf-8")
    filtered = client.get(
        reverse("people:participants") + "?q=2026&category=CENTER"
    ).content.decode("utf-8")

    assert "نتائج مصفّاة" not in plain
    assert "إلغاء التصفية" not in plain
    assert "نتائج مصفّاة" in filtered
    assert "إلغاء التصفية" in filtered


def test_the_registry_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = REGISTRY_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters"]:
        assert dead not in source, f"the registry uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The wide table scrolls inside its own wrapper, never the page body.
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]


# ---------------------------------------------------------------------------
# The enrolment registry — page polish
# ---------------------------------------------------------------------------
ENROLLMENTS_TEMPLATE = Path("templates/operations/enrollments.html")

#: The demo's tenth column. It states, per row, whether a third party earns on
#: this participant and how much — a financial fact about a partner, on a
#: screen §3.2/5 opens to six roles. Whether it may be drawn, and to whom, is a
#: privacy decision the centre has not made, so the page must not pre-empt it.
PARTNER_ENTITLEMENT_MARKERS = (
    "استحقاق الشريك",
    "يستحق",
    "partner_share",
    "entitlement",
)


@pytest.fixture
def two_enrollments(seeded_settings: None, active_semester: object) -> object:
    """
    Two enrolments in two states, on two cohorts of two programmes.

    Two rather than one because a distribution of a single status is a
    restatement of the result counter beside it, and because the second
    programme is what proves the programme column is per-row rather than
    constant.
    """
    from datetime import date

    from django.core.management import call_command

    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.operations.models import Cohort, Enrollment
    from apps.people.models import IdDocumentType, Participant, ParticipantCategory

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    price_list = PriceList.objects.filter(status=PriceListStatus.APPROVED).first()
    semester = active_semester
    assert price_list is not None

    participant = Participant.objects.create(
        participant_number="202690900",
        category=ParticipantCategory.UNIVERSITY,
        name_ar="سامية عبد الرحمن",
        id_document_type=IdDocumentType.NATIONAL_ID,
        id_document_number="9990001112",
        registered_on=date(2026, 1, 1),
    )
    programs = list(Program.objects.order_by("code")[:2])
    assert len(programs) == 2

    for index, (program, status) in enumerate(
        ((programs[0], "PENDING_APPROVAL"), (programs[1], "ACTIVE"))
    ):
        cohort = Cohort.objects.create(
            code=f"CO-UIX-{index}",
            program=program,
            semester=semester,
            name_ar=f"دفعة التحسين {index}",
            starts_on=semester.starts_on,
            ends_on=semester.ends_on,
            capacity=25,
        )
        Enrollment.objects.create(
            code=f"EN-UIX-{index}",
            participant=participant,
            cohort=cohort,
            enrolled_on=date(2026, 2, 1 + index),
            price_list=price_list,
            status=status,
        )
    return participant


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.FINANCE_MANAGER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.2/5 gives the cashier an empty cell. Under BR-080 that is a refusal.
        (Role.CASHIER, 403),
    ],
)
def test_the_enrolment_registry_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"enr.open.{role.lower()}"))

    assert client.get(reverse("operations:enrollments")).status_code == expected


def test_the_enrolment_registry_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("operations:enrollments")).status_code == 403


def test_the_registration_action_is_offered_only_where_create_is_granted(
    client: Client, two_enrollments: object
) -> None:
    """
    §3.2/5 — CREATE is the manager's and the registrar's. The finance officer,
    the finance manager and the audit account read. Both the header action and
    the «تسجيل جديد» card are the same permission, so both are asserted.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.FINANCE_MANAGER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"enr.create.{role.lower()}"))
        page = (
            client.get(reverse("operations:enrollments"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        may = Action.CREATE in allowed_actions(role, "enrollments")
        assert ('id="enrollment-new"' in page) is may, role
        assert ('href="#enrollment-new"' in page) is may, role


def test_the_voucher_and_approval_actions_follow_edit_and_approve(
    client: Client, two_enrollments: object
) -> None:
    """
    Two different permissions on two different buttons, and the matrix gives
    them to two different sets of roles: EDIT to the manager and the registrar,
    APPROVE to the manager alone (§3.2/5). Asserted in both directions — a
    button that appears for a role that cannot use it sends a reader into a
    refusal and writes a DENIED_ATTEMPT the page invited.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.FINANCE_MANAGER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"enr.act.{role.lower()}"))
        page = (
            client.get(reverse("operations:enrollments"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        actions = allowed_actions(role, "enrollments")
        assert ('value="voucher"' in page) is (Action.EDIT in actions), role
        assert ('value="approve"' in page) is (Action.APPROVE in actions), role


def test_the_approval_route_still_refuses_a_role_the_page_never_offered_it_to(
    client: Client, two_enrollments: object
) -> None:
    """
    Hiding is not protection. The registrar has EDIT and not APPROVE, and the
    POST is refused at the view whether or not a button was ever drawn.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "enr.br018.reg"))

    response = client.post(
        reverse("operations:enrollment-action", args=["EN-UIX-0"]), {"action": "approve"}
    )

    assert response.status_code == 403


def test_br018_still_blocks_approval_before_the_voucher_is_recorded(
    client: Client, two_enrollments: object
) -> None:
    """
    The rule the page explains in words is the rule the service enforces: the
    manager may approve, and not yet. The refusal names BR-018, and the polish
    left both the message and the order of the two steps alone.
    """
    from apps.operations.models import Enrollment

    client.force_login(_user(Role.CENTER_MANAGER, "enr.br018.mgr"))
    url = reverse("operations:enrollment-action", args=["EN-UIX-0"])

    client.post(url, {"action": "approve"}, follow=True)
    assert Enrollment.objects.get(code="EN-UIX-0").approved_by_id is None

    client.post(url, {"action": "voucher"}, follow=True)
    client.post(url, {"action": "approve"}, follow=True)
    assert Enrollment.objects.get(code="EN-UIX-0").approved_by_id is not None


def test_the_status_chips_count_the_rows_on_screen_and_follow_the_filter(
    client: Client, two_enrollments: object
) -> None:
    """
    The chips are computed over the result set the request produced, so they
    can be checked against the table beneath them. A registry-wide total would
    be a claim nobody reading the page could verify — and the label says which
    of the two it is.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "enr.chips"))

    def chips(url: str) -> dict[str, int]:
        body = client.get(url).content.decode("utf-8")
        page = body.split("</nav>", 1)[-1]
        return {
            label.strip(): int(n)
            for label, n in re.findall(
                r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', page
            )
        }

    everything = chips(reverse("operations:enrollments"))
    assert sum(everything.values()) == 2
    assert "توزيع النتائج المعروضة" in client.get(reverse("operations:enrollments")).content.decode(
        "utf-8"
    )

    filtered = chips(reverse("operations:enrollments") + "?status=ACTIVE")
    assert sum(filtered.values()) == 1
    assert "منتظم" in filtered


def test_the_enrolment_registry_says_which_filters_are_narrowing_it(
    client: Client, two_enrollments: object
) -> None:
    """
    ``cohort`` and ``status`` were reachable from the URL and drawn nowhere, so
    a narrowed list looked like the whole registry. Naming them adds no filter:
    both are printed in the table for every role that gets through this door.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "enr.active"))
    url = reverse("operations:enrollments")

    plain = client.get(url).content.decode("utf-8")
    assert "نتائج مصفّاة" not in plain
    assert "إلغاء التصفية" not in plain

    for query in ("?q=EN-UIX", "?status=ACTIVE", "?cohort=CO-UIX-0"):
        narrowed = client.get(url + query).content.decode("utf-8")
        assert "نتائج مصفّاة" in narrowed, query
        assert "إلغاء التصفية" in narrowed, query
    # …and a search does not drop the filter that was already applied.
    assert 'name="status" value="ACTIVE"' in client.get(url + "?status=ACTIVE").content.decode(
        "utf-8"
    )


def test_the_two_empty_states_are_not_the_same_sentence(
    client: Client, seeded_settings: None, two_enrollments: object
) -> None:
    """
    «No enrolments yet» and «nothing matched what you typed» ask for opposite
    next actions, and the second one has to offer a way back to the first.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "enr.empty"))
    url = reverse("operations:enrollments")

    no_match = client.get(url + "?q=" + "لا-يوجد").content.decode("utf-8")
    assert "لا تسجيل يطابق التصفية الحالية" in no_match
    assert "عرض كل التسجيلات" in no_match
    assert "لا توجد تسجيلات بعد" not in no_match

    from apps.operations.models import Enrollment

    Enrollment.objects.all().delete()
    virgin = client.get(url).content.decode("utf-8")
    assert "لا توجد تسجيلات بعد" in virgin
    assert "لا تسجيل يطابق التصفية الحالية" not in virgin


def test_the_empty_state_offers_the_form_only_to_a_role_that_may_use_it(
    client: Client, seeded_settings: None
) -> None:
    """A reader is not sent to a button that would refuse them."""
    for role, may in ((Role.CENTER_MANAGER, True), (Role.AUDIT_ACCOUNT, False)):
        client.force_login(_user(role, f"enr.empty.{role.lower()}"))
        page = (
            client.get(reverse("operations:enrollments"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        assert "لا توجد تسجيلات بعد" in page
        assert ('class="btn2 primary empty-act" href="#enrollment-new"' in page) is may, role


def test_the_registry_names_the_programme_from_the_row_it_already_carried(
    client: Client, two_enrollments: object
) -> None:
    """
    ``list_enrollments`` has projected ``program_name`` and ``cohort_name`` all
    along and the page printed neither, so a reader scanned bare codes. The
    column adds no key to the response — it prints one that was already in it.
    """
    from apps.catalog.models import Program

    client.force_login(_user(Role.CENTER_MANAGER, "enr.program"))

    page = (
        client.get(reverse("operations:enrollments")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    for program in Program.objects.order_by("code")[:2]:
        assert program.name_ar in page
    assert "دفعة التحسين 0" in page
    # A `{# … #}` comment is single-line in Django, and one that wraps is not a
    # comment — it is text, printed inside the table. Caught in the browser.
    assert "#}" not in page and "{#" not in page


@pytest.mark.parametrize(
    "role",
    [Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT],
)
def test_the_registry_draws_no_partner_entitlement_for_anybody(
    client: Client, two_enrollments: object, role: str
) -> None:
    """
    The demo's tenth column, deliberately absent.

    It states per row whether a third party earns on this participant and how
    much. Whether that may be shown, and to which of the six roles §3.2/5 lets
    in, is a privacy decision the centre has not made. A screen does not make
    it on their behalf.
    """
    client.force_login(_user(role, f"enr.partner.{role.lower()}"))

    # Past `</nav>`: the sidebar links «استحقاق الشركاء» for the roles §3.7 lets
    # in, and that link is not this page drawing a column.
    page = (
        client.get(reverse("operations:enrollments")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    for marker in PARTNER_ENTITLEMENT_MARKERS:
        assert marker not in page, f"{role} was shown «{marker}»"


def test_the_enrolment_registry_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = ENROLLMENTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source and f" {dead}" not in source.split("{% comment %}")[0], (
            f"the enrolment registry uses «{dead}»"
        )
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The table is the widest on the system now; it scrolls inside its wrapper.
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    # A POST button in the actions cell needs a form, and a block form drops the
    # button onto its own line. `.inline-form` is what keeps the row one row.
    assert source.count('class="inline-form"') == 2
    # The empty row spans the table it sits in — nine columns, counted.
    assert len(re.findall(r"<th[ >]", source)) == 9
    assert 'colspan="9"' in source


# ---------------------------------------------------------------------------
# The course-transfer register — page polish
# ---------------------------------------------------------------------------
TRANSFERS_TEMPLATE = Path("templates/operations/transfers.html")

#: The demo's partner columns, and the money that is nobody's business on a
#: transfer list. None of them may appear here in any form.
TRANSFER_MUST_NOT_SHOW = (
    "استحقاق الشريك",
    "يستحق",
    "partner_share",
    "entitlement",
    "الشريك المتعاقد",
)


@pytest.fixture
def two_transfers(two_enrollments: object, seeded_settings: None) -> object:
    """
    Two transfer rows in two statuses, reusing the enrolments already built.

    Two rather than one because a distribution of a single status restates the
    counter beside it, and because a filter needs something to exclude.
    """
    from datetime import date

    from apps.operations.models import Enrollment, Transfer

    first = Enrollment.objects.get(code="EN-UIX-0")
    second = Enrollment.objects.get(code="EN-UIX-1")
    registrar = _user(Role.REGISTRATION_OFFICER, "trf.fixture.reg")

    Transfer.objects.create(
        code="TR-UIX-0",
        from_enrollment=first,
        to_cohort=second.cohort,
        requested_on=date(2026, 3, 1),
        requested_by=registrar,
        reason="PARTICIPANT_REQUEST",
        lectures_attended_at_request=2,
        same_category=True,
        status="PENDING_MANAGER",
    )
    Transfer.objects.create(
        code="TR-UIX-1",
        from_enrollment=second,
        to_cohort=first.cohort,
        requested_on=date(2026, 3, 2),
        requested_by=registrar,
        reason="CENTER_CANCELLATION",
        lectures_attended_at_request=1,
        same_category=False,
        status="REJECTED",
        rejection_reason_ar="خارج المجال",
    )
    return two_enrollments


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.2/6 leaves both cells empty. Under BR-080 that is a refusal.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_transfer_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"trf.open.{role.lower()}"))

    assert client.get(reverse("operations:transfers")).status_code == expected


def test_the_transfer_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """
    Fail-closed, and it is the SERVICE that closes it: the view has no
    ``policy.require`` of its own, ``list_transfers`` does. The polish did not
    move that, and this test is here so nobody later mistakes the view's
    silence for an unguarded door.
    """
    assert client.get(reverse("operations:transfers")).status_code == 403


def test_the_request_action_is_offered_only_where_transfer_new_grants_create(
    client: Client, two_transfers: object
) -> None:
    """
    The button is gated on CREATE over TRANSFER_NEW (§3.2/7), not over this
    screen — and the audit account is why that distinction has to be tested:
    it holds VIEW on the request form and not CREATE, so the sidebar links the
    form and this page must not offer it. The finance officer is the mirror
    case: VIEW and EDIT here, nothing at all on §3.2/7.

    Scoped past ``</nav>`` — a bare ``href in body`` is answered by the menu.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"trf.new.{role.lower()}"))
        page = (
            client.get(reverse("operations:transfers"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        may = Action.CREATE in allowed_actions(role, "transfer-new")
        assert (f'href="{reverse("operations:transfer-new")}"' in page) is may, role


def test_the_register_stayed_read_only(client: Client, two_transfers: object) -> None:
    """
    §3.2/6 is a list. Recommending, rejecting and executing live on the detail
    screen behind their own gates, and the polish added no button, no form and
    no POST target here — the view still refuses anything but GET.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "trf.readonly"))

    page = (
        client.get(reverse("operations:transfers")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    assert '<form method="post"' not in page
    assert "csrfmiddlewaretoken" not in page
    assert client.post(reverse("operations:transfers")).status_code == 405


def test_the_transfer_status_chips_count_the_rows_on_screen(
    client: Client, two_transfers: object
) -> None:
    """
    Counted over the result set this request produced, so they follow the
    filter and can be checked against the table beneath them.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "trf.chips"))

    def chips(url: str) -> dict[str, int]:
        page = client.get(url).content.decode("utf-8").split("</nav>", 1)[-1]
        return {
            label.strip(): int(n)
            for label, n in re.findall(
                r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', page
            )
        }

    everything = chips(reverse("operations:transfers"))
    assert sum(everything.values()) == 2
    assert "توزيع النتائج المعروضة" in client.get(reverse("operations:transfers")).content.decode(
        "utf-8"
    )

    filtered = chips(reverse("operations:transfers") + "?status=REJECTED")
    assert sum(filtered.values()) == 1
    assert "مرفوض" in filtered


def test_the_transfer_register_says_which_filters_are_narrowing_it(
    client: Client, two_transfers: object
) -> None:
    """
    Both filters were already controls on the screen; what was missing was any
    sign, once applied, that the table is a subset — and any way back.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "trf.active"))
    url = reverse("operations:transfers")

    plain = client.get(url).content.decode("utf-8")
    assert "نتائج مصفّاة" not in plain
    assert "إلغاء التصفية" not in plain

    for query in ("?q=TR-UIX", "?status=EXECUTED"):
        narrowed = client.get(url + query).content.decode("utf-8")
        assert "نتائج مصفّاة" in narrowed, query
        assert "إلغاء التصفية" in narrowed, query

    # A filter matching NOTHING still has to name itself in Arabic. There is no
    # row to read a label off, so a chip built from the result set printed the
    # stored code — «الحالة: EXECUTED» in front of a client. Caught in browser.
    banner = (
        client.get(url + "?status=EXECUTED")
        .content.decode("utf-8")
        .split("نتائج مصفّاة", 1)[-1]
        .split("</div>", 1)[0]
    )
    assert "منفَّذ" in banner
    assert "EXECUTED" not in banner, "the chip printed the stored code"


def test_the_transfer_filters_are_the_two_that_were_already_there(
    client: Client, two_transfers: object
) -> None:
    """
    No filter was added. The status options are still the four the screen
    always offered, over a field the table prints in every row.
    """
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "trf.filters"))

    page = (
        client.get(reverse("operations:transfers")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    assert set(re.findall(r'name="(\w+)"', page)) == {"q", "status"}
    options = set(re.findall(r'<option value="(\w*)"', page))
    assert options == {"", "PENDING_MANAGER", "PENDING_FINANCE", "EXECUTED", "REJECTED"}
    # The template no longer repeats the enum; the four come from the service.
    assert TRANSFERS_TEMPLATE.read_text(encoding="utf-8").count("<option") == 2


def test_the_two_transfer_empty_states_are_not_the_same_sentence(
    client: Client, seeded_settings: None, two_transfers: object
) -> None:
    """«None yet» and «none matched» ask for opposite next actions."""
    from apps.operations.models import Transfer

    client.force_login(_user(Role.CENTER_MANAGER, "trf.empty"))
    url = reverse("operations:transfers")

    no_match = client.get(url + "?status=EXECUTED").content.decode("utf-8")
    assert "لا طلب نقل يطابق التصفية الحالية" in no_match
    assert "عرض كل الطلبات" in no_match
    assert "لا طلبات نقل بعد" not in no_match

    Transfer.objects.all().delete()
    virgin = client.get(url).content.decode("utf-8")
    assert "لا طلبات نقل بعد" in virgin
    assert "لا طلب نقل يطابق التصفية الحالية" not in virgin


def test_the_transfer_empty_state_offers_the_form_only_where_it_is_allowed(
    client: Client, seeded_settings: None
) -> None:
    """A reader is not sent to a button that would refuse them."""
    for role, may in ((Role.CENTER_MANAGER, True), (Role.AUDIT_ACCOUNT, False)):
        client.force_login(_user(role, f"trf.empty.{role.lower()}"))
        page = (
            client.get(reverse("operations:transfers"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        assert "لا طلبات نقل بعد" in page
        assert ('class="btn2 primary empty-act"' in page) is may, role


def test_the_register_prints_only_keys_the_row_already_carried(
    client: Client, two_transfers: object
) -> None:
    """
    The reason, the request date and the two enrolment codes were all in
    ``_row`` and drawn nowhere. Printing them adds no key to the response and
    opens no field: the detail screen shows all four to these same roles.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "trf.keys"))

    page = (
        client.get(reverse("operations:transfers")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    assert "طلب المشارك" in page and "إلغاء المركز للدورة" in page  # reason_display
    assert "2026-03-01" in page  # requested_on
    assert "EN-UIX-0" in page and "EN-UIX-1" in page  # from_enrollment_code
    # A `{# … #}` comment is single-line in Django; one that wraps is text.
    assert "#}" not in page and "{#" not in page


@pytest.mark.parametrize(
    "role",
    [Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT],
)
def test_the_register_shows_no_partner_or_private_field(
    client: Client, two_transfers: object, role: str
) -> None:
    """
    No partner entitlement, no partner name, and none of the participant's
    protected identity fields — the transfer row never carried them, and the
    polish did not go looking.
    """
    client.force_login(_user(role, f"trf.priv.{role.lower()}"))

    page = (
        client.get(reverse("operations:transfers")).content.decode("utf-8").split("</nav>", 1)[-1]
    )

    for marker in TRANSFER_MUST_NOT_SHOW:
        assert marker not in page, f"{role} was shown «{marker}»"
    for field in RESTRICTED_AWAY:
        assert field not in page, f"{role} was served «{field}»"
    assert "9990001112" not in page, "an identity document number reached the page"


def test_the_transfer_register_added_no_dead_class_and_no_dependency() -> None:
    """
    Every class it draws with already existed; the page needed no new CSS.

    ``.note info`` is gone on purpose: blue is an alert, and §6.5 of the polish
    rules says explaining a rule is not one. The words are unchanged.
    """
    import re

    source = TRANSFERS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the transfer register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    assert 'class="note info"' not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The wide table scrolls inside its own wrapper, never the page body.
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    # The empty row spans the table it sits in — nine columns, counted.
    assert len(re.findall(r"<th[ >]", source)) == 9
    assert 'colspan="9"' in source
    # The actions column is NAMED, and not with `.sr-only`: that class is
    # `position:absolute` with no positioned ancestor, so in RTL it resolves
    # against the initial containing block, lands ~230px off the left edge,
    # escapes `.tbl-wrap`'s clip and drags the whole page sideways. The
    # attribute reaches assistive tech and occupies no box at all.
    assert "aria-label=\"{% translate 'إجراءات' %}\"" in source
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup


# ---------------------------------------------------------------------------
# The special-cases page — page polish
# ---------------------------------------------------------------------------
SPECIAL_CASES_TEMPLATE = Path("templates/operations/special_cases.html")

#: What the DEMO's version of this screen offers and the real system does not:
#: a «حالة جديدة» button (which in the demo only fires a toast), a per-type
#: count of recorded cases, and a log of actual special-case records. None of
#: the three is backed by a service here, and a screen that draws them is
#: lying about what the centre can do today.
DEMO_ONLY_ON_THIS_SCREEN = (
    "حالة جديدة",
    "سجل الحالات الخاصة",
    "حالة مسجّلة",
)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.2/8 leaves both cells empty. Under BR-080 that is a refusal.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_special_cases_page_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"spc.open.{role.lower()}"))

    assert client.get(reverse("operations:special-cases")).status_code == expected


def test_the_special_cases_page_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("operations:special-cases")).status_code == 403


def test_the_page_stayed_a_reading_screen(client: Client, seeded_settings: None) -> None:
    """
    No data-entry service exists for this screen, so the page teaches the types
    and says so. The polish added no form, no POST target and no button that
    would refuse whoever pressed it — and the view still takes no POST.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "spc.readonly"))

    page = (
        client.get(reverse("operations:special-cases"))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    assert "<form" not in page
    assert "csrfmiddlewaretoken" not in page
    assert "<button" not in page
    # …and the screen says why, rather than leaving the absence to be guessed.
    assert "لا إدخال من هذه الصفحة" in page


def test_the_page_draws_none_of_the_demo_only_furniture(
    client: Client, seeded_settings: None
) -> None:
    """
    The demo has a «حالة جديدة» button, a per-type count of recorded cases and
    a log of records. In the demo the button fires a toast and the counts come
    from a fixture array. Here there is no service behind any of the three.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "spc.demo"))

    page = (
        client.get(reverse("operations:special-cases"))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    for marker in DEMO_ONLY_ON_THIS_SCREEN:
        assert marker not in page, f"the page draws the demo-only «{marker}»"


def test_the_coverage_chips_agree_with_the_table_beneath_them(
    client: Client, seeded_settings: None
) -> None:
    """
    Four of the six types have a service behind them and two do not, and until
    now a reader learned that only by scanning six rows. The chips are counted
    off the same list the table renders, so the two cannot disagree — and the
    numbers are about what the SYSTEM supports, not about how many cases exist.
    """
    import re

    from apps.operations.views import special_cases_view

    client.force_login(_user(Role.CENTER_MANAGER, "spc.chips"))
    body = client.get(reverse("operations:special-cases")).content.decode("utf-8")
    page = body.split("</nav>", 1)[-1]

    chips = {
        label.strip(): int(n)
        for label, n in re.findall(
            r'<span class="chip [^"]*">([^<:]+): <span class="num">(\d+)', page
        )
    }
    assert sum(chips.values()) == 6, chips
    assert chips["مسار مبني"] == 4
    assert chips["نوع مُعرَّف — بلا خدمة تُنشئه بعد"] == 2
    # The label says what is being counted, so nobody reads it as a case count.
    assert "ما يقابله في النظام اليوم" in page
    # Every «مسار مبني» chip in the table is matched by one in the summary.
    assert page.count("مسار مبني") == 4 + 1
    assert special_cases_view is not None


def test_the_related_screens_are_linked_only_where_the_reader_may_open_them(
    client: Client, seeded_settings: None
) -> None:
    """
    §5.4 of the polish rules — a link the role cannot follow is a defect: it
    drops the reader into a refusal and writes a DENIED_ATTEMPT the screen
    invited. The three cross-references are each gated on their own screen.

    The finance officer is the case that makes this worth asserting: §3.2/6
    gives them the transfer register and §3.6/30 the clearance, so all three
    appear — while a role short of one of them must see two, not three.
    """
    from apps.people.constants import Action, Screen
    from apps.people.permissions import policy

    targets = (
        (Screen.ENROLLMENTS, "operations:enrollments"),
        (Screen.TRANSFERS, "operations:transfers"),
        (Screen.CLEARANCE, "operations:clearances"),
    )
    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        user = _user(role, f"spc.links.{role.lower()}")
        client.force_login(user)
        page = (
            client.get(reverse("operations:special-cases"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        for screen, route in targets:
            may = policy.is_allowed(user, screen, Action.VIEW)
            assert (f'href="{reverse(route)}"' in page) is may, (role, screen)


def test_the_page_renders_the_same_whether_or_not_there_is_any_data(
    client: Client, two_transfers: object
) -> None:
    """
    The strongest privacy statement this screen can make: it reads no queryset,
    so its output does not move when the database fills up.

    Asserted against live participants, enrolments and transfers rather than by
    hunting for keywords — the rule text legitimately contains «الشريك لا
    يستحق عنه شيئاً (BR-045)», which is a rule being explained, not partner
    data being shown. What must never appear is a RECORD, and that is what is
    checked here. It is worth pinning because the demo's version of this screen
    lists real cases, and a future gap could be closed by copying it.
    """
    identifiers = (
        "202690900",  # a participant number
        "سامية عبد الرحمن",  # a participant name
        "9990001112",  # an identity document number
        "EN-UIX-0",  # an enrolment code
        "TR-UIX-0",  # a transfer code
    )
    for role in (Role.CENTER_MANAGER, Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT):
        client.force_login(_user(role, f"spc.priv.{role.lower()}"))
        page = (
            client.get(reverse("operations:special-cases"))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        for value in identifiers:
            assert value not in page, f"{role} was shown the record «{value}»"
        for field in RESTRICTED_AWAY:
            assert field not in page, f"{role} was served «{field}»"
        for key in ("partner_share", "entitlement", "استحقاق الشريك"):
            assert key not in page, f"{role} was shown «{key}»"


def test_the_special_cases_page_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = SPECIAL_CASES_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the special-cases page uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    # `.form-acts` closes a form. There is no form on this page, so the class
    # was drawing a divider under nothing; the links moved into the card head.
    assert 'class="form-acts"' not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The one table keeps its scroll wrapper, and every `{# … #}` is closed on
    # its own line — Django's comment tag does not span lines, and one that
    # wraps is printed to the page as text.
    assert 'class="tbl-wrap"' in source
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"
    # `.sr-only` in a table header is `position:absolute` with no positioned
    # ancestor: in RTL it lands off the left edge and drags the page sideways.
    assert "sr-only" not in source


# ---------------------------------------------------------------------------
# The catalogue programme lists — page polish
# ---------------------------------------------------------------------------
PROGRAMS_TEMPLATE = Path("templates/catalog/programs.html")

#: One template, three sidebar entries: (route, screen).
CATALOGUE_LISTS = (
    ("catalog:programs", "programs"),
    ("catalog:short-courses", "short-courses"),
    ("catalog:online-courses", "online-courses"),
)

#: The demo's online-courses screen prints «حصة الجامعة», «حصة الشريك» and a
#: «قسمة 50/50» chip. That is a commercial term agreed with a third party, and
#: a catalogue is not where it belongs — nor is a price, which lives in the
#: dated price list. None of this may appear on any of the three.
COMMERCIAL_TERMS_OFF_THE_CATALOGUE = (
    "50/50",
    "حصة الشريك",
    "حصة الجامعة",
    "الإيراد يُقسم",
    "استحقاق الشريك",
    "partner_share",
    "entitlement",
)


@pytest.fixture
def a_catalogue(seeded_settings: None, active_semester: object) -> None:
    """The demo catalogue, so all three lists have rows of their own type."""
    from django.core.management import call_command

    call_command("seed_catalog_demo", "--approve", verbosity=0)


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/9–11 leave both cells empty. Under BR-080 that is a refusal.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_catalogue_lists_open_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, route: str, screen: str, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"cat.{screen}.{role}".lower().replace("_", ".")))

    assert client.get(reverse(route)).status_code == expected


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_the_catalogue_lists_refuse_an_anonymous_visitor(
    client: Client, seeded_settings: None, route: str, screen: str
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse(route)).status_code == 403


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_the_catalogue_lists_stayed_read_only(
    client: Client, a_catalogue: None, route: str, screen: str
) -> None:
    """
    There is no data-entry service behind the catalogue and the view takes no
    POST, so a create/edit/approve button would be a button with nothing behind
    it. The centre manager is the case that matters: §3.3 gives them C, E and A
    on all three screens, and the page must STILL draw none of the three.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.CREATE in allowed_actions(Role.CENTER_MANAGER, screen)
    assert Action.EDIT in allowed_actions(Role.CENTER_MANAGER, screen)
    assert Action.APPROVE in allowed_actions(Role.CENTER_MANAGER, screen)

    client.force_login(_user(Role.CENTER_MANAGER, f"cat.ro.{screen}"))
    page = client.get(reverse(route)).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    # …and the reader is told it is a reading screen rather than left to guess.
    assert "قراءة فقط" in page

    # The view carries no `require_http_methods`, so a POST is answered rather
    # than refused — it renders the same page and writes nothing, because there
    # is no branch that could. Asserted as it IS: 405 would be the tidier
    # contract, and adding the decorator is a behaviour change, so it is
    # reported rather than made in a presentation pass.
    from apps.catalog.models import Program

    before = Program.objects.count()
    posted = client.post(reverse(route))
    assert posted.status_code == 200
    assert Program.objects.count() == before, "a POST to a read-only list changed the catalogue"
    assert "<form" not in posted.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_no_catalogue_list_prints_a_price_or_a_partner_share(
    client: Client, a_catalogue: None, route: str, screen: str
) -> None:
    """
    The demo's online page carries «حصة الجامعة», «حصة الشريك» and a «قسمة
    50/50» chip. It is a term agreed with a third party; a catalogue is not
    where it is published, and the price itself belongs to the dated price
    list. Asserted on all three, for every role that gets through the door.
    """
    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"cat.pr.{screen}.{role}".lower().replace("_", ".")))
        page = client.get(reverse(route)).content.decode("utf-8").split("</nav>", 1)[-1]
        for term in COMMERCIAL_TERMS_OFF_THE_CATALOGUE:
            assert term not in page, f"{screen}/{role} was shown «{term}»"
        client.logout()


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_the_catalogue_counts_agree_with_the_rows_beneath_them(
    client: Client, a_catalogue: None, route: str, screen: str
) -> None:
    """
    Counted off the list the template iterates, so the chips cannot disagree
    with the table — and they describe what is on screen, not the catalogue.
    """
    import re

    from apps.catalog.models import Program
    from apps.catalog.views import TYPE_BY_SCREEN

    client.force_login(_user(Role.CENTER_MANAGER, f"cat.n.{screen}"))
    page = client.get(reverse(route)).content.decode("utf-8").split("</nav>", 1)[-1]

    chips = {
        label.strip(): int(n)
        for label, n in re.findall(
            r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', page
        )
    }
    expected = Program.objects.filter(program_type=TYPE_BY_SCREEN[screen]).count()
    assert expected, f"{screen} seeded no rows, so this proves nothing"
    assert sum(chips.values()) == expected
    assert "توزيع النتائج المعروضة" in page
    # Each drawn row is a real programme of THIS type and no other.
    assert len(re.findall(r"<tr>\s*<td dir=\"ltr\"", page)) == expected


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_the_catalogue_empty_state_is_honest_and_offers_nothing_it_cannot_do(
    client: Client, seeded_settings: None, route: str, screen: str
) -> None:
    """
    On an unseeded database all three are empty. The old page said «لا توجد
    برامج» in a bare cell; the state now says what the emptiness means and
    where definition and pricing actually live — and offers no action, because
    there is no route behind one.
    """
    client.force_login(_user(Role.CENTER_MANAGER, f"cat.e.{screen}"))
    page = client.get(reverse(route)).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "لا برامج معرَّفة في هذه الفئة بعد" in page
    assert 'class="empty-body"' in page
    assert "empty-act" not in page, "the empty state offers an action that has no route"


@pytest.mark.parametrize(("route", "screen"), CATALOGUE_LISTS)
def test_each_catalogue_screen_says_which_one_it_is(
    client: Client, a_catalogue: None, route: str, screen: str
) -> None:
    """
    One template, three screens. The eyebrow is shared, the title and the
    subtitle are not — a page that reads identically on all three teaches the
    reader nothing about which of the six §3.3 rows they are standing on.
    """
    from apps.catalog.views import SUBTITLE_BY_SCREEN

    client.force_login(_user(Role.CENTER_MANAGER, f"cat.t.{screen}"))
    page = client.get(reverse(route)).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "البرامج والأسعار" in page
    assert str(SUBTITLE_BY_SCREEN[screen]) in page
    for other, subtitle in SUBTITLE_BY_SCREEN.items():
        if other != screen:
            assert str(subtitle) not in page, f"{screen} shows {other}'s subtitle"


def test_the_catalogue_list_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PROGRAMS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the catalogue list uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    # Seven columns, and the empty row spans the table it sits in.
    assert len(re.findall(r"<th[ >]", source)) == 7
    assert 'colspan="7"' in source
    # The actions column is named by attribute: `.sr-only` is `position:absolute`
    # with no positioned ancestor, so in RTL it escapes `.tbl-wrap` and drags
    # the page sideways.
    assert "aria-label=\"{% translate 'إجراءات' %}\"" in source
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup
    # Every `{# … #}` closes on its own line — Django's tag does not span lines.
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The programme detail card — page polish
# ---------------------------------------------------------------------------
PROGRAM_DETAIL_TEMPLATE = Path("templates/catalog/program_detail.html")


def _a_program(program_type: str) -> str:
    from apps.catalog.models import Program

    code = Program.objects.filter(program_type=program_type).values_list("code", flat=True).first()
    assert code, f"no {program_type} was seeded, so this proves nothing"
    return str(code)


@pytest.mark.parametrize("program_type", ["DIPLOMA", "SHORT_COURSE", "ONLINE_COURSE"])
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/9–11 leave both cells empty. Under BR-080 that is a refusal.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_programme_card_opens_exactly_where_the_matrix_says(
    client: Client, a_catalogue: None, program_type: str, role: str, expected: int
) -> None:
    """
    The gate is the SCREEN THE PROGRAMME BELONGS TO, decided from its type —
    and the view has no ``policy.require`` of its own, ``get_program`` has. The
    polish did not move that.
    """
    code = _a_program(program_type)
    client.force_login(_user(role, f"pd.{program_type}.{role}".lower().replace("_", ".")))

    assert client.get(reverse("catalog:program-detail", args=[code])).status_code == expected


def test_the_programme_card_refuses_an_anonymous_visitor(client: Client, a_catalogue: None) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    code = _a_program("DIPLOMA")

    assert client.get(reverse("catalog:program-detail", args=[code])).status_code == 403


@pytest.mark.parametrize("program_type", ["DIPLOMA", "SHORT_COURSE", "ONLINE_COURSE"])
def test_the_programme_card_stayed_read_only(
    client: Client, a_catalogue: None, program_type: str
) -> None:
    """
    ``can_edit`` is in the context and there is no editing service and no POST
    branch behind it. The centre manager holds EDIT on all three screens, and
    the card must still draw no form and no button.
    """
    from apps.catalog.views import TYPE_BY_SCREEN
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    screen = next(s for s, t in TYPE_BY_SCREEN.items() if t == program_type)
    assert Action.EDIT in allowed_actions(Role.CENTER_MANAGER, screen)

    code = _a_program(program_type)
    client.force_login(_user(Role.CENTER_MANAGER, f"pd.ro.{program_type}".lower()))
    page = (
        client.get(reverse("catalog:program-detail", args=[code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    # The word «تعديل» DOES appear — in the subtitle, saying editing is not
    # done here. What must not appear is a control: the only link on the page
    # is the ghost way back, and no primary action is drawn at all.
    assert 'class="btn2 primary"' not in page
    assert page.count("<a class=") == page.count('<a class="btn2 ghost"') == 1


@pytest.mark.parametrize("program_type", ["DIPLOMA", "SHORT_COURSE", "ONLINE_COURSE"])
def test_the_programme_card_prints_no_price_and_no_partner_share(
    client: Client, a_catalogue: None, program_type: str
) -> None:
    """
    Subject prices and the consumables figure were on this page before and stay
    — they are catalogue inputs. What must never appear is the programme's fee
    from a price list, or anything about a third party's cut of it.
    """
    code = _a_program(program_type)
    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"pd.pr.{program_type}.{role}".lower().replace("_", ".")))
        page = (
            client.get(reverse("catalog:program-detail", args=[code]))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )
        for term in COMMERCIAL_TERMS_OFF_THE_CATALOGUE:
            assert term not in page, f"{program_type}/{role} was shown «{term}»"
        client.logout()


def test_the_card_shows_the_subject_total_without_inventing_a_verdict(
    client: Client, a_catalogue: None
) -> None:
    """
    ``subject_total`` is Σ subject prices — the LEFT side of BR-006. The other
    side is the course fee from the effective price list, which this view does
    not read. The demo prints «مطابق» or «فرق N» anyway; a page that holds one
    side of an equation may not publish its result.
    """
    import re

    from django.template.defaultfilters import floatformat

    from apps.catalog.models import Program
    from apps.catalog.services import pricing_service

    program = Program.objects.filter(program_type="DIPLOMA", subjects__isnull=False).first()
    assert program is not None, "no diploma with subjects was seeded"
    total = pricing_service.subject_price_total(program)
    assert total > 0, "a zero total would make this assertion vacuous"

    client.force_login(_user(Role.CENTER_MANAGER, "pd.br006"))
    page = (
        client.get(reverse("catalog:program-detail", args=[program.code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    # The figure the page really holds, and the rule that weighs it.
    assert "مجموع أسعار المواد" in page
    # USE_L10N formats the Decimal, so compare against what a template renders.
    assert {str(total), floatformat(total, 3), floatformat(total, -3)} & set(
        re.findall(r"[\d,.]+", page)
    ), f"the subject total {total} is not printed"
    assert "BR-006" in page
    # …and no verdict it cannot compute.
    for verdict in ("مطابق", "فرق ", "غير مطابق", "مخالف"):
        assert verdict not in page, f"the card published «{verdict}» from one side of BR-006"


def test_only_the_diploma_is_told_that_its_subject_list_is_empty(
    client: Client, a_catalogue: None
) -> None:
    """
    ``Subject`` is documented as a DIPLOMA subject (DATA_MODEL §5.4) and BR-006
    weighs their sum, so an empty list is worth saying there. A short course
    legitimately has none, and an empty section on every one of them would be
    noise dressed as information.
    """
    from apps.catalog.models import Program, Subject

    Subject.objects.all().delete()
    client.force_login(_user(Role.CENTER_MANAGER, "pd.empty"))

    def page_for(program_type: str) -> str:
        code = _a_program(program_type)
        return (
            client.get(reverse("catalog:program-detail", args=[code]))
            .content.decode("utf-8")
            .split("</nav>", 1)[-1]
        )

    diploma = page_for("DIPLOMA")
    assert "لا مواد معرَّفة لهذا الدبلوم بعد" in diploma
    assert "BR-007" in diploma, "the empty state does not say a zero price is legal"

    for other in ("SHORT_COURSE", "ONLINE_COURSE"):
        body = page_for(other)
        assert "لا مواد معرَّفة لهذا الدبلوم بعد" not in body, other
        assert "مجموع أسعار المواد" not in body, other
    assert Program.objects.exists()


@pytest.mark.parametrize("program_type", ["DIPLOMA", "SHORT_COURSE", "ONLINE_COURSE"])
def test_the_card_offers_the_way_back_to_its_own_list(
    client: Client, a_catalogue: None, program_type: str
) -> None:
    """
    A reader arrives here from one of three lists and the page had no way back
    but the sidebar. The link is the screen this programme belongs to — and it
    cannot refuse them, because they passed that same gate to be here at all.
    """
    from apps.catalog.views import LIST_ROUTE_BY_SCREEN, TYPE_BY_SCREEN

    screen = next(s for s, t in TYPE_BY_SCREEN.items() if t == program_type)
    code = _a_program(program_type)
    client.force_login(_user(Role.CENTER_MANAGER, f"pd.back.{program_type}".lower()))

    page = (
        client.get(reverse("catalog:program-detail", args=[code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )
    back = reverse(LIST_ROUTE_BY_SCREEN[screen])
    assert f'href="{back}"' in page
    assert client.get(back).status_code == 200
    # …and not one of the other two lists.
    for other, route in LIST_ROUTE_BY_SCREEN.items():
        if other != screen:
            assert f'href="{reverse(route)}"' not in page, f"{program_type} links {other}"


def test_the_programme_card_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PROGRAM_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the programme card uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {c for m in re.finditer(r'class="([^"{}]+)"', source) for c in m.group(1).split()}
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The key/value block is a `.dl`, not a table: one fewer thing to scroll.
    assert 'class="dl"' in source
    # The subject table keeps its wrapper, and its empty row spans it.
    assert 'class="tbl-wrap"' in source
    assert 'colspan="4"' in source
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The dated price lists — page polish
# ---------------------------------------------------------------------------
PRICELISTS_TEMPLATE = Path("templates/catalog/pricelists.html")


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/13 leaves both cells empty. Under BR-080 that is a refusal —
        # and Q-14 is explicit that the price list is NOT the finance
        # manager's to approve, so his absence here is the rule, not a gap.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_price_lists_open_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"pl.{role}".lower().replace("_", ".")))

    assert client.get(reverse("catalog:pricelists")).status_code == expected


def test_the_price_lists_refuse_an_anonymous_visitor(client: Client, seeded_settings: None) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("catalog:pricelists")).status_code == 403


def test_the_price_list_index_stayed_read_only(client: Client, a_catalogue: None) -> None:
    """
    §3.3/13 grants the centre manager C and E, and ``can_record_approval`` is
    in the context — but there is no data-entry service behind this screen and
    the view takes no POST, so a create or edit button would be a button with
    nothing behind it. Approval is not even offered to anybody: the row grants
    APPROVE to no role, because the President approves outside the system
    (footnote 8, D-31).
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.CREATE in allowed_actions(Role.CENTER_MANAGER, "pricelists")
    assert Action.EDIT in allowed_actions(Role.CENTER_MANAGER, "pricelists")
    for role in (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT):
        assert Action.APPROVE not in allowed_actions(role, "pricelists")

    client.force_login(_user(Role.CENTER_MANAGER, "pl.readonly"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    assert 'class="btn2 primary"' not in page, "a primary action with no route behind it"
    # …and the reader is told it is a reading screen rather than left to guess.
    assert "قراءة فقط" in page

    # The view carries no `require_http_methods`, so a POST is answered rather
    # than refused — it renders the same page and writes nothing, because there
    # is no branch that could. Asserted as it IS: 405 would be the tidier
    # contract, and adding the decorator is a behaviour change, so it is
    # reported rather than made in a presentation pass.
    from apps.catalog.models import PriceList

    before = PriceList.objects.count()
    posted = client.post(reverse("catalog:pricelists"))
    assert posted.status_code == 200
    assert PriceList.objects.count() == before, "a POST to a read-only list wrote something"
    assert "<form" not in posted.content.decode("utf-8").split("</nav>", 1)[-1]


def test_the_price_list_index_prints_no_partner_or_revenue_term(
    client: Client, a_catalogue: None
) -> None:
    """
    A price list is a commercial document, and the demo published a third
    party's cut of it beside the price. Nothing about a partner's share is
    this screen's to print, for any role that gets through the door.
    """
    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"pl.pr.{role}".lower().replace("_", ".")))
        page = (
            client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]
        )
        for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "الإيراد يُقسم", "partner share"):
            assert term not in page, f"pricelists/{role} was shown «{term}»"
        client.logout()


def test_the_price_list_row_prints_the_dates_it_actually_carries(
    client: Client, a_catalogue: None
) -> None:
    """
    The old row showed one date and called it «السريان». A dated list has two —
    issued and effective — and BR-008 turns on the difference: the list is
    proposed on one day and comes into force on another.
    """
    from apps.catalog.models import PriceList

    price_list = PriceList.objects.first()
    assert price_list is not None, "no price list was seeded, so this proves nothing"

    client.force_login(_user(Role.CENTER_MANAGER, "pl.dates"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "تاريخ الإصدار" in page
    assert "تاريخ السريان" in page
    assert price_list.issued_on.strftime("%Y/%m/%d") in page
    assert price_list.effective_from.strftime("%Y/%m/%d") in page
    assert price_list.issued_on != price_list.effective_from, "the two dates are the same row"
    # …and every other field is one the row already carried.
    assert price_list.code in page
    assert price_list.name_ar in page
    assert price_list.semester.name_ar in page
    assert str(price_list.get_status_display()) in page
    assert price_list.decision_reference in page


def test_the_price_list_index_publishes_no_effective_verdict_it_cannot_compute(
    client: Client, a_catalogue: None
) -> None:
    """
    "Which list is in force today" is BR-012, and it is answered by the pricing
    service for a GIVEN date. This view never calls it, so the page says where
    the answer comes from instead of guessing it from the sort order.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.effective"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "BR-012" in page
    assert 'class="hint boxed"' in page
    for verdict in ("السارية اليوم:", "القائمة الحالية", "سارية الآن", "غير سارية"):
        assert verdict not in page, f"the index published «{verdict}» it never computed"


def test_the_price_list_status_chips_count_the_rows_beneath_them(
    client: Client, a_catalogue: None
) -> None:
    """
    Tallied off the list the template iterates, so the chips cannot disagree
    with the table — and a state nobody is in gets no chip rather than a zero.
    """
    import re

    from apps.catalog.models import PriceList

    client.force_login(_user(Role.CENTER_MANAGER, "pl.chips"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    chips = {
        label.strip(): int(n)
        for label, n in re.findall(
            r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', page
        )
    }
    total = PriceList.objects.count()
    assert total, "no price list was seeded, so this proves nothing"
    assert sum(chips.values()) == total
    assert "توزيع النتائج المعروضة" in page
    # Every state present in the data has a chip, and no state absent from it does.
    present = {str(pl.get_status_display()) for pl in PriceList.objects.all()}
    assert set(chips) == present
    assert 0 not in chips.values(), "a zero chip was drawn for a state nothing is in"


def test_the_price_list_empty_state_says_what_the_emptiness_costs(
    client: Client, seeded_settings: None
) -> None:
    """
    On an unseeded database the table is empty. The old page said «لا توجد
    قوائم أسعار» in a bare cell; the state now says what cannot happen without
    one, and offers no action, because there is no route behind one.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pl.empty"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "لا قوائم أسعار معرَّفة بعد" in page
    assert 'class="empty-body"' in page
    assert "BR-008" in page
    assert "empty-act" not in page, "the empty state offers an action that has no route"
    # The chips describe what is on screen, so an empty table draws none.
    assert "توزيع النتائج المعروضة" not in page


def test_each_price_list_row_links_to_its_own_detail_page(
    client: Client, a_catalogue: None
) -> None:
    """
    The only link the table draws is the row's own list, and the reader passed
    this screen's gate to be here — so it cannot send them into a refusal.
    """
    from apps.catalog.models import PriceList

    code = PriceList.objects.values_list("code", flat=True).first()
    assert code, "no price list was seeded, so this proves nothing"

    client.force_login(_user(Role.CENTER_MANAGER, "pl.link"))
    page = client.get(reverse("catalog:pricelists")).content.decode("utf-8").split("</nav>", 1)[-1]

    detail = reverse("catalog:pricelist-detail", args=[code])
    assert f'href="{detail}"' in page
    assert client.get(detail).status_code == 200
    # One link per row and nothing else — no clickable row, no invented action.
    rows = PriceList.objects.count()
    assert page.count("<a class=") == page.count('<a class="btn2 ghost"') == rows


def test_the_price_list_index_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PRICELISTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the price list index uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    # Eight columns, and the empty row spans the table it sits in.
    assert len(re.findall(r"<th[ >]", source)) == 8
    assert 'colspan="8"' in source
    # The actions column is named by attribute: `.sr-only` is `position:absolute`
    # with no positioned ancestor, so in RTL it escapes `.tbl-wrap` and drags
    # the page sideways.
    assert "aria-label=\"{% translate 'إجراءات' %}\"" in source
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup
    # Every `{# … #}` closes on its own line — Django's tag does not span lines.
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The dated price list detail — page polish
# ---------------------------------------------------------------------------
PRICELIST_DETAIL_TEMPLATE = Path("templates/catalog/pricelist_detail.html")


def _a_price_list() -> str:
    from apps.catalog.models import PriceList

    code = PriceList.objects.values_list("code", flat=True).first()
    assert code, "no price list was seeded, so this proves nothing"
    return str(code)


def _detail(client: Client, code: str) -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    response = client.get(reverse("catalog:pricelist-detail", args=[code]))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/13 leaves both cells empty. Under BR-080 that is a refusal, and
        # Q-14 is explicit that the price list is not the finance manager's.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_price_list_card_opens_exactly_where_the_matrix_says(
    client: Client, a_catalogue: None, role: str, expected: int
) -> None:
    """The gate is ``get_price_list``'s, not the view's. The polish did not move it."""
    code = _a_price_list()
    client.force_login(_user(role, f"pld.{role}".lower().replace("_", ".")))

    assert client.get(reverse("catalog:pricelist-detail", args=[code])).status_code == expected


def test_the_price_list_card_refuses_an_anonymous_visitor(
    client: Client, a_catalogue: None
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    code = _a_price_list()

    assert client.get(reverse("catalog:pricelist-detail", args=[code])).status_code == 403


def test_the_price_list_card_stayed_read_only(client: Client, a_catalogue: None) -> None:
    """
    ``can_edit`` is in the context and there is no editing service, no edit
    route and no POST branch behind it. There is not even a URL to post to:
    the catalogue exposes two routes, both GET reads.
    """
    from django.urls import NoReverseMatch

    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.EDIT in allowed_actions(Role.CENTER_MANAGER, "pricelists")
    for name in ("pricelist-edit", "pricelist-approve", "pricelist-item-new"):
        with pytest.raises(NoReverseMatch):
            reverse(f"catalog:{name}")

    code = _a_price_list()
    client.force_login(_user(Role.CENTER_MANAGER, "pld.readonly"))
    page = _detail(client, code)

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    assert 'class="btn2 primary"' not in page, "a primary action with no route behind it"
    # No modal, no dialog, nothing that opens over the page.
    for furniture in ("<dialog", "modal", "x-show", "data-bs-toggle", "aria-haspopup"):
        assert furniture not in page, f"the card drew «{furniture}»"
    assert "قراءة فقط" in page

    # The view carries no `require_http_methods`, so a POST renders the page
    # and writes nothing, because there is no branch that could. Asserted as
    # it IS — 405 is a behaviour change, not a presentation one.
    from apps.catalog.models import PriceListItem

    before = PriceListItem.objects.count()
    posted = client.post(reverse("catalog:pricelist-detail", args=[code]))
    assert posted.status_code == 200
    assert PriceListItem.objects.count() == before, "a POST to a read-only card wrote something"


def test_the_price_list_card_offers_the_way_back_to_the_index(
    client: Client, a_catalogue: None
) -> None:
    """
    The reader arrives from the index and had no way back but the sidebar. The
    link cannot refuse them: it is the same screen gate they just passed.
    """
    code = _a_price_list()
    client.force_login(_user(Role.CENTER_MANAGER, "pld.back"))
    page = _detail(client, code)

    back = reverse("catalog:pricelists")
    assert f'href="{back}"' in page
    assert client.get(back).status_code == 200
    # …and it is the only link on the page. Nothing else here has a route.
    assert page.count("<a class=") == page.count('<a class="btn2 ghost"') == 1


def test_the_price_list_identity_renders_from_the_real_row(
    client: Client, a_catalogue: None
) -> None:
    """
    The identity was scattered across an `<h1>` and two `.note` alerts. It is
    a `.dl` now — every field read off the row, and an unrecorded one named as
    unrecorded rather than left as an empty cell.
    """
    from apps.catalog.models import PriceList

    price_list = PriceList.objects.first()
    assert price_list is not None

    client.force_login(_user(Role.CENTER_MANAGER, "pld.identity"))
    page = _detail(client, price_list.code)

    assert 'class="dl"' in page
    assert price_list.code in page
    assert price_list.name_ar in page
    assert price_list.semester.name_ar in page
    assert price_list.issued_on.strftime("%Y/%m/%d") in page
    assert price_list.effective_from.strftime("%Y/%m/%d") in page
    assert str(price_list.get_status_display()) in page
    assert price_list.proposed_by_text in page
    assert price_list.approved_by_text in page
    assert price_list.decision_reference in page
    for label in ("الرمز", "الفصل", "تاريخ الإصدار", "تاريخ السريان", "مرجع القرار"):
        assert label in page, label

    # An absent field is named, not blanked.
    price_list.decision_reference = ""
    price_list.proposed_by_text = ""
    price_list.save()
    blank = _detail(client, price_list.code)
    assert "غير مسجَّل" in blank


def test_the_frozen_state_is_said_where_it_is_true_and_only_there(
    client: Client, a_catalogue: None
) -> None:
    """
    D-14 freezes an approved list. That is the single most consequential fact
    on the page, and it was a `.note` — an alert box, for a rule nobody is
    breaking. It is a chip plus a hint now, and a draft gets neither.
    """
    from apps.catalog.models import PriceList, PriceListStatus

    price_list = PriceList.objects.first()
    assert price_list is not None
    assert price_list.is_frozen, "the fixture approves the list; this proves nothing otherwise"

    client.force_login(_user(Role.CENTER_MANAGER, "pld.frozen"))
    frozen = _detail(client, price_list.code)
    assert "مجمَّدة" in frozen
    assert "D-14" in frozen

    price_list.status = PriceListStatus.DRAFT
    price_list.save()
    draft = _detail(client, price_list.code)
    assert not price_list.is_frozen
    assert "مجمَّدة" not in draft, "a draft was told it is frozen"
    assert str(PriceListStatus.DRAFT.label) in draft


def test_a_missing_deposit_is_not_drawn_as_a_zero(client: Client, a_catalogue: None) -> None:
    """
    BR-096 — a programme with no deposit policy carries NO deposit line, which
    is a different statement from a deposit of zero. The old cell tested
    ``{% if item.deposit_amount %}``, which cannot tell the two apart, and
    printed a dash for both. Absence is now named.
    """
    from apps.catalog.models import PriceListItem

    with_deposit = PriceListItem.objects.filter(deposit_amount__isnull=False).first()
    without = PriceListItem.objects.filter(deposit_amount__isnull=True).first()
    assert with_deposit is not None, "no item carries a deposit, so this proves nothing"
    assert without is not None, "every item carries a deposit, so this proves nothing"
    assert with_deposit.deposit_policy is not None
    assert without.deposit_policy is None

    client.force_login(_user(Role.CENTER_MANAGER, "pld.br096"))
    page = _detail(client, _a_price_list())

    # The absence is a named state, not a blank and not a number.
    assert "لا تأمين" in page
    assert "BR-096" in page
    # …and the present one is an amount, drawn differently from the absence.
    assert str(with_deposit.deposit_policy.name_ar) in page
    assert '<span class="num">' in page


def test_a_zero_deposit_cannot_exist_so_absence_is_the_only_empty(
    a_catalogue: None,
) -> None:
    """
    The display distinction is only honest because the data cannot hold a zero
    deposit: C-26 pairs amount with policy, and the amount must be positive.
    Pinned here so a later migration cannot quietly make «لا تأمين» ambiguous.
    """
    from decimal import Decimal

    from django.db import IntegrityError, transaction

    from apps.catalog.models import PriceListItem

    item = PriceListItem.objects.filter(deposit_amount__isnull=True).first()
    assert item is not None

    with pytest.raises(IntegrityError), transaction.atomic():
        PriceListItem.objects.filter(pk=item.pk).update(
            deposit_amount=Decimal("0.000"), deposit_policy=None
        )


def test_no_registration_fee_rule_is_not_drawn_as_a_fee_of_zero(
    client: Client, a_catalogue: None
) -> None:
    """
    BR-009 / T-098 — JCPA, PMP and drug registration charge NO registration
    fee. A stored zero would say "we charged nothing", which is a different
    claim, and the resolver treats the two differently. A zero is a number on
    screen; an absence is a named state.
    """
    from decimal import Decimal

    from apps.catalog.models import PriceList, RegistrationFeeRule

    price_list = PriceList.objects.first()
    assert price_list is not None
    none_rule = RegistrationFeeRule.objects.filter(price_list=price_list, fee__isnull=True).first()
    assert none_rule is not None, "no fee-less rule was seeded, so this proves nothing"

    # A genuine zero, standing beside the genuine absence on the same list.
    zero_rule = RegistrationFeeRule.objects.filter(price_list=price_list, fee__isnull=False).first()
    assert zero_rule is not None
    zero_rule.fee = Decimal("0.000")
    zero_rule.exception_note_ar = "رسم صفري — اختبار"
    zero_rule.save()

    client.force_login(_user(Role.CENTER_MANAGER, "pld.br009"))
    page = _detail(client, price_list.code)

    assert "بلا رسوم تسجيل" in page, "an absent registration fee was not named"
    assert "رسم صفري — اختبار" in page, "the zero-fee row is not on the page at all"
    # The two are drawn by different devices: the absence is a chip, the zero
    # is a number. Neither can be mistaken for the other.
    assert (
        page.count('<span class="chip">بلا رسوم تسجيل</span>')
        == RegistrationFeeRule.objects.filter(price_list=price_list, fee__isnull=True).count()
    )
    assert "T-098" in page


def test_the_fee_rule_names_the_category_instead_of_printing_its_code(
    client: Client, a_catalogue: None
) -> None:
    """
    ``participant_category`` is a bare code with no choices on the model, so
    the old cell put CENTER and UNIVERSITY in front of the client — the defect
    the participant registry and the transfer register were both fixed for.
    """
    from apps.catalog.models import PriceList, RegistrationFeeRule
    from apps.people.constants import PARTICIPANT_CATEGORY_CHOICES

    price_list = PriceList.objects.first()
    assert price_list is not None
    codes = set(
        RegistrationFeeRule.objects.filter(price_list=price_list).values_list(
            "participant_category", flat=True
        )
    )
    assert codes, "no fee rule was seeded, so this proves nothing"

    client.force_login(_user(Role.CENTER_MANAGER, "pld.category"))
    page = _detail(client, price_list.code)

    labels = dict(PARTICIPANT_CATEGORY_CHOICES)
    for code in codes:
        assert str(labels[code]) in page, f"{code} is not named on the page"
        assert code not in page, f"the stored code «{code}» was printed at the client"
    # The general rule says so in words rather than leaving the cell empty.
    assert "كل البرامج" in page


def test_the_card_prints_only_item_fields_the_row_already_carried(
    client: Client, a_catalogue: None
) -> None:
    """
    Course fee, level, deposit, deposit policy and notes — the five the page
    already showed, plus the programme's own code beneath its name. Nothing
    was fetched that the view did not already hand over.
    """
    from apps.catalog.models import PriceListItem

    item = PriceListItem.objects.select_related("program").first()
    assert item is not None

    client.force_login(_user(Role.CENTER_MANAGER, "pld.items"))
    page = _detail(client, _a_price_list())

    assert item.program.name_ar in page
    assert item.program.code in page
    # USE_L10N formats the Decimal, so compare against what a template renders.
    import re

    from django.template.defaultfilters import floatformat

    fee = item.course_fee
    assert {str(fee), floatformat(fee, 3), floatformat(fee, -3)} & set(
        re.findall(r"[\d,.]+", page)
    ), f"the course fee {fee} is not printed"
    for header in ("رسوم الدورة", "التأمين", "سياسة التأمين", "المستوى", "ملاحظات"):
        assert header in page, header
    # A non-levelled item says so rather than showing an empty cell.
    assert PriceListItem.objects.filter(level__isnull=True).exists()
    assert "بلا مستويات" in page
    # …and no figure this page never held: no subject total, no BR-006 verdict,
    # no "effective today", no consumables read off the programme.
    for invented in ("مجموع أسعار المواد", "مطابق", "سارية اليوم", "المستهلكات"):
        assert invented not in page, f"the card published «{invented}»"


def test_both_price_list_tables_have_an_empty_state_of_their_own(
    client: Client, a_catalogue: None
) -> None:
    """
    Two tables, two different emptinesses, and the old page said «لا توجد
    بنود» and «لا توجد قواعد» in bare cells. A list with no items prices
    nothing; a list with no fee rules is not a list of zero fees.
    """
    from apps.catalog.models import PriceList, PriceListItem, RegistrationFeeRule

    price_list = PriceList.objects.first()
    assert price_list is not None
    RegistrationFeeRule.objects.filter(price_list=price_list).delete()
    PriceListItem.objects.filter(price_list=price_list).delete()

    client.force_login(_user(Role.CENTER_MANAGER, "pld.empty"))
    page = _detail(client, price_list.code)

    assert "لا بنود على هذه القائمة" in page
    assert "لا قواعد رسوم تسجيل على هذه القائمة" in page
    assert page.count('class="empty-body"') == 2
    assert "empty-act" not in page, "an empty state offers an action that has no route"
    # The counts describe rows on screen, so an empty table shows no chip.
    assert "بند واحد" not in page and "قاعدة واحدة" not in page


def test_the_price_list_card_prints_no_partner_or_revenue_term(
    client: Client, a_catalogue: None
) -> None:
    """
    This is the screen where a third party's cut would be most tempting to
    print: it is the one page in the system that carries the actual prices.
    Asserted for every role that gets through the door.
    """
    code = _a_price_list()
    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"pld.pr.{role}".lower().replace("_", ".")))
        page = _detail(client, code)
        for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "partner share", "revenue"):
            assert term not in page, f"pricelist detail/{role} was shown «{term}»"
        client.logout()


def test_the_price_list_card_leaks_none_of_its_own_commentary(
    client: Client, a_catalogue: None
) -> None:
    """A developer's note above a client's prices is the 8I defect verbatim."""
    client.force_login(_user(Role.CENTER_MANAGER, "pld.comment"))
    page = _detail(client, _a_price_list())

    for note in ("A-05", "catalog_price_item_deposit_paired", "{%", "{{", "{#"):
        assert note not in page, f"the template leaked «{note}»"


def test_the_price_list_card_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PRICELIST_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the price list card uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    # The identity is a `.dl`; only the two genuinely tabular blocks are tables,
    # and each keeps its scroll wrapper and spans its own width when empty.
    assert 'class="dl"' in source
    assert source.count('class="tbl-wrap"') == 2
    assert 'colspan="6"' in source and 'colspan="4"' in source
    # Every `<th>` carries text, so nothing here needs `.sr-only` — which is
    # `position:absolute` with no positioned ancestor and drags an RTL page.
    assert "sr-only" not in source.split("{% endcomment %}", 1)[-1]
    assert not re.search(r"<th[^>]*>\s*</th>", source)
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The cohorts register — page polish
# ---------------------------------------------------------------------------
COHORTS_TEMPLATE = Path("templates/operations/cohorts.html")


@pytest.fixture
def three_cohorts(seeded_settings: None, active_semester: object) -> object:
    """
    Three cohorts that differ where the page has to show a difference.

    One planned with a trainer and a place, one running with neither, and one
    run with a partner under an agreement that carries a percentage — the
    partner's NAME belongs on an operations register, the rate behind it does
    not, and only a real agreement can prove the page does not follow the
    relation into it.
    """
    from datetime import date

    from django.core.management import call_command

    from apps.catalog.models import Program
    from apps.operations.models import Cohort, CohortStatus
    from apps.partners.models import Agreement, Partner

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    semester = active_semester
    programs = list(Program.objects.order_by("code")[:3])
    assert len(programs) == 3

    partner = Partner.objects.create(
        code="PT-UIX", name_ar="شركة التدريب المتقدّم", partner_type="COMPANY"
    )
    agreement = Agreement.objects.create(
        agreement_number="AG-UIX-1",
        partner=partner,
        title_ar="اتفاقية تشغيل مشترك",
        signed_on=date(2026, 1, 1),
        valid_from=date(2026, 1, 1),
        valid_to=date(2027, 1, 1),
        calculation_model="PERCENT",
        percent_rate="50.00",
    )

    rows = (
        ("CO-UIC-1", programs[0], CohortStatus.PLANNED, "د. سميرة العبادي", "قاعة 3", None),
        ("CO-UIC-2", programs[1], CohortStatus.RUNNING, "", "", None),
        ("CO-UIC-3", programs[2], CohortStatus.RUNNING, "", "", agreement),
    )
    for code, program, status, trainer, location, deal in rows:
        Cohort.objects.create(
            code=code,
            program=program,
            semester=semester,
            name_ar=f"دفعة {code}",
            starts_on=semester.starts_on,
            ends_on=semester.ends_on,
            capacity=20,
            status=status,
            trainer_name=trainer,
            location=location,
            agreement=deal,
        )
    return partner


def _cohorts(client: Client, params: str = "") -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    response = client.get(reverse("operations:cohorts") + params)
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.FINANCE_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/12 leaves both cells empty. Under BR-080 that is a refusal.
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_cohorts_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"coh.{role}".lower().replace("_", ".")))

    assert client.get(reverse("operations:cohorts")).status_code == expected


def test_the_cohorts_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("operations:cohorts")).status_code == 403


def test_the_open_cohort_form_is_offered_only_where_create_is_granted(
    client: Client, three_cohorts: object
) -> None:
    """
    This screen is NOT read-only, and that is the difference from the
    catalogue: ``open_cohort`` is a real service and the view answers POST on
    this same URL. So the form stays — for the one role §3.3/12 grants CREATE,
    and for nobody else.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.CREATE in allowed_actions(Role.CENTER_MANAGER, "cohorts")

    client.force_login(_user(Role.CENTER_MANAGER, "coh.create.mgr"))
    manager = _cohorts(client)
    assert "فتح دفعة جديدة" in manager
    assert "csrfmiddlewaretoken" in manager
    assert 'class="btn2 primary"' in manager

    for role in (Role.REGISTRATION_OFFICER, Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT):
        assert Action.CREATE not in allowed_actions(role, "cohorts")
        client.logout()
        client.force_login(_user(role, f"coh.nocreate.{role}".lower().replace("_", ".")))
        page = _cohorts(client)
        assert "فتح دفعة جديدة" not in page, f"{role} was offered the form"
        assert "csrfmiddlewaretoken" not in page, f"{role} was handed a write token"
        assert 'class="btn2 primary"' not in page


def test_the_open_cohort_route_still_refuses_a_role_the_page_never_offered_it_to(
    client: Client, three_cohorts: object
) -> None:
    """The button's absence is presentation; the refusal is the view's."""
    from apps.operations.models import Cohort

    before = Cohort.objects.count()
    client.force_login(_user(Role.REGISTRATION_OFFICER, "coh.post.reg"))

    assert client.post(reverse("operations:cohorts"), {}).status_code == 403
    assert Cohort.objects.count() == before


def test_the_cohort_row_prints_the_keys_the_projection_already_carried(
    client: Client, three_cohorts: object
) -> None:
    """
    ``list_cohorts`` has projected the semester, the trainer, the place and
    the seats remaining all along and the page drew none of them. Printing
    them adds no query and opens no field.
    """
    from apps.operations.services import cohort_service
    from apps.people.models import User

    actor = User.objects.filter(role=Role.CENTER_MANAGER).first() or _user(
        Role.CENTER_MANAGER, "coh.rows.mgr"
    )
    rows = {r["code"]: r for r in cohort_service.list_cohorts(actor=actor)}
    row = rows["CO-UIC-1"]

    client.force_login(_user(Role.CENTER_MANAGER, "coh.rows"))
    page = _cohorts(client)

    assert row["code"] in page
    assert row["name_ar"] in page
    assert row["program_name"] in page
    assert row["program_code"] in page
    assert str(row["semester"]) in page
    assert row["trainer_name"] in page
    assert row["location"] in page
    assert str(row["status_display"]) in page
    assert row["starts_on"].strftime("%Y/%m/%d") in page
    assert row["ends_on"].strftime("%Y/%m/%d") in page
    # Seats are the service's figure, printed, not recomputed on the page.
    assert row["capacity"] == 20
    assert row["seats_left"] == 20 - row["enrolled_count"]
    for header in ("المقاعد", "التشغيل", "الشريك", "الحالة", "الفترة"):
        assert header in page, header


def test_a_missing_trainer_or_partner_is_named_not_left_blank(
    client: Client, three_cohorts: object
) -> None:
    """
    A blank cell says nothing: is there no trainer, or was the column not
    filled? Both absences are stated, and neither invents a value from a
    neighbouring row.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "coh.blank"))
    page = _cohorts(client)

    assert "بلا مدرّب مسجَّل" in page
    assert "بلا شريك" in page
    # …and the cohort that HAS them still shows them, so the states are per-row.
    assert "د. سميرة العبادي" in page
    assert "شركة التدريب المتقدّم" in page


def test_the_cohort_status_chips_count_the_rows_beneath_them(
    client: Client, three_cohorts: object
) -> None:
    """
    Tallied off the rows the template iterates, so the chips cannot disagree
    with the table — and they follow the filter, because they describe this
    request's result rather than the register.
    """
    import re

    from apps.operations.models import Cohort, CohortStatus

    client.force_login(_user(Role.CENTER_MANAGER, "coh.chips"))
    page = _cohorts(client)

    chips = {
        label.strip(): int(n)
        for label, n in re.findall(
            r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', page
        )
    }
    assert sum(chips.values()) == Cohort.objects.count() == 3
    assert chips[str(CohortStatus.RUNNING.label)] == 2
    assert chips[str(CohortStatus.PLANNED.label)] == 1
    assert "توزيع النتائج المعروضة" in page

    # Narrowed, the chips narrow with it rather than restating the register.
    narrowed = _cohorts(client, f"?status={CohortStatus.PLANNED}")
    chips = {
        label.strip(): int(n)
        for label, n in re.findall(
            r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', narrowed
        )
    }
    assert chips == {str(CohortStatus.PLANNED.label): 1}
    # No state that nothing is in gets a zero chip.
    assert ': <span class="num">0</span>' not in narrowed


def test_the_register_says_which_filter_is_narrowing_it(
    client: Client, three_cohorts: object
) -> None:
    """
    ``status`` is read from the URL by the view and was drawn nowhere, so a
    narrowed register looked like the whole one. It is named now — and named
    by its LABEL, never the stored code, which is the defect the transfer
    register was fixed for.
    """
    from apps.operations.models import CohortStatus

    client.force_login(_user(Role.CENTER_MANAGER, "coh.filters"))

    plain = _cohorts(client)
    assert "نتائج مصفّاة" not in plain
    assert "إلغاء التصفية" not in plain

    narrowed = _cohorts(client, f"?status={CohortStatus.PLANNED}&q=CO-UIC")
    assert "نتائج مصفّاة" in narrowed
    assert str(CohortStatus.PLANNED.label) in narrowed
    assert "CO-UIC" in narrowed
    assert reverse("operations:cohorts") in narrowed, "no way to clear the filter"
    # The raw enum is never printed at the client.
    assert f"الحالة: {CohortStatus.PLANNED.value}" not in narrowed

    # A status matching nothing still names itself, and says the register is
    # not empty — it is filtered.
    empty = _cohorts(client, f"?status={CohortStatus.COMPLETED}")
    assert "لا دفعة تطابق هذه التصفية" in empty
    assert "لا دفعات مُشغّلة" not in empty


def test_the_status_filter_survives_a_search(client: Client, three_cohorts: object) -> None:
    """The GET form dropped it, so searching silently widened the result."""
    from apps.operations.models import CohortStatus

    client.force_login(_user(Role.CENTER_MANAGER, "coh.carry"))
    page = _cohorts(client, f"?status={CohortStatus.PLANNED}")

    assert f'<input type="hidden" name="status" value="{CohortStatus.PLANNED.value}">' in page


def test_the_two_cohort_empty_states_are_not_the_same_sentence(
    client: Client, seeded_settings: None
) -> None:
    """
    An empty register and an empty filter result mean different things, and
    the old page had one bare state for both. Neither offers an action it
    cannot perform.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "coh.empty"))

    bare = _cohorts(client)
    assert "لا دفعات مُشغّلة" in bare
    assert "BR-013" in bare
    assert 'class="empty-body"' in bare
    assert "empty-act" not in bare, "the empty state offers an action with no route"
    # The chips describe rows on screen, so an empty table draws none.
    assert "توزيع النتائج المعروضة" not in bare

    filtered = _cohorts(client, "?q=لا-يوجد-شيء-بهذا-الاسم")
    assert "لا دفعة تطابق هذه التصفية" in filtered
    assert "لا دفعات مُشغّلة" not in filtered


def test_the_register_invents_no_total_and_no_status(client: Client, three_cohorts: object) -> None:
    """
    Every state on screen is a real ``CohortStatus`` label, and no money,
    occupancy percentage or forecast is computed by the page.
    """
    from apps.operations.models import CohortStatus

    client.force_login(_user(Role.CENTER_MANAGER, "coh.invent"))
    page = _cohorts(client)

    labels = {str(label) for _value, label in CohortStatus.choices}
    for chip in ("مكتملة", "قيد التنفيذ", "مخطَّطة"):
        assert chip in labels, chip
    for invented in ("نسبة الإشغال", "الإيراد", "المتوقّع", "الإجمالي", "الربح", "%"):
        assert invented not in page, f"the register published «{invented}»"


def test_the_cohorts_register_prints_no_partner_share_and_no_private_data(
    client: Client, three_cohorts: object
) -> None:
    """
    One cohort here runs under an agreement carrying a 50% rate. The partner's
    NAME says who the course is run with and belongs on an operations
    register; the rate, the calculation model and the agreement number behind
    it do not, and the page must not follow the relation into them.
    """
    from apps.partners.models import Agreement

    agreement = Agreement.objects.get(agreement_number="AG-UIX-1")
    assert agreement.percent_rate is not None, "the fixture proves nothing without a rate"

    for role in (
        Role.CENTER_MANAGER,
        Role.REGISTRATION_OFFICER,
        Role.FINANCE_OFFICER,
        Role.AUDIT_ACCOUNT,
    ):
        client.force_login(_user(role, f"coh.pr.{role}".lower().replace("_", ".")))
        page = _cohorts(client)
        assert "شركة التدريب المتقدّم" in page, "the partner name is part of the contract"
        for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "50%", "PERCENT", "نسبة"):
            assert term not in page, f"cohorts/{role} was shown «{term}»"
        assert agreement.agreement_number not in page
        # …and no participant is named on a register of cohorts.
        for private in ("رقم المشارك", "الهوية", "الوصل", "المخالصة"):
            assert private not in page, f"cohorts/{role} was shown «{private}»"
        client.logout()


def test_the_cohorts_register_leaks_none_of_its_own_commentary(
    client: Client, three_cohorts: object
) -> None:
    """A developer's note above an operational register is the 8I defect."""
    client.force_login(_user(Role.CENTER_MANAGER, "coh.comment"))
    page = _cohorts(client)

    for note in ("§6.5", "list_cohorts", "{%", "{{", "{#"):
        assert note not in page, f"the template leaked «{note}»"


def test_the_cohorts_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = COHORTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the cohorts register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    # Eight columns, and the empty row spans the table it sits in.
    assert len(re.findall(r"<th[ >]", source)) == 8
    assert 'colspan="8"' in source
    # Every `<th>` carries text, so nothing here needs `.sr-only` — which is
    # `position:absolute` with no positioned ancestor and drags an RTL page.
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup
    assert not re.search(r"<th[^>]*>\s*</th>", source)
    # The rule explanation left `.note info`: blue is an alert, and explaining
    # a rule is not one (polish rules §6.5).
    assert "note info" not in markup
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


def test_every_link_on_the_cohorts_register_points_at_a_real_route(
    client: Client, three_cohorts: object
) -> None:
    """
    There is no cohort detail route, so no row is clickable and no row links
    anywhere. The page draws the link that clears the filter, and the two
    next-step links the guided-help block offers this reader — nothing else.
    """
    import re

    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("operations:cohort-detail")

    client.force_login(_user(Role.CENTER_MANAGER, "coh.links"))
    page = _cohorts(client, "?q=CO-UIC")

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    assert hrefs == {
        reverse("operations:cohorts"),
        reverse("operations:mohe"),
        reverse("catalog:pricelists"),
    }, hrefs
    for href in hrefs:
        assert client.get(href).status_code == 200


# ---------------------------------------------------------------------------
# The ministry approval register — page polish
# ---------------------------------------------------------------------------
MOHE_TEMPLATE = Path("templates/operations/mohe.html")


@pytest.fixture
def three_files(three_cohorts: object) -> object:
    """
    Three ministry files, one in each state the register has to tell apart.

    A draft that was never sent, an approved file with a course number and a
    registration deadline, and a rejected one carrying the reason — the row the
    screen exists to act on (BR-014), and the only one with prose in it.
    """
    from datetime import date

    from apps.operations.models import Cohort, MoheStatus, MoheSubmission

    cohorts = {c.code: c for c in Cohort.objects.all()}
    opener = _user(Role.CENTER_MANAGER, "moh.fixture.opener")
    content = {
        "training_axes_ar": "المحاور التدريبية",
        "practical_aspects_ar": "الجوانب العملية",
        "target_audience_ar": "الفئة المستهدفة",
        "trainer_name": "د. سميرة العبادي",
        "trainer_qualifications": "دكتوراه في الشبكات",
        "training_location": "قاعة 3",
        "responsible_entity": "مركز التعليم المستمر",
    }

    draft = MoheSubmission.objects.create(
        cohort=cohorts["CO-UIC-1"], created_by=opener, status=MoheStatus.DRAFT, **content
    )
    approved = MoheSubmission.objects.create(
        cohort=cohorts["CO-UIC-2"],
        created_by=opener,
        status=MoheStatus.APPROVED,
        submitted_on=date(2026, 7, 1),
        decided_on=date(2026, 7, 20),
        mohe_course_number="MOHE-2026-77",
        registration_deadline=date(2026, 9, 15),
        **content,
    )
    rejected = MoheSubmission.objects.create(
        cohort=cohorts["CO-UIC-3"],
        created_by=opener,
        status=MoheStatus.REJECTED,
        submitted_on=date(2026, 7, 2),
        decided_on=date(2026, 7, 25),
        rejection_reason_ar="المحاور غير مطابقة للساعات المعتمدة",
        **content,
    )
    return draft, approved, rejected


def _mohe(client: Client, params: str = "") -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    response = client.get(reverse("operations:mohe") + params)
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        # §3.3/14 leaves these three empty. Under BR-080 that is a refusal.
        (Role.FINANCE_OFFICER, 403),
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_ministry_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"moh.{role}".lower().replace("_", ".")))

    assert client.get(reverse("operations:mohe")).status_code == expected


def test_the_ministry_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("operations:mohe")).status_code == 403


def test_the_ministry_register_writes_nothing_and_draws_no_action(
    client: Client, three_files: object
) -> None:
    """
    Every act on a ministry file — attach, send, decide, resubmit — happens on
    the file's own page or on the open-file screen, each behind its own cell of
    ``MOHE_ACTIONS``. This view answers GET only, so the register draws no
    action form at all: the search box is the one form on the page.
    """
    from apps.operations.models import MoheSubmission

    client.force_login(_user(Role.CENTER_MANAGER, "moh.readonly"))
    page = _mohe(client)

    assert page.count("<form") == 1
    assert 'method="get"' in page
    assert "csrfmiddlewaretoken" not in page, "a read register was handed a write token"
    assert page.count("<button") == 1, "the only button is the search submit"
    for furniture in ("<dialog", "modal", "x-show", "data-bs-toggle", "aria-haspopup"):
        assert furniture not in page, f"the register drew «{furniture}»"

    # The route itself refuses a POST rather than answering it — this view
    # carries `require_http_methods(["GET"])`, unlike the catalogue reads.
    before = MoheSubmission.objects.count()
    assert client.post(reverse("operations:mohe")).status_code == 405
    assert MoheSubmission.objects.count() == before


def test_the_open_file_link_follows_the_screen_that_owns_it(
    client: Client, three_files: object
) -> None:
    """
    «فتح ملف وزاري» is gated by CREATE on §3.3/15 — the OPEN-FILE screen, not
    this one — because that is the screen it leads to. The audit account may
    read both and create on neither, which is the case that proves the gate is
    not just "can you see this page".
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.CREATE in allowed_actions(Role.CENTER_MANAGER, "mohe-submit")
    assert Action.CREATE in allowed_actions(Role.REGISTRATION_OFFICER, "mohe-submit")
    assert Action.CREATE not in allowed_actions(Role.AUDIT_ACCOUNT, "mohe-submit")

    submit = reverse("operations:mohe-submit")
    for role in (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER):
        client.force_login(_user(role, f"moh.open.{role}".lower().replace("_", ".")))
        assert f'href="{submit}"' in _mohe(client), role
        client.logout()

    client.force_login(_user(Role.AUDIT_ACCOUNT, "moh.open.aud"))
    audit = _mohe(client)
    assert f'href="{submit}"' not in audit
    assert 'class="btn2 primary"' not in audit


def test_the_ministry_row_renders_the_projection_it_was_given(
    client: Client, three_files: object
) -> None:
    """Every cell is a key ``_row`` already carried, printed, never derived."""
    _draft, approved, rejected = three_files  # type: ignore[misc]

    client.force_login(_user(Role.CENTER_MANAGER, "moh.rows"))
    page = _mohe(client)

    assert approved.cohort.code in page
    assert approved.cohort.name_ar in page
    assert approved.cohort.program.name_ar in page
    assert approved.cohort.program.code in page
    assert approved.mohe_course_number in page
    assert approved.submitted_on.strftime("%Y/%m/%d") in page
    assert approved.decided_on.strftime("%Y/%m/%d") in page
    assert approved.registration_deadline.strftime("%Y/%m/%d") in page
    # The rejection reason is prose and keeps the width of the table: it is the
    # only guide to what a resubmission must change (BR-014).
    assert rejected.rejection_reason_ar in page
    assert "سبب الرفض كما ورد" in page
    for header in ("الرقم الوزاري", "أُرسل", "القرار", "مهلة التسجيل"):
        assert header in page, header


def test_an_unsent_file_says_so_instead_of_showing_a_dash(
    client: Client, three_files: object
) -> None:
    """
    Four columns are empty on a draft, and a row of dashes says nothing about
    which of them is waiting on the centre and which on the ministry.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "moh.blank"))
    page = _mohe(client)

    assert "لم يُرسل بعد" in page
    assert "بلا قرار" in page
    assert "لم يصدر" in page
    assert "غير محدَّدة" in page


def test_the_ministry_status_chips_count_the_rows_beneath_them(
    client: Client, three_files: object
) -> None:
    """
    Tallied off the rows the template iterates, so the chips cannot disagree
    with the table — and they follow the filter rather than restating the
    register.
    """
    import re

    from apps.operations.models import MoheStatus, MoheSubmission

    client.force_login(_user(Role.CENTER_MANAGER, "moh.chips"))
    page = _mohe(client)

    def chips_of(body: str) -> dict[str, int]:
        return {
            label.strip(): int(n)
            for label, n in re.findall(
                r'<span class="chip[^"]*">([^<:]+): <span class="num">(\d+)', body
            )
        }

    chips = chips_of(page)
    assert sum(chips.values()) == MoheSubmission.objects.count() == 3
    assert chips == {
        str(MoheStatus.DRAFT.label): 1,
        str(MoheStatus.APPROVED.label): 1,
        str(MoheStatus.REJECTED.label): 1,
    }
    assert "توزيع النتائج المعروضة" in page

    narrowed = chips_of(_mohe(client, f"?status={MoheStatus.APPROVED}"))
    assert narrowed == {str(MoheStatus.APPROVED.label): 1}


def test_the_ministry_register_says_which_filter_is_narrowing_it(
    client: Client, three_files: object
) -> None:
    """
    Both filters were read from the URL and neither was named, so a narrowed
    register read as the whole one. Named now — by LABEL, never by the stored
    code, which is the defect the transfer register was fixed for.
    """
    from apps.operations.models import MoheStatus

    client.force_login(_user(Role.CENTER_MANAGER, "moh.filters"))

    plain = _mohe(client)
    assert "نتائج مصفّاة" not in plain
    assert "إلغاء التصفية" not in plain

    narrowed = _mohe(client, f"?status={MoheStatus.REJECTED}&q=CO-UIC")
    assert "نتائج مصفّاة" in narrowed
    assert str(MoheStatus.REJECTED.label) in narrowed
    assert "CO-UIC" in narrowed
    assert f"الحالة: {MoheStatus.REJECTED.value}" not in narrowed
    assert f'href="{reverse("operations:mohe")}"' in narrowed, "no way to clear the filter"

    # The status select keeps the reader's choice rather than resetting it.
    assert f'<option value="{MoheStatus.REJECTED.value}" selected>' in narrowed


def test_the_status_filter_options_are_the_ones_the_model_defines(
    client: Client, three_files: object
) -> None:
    """
    ``MoheStatus`` cannot reach the view — A-05 forbids importing models there
    and no service projects the vocabulary — so the four options are written in
    the template. This is what stops that copy drifting from the original.
    """
    from apps.operations.models import MoheStatus

    client.force_login(_user(Role.CENTER_MANAGER, "moh.options"))
    page = _mohe(client)

    for value, label in MoheStatus.choices:
        assert f'value="{value}"' in page, f"{value} is not offered by the filter"
        assert str(label) in page, f"{value} is offered without its own name"
    assert page.count("<option") == len(MoheStatus.choices) + 1, "an option the model never defined"


def test_the_two_ministry_empty_states_are_not_the_same_sentence(
    client: Client, three_cohorts: object
) -> None:
    """
    An empty register and an empty filter result are different facts. Neither
    offers an action the page cannot perform.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "moh.empty"))

    bare = _mohe(client)
    assert "لا ملفات وزارية" in bare
    assert "BR-013" in bare and "BR-016" in bare
    assert 'class="empty-body"' in bare
    assert "empty-act" not in bare, "the empty state offers an action with no route"
    assert "توزيع النتائج المعروضة" not in bare

    filtered = _mohe(client, "?q=لا-يوجد-ملف-بهذا-الاسم")
    assert "لا ملف يطابق هذه التصفية" in filtered
    assert "لا ملفات وزارية" not in filtered
    assert "BR-014" in filtered


def test_the_ministry_register_publishes_no_verdict_it_was_not_given(
    client: Client, three_files: object
) -> None:
    """
    The registration deadline is a date the ministry set, printed as it is.
    Whether it has passed is computed elsewhere and is not in this context, so
    the page raises no alert and pronounces no compliance verdict.

    Checked against the page WITHOUT the rejection prose, because that prose is
    the ministry's own words quoted back — «محاور غير مطابقة» is a finding that
    arrived, not a judgement this screen reached, and the whole point of the
    row is that the two are different things.
    """
    _draft, _approved, rejected = three_files  # type: ignore[misc]

    from apps.operations.models import MoheStatus

    client.force_login(_user(Role.CENTER_MANAGER, "moh.verdict"))
    page = _mohe(client)
    assert rejected.rejection_reason_ar in page
    computed = page.replace(rejected.rejection_reason_ar, "")

    for verdict in (
        "انقضت المهلة",
        "تجاوزت المهلة",
        "متأخر",
        "مخالف",
        "مطابق",
        "جاهز للإرسال",
        "أيام متبقية",
        "يوماً متبقياً",
    ):
        assert verdict not in computed, f"the register published «{verdict}»"
    # Every state chip on screen is a state the model defines — no invented
    # label, and none derived from a date or an attachment.
    import re

    labels = {str(label) for _v, label in MoheStatus.choices}
    toned = set(re.findall(r'<span class="chip [a-z]+ dot">([^<]+)</span>', page))
    assert toned, "no status chip was drawn, so this proves nothing"
    assert toned <= labels, toned - labels
    assert toned == {
        str(MoheStatus.DRAFT.label),
        str(MoheStatus.APPROVED.label),
        str(MoheStatus.REJECTED.label),
    }


def test_the_ministry_register_prints_no_commercial_or_private_data(
    client: Client, three_files: object
) -> None:
    """
    One cohort here runs under an agreement carrying a 50% rate, and a ministry
    file is not where a commercial term or a participant is published.
    """
    for role in (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT):
        client.force_login(_user(role, f"moh.pr.{role}".lower().replace("_", ".")))
        page = _mohe(client)
        for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "50%", "PERCENT", "نسبة"):
            assert term not in page, f"mohe/{role} was shown «{term}»"
        for private in ("رقم المشارك", "الهوية", "الوصل", "المخالصة", "الرصيد"):
            assert private not in page, f"mohe/{role} was shown «{private}»"
        client.logout()


def test_the_ministry_register_leaks_none_of_its_own_commentary(
    client: Client, three_files: object
) -> None:
    """A developer's note above a ministry file is the 8I defect verbatim."""
    client.force_login(_user(Role.CENTER_MANAGER, "moh.comment"))
    page = _mohe(client)

    for note in ("A-05", "MOHE_ACTIONS", "§6.5", "{%", "{{", "{#"):
        assert note not in page, f"the template leaked «{note}»"


def test_every_link_on_the_ministry_register_reaches_a_real_route(
    client: Client, three_files: object
) -> None:
    """
    Three kinds of link, and every one of them opens for the reader drawing it
    — plus the next step the guided-help block offers, filtered the same way.
    """
    import re

    from apps.operations.models import MoheSubmission

    client.force_login(_user(Role.CENTER_MANAGER, "moh.links"))
    page = _mohe(client, "?q=CO-UIC")

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    expected = {
        reverse("operations:mohe"),
        reverse("operations:mohe-submit"),
        reverse("operations:cohorts"),
    } | {
        reverse("operations:mohe-detail", args=[pk])
        for pk in MoheSubmission.objects.values_list("pk", flat=True)
    }
    assert hrefs == expected, hrefs
    for href in hrefs:
        assert client.get(href).status_code == 200, href


def test_the_ministry_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = MOHE_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the ministry register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    assert 'class="tbl-wrap"' in source
    assert "overflow-x-auto" in css.split(".tbl-wrap", 1)[1].split("}", 1)[0]
    assert len(re.findall(r"<th[ >]", source)) == 8
    assert source.count('colspan="8"') == 2, "the reason row and the empty row both span the table"
    markup = source.split("{% endcomment %}", 1)[-1]
    # The actions column is named by attribute: `.sr-only` is `position:absolute`
    # with no positioned ancestor, so in RTL it escapes `.tbl-wrap` and drags
    # the page sideways.
    assert "aria-label=\"{% translate 'إجراءات' %}\"" in source
    assert "sr-only" not in markup
    # The one header with no text carries a name; none is left nameless.
    for header in re.findall(r"<th([^>]*)>\s*</th>", markup):
        assert "aria-label" in header, "an empty column header with no name"
    # The rejection reason left `.note warn`: yellow is an alert, and recording
    # a decision that arrived is not one (polish rules §6.5).
    assert "note warn" not in markup
    assert "note info" not in markup
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The ministry file page — page polish
# ---------------------------------------------------------------------------
MOHE_DETAIL_TEMPLATE = Path("templates/operations/mohe_detail.html")

#: §3.3/14 «V C E A P · V P · — · — · — · V P»
MOHE_READERS = (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT)
MOHE_OUTSIDERS = (Role.FINANCE_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def mohe_files(three_cohorts: object) -> dict[str, object]:
    """
    One ministry file in each state the page has to tell apart.

    Built through the services rather than the ORM, because the states this
    page renders — sendable, decidable, resubmittable — are the services'
    answers, and a hand-built row could hold a combination they never produce.
    """
    from datetime import date

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.operations.models import Cohort
    from apps.operations.services import mohe_service

    content = {
        "training_axes_ar": "محاور الدورة التدريبية",
        "practical_aspects_ar": "تطبيقات مخبرية",
        "target_audience_ar": "موظفو القطاع العام",
        "trainer_name": "د. سامي العلي",
        "trainer_qualifications": "دكتوراه هندسة شبكات",
        "training_location": "مركز التعليم المستمر",
        "responsible_entity": "جامعة البترا",
    }
    actor = _user(Role.CENTER_MANAGER, "mohd.fixture.actor")
    seed = Cohort.objects.first()
    assert seed is not None

    def cohort_for(code: str) -> Cohort:
        return Cohort.objects.create(
            code=code,
            program=seed.program,
            semester=seed.semester,
            name_ar=f"دفعة {code}",
            starts_on=seed.starts_on,
            ends_on=seed.ends_on,
            capacity=20,
        )

    def attach_both(submission: object) -> None:
        for purpose, name in (("TRAINER_CV", "cv.pdf"), ("ENTITY_LICENSE", "licence.pdf")):
            mohe_service.attach_document(
                actor=actor,
                submission=submission,
                purpose=purpose,
                upload=SimpleUploadedFile(name, b"%PDF-1.4 body", "application/pdf"),
            )

    files: dict[str, object] = {}
    for key, code in (
        ("bare", "CO-MD-1"),
        ("ready", "CO-MD-2"),
        ("sent", "CO-MD-3"),
        ("rejected", "CO-MD-4"),
        ("approved", "CO-MD-5"),
    ):
        files[key] = mohe_service.create_submission(
            actor=actor, cohort=cohort_for(code), data=dict(content)
        )

    for key in ("ready", "sent", "rejected", "approved"):
        attach_both(files[key])
    for key in ("sent", "rejected", "approved"):
        mohe_service.submit_to_mohe(
            actor=actor, submission=files[key], submitted_on=date(2026, 9, 1)
        )
    mohe_service.record_decision(
        actor=actor,
        submission=files["rejected"],
        approved=False,
        decided_on=date(2026, 9, 10),
        rejection_reason_ar="بيان الجوانب العملية ناقص",
    )
    mohe_service.record_decision(
        actor=actor,
        submission=files["approved"],
        approved=True,
        decided_on=date(2026, 9, 12),
        mohe_course_number="MOHE/2026/900",
        registration_deadline=date(2026, 10, 5),
    )
    for submission in files.values():
        submission.refresh_from_db()  # type: ignore[attr-defined]
    return files


def _file_page(client: Client, submission: object) -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    response = client.get(reverse("operations:mohe-detail", args=[submission.pk]))  # type: ignore[attr-defined]
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


def _projection(actor: object, submission: object) -> dict[str, object]:
    from apps.operations.services import mohe_service

    return mohe_service.get_submission(actor=actor, submission_id=submission.pk)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        *[(role, 200) for role in MOHE_READERS],
        *[(role, 403) for role in MOHE_OUTSIDERS],
    ],
)
def test_the_ministry_file_opens_exactly_where_the_matrix_says(
    client: Client, mohe_files: dict[str, object], role: str, expected: int
) -> None:
    client.force_login(_user(role, f"mohd.{role}".lower().replace("_", ".")))
    url = reverse("operations:mohe-detail", args=[mohe_files["ready"].pk])  # type: ignore[attr-defined]

    assert client.get(url).status_code == expected


def test_the_ministry_file_refuses_an_anonymous_visitor(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    url = reverse("operations:mohe-detail", args=[mohe_files["ready"].pk])  # type: ignore[attr-defined]

    assert client.get(url).status_code == 403


def test_each_ministry_act_is_drawn_only_for_the_role_that_holds_it(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    Four acts, four different cells. Attaching is EDIT on §3.3/15, sending is
    APPROVE on §3.3/15, deciding is APPROVE on §3.3/14, and resubmitting is
    CREATE on §3.3/15 — which is why the registrar drafts and attaches but does
    not send, and the audit account reads all of it and does none of it.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.EDIT in allowed_actions(Role.REGISTRATION_OFFICER, "mohe-submit")
    assert Action.APPROVE not in allowed_actions(Role.REGISTRATION_OFFICER, "mohe-submit")
    assert Action.APPROVE not in allowed_actions(Role.AUDIT_ACCOUNT, "mohe")

    expected = {
        Role.CENTER_MANAGER: {
            "bare": {"attach"},
            "ready": {"attach", "send"},
            "sent": {"approve"},
            "rejected": {"resubmit"},
            "approved": set(),
        },
        Role.REGISTRATION_OFFICER: {
            "bare": {"attach"},
            "ready": {"attach"},
            "sent": set(),
            "rejected": {"resubmit"},
            "approved": set(),
        },
        # Reads every file and acts on none of them.
        Role.AUDIT_ACCOUNT: {
            key: set() for key in ("bare", "ready", "sent", "rejected", "approved")
        },
    }
    markers = {
        "attach": 'value="attach"',
        "send": 'value="send"',
        "approve": 'value="approve"',
        "resubmit": 'value="resubmit"',
    }

    for role, per_state in expected.items():
        client.force_login(_user(role, f"mohd.act.{role}".lower().replace("_", ".")))
        for key, acts in per_state.items():
            page = _file_page(client, mohe_files[key])
            for act, marker in markers.items():
                drawn = marker in page
                assert drawn is (act in acts), f"{role}/{key}: «{act}» drawn={drawn}"
            # A page with no act draws no write token at all, and one form per
            # act offered — «approve» and «reject» share the decision form, and
            # are counted once because they are one control surface.
            assert ("csrfmiddlewaretoken" in page) is bool(acts), f"{role}/{key} token mismatch"
            assert page.count("<form") == len(acts), f"{role}/{key} form count"
            assert ('value="reject"' in page) is ("approve" in acts), f"{role}/{key} reject"
        client.logout()


@pytest.mark.parametrize(
    ("action", "role", "state"),
    [
        ("send", Role.REGISTRATION_OFFICER, "ready"),
        ("approve", Role.REGISTRATION_OFFICER, "sent"),
        ("reject", Role.REGISTRATION_OFFICER, "sent"),
        ("attach", Role.AUDIT_ACCOUNT, "bare"),
        ("resubmit", Role.AUDIT_ACCOUNT, "rejected"),
    ],
)
def test_an_act_the_page_never_offered_is_still_refused_by_the_route(
    client: Client, mohe_files: dict[str, object], action: str, role: str, state: str
) -> None:
    """The button's absence is presentation; the refusal is ``MOHE_ACTIONS``."""
    from apps.core.models import Attachment
    from apps.operations.models import MoheSubmission

    submission = mohe_files[state]
    before = (
        MoheSubmission.objects.count(),
        Attachment.objects.count(),
        MoheSubmission.objects.get(pk=submission.pk).status,  # type: ignore[attr-defined]
    )
    client.force_login(_user(role, f"mohd.post.{action}.{role}".lower().replace("_", ".")))

    url = reverse("operations:mohe-detail", args=[submission.pk])  # type: ignore[attr-defined]
    assert client.post(url, {"action": action}).status_code == 403
    assert (
        MoheSubmission.objects.count(),
        Attachment.objects.count(),
        MoheSubmission.objects.get(pk=submission.pk).status,  # type: ignore[attr-defined]
    ) == before


def test_the_file_identity_renders_the_projection_it_was_given(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """A `.dl` of keys ``get_submission`` already returned, none of them derived."""
    manager = _user(Role.CENTER_MANAGER, "mohd.identity")
    row = _projection(manager, mohe_files["approved"])

    client.force_login(manager)
    page = _file_page(client, mohe_files["approved"])

    assert 'class="dl"' in page
    assert row["cohort_code"] in page
    assert row["cohort_name_ar"] in page
    assert row["program_name_ar"] in page
    assert row["program_code"] in page
    assert str(row["status_display"]) in page
    assert str(row["created_by"]) in page
    assert row["mohe_course_number"] in page
    assert row["submitted_on"].strftime("%Y/%m/%d") in page  # type: ignore[attr-defined]
    assert row["decided_on"].strftime("%Y/%m/%d") in page  # type: ignore[attr-defined]
    assert row["registration_deadline"].strftime("%Y/%m/%d") in page  # type: ignore[attr-defined]


def test_an_undecided_file_names_each_absence_instead_of_hiding_the_row(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    The old `.dl` wrapped four rows in ``{% if %}``, so a draft simply had no
    «تاريخ القرار» line — and a reader could not tell a missing decision from a
    field the screen does not show. Each absence is named in its own words.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mohd.absent"))
    page = _file_page(client, mohe_files["bare"])

    assert "لم يُرسل بعد" in page
    assert "بلا قرار" in page
    assert "لم يصدر" in page
    assert "غير محدَّدة" in page
    assert "ملف أصلي، لا يردّ على رفض" in page
    assert "لم يُعَد إرساله" in page


def test_what_is_missing_is_the_services_list_and_nothing_else(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    BR-016 is computed in ``missing_attachments`` so the screen does not become
    a second copy of the rule. The labels drawn are exactly that list — not
    "required minus uploaded" worked out in the template.
    """
    manager = _user(Role.CENTER_MANAGER, "mohd.missing")
    client.force_login(manager)

    bare = _projection(manager, mohe_files["bare"])
    assert [m["label"] for m in bare["missing_attachments"]], "nothing missing, so this is vacuous"  # type: ignore[index]
    page = _file_page(client, mohe_files["bare"])
    assert "ما زال ناقصاً" in page
    for missing in bare["missing_attachments"]:  # type: ignore[attr-defined]
        assert f'<span class="chip warn">{missing["label"]}</span>' in page
    assert "BR-016" in page

    # …and a complete file draws no missing block at all.
    ready = _projection(manager, mohe_files["ready"])
    assert ready["missing_attachments"] == []
    complete = _file_page(client, mohe_files["ready"])
    assert "ما زال ناقصاً" not in complete


def test_required_uploaded_and_missing_stay_three_separate_statements(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    Three different facts — what the rule asks for, what is on file, and what
    is still absent — each printed from its own key. Collapsing them would
    make the page decide something the service already decided.
    """
    manager = _user(Role.CENTER_MANAGER, "mohd.three")
    client.force_login(manager)

    bare = _projection(manager, mohe_files["bare"])
    page = _file_page(client, mohe_files["bare"])
    # What the rule asks for is stated even when nothing is uploaded.
    assert "ما يطلبه BR-016" in page
    for required in bare["required_purposes"]:  # type: ignore[attr-defined]
        assert str(required["label"]) in page
    # What is uploaded has its own table, with its own empty state.
    assert "لم يُرفع أي مستند بعد" in page
    assert 'class="empty-body"' in page

    ready = _projection(manager, mohe_files["ready"])
    assert len(ready["attachments"]) == 2  # type: ignore[arg-type]
    complete = _file_page(client, mohe_files["ready"])
    assert "لم يُرفع أي مستند بعد" not in complete
    for uploaded in ready["attachments"]:  # type: ignore[attr-defined]
        assert uploaded["original_filename"] in complete
        assert uploaded["sha256"][:12] in complete
    assert "ما يطلبه BR-016" in complete, "the requirement disappeared once it was met"


def test_the_readiness_chips_read_the_service_flags_not_the_viewers_permission(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    ``is_sendable`` / ``is_decidable`` / ``is_resubmittable`` are the service's
    answers about the FILE, and ``can_send`` and friends are answers about the
    READER. Drawing the chips from the latter would tell a registrar who just
    completed the documents nothing about whether the file is now ready.

    So the chip is asserted on a role that may not perform the act at all.
    """
    manager = _user(Role.CENTER_MANAGER, "mohd.flags.mgr")
    flags = {key: _projection(manager, sub) for key, sub in mohe_files.items()}
    assert flags["ready"]["is_sendable"] is True
    assert flags["sent"]["is_decidable"] is True
    assert flags["rejected"]["is_resubmittable"] is True

    chips = {
        "is_sendable": "مكتمل ويقبل الإرسال",
        "is_decidable": "يقبل تسجيل قرار الوزارة",
        "is_resubmittable": "يقبل فتح ملف يردّ عليه",
    }
    # The audit account may perform none of the four acts.
    client.force_login(_user(Role.AUDIT_ACCOUNT, "mohd.flags.aud"))
    for key, submission in mohe_files.items():
        page = _file_page(client, submission)
        assert "<form" not in page, f"{key} drew a form for a reader who may write nothing"
        for flag, label in chips.items():
            assert (label in page) is bool(flags[key][flag]), f"{key}/{flag} chip disagrees"


def test_the_rejection_is_quoted_as_a_decision_not_pronounced_as_a_verdict(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    The reason is the ministry's words, attributed to the ministry, and the
    page adds nothing to them. It left `.note danger` — red is an alert, and a
    decision that arrived is not one (polish rules §6.5).
    """
    manager = _user(Role.CENTER_MANAGER, "mohd.reject")
    row = _projection(manager, mohe_files["rejected"])
    reason = str(row["rejection_reason_ar"])
    assert reason

    client.force_login(manager)
    page = _file_page(client, mohe_files["rejected"])

    assert reason in page
    assert "سبب الرفض كما ورد من الوزارة" in page
    assert "BR-014" in page
    assert "note danger" not in page
    # The page states no judgement of its own — checked with the ministry's own
    # words removed, since those are a finding that arrived, not one reached here.
    computed = page.replace(reason, "")
    for verdict in ("مخالف", "غير مطابق", "انقضت المهلة", "تجاوزت المهلة", "متأخر", "أيام متبقية"):
        assert verdict not in computed, f"the file page published «{verdict}»"


def test_the_approved_file_states_the_rule_without_judging_the_deadline(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    BR-013 and BR-019 are quoted with the ministry's own date. Whether that
    date has passed is not computed anywhere in this context, so the page says
    nothing about it.
    """
    manager = _user(Role.CENTER_MANAGER, "mohd.approved")
    row = _projection(manager, mohe_files["approved"])

    client.force_login(manager)
    page = _file_page(client, mohe_files["approved"])

    assert "BR-013" in page
    assert "BR-019" in page
    assert row["registration_deadline"].strftime("%Y/%m/%d") in page  # type: ignore[attr-defined]
    for verdict in ("انقضت", "سارية اليوم", "متبقٍّ من المهلة", "منتهية"):
        assert verdict not in page, f"the file page judged the deadline: «{verdict}»"


def test_the_resubmission_chain_is_shown_only_where_the_service_gave_one(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """Both ends of the relation, and each end links to a file that opens."""
    from apps.operations.models import MoheSubmission

    manager = _user(Role.CENTER_MANAGER, "mohd.chain")
    client.force_login(manager)

    rejected = mohe_files["rejected"]
    response = client.post(
        reverse("operations:mohe-detail", args=[rejected.pk]),  # type: ignore[attr-defined]
        {"action": "resubmit", "training_axes_ar": "محاور مصحَّحة"},
        follow=True,
    )
    assert response.status_code == 200
    reply = MoheSubmission.objects.filter(resubmission_of=rejected).first()
    assert reply is not None, "the resubmission was not created, so this proves nothing"

    child = _file_page(client, reply)
    assert f'href="{reverse("operations:mohe-detail", args=[rejected.pk])}"' in child  # type: ignore[attr-defined]
    assert "ملف أصلي، لا يردّ على رفض" not in child

    parent = _file_page(client, rejected)
    assert f'href="{reverse("operations:mohe-detail", args=[reply.pk])}"' in parent
    assert "لم يُعَد إرساله" not in parent


def test_the_ministry_file_prints_no_commercial_or_private_data(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    A cohort in this fixture runs under an agreement carrying a 50% rate, and a
    ministry file is not where a commercial term or a participant is published.
    """
    for role in MOHE_READERS:
        client.force_login(_user(role, f"mohd.pr.{role}".lower().replace("_", ".")))
        for key, submission in mohe_files.items():
            page = _file_page(client, submission)
            for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "50%", "PERCENT", "نسبة"):
                assert term not in page, f"mohe file {key}/{role} was shown «{term}»"
            for private in ("رقم المشارك", "الوصل", "المخالصة", "الرصيد", "رقم الهوية"):
                assert private not in page, f"mohe file {key}/{role} was shown «{private}»"
        client.logout()


def test_the_ministry_file_leaks_none_of_its_own_commentary(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """A developer's note on a file that leaves the centre is the 8I defect."""
    client.force_login(_user(Role.CENTER_MANAGER, "mohd.comment"))
    page = _file_page(client, mohe_files["rejected"])

    for note in ("MOHE_ACTIONS", "§6.5", "is_sendable", "{%", "{{", "{#"):
        assert note not in page, f"the template leaked «{note}»"


def test_every_link_on_the_ministry_file_reaches_a_real_route(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """The way back, and the two ends of a resubmission. Nothing else."""
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "mohd.links"))
    for key, submission in mohe_files.items():
        page = _file_page(client, submission)
        hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
        assert reverse("operations:mohe") in hrefs, key
        for href in hrefs:
            assert client.get(href).status_code == 200, f"{key} → {href}"


def test_the_ministry_file_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = MOHE_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the ministry file page uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    # Only the uploaded documents are tabular; every other block is a `.dl` or
    # a hint, and the one table keeps its wrapper and spans it when empty.
    assert markup.count('class="tbl-wrap"') == 1
    # Two key/value blocks — the file's identity and the form's content. The
    # attachments are the only genuinely tabular thing on the page.
    assert markup.count('class="dl"') == 2
    assert 'colspan="5"' in markup
    assert len(re.findall(r"<th[ >]", markup)) == 5
    # Every alert box that was explaining a rule is gone: colour is reserved
    # for the refusal itself (polish rules §6.5).
    for alert in ("note danger", "note info", "note warn", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    assert "sr-only" not in markup
    for header in re.findall(r"<th([^>]*)>\s*</th>", markup):
        assert "aria-label" in header, "an empty column header with no name"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The ministry submission form — page polish
# ---------------------------------------------------------------------------
MOHE_SUBMIT_TEMPLATE = Path("templates/operations/mohe_submit.html")

#: The ministry's own form, transcribed (BR-014). Mirrors
#: ``mohe_service.CONTENT_FIELDS`` — written out so a field quietly dropped
#: from either side is caught rather than agreed with.
MOHE_CONTENT_FIELDS = (
    "training_axes_ar",
    "practical_aspects_ar",
    "target_audience_ar",
    "trainer_name",
    "trainer_qualifications",
    "training_location",
    "responsible_entity",
)


def _submit_page(client: Client) -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    response = client.get(reverse("operations:mohe-submit"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        # §3.3/15 «V C E A P · V C E · — · — · — · V». VIEW opens it.
        (Role.CENTER_MANAGER, 200),
        (Role.REGISTRATION_OFFICER, 200),
        (Role.AUDIT_ACCOUNT, 200),
        (Role.FINANCE_OFFICER, 403),
        (Role.FINANCE_MANAGER, 403),
        (Role.CASHIER, 403),
    ],
)
def test_the_submission_form_opens_exactly_where_the_matrix_says(
    client: Client, three_cohorts: object, role: str, expected: int
) -> None:
    client.force_login(_user(role, f"mohs.{role}".lower().replace("_", ".")))

    assert client.get(reverse("operations:mohe-submit")).status_code == expected


def test_the_submission_form_refuses_an_anonymous_visitor(
    client: Client, three_cohorts: object
) -> None:
    """Fail-closed. The polish moved presentation, never the door."""
    assert client.get(reverse("operations:mohe-submit")).status_code == 403


def test_the_save_button_follows_create_while_the_fields_follow_view(
    client: Client, three_cohorts: object
) -> None:
    """
    §3.3/15 gives the audit account V and withholds C, so the form is READABLE
    by the role that reads everything and fillable only by the two that draft.
    That is the view's own design, and the polish kept it: the audit account
    still sees every field and is offered no way to save one.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER):
        assert Action.CREATE in allowed_actions(role, "mohe-submit")
        client.force_login(_user(role, f"mohs.save.{role}".lower().replace("_", ".")))
        page = _submit_page(client)
        assert 'class="btn2 primary"' in page, role
        assert "csrfmiddlewaretoken" in page, role
        client.logout()

    assert Action.CREATE not in allowed_actions(Role.AUDIT_ACCOUNT, "mohe-submit")
    client.force_login(_user(Role.AUDIT_ACCOUNT, "mohs.save.aud"))
    audit = _submit_page(client)
    assert 'class="btn2 primary"' not in audit
    assert "للاطلاع فقط" in audit
    # …and every field is still drawn for them, which is what V buys.
    for name in MOHE_CONTENT_FIELDS:
        assert f'id="id_{name}"' in audit, name


def test_a_role_without_create_cannot_post_and_writes_nothing(
    client: Client, three_cohorts: object
) -> None:
    """The button's absence is presentation; the refusal is the view's."""
    from apps.operations.models import Cohort, MoheSubmission

    before = MoheSubmission.objects.count()
    code = Cohort.objects.values_list("code", flat=True).first()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "mohs.post.aud"))

    response = client.post(reverse("operations:mohe-submit"), {"cohort_code": code})
    assert response.status_code == 403
    assert MoheSubmission.objects.count() == before


def test_an_invalid_submission_creates_nothing_and_says_which_field(
    client: Client, three_cohorts: object
) -> None:
    """
    ``cohort_code`` is the one required field. A POST naming a cohort that is
    not on the offered list is refused by the form, and the error is rendered
    against the field rather than swallowed.
    """
    from apps.operations.models import MoheSubmission

    before = MoheSubmission.objects.count()
    client.force_login(_user(Role.CENTER_MANAGER, "mohs.invalid"))

    response = client.post(
        reverse("operations:mohe-submit"),
        {"cohort_code": "CO-DOES-NOT-EXIST", "trainer_name": "د. فلان"},
    )
    assert response.status_code == 200
    assert MoheSubmission.objects.count() == before

    page = response.content.decode("utf-8").split("</nav>", 1)[-1]
    assert 'class="err"' in page, "the field error was not rendered"
    assert "has-error" in page
    # What was typed survives the refusal rather than being thrown away.
    assert "د. فلان" in page


def test_a_valid_submission_opens_exactly_one_file_through_the_service(
    client: Client, three_cohorts: object
) -> None:
    """One POST, one file, and it lands on the file's own page."""
    from apps.operations.models import Cohort, MoheStatus, MoheSubmission
    from apps.operations.services import mohe_service

    manager = _user(Role.CENTER_MANAGER, "mohs.valid")
    offered = mohe_service.submittable_cohort_choices(actor=manager)
    assert offered, "no cohort was offered, so this proves nothing"
    code = offered[0][0]
    before = MoheSubmission.objects.count()

    client.force_login(manager)
    content = {name: f"نص {name}" for name in MOHE_CONTENT_FIELDS}
    response = client.post(
        reverse("operations:mohe-submit"), {"cohort_code": code, **content}, follow=True
    )
    assert response.status_code == 200
    assert MoheSubmission.objects.count() == before + 1

    created = MoheSubmission.objects.latest("pk")
    assert created.cohort == Cohort.objects.get(code=code)
    assert created.status == MoheStatus.DRAFT, "a new file is a draft, never further along"
    for name in MOHE_CONTENT_FIELDS:
        assert getattr(created, name) == content[name], name
    assert response.redirect_chain[-1][0] == reverse("operations:mohe-detail", args=[created.pk])


def test_the_cohorts_offered_are_the_services_list_and_no_wider(
    client: Client, three_cohorts: object
) -> None:
    """
    The selector is the service's projection, verbatim. Widening it to every
    cohort would offer a choice ``create_submission`` then has to refuse.
    """
    import re

    from apps.operations.models import Cohort
    from apps.operations.services import mohe_service

    manager = _user(Role.CENTER_MANAGER, "mohs.choices")
    offered = mohe_service.submittable_cohort_choices(actor=manager)
    assert offered

    client.force_login(manager)
    page = _submit_page(client)
    drawn = set(re.findall(r'<option value="([^"]*)"', page))

    assert drawn == {code for code, _label in offered}
    assert drawn <= set(Cohort.objects.values_list("code", flat=True))
    assert len(drawn) == Cohort.objects.count(), "the fixture leaves every cohort free"


def test_a_cohort_that_already_holds_a_file_is_not_offered_again(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    A cohort with a draft, a sent or an approved file is off the list; one
    whose file was rejected comes back on it. Both directions asserted, and
    both read off the service rather than restated here.
    """
    import re

    from apps.operations.services import mohe_service

    manager = _user(Role.CENTER_MANAGER, "mohs.busy")
    offered = {code for code, _label in mohe_service.submittable_cohort_choices(actor=manager)}

    client.force_login(manager)
    drawn = set(re.findall(r'<option value="([^"]*)"', _submit_page(client)))
    assert drawn == offered

    for key in ("bare", "ready", "sent", "approved"):
        busy = mohe_files[key].cohort.code  # type: ignore[attr-defined]
        assert busy not in drawn, f"{key}: a cohort with a file was offered again"
    rejected = mohe_files["rejected"].cohort.code  # type: ignore[attr-defined]
    assert rejected in drawn, "a rejection was treated as a dead end"


def test_no_cohort_to_submit_is_a_stated_state_not_an_unusable_form(
    client: Client, mohe_files: dict[str, object]
) -> None:
    """
    ``cohort_code`` is required with zero choices, so the old page drew an
    empty selector and a save button that no input could ever satisfy — the
    press would come back a validation error every time. The state is said
    instead, and no control is offered that cannot work.
    """
    from apps.operations.models import Cohort, MoheStatus, MoheSubmission
    from apps.operations.services import mohe_service

    # Take the last free cohort out of the running the way the service does.
    MoheSubmission.objects.filter(status=MoheStatus.REJECTED).update(status=MoheStatus.SUBMITTED)
    for cohort in Cohort.objects.exclude(mohe_submissions__isnull=False):
        MoheSubmission.objects.create(
            cohort=cohort,
            created_by=_user(Role.CENTER_MANAGER, f"mohs.filler.{cohort.pk}"),
            status=MoheStatus.DRAFT,
        )
    manager = _user(Role.CENTER_MANAGER, "mohs.none")
    assert mohe_service.submittable_cohort_choices(actor=manager) == []

    client.force_login(manager)
    page = _submit_page(client)

    assert "لا دفعة تقبل فتح ملف الآن" in page
    assert 'class="empty-body"' in page
    assert "<form" not in page, "an unsubmittable form was drawn anyway"
    assert "csrfmiddlewaretoken" not in page
    assert 'class="btn2 primary"' not in page
    # The way back is still offered, because it is the only thing left to do.
    assert f'href="{reverse("operations:mohe")}"' in page


def test_all_seven_ministry_fields_are_drawn_and_named(
    client: Client, three_cohorts: object
) -> None:
    """
    Seven fields plus the cohort. None is hidden, none is renamed, and the
    page says out loud that they are the ministry's own form rather than an
    internal note — which is the difference between a draft and a leak.
    """
    from apps.operations.forms import MoheSubmissionForm
    from apps.operations.services import mohe_service

    assert set(mohe_service.CONTENT_FIELDS) == set(MOHE_CONTENT_FIELDS)

    client.force_login(_user(Role.CENTER_MANAGER, "mohs.fields"))
    page = _submit_page(client)

    blank = MoheSubmissionForm(cohort_choices=[("X", "x")])
    for name in MOHE_CONTENT_FIELDS:
        assert f'id="id_{name}"' in page, f"{name} is not on the page"
        assert f'name="{name}"' in page, name
        assert str(blank.fields[name].label) in page, f"{name} lost its own label"
        assert "hidden" not in page.split(f'id="id_{name}"')[0][-200:], name
    assert 'id="id_cohort_code"' in page
    assert "نصّ النموذج الوزاري نفسه" in page
    assert "BR-014" in page


def test_the_submission_form_pronounces_no_verdict_of_its_own(
    client: Client, three_cohorts: object
) -> None:
    """
    Nothing here judges completeness, eligibility, sendability or a deadline.
    Saving a draft is explicitly allowed to be incomplete, and BR-016 is
    answered on the file page, which this one says.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "mohs.verdict"))
    page = _submit_page(client)

    assert "BR-016" in page
    for verdict in (
        "جاهز للإرسال",
        "مكتمل",
        "غير مؤهلة",
        "مؤهلة للاعتماد",
        "انقضت المهلة",
        "سيُعتمد",
    ):
        assert verdict not in page, f"the form published «{verdict}»"


def test_the_submission_form_prints_no_commercial_or_private_data(
    client: Client, three_cohorts: object
) -> None:
    """
    The cohorts offered include one running under an agreement with a 50%
    rate. The selector names the cohort and says nothing of the deal.
    """
    for role in (Role.CENTER_MANAGER, Role.REGISTRATION_OFFICER, Role.AUDIT_ACCOUNT):
        client.force_login(_user(role, f"mohs.pr.{role}".lower().replace("_", ".")))
        page = _submit_page(client)
        for term in (*COMMERCIAL_TERMS_OFF_THE_CATALOGUE, "حصة", "50%", "PERCENT", "نسبة"):
            assert term not in page, f"mohe-submit/{role} was shown «{term}»"
        for private in ("رقم المشارك", "الوصل", "المخالصة", "الرصيد", "رقم الهوية"):
            assert private not in page, f"mohe-submit/{role} was shown «{private}»"
        client.logout()


def test_the_submission_form_leaks_none_of_its_own_commentary(
    client: Client, three_cohorts: object
) -> None:
    """A developer's note on the form that opens a ministry file is the 8I defect."""
    client.force_login(_user(Role.CENTER_MANAGER, "mohs.comment"))
    page = _submit_page(client)

    for note in ("submittable_cohort_choices", "§6.5", "{%", "{{", "{#"):
        assert note not in page, f"the template leaked «{note}»"


def test_every_link_on_the_submission_form_reaches_a_real_route(
    client: Client, three_cohorts: object
) -> None:
    """Two links, both the way back, and the form posts to this page itself."""
    import re

    client.force_login(_user(Role.CENTER_MANAGER, "mohs.links"))
    page = _submit_page(client)

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    assert hrefs == {reverse("operations:mohe")}, hrefs
    for href in hrefs:
        assert client.get(href).status_code == 200
    # The form posts to the page it is on; it names no other target.
    assert re.search(r"<form[^>]*action=", page) is None


def test_the_submission_form_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = MOHE_SUBMIT_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "form-section", "tight"]:
        assert f'"{dead}"' not in source, f"the submission form uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    # A form page, not a table page: it renders the shared partial whole and
    # invents no markup of its own for the fields.
    assert markup.count('{% include "partials/_form.html" %}') == 1
    assert "tbl-wrap" not in markup
    # The rule explanation left `.note info`: blue is an alert, and explaining
    # a rule is not one (polish rules §6.5).
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    assert "sr-only" not in markup
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The guided-help slice — the six screens the polish pass left untaught
# ---------------------------------------------------------------------------
# Each polish slice closed with the same note: the screen was correct and
# silent. It carried no entry in the guidance registry and drew no
# ``{% guided_help %}``, while the screens around it did — so a reader arriving
# at the dated price list or the ministry file learned nothing about what the
# page decides and, more to the point, what it does not.
#
# Seven of them close the §3.3 chain end to end: programme → price list →
# cohort → ministry file. The last four are the §3.4 cash path end to end: the
# till, the register, the receipt a collection redirects to, and the daily
# closing that gathers them.
# ``tests/test_demo_readiness.py`` walks the arg-less routes with the rest of
# the taught set; the three detail pages need an object to open, so they are
# proved here, beside the fixtures that build one.
CLOSING_TEMPLATE = Path("templates/cashbox/closing.html")
RECEIPT_DETAIL_TEMPLATE = Path("templates/cashbox/receipt_detail.html")
PAYMENT_NEW_TEMPLATE = Path("templates/cashbox/payment_new.html")
PAYMENTS_TEMPLATE = Path("templates/cashbox/payments.html")

GUIDED_HELP_SLICE: tuple[tuple[str, Path], ...] = (
    ("program-detail", PROGRAM_DETAIL_TEMPLATE),
    ("pricelists", PRICELISTS_TEMPLATE),
    ("pricelist-detail", PRICELIST_DETAIL_TEMPLATE),
    ("cohorts", COHORTS_TEMPLATE),
    ("mohe", MOHE_TEMPLATE),
    ("mohe-detail", MOHE_DETAIL_TEMPLATE),
    ("mohe-submit", MOHE_SUBMIT_TEMPLATE),
    ("cashbox-closing", CLOSING_TEMPLATE),
    ("receipt-detail", RECEIPT_DETAIL_TEMPLATE),
    ("payment-new", PAYMENT_NEW_TEMPLATE),
    ("payments", PAYMENTS_TEMPLATE),
)

#: A verdict none of these six screens computes. The guidance may say the
#: question is answered elsewhere; it may not answer it.
VERDICTS_THE_GUIDANCE_MAY_NOT_PRONOUNCE = (
    "سارية اليوم",
    "السارية اليوم",
    "تلقائي",
    "تلقائياً",
    "BR-006",
    "قسمة",
    "الإيراد",
    # The programme card holds one side of BR-006 and the page is already
    # forbidden its result; the sentence above the page may not supply it
    # either. «مطابق» covers «مطابقة» as a substring.
    "مطابق",
    "مجموع أسعار المواد",
)


@pytest.mark.parametrize(("key", "template"), GUIDED_HELP_SLICE)
def test_each_polished_screen_now_has_guidance_of_its_own(key: str, template: Path) -> None:
    from apps.people.guidance import GUIDES

    assert key in GUIDES, f"{template} renders «{key}», which the registry does not define"
    guide = GUIDES[key]
    assert str(guide.what).strip()
    assert str(guide.who).strip()


def test_no_screen_is_taught_twice() -> None:
    """
    A repeated key in the literal loses silently — the second wins and the
    first screen quietly inherits the wrong help. Counting the written keys
    against the built registry is the only place that shows.
    """
    import re

    source = Path("apps/people/guidance.py").read_text(encoding="utf-8")
    body = source.split("GUIDES: dict[str, Guide] = {", 1)[1].split("\ndef guide_for", 1)[0]
    written = re.findall(r'^    "([a-z-]+)": Guide\(', body, flags=re.MULTILINE)

    from apps.people.guidance import GUIDES

    assert len(written) == len(set(written)), "a guidance key is written twice"
    assert set(written) == set(GUIDES)


@pytest.mark.parametrize(("key", "template"), GUIDED_HELP_SLICE)
def test_the_slice_templates_carry_the_tag_where_every_taught_screen_does(
    key: str, template: Path
) -> None:
    """
    Directly under ``.page-head`` and nowhere else: the block explains the
    screen before the screen starts, the way it does on the twenty-six that
    had it already.
    """
    source = template.read_text(encoding="utf-8")

    assert "guided_help" in source.split("\n", 2)[1], f"{template} does not load the tag"
    tag = '{% guided_help "' + key + '" %}'
    assert source.count(tag) == 1, f"{template} draws «{key}» help {source.count(tag)} times"
    assert f"</div>\n\n{tag}\n" in source, f"{template} moved the block off the page head"


#: §3.4/16 «V P · V E P · V A X P · V P · V C P · V P» — the register opens for
#: everyone, and (role, whether §3.4/17 lets them start a collection from it).
REGISTER_READERS = (
    (Role.CENTER_MANAGER, False),
    (Role.REGISTRATION_OFFICER, False),
    (Role.FINANCE_OFFICER, True),
    (Role.FINANCE_MANAGER, False),
    (Role.CASHIER, True),
    (Role.AUDIT_ACCOUNT, False),
)


@pytest.mark.parametrize(("role", "may_collect"), REGISTER_READERS)
def test_the_register_offers_the_till_exactly_where_the_matrix_says(
    client: Client, a_receipt: object, role: str, may_collect: bool
) -> None:
    """
    BR-081 — the centre manager reads the register and never opens the till,
    and the polish moved the button's markup, never its guard. The flag is the
    view's, off §3.4/17, and the template still draws off it and nothing else.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "payment-new")) is may_collect
    client.force_login(_user(role, f"pl.till.{role}".lower().replace("_", ".")))

    response = client.get(reverse("cashbox:payments"))
    assert response.status_code == 200, role
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["can_create"] is may_collect
    assert (f'href="{reverse("cashbox:payment-new")}"' in page) is may_collect
    # A read-only register whichever way it is read: the acts all live on the
    # receipt's own page, so no POST is drawn here for anyone.
    assert '<form method="post"' not in page
    assert "csrfmiddlewaretoken" not in page


def test_every_link_on_the_register_reaches_a_real_route(client: Client, a_receipt: object) -> None:
    """
    Three kinds of link and no fourth: the filter's own address, the till, and
    one «عرض» per row — each of which opens for the reader drawing it.
    """
    import re

    from apps.cashbox.models import Receipt

    client.force_login(_user(Role.CASHIER, "pl.links"))
    page = client.get(reverse("cashbox:payments")).content.decode("utf-8").split("</nav>", 1)[-1]

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    expected = {reverse("cashbox:payment-new"), reverse("cashbox:closing")} | {
        reverse("cashbox:receipt-detail", args=[n])
        for n in Receipt.objects.values_list("internal_receipt_number", flat=True)
    }
    assert hrefs == expected, hrefs
    for href in hrefs:
        assert client.get(href).status_code == 200, href
    # One «عرض» per row and nothing else clickable in the table.
    assert page.count('<a class="btn2 ghost"') == Receipt.objects.count()


def test_the_register_filters_keep_their_names_and_their_values(
    client: Client, a_receipt: object
) -> None:
    """
    The view reads ``q`` and ``on`` off the query string; a renamed input would
    silently stop filtering while still looking like it worked. Both names, and
    both values echoed back, are asserted here rather than eyeballed.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "pl.filters"))

    response = client.get(reverse("cashbox:payments"), {"q": "R-2026", "on": "2026-09-20"})
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.status_code == 200
    assert 'name="q"' in page and 'value="R-2026"' in page
    assert 'name="on"' in page and 'value="2026-09-20"' in page
    assert response.context["query"] == "R-2026"
    assert response.context["on_date"] == "2026-09-20"
    # A GET form that posts nowhere: the filter is the address bar.
    assert 'method="get"' in page
    assert 'role="search"' in page


def test_the_register_head_reads_like_every_polished_screen(
    client: Client, a_receipt: object
) -> None:
    """It was an ``<h1>`` alone; it now says its section and what a row is."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pl.head"))

    page = client.get(reverse("cashbox:payments")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="card2-head"' in page
    # The count describes the rows drawn, not the register behind them.
    assert 'class="count"' in page


def test_the_register_table_names_its_action_column_and_keeps_its_order(
    client: Client, a_receipt: object
) -> None:
    """
    Nine columns in the order they were in, and the ninth finally named. By
    ``aria-label`` rather than `.sr-only`, which in RTL lands off the left edge
    and drags the page sideways.
    """
    import re

    client.force_login(_user(Role.CASHIER, "pl.cols"))

    page = client.get(reverse("cashbox:payments")).content.decode("utf-8").split("</nav>", 1)[-1]
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]

    assert len(re.findall(r"<th[\s>]", head)) == 9
    assert 'aria-label="الإجراء"' in head
    assert "sr-only" not in page
    labels = re.findall(r"<th[^>]*>([^<]*)</th>", head)
    assert [label.strip() for label in labels] == [
        "رقم السند",
        "سند الدائرة المالية",
        "المشارك",
        "التاريخ",
        "المبلغ",
        "الطريقة",
        "الصندوق",
        "الحالة",
        "",
    ]
    assert 'class="tbl-wrap"' in page


def test_the_register_empty_state_kept_its_words(client: Client, seeded_settings: None) -> None:
    """
    The empty state was already right and is not what this slice was for: the
    same title, the same body, the same rule, and still no action offered.
    """
    from apps.cashbox.models import Receipt

    assert not Receipt.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pl.empty"))

    page = client.get(reverse("cashbox:payments")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "لا سندات قبض في هذا النطاق" in page
    assert "BR-022" in page
    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "empty-act" not in page
    assert 'colspan="9"' in page


def test_the_register_added_no_dead_class_and_no_dependency() -> None:
    """
    Every class it draws with already existed — and one it drew with did not
    work: `.spacer` is defined for `.toolbar` and `.action-bar`, never inside
    `.filterbar`, where the design system already pushes the primary link out
    with ``margin-inline-start: auto``.
    """
    import re

    source = PAYMENTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    markup = source.split("{% endcomment %}", 1)[-1]
    assert "spacer" not in markup, "an inert `.spacer` is back inside the filterbar"
    assert ".filterbar > a.btn2.primary { margin-inline-start: auto; }" in css

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    assert 'class="tbl-wrap"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is being alerted in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


#: §3.4/17 «— · — · V C · — · V C · —» — the till opens for two roles only,
#: and both of them hold CREATE, so no flag in the context gates the button.
TILL_ROLES = (
    (Role.FINANCE_OFFICER, 200),
    (Role.CASHIER, 200),
    (Role.CENTER_MANAGER, 403),
    (Role.REGISTRATION_OFFICER, 403),
    (Role.FINANCE_MANAGER, 403),
    (Role.AUDIT_ACCOUNT, 403),
)


@pytest.mark.parametrize(("role", "expected"), TILL_ROLES)
def test_the_till_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, expected: int
) -> None:
    """
    BR-081 — the centre manager never takes cash, and §3.4/17 withholds the
    screen from four of the six roles entirely. The polish moved presentation,
    never the door.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    may_open = Action.VIEW in allowed_actions(role, "payment-new")
    assert may_open is (expected == 200)
    # Whoever may open it may also save: there is no read-only reader here, and
    # so no flag the template could get wrong.
    assert may_open == (Action.CREATE in allowed_actions(role, "payment-new"))

    client.force_login(_user(role, f"pn.door.{role}".lower().replace("_", ".")))

    assert client.get(reverse("cashbox:payment-new")).status_code == expected


def test_the_till_refuses_an_anonymous_visitor(client: Client, seeded_settings: None) -> None:
    """Fail-closed."""
    assert client.get(reverse("cashbox:payment-new")).status_code == 403


@pytest.mark.parametrize("role", [Role.FINANCE_OFFICER, Role.CASHIER])
def test_the_till_polish_moved_no_control(client: Client, seeded_settings: None, role: str) -> None:
    """
    One form, one submit button, one POST to this same address — exactly what
    was there before the head and the card were built around it.
    """
    import re

    client.force_login(_user(role, f"pn.ctl.{role}".lower().replace("_", ".")))

    page = client.get(reverse("cashbox:payment-new")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert page.count("<form") == 1
    assert page.count("<button") == 1
    assert "csrfmiddlewaretoken" in page
    assert 'type="submit"' in page
    # The form posts to the page it is on; it names no other target.
    assert re.search(r"<form[^>]*action=", page) is None
    # …and the button now sits in the same acts row every other cash form uses.
    assert 'class="form-acts"' in page
    assert page.index('class="form-acts"') > page.index("<form")


@pytest.mark.parametrize("role", [Role.FINANCE_OFFICER, Role.CASHIER])
def test_the_till_keeps_every_field_it_had(
    client: Client, seeded_settings: None, role: str
) -> None:
    """
    The fields are the form's, not the template's: the shared partial is
    rendered whole, and the names the view reads are the names on screen.
    """
    client.force_login(_user(role, f"pn.fld.{role}".lower().replace("_", ".")))

    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8")

    for name in response.context["form"].fields:
        assert f'name="{name}"' in page, f"{role} is not shown the «{name}» field"


def test_the_till_head_reads_like_every_polished_screen(
    client: Client, seeded_settings: None
) -> None:
    """It was an ``<h1>`` alone, under a blue box, under the guided help."""
    client.force_login(_user(Role.CASHIER, "pn.head"))

    page = client.get(reverse("cashbox:payment-new")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="card2-head"' in page


def test_the_first_payment_minimum_is_read_from_its_setting(
    client: Client, seeded_settings: None
) -> None:
    """
    BR-020's figure was prose. The rule is enforced from the effective-dated
    ``diploma_minimum_first_payment``, so a centre that raised the minimum was
    left with a screen quoting a number the save no longer refused below.
    """
    from datetime import date

    from apps.core.services.settings_service import get_setting

    configured = get_setting("diploma_minimum_first_payment", as_of=date(2026, 9, 20))
    assert configured is not None, "the setting is not seeded, so this proves nothing"

    client.force_login(_user(Role.CASHIER, "pn.min.seeded"))
    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["minimum_first_payment"] == configured
    assert "أقل دفعة أولى للدبلوم" in page
    # The seeded value reads the way it always did, breakdown included.
    assert "400" in page
    assert "300 تسجيل + 100 أول مادة" in page
    assert response.context["minimum_breakdown_holds"] is True


def test_changing_the_setting_changes_the_figure_on_screen(
    client: Client, seeded_settings: None
) -> None:
    """
    The point of the slice, proved the only way that counts — and proved
    through the settings service, closing the open period before opening the
    next one, because a value is superseded here and never overwritten
    (ADR-009).
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    client.force_login(_user(Role.CASHIER, "pn.min.raised"))
    before = client.get(reverse("cashbox:payment-new")).content.decode("utf-8")
    assert "400" in before

    today = timezone.localdate()
    close_setting("diploma_minimum_first_payment", effective_to=today - timedelta(days=1))
    set_setting(
        "diploma_minimum_first_payment",
        Decimal("575.000"),
        value_type=SettingValueType.DECIMAL,
        effective_from=today,
        note="BR-020 — raised for the regression test.",
    )

    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["minimum_first_payment"] == Decimal("575.000")
    assert "575" in page
    assert "أقل دفعة أولى للدبلوم" in page
    assert "400" not in page, "the old figure is still on screen"
    # The split belonged to 400 and nothing records how 575 divides, so it goes.
    assert response.context["minimum_breakdown_holds"] is False
    assert "300 تسجيل" not in page
    assert "100 أول مادة" not in page
    # …and the screen is otherwise the screen it was: same form, same button.
    assert page.count("<form") == 1
    assert page.count("<button") == 1
    assert "csrfmiddlewaretoken" in page


@pytest.fixture
def a_payable_diploma(seeded_settings: None, active_semester: object, participant_data: dict):  # type: ignore[no-untyped-def]
    """
    A diploma enrolment with an outstanding balance, so the till offers it.

    Built through the pricing and charge services for the same reason the
    receipt fixture is: the balance that decides whether an enrolment is
    offered at all is the billing layer's answer, not a number typed here.
    """
    from datetime import date

    from django.core.management import call_command

    from apps.billing.services import charge_service
    from apps.catalog.models import PriceList, PriceListStatus, Program, ProgramType
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    actor = _user(Role.CENTER_MANAGER, "q15.fixture.actor")
    program = Program.objects.filter(program_type=ProgramType.DIPLOMA).order_by("code").first()
    assert program is not None, "no diploma was seeded, so this proves nothing"
    assert program.minimum_first_payment_override is None, "the seed already carries an override"

    cohort = Cohort.objects.create(
        code="CO-Q15-1",
        program=program,
        semester=active_semester,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )
    participant = participant_service.create_participant(actor=actor, data=participant_data)
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    enrollment = Enrollment.objects.create(
        code="EN-Q15-1",
        participant=participant,
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=PriceList.objects.get(status=PriceListStatus.APPROVED),
    )
    charge_service.charge_lines_from_quote(
        actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
    )
    return enrollment


def _post_selecting(client: Client, enrollment: object, **extra: str) -> str:
    """
    Re-render the till with an enrolment selected, without saving anything.

    The amount is left blank so the form fails validation and the page comes
    back — the only way a selection exists on this screen, since a GET carries
    none and there is no script on the page.
    """
    payload = {"enrollment_code": enrollment.code, "amount": "", **extra}  # type: ignore[attr-defined]
    response = client.post(reverse("cashbox:payment-new"), payload)
    assert response.status_code == 200, "the page saved or redirected instead of coming back"
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


def test_no_selection_shows_the_general_minimum_and_guesses_no_override(
    client: Client, a_payable_diploma: object
) -> None:
    """
    A GET carries no selection, so the screen has no programme to read. It says
    the general figure and the rule's scope, and invents nothing.
    """
    client.force_login(_user(Role.CASHIER, "q15.none"))

    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["selected_program_minimum"] is None
    assert "أقل دفعة أولى للدبلوم" in page
    assert "400" in page
    assert "حدّ أدنى خاص للدفعة الأولى" not in page
    # …and the reader is told the rule's scope instead of a guessed figure.
    assert "القاعدة تخصّ الدبلومات وحدها" in page
    assert "Q-15" in page


def test_a_selected_programme_without_an_override_says_nothing_extra(
    client: Client, a_payable_diploma: object
) -> None:
    """
    The general setting stands on its own. A programme with no override adds no
    second figure — silence is the honest answer, not a repeat of the general
    one dressed as a programme rule.
    """
    client.force_login(_user(Role.CASHIER, "q15.plain"))

    page = _post_selecting(client, a_payable_diploma)

    assert "حدّ أدنى خاص للدفعة الأولى" not in page
    assert "القاعدة تخصّ الدبلومات وحدها" in page
    assert "400" in page


def test_a_selected_programme_with_an_override_names_its_own_floor(
    client: Client, a_payable_diploma: object
) -> None:
    """
    Q-15 — a diploma may carry a higher floor than the general setting, and the
    till said only the general one. Now it names the programme's own, and says
    which of the two takes precedence.
    """
    from decimal import Decimal

    program = a_payable_diploma.cohort.program  # type: ignore[attr-defined]
    program.minimum_first_payment_override = Decimal("650.000")
    program.save(update_fields=["minimum_first_payment_override"])

    client.force_login(_user(Role.CASHIER, "q15.override"))
    page = _post_selecting(client, a_payable_diploma)

    assert "حدّ أدنى خاص للدفعة الأولى" in page
    assert "650" in page
    assert "يتقدّم على الحدّ العام" in page
    assert "Q-15" in page
    # The general figure is still shown; the page names both and ranks them
    # rather than replacing one with the other.
    assert "400" in page
    # …and it does not claim this payment WILL be checked against it: whether
    # BR-020 applies at all is the service's call, on the receipt's date and on
    # whether a first payment was already taken.
    assert "سيُفحص" not in page


def test_an_unoffered_code_is_never_resolved(client: Client, a_payable_diploma: object) -> None:
    """
    The posted code is checked against the very list the form was built from,
    so a code the reader was not offered reaches no lookup at all.
    """
    client.force_login(_user(Role.CASHIER, "q15.unoffered"))

    response = client.post(
        reverse("cashbox:payment-new"), {"enrollment_code": "EN-NOT-OFFERED", "amount": ""}
    )
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.status_code == 200
    assert response.context["selected_program_minimum"] is None
    assert "حدّ أدنى خاص للدفعة الأولى" not in page


@pytest.mark.parametrize("role", [Role.FINANCE_OFFICER, Role.CASHIER])
def test_the_q15_line_moved_no_control(
    client: Client, a_payable_diploma: object, role: str
) -> None:
    """
    A sentence was added and nothing else: same fields, same names, same single
    form and button, same POST target, on both branches of the new condition.
    """
    from decimal import Decimal

    client.force_login(_user(role, f"q15.ctl.{role}".lower().replace("_", ".")))

    plain = _post_selecting(client, a_payable_diploma)

    program = a_payable_diploma.cohort.program  # type: ignore[attr-defined]
    program.minimum_first_payment_override = Decimal("650.000")
    program.save(update_fields=["minimum_first_payment_override"])
    with_override = _post_selecting(client, a_payable_diploma)

    for page in (plain, with_override):
        assert page.count("<form") == 1
        assert page.count("<button") == 1
        assert "csrfmiddlewaretoken" in page
        for name in (
            "enrollment_code",
            "amount",
            "payment_method",
            "received_on",
            "external_receipt_ref",
            "breakdown_text_ar",
        ):
            assert f'name="{name}"' in page


def test_the_override_is_read_and_never_enforced_by_the_screen() -> None:
    """
    BR-020 stays in one place. The view reads a field for display; the rule —
    diploma only, first payment only, override before setting — is decided in
    ``payment_service`` and is not re-derived in the view.
    """
    from pathlib import Path

    source = Path("apps/cashbox/views.py").read_text(encoding="utf-8")
    service = Path("apps/cashbox/services/payment_service.py").read_text(encoding="utf-8")

    # The service still owns every part of the decision…
    assert "def check_minimum_first_payment" in service
    assert "ProgramType.DIPLOMA" in service
    assert "minimum_first_payment_override" in service
    assert "BR-020" in service
    # …and the view neither raises for it nor re-tests the programme type.
    # The function's own source, not everything after it in the module.
    import inspect

    from apps.cashbox.views import _selected_program_minimum

    body = inspect.getsource(_selected_program_minimum)
    assert "ProgramType" not in source, "the view re-tests the programme type"
    assert "ValidationError" not in body
    assert "raise" not in body
    assert "minimum_first_payment_override" in body


@pytest.mark.parametrize(
    ("configured", "shows_breakdown"),
    [
        # The split the centre published, beside the value it was published for.
        ("400.000", True),
        # A raised minimum: nothing in the system says how it divides…
        ("575.000", False),
        # …and neither does a lowered one, nor one that merely looks near 400.
        ("250.000", False),
        ("400.500", False),
    ],
)
def test_the_breakdown_is_shown_only_beside_the_value_it_explains(
    client: Client, seeded_settings: None, configured: str, shows_breakdown: bool
) -> None:
    """
    «300 تسجيل + 100 أول مادة» is the reasoning behind 400 and no other figure.
    Printed beside a different minimum it would be arithmetic that does not add
    up, so it is dropped rather than guessed at — while the amount itself keeps
    tracking the setting either way.
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from apps.core.models import SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    today = timezone.localdate()
    if configured != "400.000":
        close_setting("diploma_minimum_first_payment", effective_to=today - timedelta(days=1))
        set_setting(
            "diploma_minimum_first_payment",
            Decimal(configured),
            value_type=SettingValueType.DECIMAL,
            effective_from=today,
            note="BR-020 — regression fixture.",
        )

    client.force_login(_user(Role.CASHIER, f"pn.split.{configured}".replace(".", "-")))
    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["minimum_first_payment"] == Decimal(configured)
    assert response.context["minimum_breakdown_holds"] is shows_breakdown
    assert ("300 تسجيل + 100 أول مادة" in page) is shows_breakdown
    # The figure itself is on screen whichever branch was taken — compared
    # against what a template really renders, since USE_L10N formats the
    # Decimal and 400.500 reaches the page as «400,500».
    from django.template.defaultfilters import floatformat

    assert "أقل دفعة أولى للدبلوم" in page
    assert floatformat(Decimal(configured), -3) in page
    # And the till is the till, in both branches.
    assert page.count("<form") == 1
    assert page.count("<button") == 1
    for name in response.context["form"].fields:
        assert f'name="{name}"' in page


def test_the_minimum_is_left_unsaid_when_it_is_not_configured(client: Client, db: None) -> None:
    """
    No settings seeded at all. A guessed figure would be worse than a missing
    line, so the sentence is absent and the form is untouched.
    """
    from apps.core.models import EffectiveSetting

    assert not EffectiveSetting.objects.filter(key="diploma_minimum_first_payment").exists()
    client.force_login(_user(Role.CASHIER, "pn.min.absent"))

    response = client.get(reverse("cashbox:payment-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.status_code == 200
    assert response.context["minimum_first_payment"] is None
    assert "أقل دفعة أولى للدبلوم" not in page
    # The screen still works: same form, same button, same door.
    assert page.count("<form") == 1
    assert page.count("<button") == 1


def test_the_screen_quotes_the_setting_the_service_enforces(
    client: Client, seeded_settings: None
) -> None:
    """
    One key, read in two places, and they must be the same key. The service
    owns the rule; the screen only repeats what the service would read.
    """
    from datetime import date

    from apps.cashbox.services import payment_service
    from apps.core.services.settings_service import get_setting

    assert payment_service.MIN_FIRST_PAYMENT_KEY == "diploma_minimum_first_payment"

    client.force_login(_user(Role.FINANCE_OFFICER, "pn.min.samekey"))
    shown = client.get(reverse("cashbox:payment-new")).context["minimum_first_payment"]

    assert shown == get_setting(payment_service.MIN_FIRST_PAYMENT_KEY, as_of=date.today())


def test_the_first_payment_minimum_is_explained_and_not_alerted(
    client: Client, seeded_settings: None
) -> None:
    """
    BR-020's figure is still on screen, word for word — it is the one thing the
    guide does not carry, which names the rule without the number. It is no
    longer blue: explaining a rule is not an alert (polish rules §6.5), and it
    now sits above the fields it constrains rather than above the whole page.
    """
    from apps.people.guidance import GUIDES

    client.force_login(_user(Role.CASHIER, "pn.br020"))

    page = client.get(reverse("cashbox:payment-new")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert "أقل دفعة أولى للدبلوم" in page
    assert "note info" not in page
    assert 'class="hint boxed"' in page
    assert page.index("أقل دفعة أولى") < page.index("<form")
    # The guide names the rule; the page carries the figure. Neither repeats
    # the other, and BR-020 is said on this screen and no other.
    assert "BR-020" in str(GUIDES["payment-new"].stops)
    assert "400" not in str(GUIDES["payment-new"].stops)


def test_the_till_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PAYMENT_NEW_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the till uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    # A form page: it renders the shared partial whole and invents no markup of
    # its own for the fields.
    assert markup.count('{% include "partials/_form.html" %}') == 1
    assert "tbl-wrap" not in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


#: §3.4/16 «V P · V E P · V A X P · V P · V C P · V P» — every role may open a
#: receipt, and (role, may request a void, may approve one). Δ-06 puts the two
#: in different hands on purpose, and no role holds both.
RECEIPT_READERS = (
    (Role.CENTER_MANAGER, False, False),
    (Role.REGISTRATION_OFFICER, False, False),
    (Role.FINANCE_OFFICER, False, True),
    (Role.FINANCE_MANAGER, False, False),
    (Role.CASHIER, True, False),
    (Role.AUDIT_ACCOUNT, False, False),
)


@pytest.fixture
def a_receipt(seeded_settings: None, active_semester: object, participant_data: dict) -> object:
    """
    One issued receipt with its stored allocations, built through the services.

    Through ``take_payment`` rather than the ORM because the allocations this
    page prints are BR-022's stored split, and a hand-built row could hold a
    breakdown the algorithm never produces.
    """
    from datetime import date
    from decimal import Decimal

    from django.core.management import call_command

    from apps.billing.services import charge_service
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service
    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    actor = _user(Role.CENTER_MANAGER, "rc.fixture.actor")
    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-RC-1",
        program=program,
        semester=active_semester,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )
    participant = participant_service.create_participant(actor=actor, data=participant_data)
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    enrollment = Enrollment.objects.create(
        code="EN-RC-1",
        participant=participant,
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=PriceList.objects.get(status=PriceListStatus.APPROVED),
    )
    charge_service.charge_lines_from_quote(
        actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
    )
    method, _created = PaymentMethod.objects.get_or_create(
        code="CASH", defaults={"name_ar": "نقداً"}
    )
    return payment_service.take_payment(
        actor=_user(Role.CASHIER, "rc.fixture.cashier"),
        enrollment=enrollment,
        amount=Decimal("50.000"),
        payment_method=method,
        received_on=date(2026, 9, 20),
    )


def _receipt_page(client: Client, receipt: object) -> str:
    """The page body, with the sidebar cut off so nav copy cannot answer for it."""
    url = reverse("cashbox:receipt-detail", args=[receipt.internal_receipt_number])  # type: ignore[attr-defined]
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "may_request", "may_approve"), RECEIPT_READERS)
def test_the_receipt_teaches_the_void_split_it_enforces(
    client: Client, a_receipt: object, role: str, may_request: bool, may_approve: bool
) -> None:
    """
    Every role may read a receipt and no role may do both halves of a void.
    The block says the split to all six — including the four who do neither
    and would otherwise never learn that a receipt is cancelled, not erased.
    """
    from apps.people.guidance import GUIDES

    client.force_login(_user(role, f"gh.rc.{role}".lower().replace("_", ".")))

    page = _receipt_page(client, a_receipt)
    guide = GUIDES["receipt-detail"]

    assert str(guide.what) in page
    assert str(guide.who) in page
    assert str(guide.stops) in page
    assert "BR-025" in str(guide.stops)
    assert page.index(str(guide.what)) < page.index('class="card2"')


def test_the_receipt_head_reads_like_every_polished_screen(
    client: Client, a_receipt: object
) -> None:
    """It was an ``<h1>`` alone; it now says its section and what a row is."""
    client.force_login(_user(Role.FINANCE_OFFICER, "rc.head"))

    page = _receipt_page(client, a_receipt)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert a_receipt.internal_receipt_number in page  # type: ignore[attr-defined]


def test_a_recorded_void_request_is_not_an_alert(client: Client, a_receipt: object) -> None:
    """
    A request that was recorded, with its reason, is a fact — the same shape
    the ministry rejection was moved out of `.note danger` for. Amber is for
    the moment something is refused (polish rules §6.5), and nothing is being
    refused by a request that went through.
    """
    from apps.cashbox.services import payment_service

    cashier = _user(Role.CASHIER, "rc.void.asks")
    payment_service.request_void(
        actor=cashier, receipt=a_receipt, reason_ar="خطأ في المبلغ المستوفى"
    )

    client.force_login(_user(Role.AUDIT_ACCOUNT, "rc.void.reads"))
    page = _receipt_page(client, a_receipt)

    # Still taught, still attributed, and still carrying its reason…
    assert "خطأ في المبلغ المستوفى" in page
    assert "BR-025" in page
    assert 'class="hint boxed"' in page
    # …but no longer in amber, and no longer floating outside a card.
    assert "note warn" not in page
    assert "note danger" not in page


def test_the_allocations_empty_state_says_what_an_allocation_is(
    client: Client, a_receipt: object
) -> None:
    """
    «لا تخصيصات» alone told a reader nothing. The body says what the stored
    split is — and it offers no action, because there is none to offer.
    """
    from apps.cashbox.models import PaymentAllocation

    PaymentAllocation.objects.all().delete()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "rc.alloc.empty"))

    page = _receipt_page(client, a_receipt)

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "BR-022" in page
    assert "empty-act" not in page


@pytest.mark.parametrize(("role", "may_request", "may_approve"), RECEIPT_READERS)
def test_the_receipt_polish_moved_no_control(
    client: Client, a_receipt: object, role: str, may_request: bool, may_approve: bool
) -> None:
    """
    Δ-06 puts the asking and the deciding in different hands, and the polish
    did not move either. The four roles that hold neither get a page with no
    form, no button and no CSRF token on it at all.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "payments")) is may_request
    assert (Action.VOID in allowed_actions(role, "payments")) is may_approve
    assert not (may_request and may_approve), "one role would hold both halves of a void"

    client.force_login(_user(role, f"rc.ctl.{role}".lower().replace("_", ".")))
    url = reverse("cashbox:receipt-detail", args=[a_receipt.internal_receipt_number])  # type: ignore[attr-defined]
    response = client.get(url)
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["can_request_void"] is may_request
    assert response.context["can_approve_void"] is may_approve
    # No void exists yet, so only the request form can be drawn at all.
    assert ('name="action" value="request"' in page) is may_request
    assert 'name="action" value="approve"' not in page
    if not may_request:
        assert "<form" not in page
        assert "<button" not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_approve_control_appears_only_for_the_role_that_decides(
    client: Client, a_receipt: object
) -> None:
    """The other half of Δ-06: the button exists, and for one role only."""
    from apps.cashbox.services import payment_service

    payment_service.request_void(
        actor=_user(Role.CASHIER, "rc.appr.asks"), receipt=a_receipt, reason_ar="سبب مسجَّل"
    )

    for role, _may_request, may_approve in RECEIPT_READERS:
        client.force_login(_user(role, f"rc.appr.{role}".lower().replace("_", ".")))
        page = _receipt_page(client, a_receipt)
        assert ('name="action" value="approve"' in page) is may_approve, role
        client.logout()


def test_the_receipt_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen reads a receipt and carries the two halves of a void. The help
    may not imply the receipt is edited or deleted, nor that a price, a
    participant or an allocation is recalculated from here.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["receipt-detail"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "لا يُعدَّل سند صادر ولا يُحذف" in text
    assert "BR-025" in text
    # The register's guide says the same rule; two screens may not say it two
    # ways, so the shared clause is quoted from it word for word.
    assert "الإلغاء يكتب قيداً عكسياً ويُبقي الأصل" in str(GUIDES["payments"].stops)
    assert "الإلغاء يكتب قيداً عكسياً ويُبقي الأصل" in text
    # …and no rule that belongs on a different screen.
    for elsewhere in ("BR-020", "BR-028", "أقل دفعة أولى"):
        assert elsewhere not in text, f"the receipt guidance repeats «{elsewhere}»"


def test_the_receipt_detail_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = RECEIPT_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the receipt page uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="dl"' in markup
    assert 'class="tbl-wrap"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a fact is still being alerted in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


#: §3.4/18 «V A P · — · V C E A P · — · V C · V P» — everyone who may open the
#: daily closing, and whether the matrix lets them approve one.
CLOSING_READERS = (
    (Role.CENTER_MANAGER, True),
    (Role.FINANCE_OFFICER, True),
    (Role.CASHIER, False),
    (Role.AUDIT_ACCOUNT, False),
)


@pytest.mark.parametrize(("role", "may_approve"), CLOSING_READERS)
def test_the_daily_closing_teaches_the_separation_it_enforces(
    client: Client, seeded_settings: None, role: str, may_approve: bool
) -> None:
    """
    BR-028 is enforced in ``closing_service.reconcile`` and said nowhere on the
    screen: the cashier simply finds no approve button, which teaches them the
    button is missing and not why. The block says it, to every reader — the
    cashier included, who is the one the rule is about.
    """
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.APPROVE in allowed_actions(role, "closing")) is may_approve
    client.force_login(_user(role, f"gh.cl.{role}".lower().replace("_", ".")))

    response = client.get(reverse("cashbox:closing"))
    assert response.status_code == 200, role
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    guide = GUIDES["cashbox-closing"]
    assert str(guide.what) in page
    assert str(guide.who) in page
    assert str(guide.stops) in page
    assert "BR-028" in str(guide.stops)
    # …above everything the screen itself says.
    assert page.index(str(guide.what)) < page.index('class="card2"')
    # BR-027 is still taught, and no longer as a blue alert: it explains the
    # columns from a `.hint` under the table they describe (polish rules §6.5),
    # so the two rules no longer stack as two framed boxes above the page.
    assert "BR-027" in page
    assert "note info" not in page
    assert '<p class="hint">' in page, "BR-027 no longer sits in a neutral hint"
    assert page.index("BR-027") > page.index('class="tbl"'), "the rule left its columns behind"


@pytest.mark.parametrize(("role", "_may_approve"), CLOSING_READERS)
def test_the_daily_closing_offers_only_a_next_step_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_approve: bool
) -> None:
    """
    §3.4/16 gives every one of these four readers VIEW on the receipts
    register, so the one link is offered to all four — and it has to open.
    """
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"gh.cl.link.{role}".lower().replace("_", ".")))
    page = client.get(reverse("cashbox:closing")).content.decode("utf-8").split("</nav>", 1)[-1]

    for screen, route, _label in GUIDES["cashbox-closing"].links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_daily_closing_head_reads_like_every_polished_screen(
    client: Client, seeded_settings: None
) -> None:
    """
    It was an ``<h1>`` alone — no section, no sentence saying what the rows
    are. The head now carries the three the other registers carry, off the
    view's own ``title`` and no new context.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "cl.head"))

    page = client.get(reverse("cashbox:closing")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    # The count describes the rows drawn, not the register behind them.
    assert 'class="count"' in page


def test_the_daily_closing_table_names_its_action_column(
    client: Client, seeded_settings: None
) -> None:
    """
    Nine columns and the ninth had no name at all. It is named by
    ``aria-label`` rather than `.sr-only`: the latter is ``position:absolute``
    with no positioned ancestor, so in RTL it lands off the left edge and drags
    the page sideways — the defect three earlier slices were fixed for.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "cl.head.col"))

    page = client.get(reverse("cashbox:closing")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    # Nine headers for nine cells, still inside the wrapper that scrolls.
    import re

    assert len(re.findall(r"<th[\s>]", page)) == 9
    assert 'class="tbl-wrap"' in page


def test_the_daily_closing_empty_state_says_what_a_closing_is(
    client: Client, seeded_settings: None
) -> None:
    """
    «لا إقفالات» and nothing else told a reader on a quiet morning neither what
    the screen is for nor how a row ever appears. The empty state now carries a
    body like every other register — and still offers no action, because the
    reader may not hold the one that opens a closing.
    """
    from apps.cashbox.models import DailyClosing

    assert not DailyClosing.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "cl.empty"))

    page = client.get(reverse("cashbox:closing")).content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "BR-026" in page
    assert "empty-act" not in page, "the empty state offers an action that has no route"


@pytest.mark.parametrize(
    ("role", "may_create", "may_approve"),
    [
        (Role.CENTER_MANAGER, False, True),
        (Role.FINANCE_OFFICER, True, True),
        (Role.CASHIER, True, False),
        (Role.AUDIT_ACCOUNT, False, False),
    ],
)
def test_the_daily_closing_polish_moved_no_control(
    client: Client, seeded_settings: None, role: str, may_create: bool, may_approve: bool
) -> None:
    """
    The polish is presentation. Both forms are still drawn off the same two
    flags the view computes from §3.4/18, and the audit account — who holds
    neither — still gets a page with no form, no button and no CSRF token on
    it at all.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "closing")) is may_create
    assert (Action.APPROVE in allowed_actions(role, "closing")) is may_approve
    client.force_login(_user(role, f"cl.ctl.{role}".lower().replace("_", ".")))

    response = client.get(reverse("cashbox:closing"))
    assert response.status_code == 200
    assert response.context["can_create"] is may_create
    assert response.context["can_approve"] is may_approve
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    # «فتح إقفال» is the only form an empty register can draw; the approve form
    # lives on a row and there are none.
    assert ('name="action" value="open"' in page) is may_create
    if not may_create:
        assert "<form" not in page
        assert "<button" not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_daily_closing_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = CLOSING_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the daily closing uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert "sr-only" not in markup
    # A rule is explained, not alerted (polish rules §6.5).
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


def test_the_daily_closing_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The block explains; it never promises. The closing screen opens a closing
    and approves one, and the help may not imply a cashier approves their own
    or that a receipt is corrected on the summary — which is exactly what the
    rule and the correction route in ``stops`` say the other way round.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["cashbox-closing"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "لا يعتمد أمين الصندوق إقفال يومه" in text
    assert "سجل الدفعات" in text
    # No act this screen does not perform, and no participant on a till page.
    for absent in ("تعديل السند", "حذف", "المشارك", "الرسوم الدراسية"):
        assert absent not in text, f"the closing guidance offers «{absent}»"


@pytest.mark.parametrize("program_type", ["DIPLOMA", "SHORT_COURSE", "ONLINE_COURSE"])
def test_the_programme_card_teaches_whichever_list_it_was_opened_from(
    client: Client, a_catalogue: None, program_type: str
) -> None:
    """
    One template serves the diploma, the short course and the online course, so
    the help is one entry and has to read correctly for all three. It teaches
    above the first card, and it points on down the chain — the price list, the
    cohort and the ministry file — never sideways at the other two catalogues,
    which is the guarantee ``test_the_card_offers_the_way_back_to_its_own_list``
    holds.
    """
    from apps.catalog.views import LIST_ROUTE_BY_SCREEN
    from apps.people.guidance import GUIDES

    code = _a_program(program_type)
    client.force_login(_user(Role.CENTER_MANAGER, f"gh.pd.{program_type}".lower()))

    page = (
        client.get(reverse("catalog:program-detail", args=[code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    guide = GUIDES["program-detail"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for _screen, route, _label in guide.links:
        assert f'href="{reverse(route)}"' in page
        assert client.get(reverse(route)).status_code == 200
    for route in LIST_ROUTE_BY_SCREEN.values():
        assert route not in {r for _s, r, _l in guide.links}


@pytest.mark.parametrize(
    ("role", "offered"),
    [
        (Role.CENTER_MANAGER, ("catalog:pricelists", "operations:cohorts", "operations:mohe")),
        (
            Role.REGISTRATION_OFFICER,
            ("catalog:pricelists", "operations:cohorts", "operations:mohe"),
        ),
        # §3.3/14 leaves the finance officer's ministry cell empty, so the help
        # offers them the two they may open and not the one they may not.
        (Role.FINANCE_OFFICER, ("catalog:pricelists", "operations:cohorts")),
    ],
)
def test_the_programme_card_offers_only_the_next_steps_the_reader_may_open(
    client: Client, a_catalogue: None, role: str, offered: tuple[str, ...]
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.guidance import GUIDES

    code = _a_program("DIPLOMA")
    client.force_login(_user(role, f"gh.pd.links.{role}".lower().replace("_", ".")))

    page = (
        client.get(reverse("catalog:program-detail", args=[code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    for _screen, route, _label in GUIDES["program-detail"].links:
        assert (f'href="{reverse(route)}"' in page) is (route in offered), f"{role} · {route}"


def test_the_price_list_detail_page_teaches_before_it_lists(
    client: Client, a_catalogue: None
) -> None:
    from apps.catalog.models import PriceList
    from apps.people.guidance import GUIDES

    code = PriceList.objects.values_list("code", flat=True).first()
    assert code, "no price list was seeded, so this proves nothing"
    client.force_login(_user(Role.CENTER_MANAGER, "gh.pl.detail"))

    page = (
        client.get(reverse("catalog:pricelist-detail", args=[code]))
        .content.decode("utf-8")
        .split("</nav>", 1)[-1]
    )

    assert str(GUIDES["pricelist-detail"].what) in page
    assert str(GUIDES["pricelist-detail"].stops) in page
    assert page.index(str(GUIDES["pricelist-detail"].what)) < page.index('class="card2"')


def test_the_ministry_file_page_teaches_before_it_lists(
    client: Client, mohe_files: dict[str, object]
) -> None:
    from apps.people.guidance import GUIDES

    client.force_login(_user(Role.CENTER_MANAGER, "gh.mohe.detail"))

    page = _file_page(client, mohe_files["ready"])

    assert str(GUIDES["mohe-detail"].what) in page
    assert str(GUIDES["mohe-detail"].stops) in page
    assert page.index(str(GUIDES["mohe-detail"].what)) < page.index('class="card2"')


@pytest.mark.parametrize(("key", "template"), GUIDED_HELP_SLICE)
def test_the_new_guidance_carries_no_commercial_term(key: str, template: Path) -> None:
    """
    The same rule the six screens themselves were held to: a share agreed with
    a third party has no place on a price list, an operations register or a
    ministry file — and none in the sentence printed above them either.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES[key]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))
    for term in COMMERCIAL_TERMS_OFF_THE_CATALOGUE:
        assert term not in text, f"the «{key}» guidance says «{term}»"


@pytest.mark.parametrize(("key", "template"), GUIDED_HELP_SLICE)
def test_the_new_guidance_pronounces_no_verdict_its_screen_does_not_compute(
    key: str, template: Path
) -> None:
    """
    The teaching must not out-claim the page. Which list applies is settled by
    the pricing service on the event date, whether a file may be sent is the
    ministry service's answer, and the ministry's decision is recorded as it
    arrived — so the help says where each is decided, never what it decided.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES[key]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))
    for verdict in VERDICTS_THE_GUIDANCE_MAY_NOT_PRONOUNCE:
        assert verdict not in text, f"the «{key}» guidance claims «{verdict}»"


@pytest.mark.parametrize(("key", "template"), GUIDED_HELP_SLICE)
def test_the_slice_templates_kept_their_structural_guarantees(key: str, template: Path) -> None:
    """
    The block was inserted and nothing else moved: no style attribute, no
    script, no external address, no unbalanced comment and no `.sr-only` —
    the guarantees each of these slices closed on.
    """
    source = template.read_text(encoding="utf-8")

    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source
    markup = source.split("{% endcomment %}", 1)[-1]
    assert "sr-only" not in markup
    assert "{% comment %}" not in markup
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The discounts register — page polish
# ---------------------------------------------------------------------------
# §3.4/19 «V C E A P · — · V P · — · — · V P». Three roles read it; only the
# centre manager grants, and only a manager who did not raise the row may
# countersign it (D-18). The screen was a bare ``<h1>``, a blue alert carrying
# a rule, an unnamed tenth column and an empty state that said «لا خصومات».
DISCOUNTS_TEMPLATE = Path("templates/billing/discounts.html")

#: role, may grant, may countersign — read straight off §3.4/19.
DISCOUNT_READERS = (
    (Role.CENTER_MANAGER, True, True),
    (Role.FINANCE_OFFICER, False, False),
    (Role.AUDIT_ACCOUNT, False, False),
)

#: The roles §3.4/19 leaves empty. An empty cell is an explicit deny (BR-080).
DISCOUNT_NON_READERS = (Role.REGISTRATION_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def a_discount(seeded_settings: None, active_semester: object, participant_data: dict) -> object:
    """
    One granted discount, raised through the service that decides the split.

    Built the long way for the reason the receipt fixture is: ``university_burden``
    and ``discount_split_mode_snapshot`` are the service's answer to the
    agreement behind the cohort, and a hand-built row could carry a split the
    algorithm never produces.
    """
    from datetime import date
    from decimal import Decimal

    from django.core.management import call_command

    from apps.billing.models import DiscountType
    from apps.billing.services import charge_service, discount_service
    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    actor = _user(Role.CENTER_MANAGER, "dc.fixture.actor")
    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-DC-1",
        program=program,
        semester=active_semester,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )
    participant = participant_service.create_participant(actor=actor, data=participant_data)
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    enrollment = Enrollment.objects.create(
        code="EN-DC-1",
        participant=participant,
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=PriceList.objects.get(status=PriceListStatus.APPROVED),
    )
    charge_service.charge_lines_from_quote(
        actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
    )
    return discount_service.grant_discount(
        actor=actor,
        enrollment=enrollment,
        discount_type=DiscountType.AMOUNT,
        amount=Decimal("25.000"),
        reason_ar="حالة اجتماعية موثّقة",
        president_approval_ref="PR-2026-77",
        president_approval_date=date(2026, 9, 20),
    )


def _discounts_page(client: Client) -> str:
    response = client.get(reverse("billing:discounts"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_grant", "_may_approve"), DISCOUNT_READERS)
def test_the_discounts_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_grant: bool, _may_approve: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "discounts")
    client.force_login(_user(role, f"dc.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:discounts")).status_code == 200


@pytest.mark.parametrize("role", DISCOUNT_NON_READERS)
def test_the_discounts_register_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "discounts")
    client.force_login(_user(role, f"dc.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:discounts")).status_code == 403


def test_the_discounts_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("billing:discounts")).status_code in (302, 403)


@pytest.mark.parametrize(("role", "may_grant", "_may_approve"), DISCOUNT_READERS)
def test_the_grant_form_is_drawn_only_where_create_is_granted(
    client: Client, seeded_settings: None, role: str, may_grant: bool, _may_approve: bool
) -> None:
    """
    The form follows ``can_create`` exactly as it did, and a reader without it
    gets a page with no form, no button and no CSRF token at all.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "discounts")) is may_grant
    client.force_login(_user(role, f"dc.grant.{role}".lower().replace("_", ".")))

    response = client.get(reverse("billing:discounts"))
    assert response.context["can_create"] is may_grant
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert ('name="action" value="grant"' in page) is may_grant
    if not may_grant:
        assert "<form" not in page
        assert "<button" not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_grant_form_still_carries_every_field_it_carried(
    client: Client, seeded_settings: None
) -> None:
    """Presentation only: same fields, same names, one form, one button."""
    client.force_login(_user(Role.CENTER_MANAGER, "dc.fields"))

    response = client.get(reverse("billing:discounts"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for name in response.context["form"].fields:
        assert f'name="{name}"' in page, name
    assert page.count("<form") == 1
    assert page.count("<button") == 1
    assert 'class="form-acts"' in page


@pytest.mark.parametrize(("role", "_may_grant", "may_approve"), DISCOUNT_READERS)
def test_the_countersign_button_follows_approve_and_never_the_raiser(
    client: Client, a_discount: object, role: str, _may_grant: bool, may_approve: bool
) -> None:
    """
    D-18 is a service refusal AND a drawing condition: the manager who raised
    the row is offered no button, and every other reader is offered one only
    if the matrix grants APPROVE. Both directions, as the rules require.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.APPROVE in allowed_actions(role, "discounts")) is may_approve
    client.force_login(_user(role, f"dc.appr.{role}".lower().replace("_", ".")))
    assert ('name="action" value="approve"' in _discounts_page(client)) is may_approve
    client.logout()

    # The raiser themselves — same role, same permission, no button.
    from apps.people.models import User

    client.force_login(User.objects.get(username="dc.fixture.actor"))
    page = _discounts_page(client)
    assert 'name="action" value="approve"' not in page
    assert "بانتظار اعتماد غير المُنشئ" in page


def test_the_discounts_register_prints_only_keys_the_row_already_carried(
    client: Client, a_discount: object
) -> None:
    """The nine data columns are the projection's own values, unchanged."""
    client.force_login(_user(Role.FINANCE_OFFICER, "dc.row"))

    response = client.get(reverse("billing:discounts"))
    row = response.context["discounts"][0]
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for key in ("enrollment_code", "participant_name", "reason_ar", "president_approval_ref"):
        assert str(row[key]) in page, key
    assert row["split_mode"] in page
    from django.template.defaultfilters import floatformat

    for key in ("amount", "base_amount", "university_burden", "partner_burden"):
        assert floatformat(row[key], -3) in page, key


def test_the_discounts_head_reads_like_every_polished_screen(
    client: Client, a_discount: object
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and the count the other
    finance registers carry — all off the view's own ``title`` and no new
    context key.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "dc.head"))

    page = _discounts_page(client)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page
    assert 'class="card2-head"' in page


def test_the_discounts_table_names_its_tenth_column(client: Client, a_discount: object) -> None:
    """
    Ten columns and the tenth had no name. Named by ``aria-label`` and not by
    `.sr-only`, which is ``position:absolute`` with no positioned ancestor and
    lands off the left edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.FINANCE_OFFICER, "dc.col"))

    page = _discounts_page(client)

    assert 'aria-label="الاعتماد الداخلي"' in page
    assert "sr-only" not in page
    assert len(re.findall(r"<th[\s>]", page)) == 10
    assert 'class="tbl-wrap"' in page


def test_the_discounts_rule_is_explained_and_no_longer_alerted(
    client: Client, a_discount: object
) -> None:
    """
    §5.1 and the partner's single absorption were a blue `.note info` above the
    page. Blue is an alert and explaining a rule is not one (polish rules §6.5),
    so the same sentence now sits as a `.hint` beneath the columns it explains.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "dc.rule"))

    page = _discounts_page(client)

    assert "لا تُحسم مرة ثانية من وعاء المطالبة" in page
    assert "note info" not in page
    assert '<p class="hint">' in page
    assert page.index("وعاء المطالبة") > page.index('class="tbl"'), "the rule left its columns"


def test_the_discounts_empty_state_says_what_the_emptiness_means(
    client: Client, seeded_settings: None
) -> None:
    """
    «لا خصومات» told a reader neither what a discount is nor how one appears.
    The body now names the two conditions, and the state offers no action: the
    reader who may grant one finds the form below, and the reader who may not
    is never invited into a refusal (BR-085).
    """
    from apps.billing.models import Discount

    assert not Discount.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "dc.empty"))

    page = _discounts_page(client)

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "BR-030" in page
    assert "empty-act" not in page


def test_the_discounts_register_renders_on_an_empty_database(
    client: Client, seeded_settings: None
) -> None:
    """Settings and nothing else — the screen still draws for all three readers."""
    for role, _grant, _approve in DISCOUNT_READERS:
        client.force_login(_user(role, f"dc.bare.{role}".lower().replace("_", ".")))
        assert client.get(reverse("billing:discounts")).status_code == 200
        client.logout()


def test_the_discounts_register_kept_its_enrolment_filter_untouched(
    client: Client, a_discount: object
) -> None:
    """
    The view has always narrowed by ``?enrollment=``, and the polish neither
    added a control for it nor changed what it does.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "dc.filter"))

    matched = client.get(reverse("billing:discounts"), {"enrollment": "EN-DC-1"})
    missed = client.get(reverse("billing:discounts"), {"enrollment": "EN-NOT-THERE"})

    assert len(matched.context["discounts"]) == 1
    assert len(missed.context["discounts"]) == 0
    source = Path("apps/billing/views.py").read_text(encoding="utf-8")
    assert 'request.GET.get("enrollment", "").strip()' in source


def test_the_discounts_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen grants a discount and countersigns one. The help may not imply
    the row is edited or deleted, nor that money is taken here — and it names
    the three refusals a reader meets as a message they could not predict.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["discounts"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "BR-030" in text
    assert "D-18" in text
    assert "§5.1" in text
    for absent in ("حذف", "تعديل الخصم", "استيفاء", "إلغاء السند"):
        assert absent not in text, f"the discounts guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_grant", "_may_approve"), DISCOUNT_READERS)
def test_the_discounts_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_grant: bool, _may_approve: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"dc.links.{role}".lower().replace("_", ".")))
    page = _discounts_page(client)

    guide = GUIDES["discounts"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_discounts_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = DISCOUNTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the discounts register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert 'class="form-acts"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The refunds and credit returns screen — page polish
# ---------------------------------------------------------------------------
# §3.4/20 «V A P · — · V C E P · — · — · V P». Three roles read it, and the
# three acts sit in two different pairs of hands: the finance officer requests
# and executes, the centre manager approves or rejects. The screen was a bare
# <h1>, a yellow alert carrying two rules at once, an unnamed ninth column, a
# rejection box with no accessible name and two empty states saying «لا
# استردادات» and «لا حركات».
REFUNDS_TEMPLATE = Path("templates/billing/refunds.html")

#: role, may request/execute, may approve — read straight off §3.4/20.
REFUND_READERS = (
    (Role.CENTER_MANAGER, False, True),
    (Role.FINANCE_OFFICER, True, False),
    (Role.AUDIT_ACCOUNT, False, False),
)

#: The roles §3.4/20 leaves empty. An empty cell is an explicit deny (BR-080).
REFUND_NON_READERS = (Role.REGISTRATION_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def a_refund_and_a_credit(  # type: ignore[no-untyped-def]
    seeded_settings: None, active_semester: object, participant_data: dict
):
    """
    One requested refund and one returned credit, each on its own enrolment.

    Both through the services: ``partner_recovery_amount`` is the refund
    service's answer to what the partner was already paid, and the credit is
    whatever ``get_account_state`` calls an overpayment — neither is a number
    this fixture is entitled to invent.
    """
    from datetime import date
    from decimal import Decimal

    from django.core.management import call_command

    from apps.billing.services import charge_service, credit_service, refund_service
    from apps.cashbox.models import PaymentMethod
    from apps.cashbox.services import payment_service
    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    actor = _user(Role.CENTER_MANAGER, "rf.fixture.actor")
    finance = _user(Role.FINANCE_OFFICER, "rf.fixture.finance")
    cashier = _user(Role.CASHIER, "rf.fixture.cashier")
    program = Program.objects.get(code="SC-NET")
    price_list = PriceList.objects.get(status=PriceListStatus.APPROVED)
    method, _created = PaymentMethod.objects.get_or_create(
        code="CASH", defaults={"name_ar": "نقداً"}
    )
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    due = quote.course_fee + (quote.registration_fee or Decimal("0.000"))

    def _enrol(suffix: str, id_number: str, paid: object) -> object:
        cohort = Cohort.objects.create(
            code=f"CO-RF-{suffix}",
            program=program,
            semester=active_semester,
            name_ar=f"دفعة {program.name_ar} {suffix}",
            starts_on=date(2026, 9, 20),
            ends_on=date(2026, 12, 20),
            capacity=25,
        )
        participant = participant_service.create_participant(
            actor=actor, data={**participant_data, "id_document_number": id_number}
        )
        enrollment = Enrollment.objects.create(
            code=f"EN-RF-{suffix}",
            participant=participant,
            cohort=cohort,
            enrolled_on=date(2026, 9, 20),
            price_list=price_list,
        )
        charge_service.charge_lines_from_quote(
            actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
        )
        payment_service.take_payment(
            actor=cashier,
            enrollment=enrollment,
            amount=paid,
            payment_method=method,
            received_on=date(2026, 9, 20),
        )
        return enrollment

    # One enrolment paid exactly, so a refund has something to reverse…
    refunded = _enrol("1", participant_data["id_document_number"], due)
    refund = refund_service.request_refund(
        actor=finance,
        enrollment=refunded,
        refund_type="PARTIAL",
        amount=Decimal("30.000"),
        reason_ar="إلغاء الدورة لعدم اكتمال العدد",
        official_letter_ref="LT-2026-9",
        official_letter_date=date(2026, 9, 20),
        president_approval_ref="PR-2026-9",
        president_approval_date=date(2026, 9, 20),
        code="RF-UI-1",
    )
    # …and one overpaid, so a credit exists to hand back. Two enrolments, not
    # one: the point of the screen is that these are different movements.
    overpaid = _enrol(
        "2", "8" + participant_data["id_document_number"][1:], due + Decimal("80.000")
    )
    credit = credit_service.return_credit(
        actor=finance,
        enrollment=overpaid,
        returned_on=date(2026, 9, 20),
        reason_ar="دفع زائد",
        code="CR-UI-1",
    )
    return refund, credit


def _refunds_page(client: Client) -> str:
    response = client.get(reverse("billing:refunds"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_create", "_may_approve"), REFUND_READERS)
def test_the_refunds_screen_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_create: bool, _may_approve: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "refunds")
    client.force_login(_user(role, f"rf.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:refunds")).status_code == 200


@pytest.mark.parametrize("role", REFUND_NON_READERS)
def test_the_refunds_screen_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "refunds")
    client.force_login(_user(role, f"rf.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:refunds")).status_code == 403


def test_the_refunds_screen_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("billing:refunds")).status_code in (302, 403)


@pytest.mark.parametrize(("role", "may_create", "_may_approve"), REFUND_READERS)
def test_both_forms_are_drawn_only_where_create_is_granted(
    client: Client, seeded_settings: None, role: str, may_create: bool, _may_approve: bool
) -> None:
    """
    The two forms are separate on purpose and both follow the one flag the
    view computes. A reader without it gets neither, and no CSRF token at all.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "refunds")) is may_create
    client.force_login(_user(role, f"rf.create.{role}".lower().replace("_", ".")))

    response = client.get(reverse("billing:refunds"))
    assert response.context["can_create"] is may_create
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert ('name="action" value="request"' in page) is may_create
    assert ('name="action" value="return-credit"' in page) is may_create
    if not may_create:
        assert "<form" not in page
        assert "<button" not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_two_forms_kept_every_field_they_carried(client: Client, seeded_settings: None) -> None:
    """
    Presentation only. The refund form and the credit form stay two forms with
    two actions, and both keep every field name the view built them from.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "rf.fields"))

    response = client.get(reverse("billing:refunds"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for name in response.context["refund_form"].fields:
        assert f'name="{name}"' in page, f"refund_form.{name}"
    for name in response.context["credit_form"].fields:
        assert f'name="{name}"' in page, f"credit_form.{name}"
    assert page.count("<form") == 2
    assert page.count('class="form-acts"') == 2


@pytest.mark.parametrize(("role", "may_execute", "may_approve"), REFUND_READERS)
def test_each_refund_act_is_drawn_only_for_the_role_that_holds_it(
    client: Client, a_refund_and_a_credit: object, role: str, may_execute: bool, may_approve: bool
) -> None:
    """
    §8 splits the three acts deliberately: the manager approves or rejects a
    REQUESTED row, the finance officer executes an APPROVED one, and the audit
    account does neither. Asserted in both directions on both states.
    """
    from apps.billing.services import refund_service
    from apps.people.constants import Action
    from apps.people.models import User
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.APPROVE in allowed_actions(role, "refunds")) is may_approve
    assert (Action.EDIT in allowed_actions(role, "refunds")) is may_execute

    client.force_login(_user(role, f"rf.act.{role}".lower().replace("_", ".")))
    page = _refunds_page(client)
    assert ('name="action" value="approve"' in page) is may_approve
    assert ('name="action" value="reject"' in page) is may_approve
    assert 'name="action" value="execute"' not in page, "nothing is APPROVED yet"
    client.logout()

    # Once the manager has approved it, the execute button appears for exactly
    # the role that may move the money — and for no one else.
    refund, _credit = a_refund_and_a_credit  # type: ignore[misc]
    refund_service.approve_refund(
        actor=User.objects.get(username="rf.fixture.actor"), refund=refund
    )
    client.force_login(_user(role, f"rf.exec.{role}".lower().replace("_", ".")))
    page = _refunds_page(client)
    assert ('name="action" value="execute"' in page) is may_execute
    assert 'name="action" value="approve"' not in page, "an approved row is not approved twice"


def test_the_requester_is_never_offered_the_approval_of_their_own_refund(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    D-18 is a service refusal AND a drawing condition. The finance officer who
    raised the row holds no APPROVE anyway, so this pins the template's own
    half of the rule: the identity test is still on the row.
    """
    source = REFUNDS_TEMPLATE.read_text(encoding="utf-8")

    assert "r.requested_by_id != current_user_id" in source
    from apps.people.models import User

    client.force_login(User.objects.get(username="rf.fixture.finance"))
    page = _refunds_page(client)
    assert 'name="action" value="approve"' not in page


def test_the_rejection_reason_box_has_a_name_a_screen_reader_reads(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    It was a bare box with a placeholder and nothing else. A placeholder is not
    a label — it disappears on the first keystroke and is not an accessible
    name — so the field is named the way the daily closing names its own
    in-row input, and the field name itself is untouched.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "rf.reason"))

    page = _refunds_page(client)

    assert 'name="reason_ar" aria-label="سبب الرفض"' in page
    assert 'placeholder="سبب الرفض…"' in page


def test_the_refunds_screen_prints_only_keys_the_rows_already_carried(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """Both tables render their own projection's values, unchanged."""
    from django.template.defaultfilters import floatformat

    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.rows"))

    response = client.get(reverse("billing:refunds"))
    refund_row = response.context["refunds"][0]
    credit_row = response.context["credit_returns"][0]
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for key in (
        "code",
        "enrollment_code",
        "participant_name",
        "official_letter_ref",
        "president_approval_ref",
        "status_display",
    ):
        assert str(refund_row[key]) in page, f"refund.{key}"
    for key in ("amount", "partner_recovery_amount"):
        assert floatformat(refund_row[key], -3) in page, f"refund.{key}"
    for key in ("code", "enrollment_code", "participant_name", "reason_ar"):
        assert str(credit_row[key]) in page, f"credit.{key}"
    assert floatformat(credit_row["amount"], -3) in page


def test_the_refunds_head_reads_like_every_polished_screen(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and a count per table —
    all off the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.head"))

    page = _refunds_page(client)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    # One count per table: the two movements are counted separately, because
    # counting them together is exactly the conflation the screen exists to
    # prevent.
    assert page.count('class="count"') == 2


def test_the_refunds_table_names_its_ninth_column(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.col"))

    page = _refunds_page(client)

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    # Nine headers on the refunds table and six on the credit table.
    assert len(re.findall(r"<th[\s>]", page)) == 15
    assert page.count('class="tbl-wrap"') == 2


def test_the_two_rules_are_explained_where_each_one_applies(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    One yellow `.note warn` carried both rules above the page. Yellow is the
    colour of a refusal and explaining a rule is not one (polish rules §6.5),
    so each half now sits as a `.hint` under the table it describes — which is
    also where the difference between the two movements is legible.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.rules"))

    page = _refunds_page(client)

    assert "note warn" not in page
    assert page.count('<p class="hint">') == 2
    # Anchored on wording the hints alone carry: the guidance block above the
    # page states the same rule in its own words, so a phrase they share would
    # find the guide and prove nothing about where the hint sits.
    first = "ويُنفَّذ بعد الاعتماد من شخص آخر"
    second = "لا يعكس إيراداً ولا يحتاج موافقة رئيس الجامعة"
    assert first in page
    assert second in page
    # The §5.3 rule sits after the refunds table, and BR-071's after the second.
    assert page.index(first) > page.index('class="tbl"')
    assert page.index(second) > page.index(first)


def test_the_two_refund_empty_states_are_not_the_same_sentence(
    client: Client, seeded_settings: None
) -> None:
    """
    «لا استردادات» and «لا حركات» taught nothing and, worse, read as the same
    absence. Each now says what its own movement is and how a row appears —
    and neither offers an action, because the reader may not hold it.
    """
    from apps.billing.models import CreditReturn, Refund

    assert not Refund.objects.exists() and not CreditReturn.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.empty"))

    page = _refunds_page(client)

    assert page.count('class="empty-title"') == 2
    assert page.count('class="empty-body"') == 2
    assert "BR-034" in page
    assert "BR-071" in page
    assert "empty-act" not in page


def test_the_refunds_screen_renders_on_an_empty_database(
    client: Client, seeded_settings: None
) -> None:
    """Settings and nothing else — the screen still draws for all three readers."""
    for role, _create, _approve in REFUND_READERS:
        client.force_login(_user(role, f"rf.bare.{role}".lower().replace("_", ".")))
        assert client.get(reverse("billing:refunds")).status_code == 200
        client.logout()


def test_the_refunds_screen_still_takes_no_query_parameter(
    client: Client, a_refund_and_a_credit: object
) -> None:
    """
    The services can narrow by enrolment and status; this view has never asked
    them to, and the polish added no filter control that would imply otherwise.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "rf.filter"))

    plain = client.get(reverse("billing:refunds"))
    with_noise = client.get(reverse("billing:refunds"), {"status": "EXECUTED", "enrollment": "X"})

    assert len(plain.context["refunds"]) == len(with_noise.context["refunds"]) == 1
    assert 'class="filterbar"' not in _refunds_page(client)


def test_the_refunds_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen requests, approves, rejects and executes. The help may not imply
    a row is edited or deleted, nor that cash is taken here — and it keeps the
    two movements apart, which is the whole reason they share a page.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["refunds"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "BR-034" in text
    assert "D-18" in text
    assert "BR-071" in text
    for absent in ("حذف", "تعديل الاسترداد", "استيفاء دفعة"):
        assert absent not in text, f"the refunds guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_create", "_may_approve"), REFUND_READERS)
def test_the_refunds_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_create: bool, _may_approve: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"rf.links.{role}".lower().replace("_", ".")))
    page = _refunds_page(client)

    guide = GUIDES["refunds"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_refunds_screen_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = REFUNDS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the refunds screen uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert markup.count('class="tbl-wrap"') == 2
    assert markup.count('class="form-acts"') == 2
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The extra fees register — page polish
# ---------------------------------------------------------------------------
# §3.4/21 «V C E P · V P · V C E P · — · — · V P». Four roles read it and two
# may charge. The screen was a bare <h1>, a blue alert that printed two fee
# amounts as literal text, an empty state saying «لا رسوم إضافية», and a row
# that never showed the one fact BR-040 turns on.
EXTRA_FEES_TEMPLATE = Path("templates/billing/extra_fees.html")

#: role, may charge — read straight off §3.4/21.
EXTRA_FEE_READERS = (
    (Role.CENTER_MANAGER, True),
    (Role.REGISTRATION_OFFICER, False),
    (Role.FINANCE_OFFICER, True),
    (Role.AUDIT_ACCOUNT, False),
)

#: The roles §3.4/21 leaves empty. An empty cell is an explicit deny (BR-080).
EXTRA_FEE_NON_READERS = (Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def three_extra_fees(  # type: ignore[no-untyped-def]
    seeded_settings: None, active_semester: object, participant_data: dict
):
    """
    One fee of each shape: shareable, centre-only, and the one BR-040 gates.

    Charged through the service so ``is_partner_shareable`` is §5.5's answer
    and each amount is the seeded setting's — the two figures the template
    used to print as literal text.
    """
    from datetime import date
    from decimal import Decimal

    from django.core.management import call_command

    from apps.billing.models import ExtraFeeType
    from apps.billing.services import charge_service, extra_fee_service
    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    actor = _user(Role.CENTER_MANAGER, "xf.fixture.actor")
    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-XF-1",
        program=program,
        semester=active_semester,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )
    participant = participant_service.create_participant(actor=actor, data=participant_data)
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    enrollment = Enrollment.objects.create(
        code="EN-XF-1",
        participant=participant,
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=PriceList.objects.get(status=PriceListStatus.APPROVED),
    )
    charge_service.charge_lines_from_quote(
        actor=actor, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
    )
    return [
        extra_fee_service.charge_extra_fee(
            actor=actor,
            enrollment=enrollment,
            fee_type=ExtraFeeType.SUBJECT_REPEAT,
            charged_on=date(2026, 9, 20),
            subject_name="مقدمة في الشبكات",
        ),
        extra_fee_service.charge_extra_fee(
            actor=actor,
            enrollment=enrollment,
            fee_type=ExtraFeeType.CERTIFICATE_REPLACEMENT,
            charged_on=date(2026, 9, 21),
        ),
        extra_fee_service.charge_extra_fee(
            actor=actor,
            enrollment=enrollment,
            fee_type=ExtraFeeType.INTERNATIONAL_EXAM,
            charged_on=date(2026, 9, 22),
            amount=Decimal("120.000"),
            prior_agreement_with_participant=True,
        ),
    ]


def _extra_fees_page(client: Client) -> str:
    response = client.get(reverse("billing:extra-fees"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_create"), EXTRA_FEE_READERS)
def test_the_extra_fees_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_create: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "extra-fees")
    client.force_login(_user(role, f"xf.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:extra-fees")).status_code == 200


@pytest.mark.parametrize("role", EXTRA_FEE_NON_READERS)
def test_the_extra_fees_register_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "extra-fees")
    client.force_login(_user(role, f"xf.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:extra-fees")).status_code == 403


def test_the_extra_fees_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("billing:extra-fees")).status_code in (302, 403)


@pytest.mark.parametrize(("role", "may_create"), EXTRA_FEE_READERS)
def test_the_charge_form_is_drawn_only_where_create_is_granted(
    client: Client, seeded_settings: None, role: str, may_create: bool
) -> None:
    """
    The form follows ``can_create`` exactly as it did, and a reader without it
    gets a page with no form, no button and no CSRF token at all.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "extra-fees")) is may_create
    client.force_login(_user(role, f"xf.create.{role}".lower().replace("_", ".")))

    response = client.get(reverse("billing:extra-fees"))
    assert response.context["can_create"] is may_create
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    if may_create:
        assert page.count("<form") == 1
        assert page.count("<button") == 1
        for name in response.context["form"].fields:
            assert f'name="{name}"' in page, name
        assert 'class="form-acts"' in page
    else:
        assert "<form" not in page
        assert "<button" not in page
        assert "csrfmiddlewaretoken" not in page


def test_a_role_without_create_cannot_charge_an_extra_fee(
    client: Client, seeded_settings: None
) -> None:
    """The page never offered the form; the route still refuses the POST."""
    from apps.billing.models import ExtraFee

    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.post"))

    response = client.post(reverse("billing:extra-fees"), {"fee_type": "SUBJECT_REPEAT"})

    assert response.status_code == 403
    assert not ExtraFee.objects.exists()


def test_the_screen_prints_no_fee_amount_of_its_own(client: Client, seeded_settings: None) -> None:
    """
    «75 ديناراً» and «15 ديناراً» were literal text in the template, while both
    are dated settings the service reads at charge time. A figure frozen in
    markup keeps saying the old number after the setting moves — the BR-020
    defect, on a second screen. On an empty register the page now names no
    amount at all.
    """
    import re
    from decimal import Decimal

    from apps.core.services.settings_service import get_setting

    client.force_login(_user(Role.FINANCE_OFFICER, "xf.amounts"))
    page = _extra_fees_page(client)
    # Attribute VALUES are stripped before searching. The page carries a CSRF
    # token — 64 random alphanumerics — and a bare `"75" not in page` matched
    # it roughly once in sixty renders, which is a test that fails on a coin
    # toss rather than on a regression. What the guarantee is about is the
    # text a reader sees, so that is what is searched.
    visible = re.sub(r'\s(?:value|name|id|for|class|href|action)="[^"]*"', " ", page)

    assert "75" not in visible
    assert "15 " not in visible
    # The settings are still where the service reads them from; the screen
    # simply no longer duplicates them.
    from datetime import date

    assert get_setting("subject_repeat_fee", as_of=date(2026, 9, 20)) == Decimal("75.000")
    assert get_setting("certificate_replacement_fee", as_of=date(2026, 9, 20)) == Decimal("15.000")
    # …and the rule that survives any amount is still taught.
    assert "بدل فاقد الشهادة للمركز وحده" in page
    assert "لا تُحمَّل بلا اتفاق مسبق" in page


def test_the_seeded_amount_reaches_the_screen_from_the_row_it_charged(
    client: Client, three_extra_fees: object
) -> None:
    """
    Dropping the literal figures cost the reader nothing: every amount on the
    page is the one actually charged, printed in its own column.
    """
    from decimal import Decimal

    from django.template.defaultfilters import floatformat

    client.force_login(_user(Role.FINANCE_OFFICER, "xf.row.amount"))

    response = client.get(reverse("billing:extra-fees"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    amounts = {row["amount"] for row in response.context["fees"]}
    assert amounts == {Decimal("75.000"), Decimal("15.000"), Decimal("120.000")}
    for amount in amounts:
        assert floatformat(amount, -3) in page


def test_the_prior_agreement_is_shown_beside_the_only_type_it_governs(
    client: Client, three_extra_fees: object
) -> None:
    """
    BR-040 gates the international exam fee and nothing else, so its evidence
    sits beside that type rather than in a column of its own — the same reading
    the till gives BR-020's breakdown. The value is the row's; nothing here
    re-decides the rule.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "xf.agreed"))

    response = client.get(reverse("billing:extra-fees"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    exam = next(r for r in response.context["fees"] if r["fee_type"] == "INTERNATIONAL_EXAM")
    assert exam["prior_agreement_with_participant"] is True
    assert page.count("باتفاق مسبق") == 1, "the chip is on the exam row and no other"
    assert page.index("باتفاق مسبق") > page.index("امتحان دولي")
    # The other two rows carry the flag as False and get no chip.
    for row in response.context["fees"]:
        if row["fee_type"] != "INTERNATIONAL_EXAM":
            assert row["prior_agreement_with_participant"] is False


def test_the_extra_fees_row_prints_only_keys_it_already_carried(
    client: Client, three_extra_fees: object
) -> None:
    """The eight columns are the projection's own values, unchanged."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.rows"))

    response = client.get(reverse("billing:extra-fees"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for row in response.context["fees"]:
        assert row["enrollment_code"] in page
        assert row["participant_name"] in page
        assert str(row["fee_type_display"]) in page
        assert row["charge_line_description"] in page
    assert "مقدمة في الشبكات" in page


def test_the_extra_fees_head_reads_like_every_polished_screen(
    client: Client, three_extra_fees: object
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and the count — all off
    the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.head"))

    page = _extra_fees_page(client)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page
    assert 'class="card2-head"' in page


def test_the_extra_fees_rule_is_explained_and_no_longer_alerted(
    client: Client, three_extra_fees: object
) -> None:
    """
    §5.5 was a blue `.note info` above the page. Blue is an alert and
    explaining a rule is not one (polish rules §6.5), so it now sits as a
    `.hint` beneath the columns it explains.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.rule"))

    page = _extra_fees_page(client)

    assert "note info" not in page
    assert '<p class="hint">' in page
    anchor = "بدل فاقد الشهادة للمركز وحده"
    assert page.index(anchor) > page.index('class="tbl"'), "the rule left its columns behind"


def test_the_extra_fees_empty_state_says_what_a_fee_is(
    client: Client, seeded_settings: None
) -> None:
    """
    «لا رسوم إضافية» told a reader neither what the screen is for nor how a row
    appears. The body now names the three types and where the fee lands, and
    offers no action: whoever may charge one finds the form below.
    """
    from apps.billing.models import ExtraFee

    assert not ExtraFee.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.empty"))

    page = _extra_fees_page(client)

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "§5.5" in page
    assert "empty-act" not in page


def test_the_extra_fees_register_renders_on_an_empty_database(
    client: Client, seeded_settings: None
) -> None:
    """Settings and nothing else — the screen still draws for all four readers."""
    for role, _create in EXTRA_FEE_READERS:
        client.force_login(_user(role, f"xf.bare.{role}".lower().replace("_", ".")))
        assert client.get(reverse("billing:extra-fees")).status_code == 200
        client.logout()


def test_the_extra_fees_screen_still_takes_no_query_parameter(
    client: Client, three_extra_fees: object
) -> None:
    """
    The service can narrow by enrolment; this view has never asked it to, and
    the polish added no filter control that would imply otherwise.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xf.filter"))

    plain = client.get(reverse("billing:extra-fees"))
    noisy = client.get(reverse("billing:extra-fees"), {"enrollment": "EN-NOT-THERE"})

    assert len(plain.context["fees"]) == len(noisy.context["fees"]) == 3
    assert 'class="filterbar"' not in _extra_fees_page(client)


def test_the_extra_fees_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen charges a fee and lists what was charged. The help may not imply
    a fee is edited, deleted or refunded here, and it says who decides the
    split — which the form's own field makes easy to misread as a free choice.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["extra-fees"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "BR-040" in text
    assert "§5.5" in text
    assert "تقرّرها القاعدة لا مُدخِل الرسم" in text
    for absent in ("حذف", "تعديل الرسم", "استرداد"):
        assert absent not in text, f"the extra-fees guidance offers «{absent}»"
    # The guidance may not print an amount either — same reason the page no
    # longer does.
    for figure in ("75", "15"):
        assert figure not in text, f"the guidance froze the amount «{figure}»"


@pytest.mark.parametrize(("role", "_may_create"), EXTRA_FEE_READERS)
def test_the_extra_fees_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_create: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"xf.links.{role}".lower().replace("_", ".")))
    page = _extra_fees_page(client)

    guide = GUIDES["extra-fees"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_extra_fees_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = EXTRA_FEES_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the extra fees register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert 'class="form-acts"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The expenses register — page polish
# ---------------------------------------------------------------------------
# §3.4/22 «V A P · — · V C E P · — · — · V P». Three roles read it; the finance
# officer records and the centre manager decides, and never the same person for
# one row (D-18). The screen already had a filter bar, counters and a written
# empty state, so the polish is narrower here: a blue alert carrying a
# definition, a counter footer using a class the card does not define, four
# equal-weight numbers with no lead, an unnamed ninth column and a decision
# note with no accessible name.
EXPENSES_TEMPLATE = Path("templates/expenses/expenses.html")

#: role, may record, may decide — read straight off §3.4/22.
EXPENSE_READERS = (
    (Role.CENTER_MANAGER, False, True),
    (Role.FINANCE_OFFICER, True, False),
    (Role.AUDIT_ACCOUNT, False, False),
)

#: The roles §3.4/22 leaves empty. An empty cell is an explicit deny (BR-080).
EXPENSE_NON_READERS = (Role.REGISTRATION_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def three_expenses(seeded_settings: None) -> list[object]:
    """
    Three expenses: one still recorded, one approved, one rejected.

    Recorded and decided through the service so ``counts_toward_net_income``
    and every status is the service's answer — the approved one is the only
    figure ``approved_total`` is entitled to include.
    """
    from datetime import date
    from decimal import Decimal

    from apps.expenses.services import expense_service

    officer = _user(Role.FINANCE_OFFICER, "xp.fixture.officer")
    manager = _user(Role.CENTER_MANAGER, "xp.fixture.manager")
    categories = [code for code, _label in expense_service.category_choices(as_of=date.today())]
    assert categories, "no expense categories are seeded, so this proves nothing"

    def _record(code: str, amount: str, category: str) -> object:
        return expense_service.record(
            actor=officer,
            code=code,
            category=category,
            amount=Decimal(amount),
            incurred_on=date(2026, 9, 20),
            description_ar=f"مصروف {code}",
            reference=f"INV-{code}",
        )

    still_recorded = _record("EX-UI-1", "40.000", categories[0])
    approved = _record("EX-UI-2", "60.000", categories[0])
    rejected = _record("EX-UI-3", "25.000", categories[-1])
    expense_service.approve(actor=manager, expense=approved, note_ar="ضمن الموازنة")
    expense_service.reject(actor=manager, expense=rejected, note_ar="بلا فاتورة")
    return [still_recorded, approved, rejected]


def _expenses_page(client: Client) -> str:
    response = client.get(reverse("expenses:expenses"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_record", "_may_decide"), EXPENSE_READERS)
def test_the_expenses_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_record: bool, _may_decide: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "expenses")
    client.force_login(_user(role, f"xp.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("expenses:expenses")).status_code == 200


@pytest.mark.parametrize("role", EXPENSE_NON_READERS)
def test_the_expenses_register_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "expenses")
    client.force_login(_user(role, f"xp.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("expenses:expenses")).status_code == 403


def test_the_expenses_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("expenses:expenses")).status_code in (302, 403)


@pytest.mark.parametrize(("role", "may_record", "may_decide"), EXPENSE_READERS)
def test_each_expense_act_is_drawn_only_for_the_role_that_holds_it(
    client: Client, three_expenses: list[object], role: str, may_record: bool, may_decide: bool
) -> None:
    """
    §9.7 puts recording and deciding in two different hands, and the screen
    reads that split rather than inventing it. Asserted in both directions.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "expenses")) is may_record
    assert (Action.APPROVE in allowed_actions(role, "expenses")) is may_decide
    client.force_login(_user(role, f"xp.act.{role}".lower().replace("_", ".")))

    response = client.get(reverse("expenses:expenses"))
    assert response.context["can_create"] is may_record
    assert response.context["can_approve"] is may_decide
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert ('name="action" value="record"' in page) is may_record
    assert ('value="approve"' in page) is may_decide
    assert ('value="reject"' in page) is may_decide
    if not (may_record or may_decide):
        assert '<form method="post"' not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_separation_holds_before_the_identity_test_is_ever_needed() -> None:
    """
    D-18 is enforced twice over here, and the outer guard is the matrix itself:
    §3.4/22 gives no role both CREATE and APPROVE, so on this screen the person
    who records an entry can never be the person offered its decision.

    The template's own identity test is kept all the same — it is the branch
    that would catch a matrix change, and the chip that explains the refusal
    instead of leaving a missing button to explain itself. Deleting a guard
    because the layer above it currently makes it unreachable is how the layer
    above it becomes load-bearing by accident.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    for role in (Role.CENTER_MANAGER, Role.FINANCE_OFFICER, Role.AUDIT_ACCOUNT):
        actions = allowed_actions(role, "expenses")
        assert not (Action.CREATE in actions and Action.APPROVE in actions), role

    source = EXPENSES_TEMPLATE.read_text(encoding="utf-8")
    assert "e.created_by_id != current_user_id" in source
    assert "الاعتماد لغير من قيّده" in source
    # …and the service refuses it regardless of what any screen draws.
    service = Path("apps/expenses/services/expense_service.py").read_text(encoding="utf-8")
    assert "لا يعتمد المصروفَ من قيّده (D-18)." in service


def test_the_record_form_keeps_every_field_it_carried(
    client: Client, seeded_settings: None
) -> None:
    """Presentation only: same fields, same names, one record form."""
    client.force_login(_user(Role.FINANCE_OFFICER, "xp.fields"))

    response = client.get(reverse("expenses:expenses"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for name in response.context["form"].fields:
        assert f'name="{name}"' in page, name
    assert 'class="form-acts"' in page


def test_the_decision_note_box_has_a_name_a_screen_reader_reads(
    client: Client, three_expenses: list[object]
) -> None:
    """
    It was a bare box with a placeholder and nothing else. A placeholder is not
    an accessible name, so the field is named the way the daily closing names
    its own in-row input — and the field name itself is untouched.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "xp.note"))

    page = _expenses_page(client)

    assert 'name="note_ar" aria-label="ملاحظة القرار"' in page
    assert 'placeholder="ملاحظة…"' in page


def test_the_counter_footers_use_the_slot_the_card_defines(
    client: Client, three_expenses: list[object]
) -> None:
    """
    The footers were `.muted` — a global colour with no place inside a `.kpi`,
    which defines `.kpi .foot` for exactly this line, with its own size and
    spacing. The words are unchanged.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.foot"))

    page = _expenses_page(client)
    css = CSS_SOURCE.read_text(encoding="utf-8")

    assert ".kpi .foot" in css
    assert 'class="foot"' in page
    assert "قيد معتمَد" in page
    assert "يُخصم من صافي دخل المركز" in page
    # `.muted` survives where it belongs — on the recorded decision note.
    assert 'class="muted"' in page


def test_the_approved_total_is_the_one_number_the_screen_leads_with(
    client: Client, three_expenses: list[object]
) -> None:
    """
    Four equal cards gave a reader no lead. The design system defines exactly
    one highlight per screen, and the number that earns it is the one the
    income report subtracts. The value is still the service's own.
    """
    from decimal import Decimal

    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.lead"))

    response = client.get(reverse("expenses:expenses"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    # Only the approved entry counts, and the highlight carries that figure.
    assert response.context["approved_total"] == Decimal("60.000")
    assert page.count('class="kpi primary"') == 1
    assert ".kpi.primary" in CSS_SOURCE.read_text(encoding="utf-8")


def test_the_expense_row_prints_only_keys_it_already_carried(
    client: Client, three_expenses: list[object]
) -> None:
    """The nine columns are the projection's own values, unchanged."""
    from django.template.defaultfilters import floatformat

    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.rows"))

    response = client.get(reverse("expenses:expenses"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for row in response.context["expenses"]:
        assert row["code"] in page
        assert row["description_ar"] in page
        assert row["reference"] in page
        assert str(row["category_label"]) in page
        assert str(row["status_display"]) in page
        assert floatformat(row["amount"], -3) in page


def test_the_expenses_head_reads_like_every_polished_screen(
    client: Client, three_expenses: list[object]
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and a count over the rows
    drawn — all off the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.head"))

    page = _expenses_page(client)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page


def test_the_expenses_table_names_its_ninth_column(
    client: Client, three_expenses: list[object]
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.col"))

    page = _expenses_page(client)

    assert 'aria-label="القرار"' in page
    assert "sr-only" not in page
    assert len(re.findall(r"<th[\s>]", page)) == 9
    assert 'class="tbl-wrap"' in page


def test_the_definition_is_explained_and_no_longer_alerted(
    client: Client, three_expenses: list[object]
) -> None:
    """
    What an expense is NOT was a blue `.note info` above the page. Blue is an
    alert and a definition is not one (polish rules §6.5), so it now sits as a
    `.hint` beneath the rows it describes.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.rule"))

    page = _expenses_page(client)

    assert "note info" not in page
    assert '<p class="hint">' in page
    anchor = "لا علاقة لها بالتزامات الشريك"
    assert anchor in page
    assert page.index(anchor) > page.index('class="tbl"'), "the definition left its rows behind"


def test_the_expenses_filters_are_the_three_that_were_already_there(
    client: Client, three_expenses: list[object]
) -> None:
    """
    ``from``, ``to`` and ``category`` narrow the register and did before; the
    polish added no control and renamed nothing. The counters follow the same
    window, which is what makes the filter honest.
    """
    from decimal import Decimal

    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.filter"))

    page = _expenses_page(client)
    assert 'name="from"' in page and 'name="to"' in page and 'name="category"' in page
    assert 'name="status"' not in page, "a control appeared for a parameter that had none"

    everything = client.get(reverse("expenses:expenses"))
    assert len(everything.context["expenses"]) == 3

    # A window that excludes every entry empties the rows AND the totals.
    outside = client.get(reverse("expenses:expenses"), {"from": "2027-01-01"})
    assert len(outside.context["expenses"]) == 0
    assert outside.context["approved_total"] == Decimal("0.000")

    # Narrowing by category still narrows.
    category = everything.context["expenses"][0]["category"]
    narrowed = client.get(reverse("expenses:expenses"), {"category": category})
    assert all(r["category"] == category for r in narrowed.context["expenses"])
    assert 0 < len(narrowed.context["expenses"]) <= 3


def test_the_status_parameter_the_view_reads_is_still_read(
    client: Client, three_expenses: list[object]
) -> None:
    """
    The view has always accepted ``?status=``; it has never had a control, and
    the polish did not add one. Pinned so a later slice that adds the control
    knows the parameter it must keep.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.status"))

    approved = client.get(reverse("expenses:expenses"), {"status": "APPROVED"})

    assert [r["status"] for r in approved.context["expenses"]] == ["APPROVED"]
    source = Path("apps/expenses/views.py").read_text(encoding="utf-8")
    assert 'request.GET.get("status", "").strip()' in source


def test_the_expenses_empty_state_survived_the_polish(
    client: Client, seeded_settings: None
) -> None:
    """
    This screen already had a written empty state; the polish kept it word for
    word and still offers no action, because the reader may not hold it.
    """
    from apps.expenses.models import Expense

    assert not Expense.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "xp.empty"))

    page = _expenses_page(client)

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "المصروف يُقيَّد على دورة أو على المركز" in page
    assert "empty-act" not in page


def test_the_expenses_register_renders_on_an_empty_database(
    client: Client, seeded_settings: None
) -> None:
    """Settings and nothing else — the screen still draws for all three readers."""
    for role, _record, _decide in EXPENSE_READERS:
        client.force_login(_user(role, f"xp.bare.{role}".lower().replace("_", ".")))
        assert client.get(reverse("expenses:expenses")).status_code == 200
        client.logout()


def test_the_expenses_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen records an expense and decides one. The help may not imply a row
    is edited or deleted, and it says what an expense is NOT — the confusion
    the screen exists to prevent.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["expenses"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "D-18" in text
    assert "ليس التزام شريك" in text
    assert "إيراد لا تكلفة" in text
    for absent in ("حذف", "تعديل المصروف", "استرداد"):
        assert absent not in text, f"the expenses guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_record", "_may_decide"), EXPENSE_READERS)
def test_the_expenses_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_record: bool, _may_decide: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"xp.links.{role}".lower().replace("_", ".")))
    page = _expenses_page(client)

    guide = GUIDES["expenses"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="kpi-grid"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_expenses_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = EXPENSES_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the expenses register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert 'class="form-acts"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The opening balances screen — page polish
# ---------------------------------------------------------------------------
# §3.7/36أ «V A P · — · V C E P · — · — · V P». BR-094's four hands on one page
# on purpose (D-24): a reader must see who proposed, who reviewed and who
# approved side by side. The screen carried FOUR framed alert blocks — two
# stacked above the page — counter footers using a class the card does not
# define, eight equal-weight numbers, an unnamed tenth column, an empty state
# saying «لا أرصدة افتتاحية», and eleven in-row inputs with no accessible name.
OPENING_BALANCES_TEMPLATE = Path("templates/billing/opening_balances.html")

#: role, may propose/review, may decide — read straight off §3.7/36أ.
OPENING_BALANCE_READERS = (
    (Role.CENTER_MANAGER, False, True),
    (Role.FINANCE_OFFICER, True, False),
    (Role.AUDIT_ACCOUNT, False, False),
)

#: The roles §3.7/36أ leaves empty. An empty cell is an explicit deny (BR-080).
OPENING_BALANCE_NON_READERS = (
    Role.REGISTRATION_OFFICER,
    Role.FINANCE_MANAGER,
    Role.CASHIER,
)

#: Every in-row control the screen draws, and the name each must answer to. A
#: placeholder is not an accessible name, and every one of these had only that.
IN_ROW_CONTROLS = (
    ("enrollment_code", "رمز التسجيل"),
    ("note_ar", "ملاحظة المراجعة"),
    ("note_ar", "ملاحظة القرار"),
    ("enrollment_code", "تسجيل لاحق"),
    ("note_ar", "مسوّغ التسوية"),
    ("payout_code", "رمز الصرف"),
    ("payment_method", "طريقة الصرف"),
    ("external_reference", "رقم سند الصرف"),
    ("payee_name_ar", "اسم المستلم"),
    ("reason_ar", "سبب عكس الصرف"),
    ("reversal_reference", "مرجع التصحيح"),
)


@pytest.fixture
def four_opening_balances(  # type: ignore[no-untyped-def]
    seeded_settings: None, active_semester: object, participant_data: dict
):
    """
    One balance at each of the first four stages, through the real four hands.

    D-24 needs different PEOPLE, not different roles, so the proposer and the
    reviewer are two finance officers. Driven through the service because
    ``is_postable``, ``is_resolvable_credit`` and every status are its answers
    — a hand-built row could sit in a state the workflow never produces.
    """
    from datetime import date
    from decimal import Decimal

    from django.core.management import call_command

    from apps.billing.models import OpeningBalanceDirection
    from apps.billing.services import charge_service
    from apps.billing.services import opening_balance_service as obs
    from apps.catalog.models import PriceList, PriceListStatus, Program
    from apps.catalog.services import pricing_service
    from apps.operations.models import Cohort, Enrollment
    from apps.people.services import participant_service

    proposer = _user(Role.FINANCE_OFFICER, "ob.fixture.propose")
    reviewer = _user(Role.FINANCE_OFFICER, "ob.fixture.review")
    approver = _user(Role.CENTER_MANAGER, "ob.fixture.approve")

    # An old debt has to land on a live enrolment before anyone may approve it
    # — «الذمة التي لا مكان لها لا تُحصَّل» — so the fixture builds one.
    call_command("seed_catalog_demo", "--approve", verbosity=0)
    program = Program.objects.get(code="SC-NET")
    cohort = Cohort.objects.create(
        code="CO-OB-1",
        program=program,
        semester=active_semester,
        name_ar=f"دفعة {program.name_ar}",
        starts_on=date(2026, 9, 20),
        ends_on=date(2026, 12, 20),
        capacity=25,
    )
    participant = participant_service.create_participant(actor=approver, data=participant_data)
    quote = pricing_service.resolve_price(
        program=program, participant_category="UNIVERSITY", as_of=date(2026, 9, 20)
    )
    enrollment = Enrollment.objects.create(
        code="EN-OB-1",
        participant=participant,
        cohort=cohort,
        enrolled_on=date(2026, 9, 20),
        price_list=PriceList.objects.get(status=PriceListStatus.APPROVED),
    )
    charge_service.charge_lines_from_quote(
        actor=approver, enrollment=enrollment, quote=quote, charged_on=date(2026, 9, 20)
    )

    def _propose(code: str, direction: str = OpeningBalanceDirection.RECEIVABLE) -> object:
        return obs.propose_manually(
            actor=proposer,
            code=code,
            direction=direction,
            amount=Decimal("120.000"),
            as_of=date(2026, 1, 15),
            description_ar=f"ذمة قديمة {code}",
            legacy_number="202251024",
        )

    draft = _propose("OB-UI-1")
    reviewed = _propose("OB-UI-2")
    approved = _propose("OB-UI-3")
    credit = _propose("OB-UI-4", direction=OpeningBalanceDirection.CREDIT)

    obs.review(actor=reviewer, balance=reviewed, enrollment=enrollment, note_ar="قوبل بالصف")
    obs.review(actor=reviewer, balance=approved, enrollment=enrollment, note_ar="قوبل بالصف")
    obs.approve(actor=approver, balance=approved, note_ar="معتمد")
    obs.review(actor=reviewer, balance=credit, enrollment=enrollment, note_ar="رصيد دائن قديم")
    obs.approve(actor=approver, balance=credit, note_ar="معتمد")
    for balance in (draft, reviewed, approved, credit):
        balance.refresh_from_db()
    return {"draft": draft, "reviewed": reviewed, "approved": approved, "credit": credit}


def _opening_balances_page(client: Client) -> str:
    response = client.get(reverse("billing:opening-balances"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_review", "_may_decide"), OPENING_BALANCE_READERS)
def test_the_opening_balances_open_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_review: bool, _may_decide: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "opening-balances")
    client.force_login(_user(role, f"ob.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:opening-balances")).status_code == 200


@pytest.mark.parametrize("role", OPENING_BALANCE_NON_READERS)
def test_the_opening_balances_still_refuse_the_roles_they_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "opening-balances")
    client.force_login(_user(role, f"ob.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("billing:opening-balances")).status_code == 403


def test_the_opening_balances_refuse_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("billing:opening-balances")).status_code in (302, 403)


@pytest.mark.parametrize(("role", "may_review", "may_decide"), OPENING_BALANCE_READERS)
def test_each_hand_is_drawn_only_for_the_role_that_holds_it(
    client: Client,
    four_opening_balances: dict[str, object],
    role: str,
    may_review: bool,
    may_decide: bool,
) -> None:
    """
    BR-094's hands are three permissions, and every one of the seven in-row
    forms follows the flag it always followed. Asserted in both directions,
    against rows that are actually in each triggering state.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "opening-balances")) is may_review
    assert (Action.EDIT in allowed_actions(role, "opening-balances")) is may_review
    assert (Action.APPROVE in allowed_actions(role, "opening-balances")) is may_decide

    client.force_login(_user(role, f"ob.hand.{role}".lower().replace("_", ".")))
    response = client.get(reverse("billing:opening-balances"))
    assert response.context["can_propose"] is may_review
    assert response.context["can_review"] is may_review
    assert response.context["can_decide"] is may_decide
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    # EDIT draws the review form on the DRAFT row; APPROVE draws the decision
    # form on the REVIEWED one and the post button on the APPROVED one.
    assert ('name="action" value="review"' in page) is may_review
    assert ('name="action" value="propose"' in page) is may_review
    assert ('value="approve"' in page) is may_decide
    assert ('name="action" value="post"' in page) is may_decide
    assert ('value="carry-forward"' in page) is may_decide
    if not (may_review or may_decide):
        assert '<form method="post"' not in page
        assert "csrfmiddlewaretoken" not in page


def test_the_reader_who_holds_nothing_is_offered_nothing(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """The audit account reads four rows and is offered no act on any of them."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.readonly"))

    page = _opening_balances_page(client)

    assert "OB-UI-1" in page and "OB-UI-4" in page
    for action in ("review", "approve", "reject", "post", "carry-forward", "propose"):
        assert f'value="{action}"' not in page, action


def test_the_propose_form_keeps_every_field_it_carried(
    client: Client, seeded_settings: None
) -> None:
    """Presentation only: same fields, same names, one propose form."""
    client.force_login(_user(Role.FINANCE_OFFICER, "ob.fields"))

    response = client.get(reverse("billing:opening-balances"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for name in response.context["form"].fields:
        assert f'name="{name}"' in page, name
    assert 'class="form-acts"' in page


@pytest.mark.parametrize(("field", "label"), IN_ROW_CONTROLS)
def test_every_in_row_control_has_a_name_a_screen_reader_reads(
    client: Client, four_opening_balances: dict[str, object], field: str, label: str
) -> None:
    """
    Eleven boxes carried a placeholder and nothing else. A placeholder is not
    an accessible name — it vanishes on the first keystroke — so each is named
    the way the daily closing names its own in-row input. No field was renamed
    and none became required that was not.
    """
    source = OPENING_BALANCES_TEMPLATE.read_text(encoding="utf-8")

    # Matched in the template rather than in a rendered page: drawing all seven
    # in-row forms at once needs seven different row states AND two different
    # roles, and until it is rendered the label is a `{% translate %}` tag.
    expected = 'name="' + field + '" aria-label="{% translate \'' + label + "' %}\""
    assert expected in source


def test_the_polish_made_no_control_required_that_was_not(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    ``required`` is submit behaviour, not presentation. The five in-row fields
    the screen already marked required are still exactly those five.
    """
    import re

    source = OPENING_BALANCES_TEMPLATE.read_text(encoding="utf-8")

    required = {
        m.group(1)
        for m in re.finditer(r'<(?:input|select)\s+name="([^"]+)"[^>]*\srequired', source)
    }
    assert required == {
        "note_ar",
        "payout_code",
        "payment_method",
        "external_reference",
        "payee_name_ar",
        "reason_ar",
    }
    # The two optional in-row fields gained a name and stayed optional: an
    # `aria-label` tells a screen reader what the box is, and `required` would
    # tell the browser to refuse a submission the service accepts today.
    for optional in ("reversal_reference", "enrollment_code"):
        assert optional not in required, optional
        assert f'name="{optional}" aria-label=' in source, optional


def test_the_opening_balance_footers_use_the_slot_the_card_defines(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    The eight footers were `.muted` — a global colour with no place inside a
    `.kpi`, which defines `.kpi .foot` for exactly this line. Words unchanged.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.foot"))

    page = _opening_balances_page(client)

    assert page.count('class="foot"') == 8
    assert "هذه وحدها موجودة في الحسابات" in page
    assert "السجل الأصلي محفوظ" in page


def test_the_posted_total_is_the_one_number_the_screen_leads_with(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    Eight equal cards gave a reader no lead on a screen whose whole point is
    that only one of the eight is in the accounts — which is what that card's
    own footer says. The value is still the service's own.
    """
    from decimal import Decimal

    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.lead"))

    response = client.get(reverse("billing:opening-balances"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert page.count('class="kpi primary"') == 1
    lead = page.index('class="kpi primary"')
    assert page.index("هذه وحدها موجودة في الحسابات") > lead
    # Nothing has been posted, so the highlighted figure is honestly zero.
    assert response.context["totals"]["posted"] == Decimal("0.000")


def test_the_opening_balances_head_reads_like_every_polished_screen(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and a count over the rows
    drawn — all off the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.head"))

    page = _opening_balances_page(client)

    assert 'class="eyebrow"' in page
    assert "الشؤون المالية" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page


def test_the_opening_balances_table_names_its_tenth_column(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.col"))

    page = _opening_balances_page(client)

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    # Ten headers on the register; the waiting-payout card is absent here.
    assert len(re.findall(r"<th[\s>]", page)) == 10
    assert 'class="tbl-wrap"' in page


def test_the_four_rules_are_explained_and_no_longer_alerted(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    Four framed `.note` blocks — two stacked yellow ones above the page — said
    what the screen does. Yellow is the colour of a refusal and blue is an
    alert; explaining a rule is neither (polish rules §6.5). Two framed blocks
    above the register also pushed the table itself below the fold.

    The words are unchanged; only the frame and the position are.
    """
    client.force_login(_user(Role.FINANCE_OFFICER, "ob.rules"))

    page = _opening_balances_page(client)

    for alert in ("note warn", "note info", "note danger"):
        assert alert not in page, alert
    assert 'class="hint"' in page
    # BR-094 and the reversal rule now sit under the rows they describe.
    for anchor in ("المعبر الوحيد من الأرشيف", "الصرف يُصحَّح ولا يُمحى"):
        assert anchor in page
        assert page.index(anchor) > page.index('class="tbl"'), anchor
    # The proposer's warning stays above the form it is about.
    assert 'class="hint boxed"' in page
    assert "اكتب رقماً تستطيع الدفاع عنه" in page


def test_the_opening_balances_empty_state_says_what_the_screen_is_for(
    client: Client, seeded_settings: None
) -> None:
    """
    «لا أرصدة افتتاحية» told a reader neither what the screen does nor how a
    row appears. The body now names the four hands and the one that moves the
    ledger, and offers no action.
    """
    from apps.billing.models import OpeningBalance

    assert not OpeningBalance.objects.exists()
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.empty"))

    page = _opening_balances_page(client)

    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "BR-094" in page
    assert "empty-act" not in page


def test_the_opening_balances_render_on_an_empty_database(
    client: Client, seeded_settings: None
) -> None:
    """Settings and nothing else — the screen still draws for all three readers."""
    for role, _review, _decide in OPENING_BALANCE_READERS:
        client.force_login(_user(role, f"ob.bare.{role}".lower().replace("_", ".")))
        assert client.get(reverse("billing:opening-balances")).status_code == 200
        client.logout()


def test_the_opening_balance_filters_are_the_two_that_were_already_there(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """
    ``status`` and ``direction`` narrow the register and did before; the polish
    added no control and renamed nothing.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.filter"))

    page = _opening_balances_page(client)
    assert 'name="status"' in page and 'name="direction"' in page

    everything = client.get(reverse("billing:opening-balances"))
    assert len(everything.context["balances"]) == 4

    drafts = client.get(reverse("billing:opening-balances"), {"status": "DRAFT"})
    assert [b["code"] for b in drafts.context["balances"]] == ["OB-UI-1"]

    credits = client.get(reverse("billing:opening-balances"), {"direction": "CREDIT"})
    assert [b["code"] for b in credits.context["balances"]] == ["OB-UI-4"]


def test_the_opening_balance_row_prints_only_keys_it_already_carried(
    client: Client, four_opening_balances: dict[str, object]
) -> None:
    """The ten columns are the projection's own values, unchanged."""
    from django.template.defaultfilters import floatformat

    client.force_login(_user(Role.AUDIT_ACCOUNT, "ob.rows"))

    response = client.get(reverse("billing:opening-balances"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for row in response.context["balances"]:
        assert row["code"] in page
        assert row["description_ar"] in page
        assert str(row["direction_display"]) in page
        assert str(row["status_display"]) in page
        assert floatformat(row["amount"], -3) in page
        assert row["legacy_number"] in page
    # The four hands are still printed side by side — the reason D-24 wanted
    # one screen rather than four.
    for hand in ("اقترح", "راجع", "اعتمد", "رحّل"):
        assert hand in page


def test_the_opening_balances_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen proposes, reviews, decides, posts, pays and reverses. The help
    may not imply a row is edited or deleted, and it names the mistake the
    screen invites: reading an approval as a posting.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["opening-balances"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "BR-094" in text
    assert "لا تُحرّك في الدفتر شيئاً" in text
    assert "لا يجمع شخص واحد دورين" in text
    for absent in ("حذف", "تعديل الرصيد", "سند قبض"):
        assert absent not in text, f"the opening-balances guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_review", "_may_decide"), OPENING_BALANCE_READERS)
def test_the_opening_balances_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_review: bool, _may_decide: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"ob.links.{role}".lower().replace("_", ".")))
    page = _opening_balances_page(client)

    guide = GUIDES["opening-balances"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="kpi-grid"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_opening_balances_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = OPENING_BALANCES_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the opening balances screen uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert markup.count('class="tbl-wrap"') == 2
    assert 'class="form-acts"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The partners register — page polish (Wave 1)
# ---------------------------------------------------------------------------
# §3.5/23 «V C E P · — · V P · — · — · V P». Three roles read it and only the
# centre manager records a partner. The screen already carried guided help, a
# search bar and a written empty state, so the polish is narrow: no title
# block, a bare <h1>, a blue alert wedged between the teaching and its search
# box, and an unnamed seventh column.
PARTNERS_TEMPLATE = Path("templates/partners/partners.html")

#: role, may record a partner — read straight off §3.5/23.
PARTNER_READERS = (
    (Role.CENTER_MANAGER, True),
    (Role.FINANCE_OFFICER, False),
    (Role.AUDIT_ACCOUNT, False),
)

#: The roles §3.5/23 leaves empty. An empty cell is an explicit deny (BR-080).
PARTNER_NON_READERS = (Role.REGISTRATION_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def three_partners(seeded_settings: None) -> list[object]:
    """
    Three partners through the creating service, one of them former.

    Built by ``create_partner`` rather than the ORM so ``agreement_count`` and
    both display labels are the service's own answers.
    """
    from apps.partners.models import PartnerStatus, PartnerType
    from apps.partners.services import partner_service

    manager = _user(Role.CENTER_MANAGER, "pt.fixture.manager")
    rows = [
        {
            "code": "PN-UI-1",
            "name_ar": "شركة تناغم للتدريب",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.ACTIVE,
            "registry_number": "12345",
        },
        {
            "code": "PN-UI-2",
            "name_ar": "مؤسسة صرح",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.ACTIVE,
        },
        {
            "code": "PN-UI-3",
            "name_ar": "مدرب مستقل سابق",
            "partner_type": PartnerType.FREELANCE_TRAINER,
            "status": PartnerStatus.FORMER,
        },
    ]
    return [partner_service.create_partner(actor=manager, data=data) for data in rows]


def _partners_page(client: Client) -> str:
    response = client.get(reverse("partners:partners"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_create"), PARTNER_READERS)
def test_the_partners_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _may_create: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "partners")
    client.force_login(_user(role, f"pt.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:partners")).status_code == 200


@pytest.mark.parametrize("role", PARTNER_NON_READERS)
def test_the_partners_register_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "partners")
    client.force_login(_user(role, f"pt.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:partners")).status_code == 403


def test_the_partners_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("partners:partners")).status_code in (302, 403)


def test_the_partners_register_stayed_read_only(
    client: Client, three_partners: list[object]
) -> None:
    """
    The screen takes GET alone. It draws two links and no form at all, so the
    polish may not have introduced a POST target or a CSRF token.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pt.readonly"))

    page = _partners_page(client)

    assert 'method="post"' not in page
    assert "csrfmiddlewaretoken" not in page
    assert "<button" in page, "the search submit is still a button"
    assert page.count('name="action"') == 0


@pytest.mark.parametrize(("role", "may_create"), PARTNER_READERS)
def test_the_new_partner_link_is_offered_only_where_create_is_granted(
    client: Client, three_partners: list[object], role: str, may_create: bool
) -> None:
    """
    A link a reader may not follow ends in a refusal and a BR-085 row, so the
    gate is asserted in both directions — and the route still refuses whoever
    the page never offered it to.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "partners")) is may_create
    client.force_login(_user(role, f"pt.new.{role}".lower().replace("_", ".")))

    response = client.get(reverse("partners:partners"))
    assert response.context["can_create"] is may_create
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    href = reverse("partners:partner-new")
    assert (f'href="{href}"' in page) is may_create
    if not may_create:
        assert client.get(href).status_code == 403


def test_every_row_links_to_a_detail_page_that_opens(
    client: Client, three_partners: list[object]
) -> None:
    """The «عرض» link exists for every row and reaches a real page."""
    client.force_login(_user(Role.FINANCE_OFFICER, "pt.detail"))

    response = client.get(reverse("partners:partners"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for row in response.context["partners"]:
        href = reverse("partners:partner-detail", args=[row["code"]])
        assert f'href="{href}"' in page, row["code"]
        assert client.get(href).status_code == 200, row["code"]


def test_the_partner_row_prints_only_keys_it_already_carried(
    client: Client, three_partners: list[object]
) -> None:
    """
    The seven columns are the projection's own values. In particular the
    agreement count is the service's number, not a length computed in markup.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.rows"))

    response = client.get(reverse("partners:partners"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for row in response.context["partners"]:
        assert row["code"] in page
        assert row["name_ar"] in page
        assert str(row["partner_type_display"]) in page
        assert str(row["status_display"]) in page
        assert row["agreement_count"] == 0, "no agreements were created for these partners"
    # A partner with no registry number says so rather than leaving a blank.
    assert "—" in page


def test_the_partners_register_prints_no_commercial_term_from_an_agreement(
    client: Client, three_partners: list[object]
) -> None:
    """
    A partner record is a name, a type and its registry details. The share,
    the exclusions and the settlement cycle live on the AGREEMENT, and a
    register that printed them here would invite a reader to take a figure
    from the wrong document.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.terms"))

    page = _partners_page(client)

    for term in ("النسبة", "الاستثناءات", "دورة المخالصة", "حصة الشريك", "%"):
        assert term not in page, f"the partners register prints «{term}»"


def test_the_partners_head_reads_like_every_polished_screen(
    client: Client, three_partners: list[object]
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and a count over the rows
    drawn — all off the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.head"))

    page = _partners_page(client)

    assert 'class="eyebrow"' in page
    assert "الشركاء والمخالصات" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page
    assert 'class="card2-head"' in page


def test_the_partner_count_describes_the_rows_on_screen(
    client: Client, three_partners: list[object]
) -> None:
    """The chip counts what was drawn, so it follows the search."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.count"))

    everything = client.get(reverse("partners:partners"))
    assert len(everything.context["partners"]) == 3
    assert "3 شركاء" in everything.content.decode("utf-8")

    narrowed = client.get(reverse("partners:partners"), {"q": "تناغم"})
    assert len(narrowed.context["partners"]) == 1
    assert "شريك واحد" in narrowed.content.decode("utf-8")


def test_the_partners_table_names_its_seventh_column(
    client: Client, three_partners: list[object]
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.col"))

    page = _partners_page(client)

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    assert len(re.findall(r"<th[\s>]", page)) == 7
    assert 'class="tbl-wrap"' in page


def test_the_paper_rule_is_explained_and_no_longer_alerted(
    client: Client, three_partners: list[object]
) -> None:
    """
    «الاتفاقيات تُوقَّع على الورق» was a blue `.note info` sitting between the
    guided-help block and the search box — a third framed block separating the
    teaching from its own tool. Blue is an alert and explaining a rule is not
    one (polish rules §6.5), so it now sits as a `.hint` under the rows.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.rule"))

    page = _partners_page(client)

    assert "note info" not in page
    assert '<p class="hint">' in page
    # Anchored on wording the hint alone carries: the guidance block above the
    # page must not repeat a sentence the screen already prints, and a shared
    # phrase would find the guide and prove nothing about where the hint sits.
    anchor = "الاتفاقيات تُوقَّع على الورق"
    assert anchor in page
    assert page.count(anchor) == 1, "the paper rule is printed twice on one page"
    assert page.index(anchor) > page.index('class="tbl"'), "the rule left its rows behind"
    # …and the teaching now sits directly above the search box it belongs to.
    assert page.index('class="filterbar"') > page.index("من يستخدمها")


def test_the_partners_search_is_the_one_that_was_already_there(
    client: Client, three_partners: list[object]
) -> None:
    """``?q=`` narrows by name or code and did before; nothing was renamed."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.search"))

    page = _partners_page(client)
    assert 'name="q"' in page

    by_name = client.get(reverse("partners:partners"), {"q": "صرح"})
    assert [p["code"] for p in by_name.context["partners"]] == ["PN-UI-2"]

    by_code = client.get(reverse("partners:partners"), {"q": "PN-UI-3"})
    assert [p["code"] for p in by_code.context["partners"]] == ["PN-UI-3"]

    # The box keeps what was typed, so the reader can see what narrowed it.
    assert 'value="صرح"' in by_name.content.decode("utf-8")


def test_a_search_that_matches_nothing_still_draws_the_written_empty_state(
    client: Client, three_partners: list[object]
) -> None:
    """The register is populated; this empty is the search's, and it still teaches."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pt.noresult"))

    response = client.get(reverse("partners:partners"), {"q": "لا-يطابق-شيئاً"})
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert len(response.context["partners"]) == 0
    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    # The audit account may not record a partner, so the empty offers no act.
    assert "empty-act" not in page


def test_the_partner_empty_state_offers_the_form_only_where_it_is_allowed(
    client: Client, seeded_settings: None
) -> None:
    """An empty register on a fresh install, and its action follows CREATE."""
    from apps.partners.models import Partner

    assert not Partner.objects.exists()
    for role, may_create in PARTNER_READERS:
        client.force_login(_user(role, f"pt.empty.{role}".lower().replace("_", ".")))
        page = _partners_page(client)
        assert 'class="empty-title"' in page
        assert ("empty-act" in page) is may_create, role
        client.logout()


def test_the_partners_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The screen lists partners and links to the form. The help may not imply a
    partner is edited or deleted here, and it says where the commercial terms
    actually live — the reason a thin record is not a poor one.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["partners"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "تلك كلّها على الاتفاقية" in text
    # The screen prints the paper rule itself; the guide may not repeat it.
    assert "لا يؤلّفها ولا يعدّلها" not in text
    for absent in ("حذف", "تعديل الشريك", "مطالبة", "مخالصة نقدية"):
        assert absent not in text, f"the partners guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_create"), PARTNER_READERS)
def test_the_partners_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _may_create: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"pt.links.{role}".lower().replace("_", ".")))
    page = _partners_page(client)

    guide = GUIDES["partners"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="filterbar"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_partners_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PARTNERS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the partners register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The partner card — page polish (Wave 1)
# ---------------------------------------------------------------------------
# Reached through §3.5/23, and it reads §3.5/24 too: ``get_partner`` calls
# ``list_agreements``, so a reader needs VIEW on both. The three roles that
# hold PARTNERS hold AGREEMENTS as well, which is why the page opens at all.
# It was a bare <h1> with no section, no way back, an unnamed sixth column, an
# empty state saying «لا اتفاقيات», and a status printed as plain text on a
# screen whose own register draws it as a chip.
PARTNER_DETAIL_TEMPLATE = Path("templates/partners/partner_detail.html")


@pytest.fixture
def a_partner_with_agreements(seeded_settings: None) -> object:
    """
    One partner holding a live agreement and one whose term has run out.

    The second exists for the flag: ``is_expired`` is computed in the service
    (Sprint 8F-1) precisely so a screen can say a contract has ended without
    anybody comparing dates by eye, and it was going unused.
    """
    from datetime import date
    from decimal import Decimal

    from apps.partners.models import (
        Agreement,
        AgreementStatus,
        CalculationModel,
        PartnerStatus,
        PartnerType,
    )
    from apps.partners.services import partner_service

    manager = _user(Role.CENTER_MANAGER, "pd.fixture.manager")
    partner = partner_service.create_partner(
        actor=manager,
        data={
            "code": "PN-PD-1",
            "name_ar": "شركة تناغم للتدريب",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.ACTIVE,
            "registry_number": "12345",
            "registry_date": date(2020, 3, 1),
            "contact_name": "أبو محمد",
            "phone": "0791234567",
            "email": "tanaghom@example.com",
        },
    )
    Agreement.objects.create(
        agreement_number="2026/PD-1",
        partner=partner,
        title_ar="اتفاقية سارية",
        signed_on=date(2026, 8, 1),
        valid_from=date(2026, 9, 1),
        valid_to=date(2099, 8, 31),
        calculation_model=CalculationModel.PERCENT,
        percent_rate=Decimal("50.0000"),
        status=AgreementStatus.ACTIVE,
    )
    Agreement.objects.create(
        agreement_number="2020/PD-0",
        partner=partner,
        title_ar="اتفاقية انقضت",
        signed_on=date(2019, 8, 1),
        valid_from=date(2019, 9, 1),
        valid_to=date(2020, 8, 31),
        calculation_model=CalculationModel.PERCENT,
        percent_rate=Decimal("40.0000"),
        status=AgreementStatus.ACTIVE,
    )
    return partner


def _partner_card(client: Client, code: str = "PN-PD-1") -> str:
    response = client.get(reverse("partners:partner-detail", args=[code]))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_may_create"), PARTNER_READERS)
def test_the_partner_card_opens_exactly_where_the_matrix_says(
    client: Client, a_partner_with_agreements: object, role: str, _may_create: bool
) -> None:
    """
    The card needs VIEW on §3.5/23 AND on §3.5/24, because it lists agreements.
    All three partner readers hold both — asserted, not assumed.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "partners")
    assert Action.VIEW in allowed_actions(role, "agreements")
    client.force_login(_user(role, f"pd.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:partner-detail", args=["PN-PD-1"])).status_code == 200


@pytest.mark.parametrize("role", PARTNER_NON_READERS)
def test_the_partner_card_still_refuses_the_roles_it_always_did(
    client: Client, a_partner_with_agreements: object, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    client.force_login(_user(role, f"pd.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:partner-detail", args=["PN-PD-1"])).status_code == 403


def test_the_partner_card_refuses_an_anonymous_visitor(
    client: Client, a_partner_with_agreements: object
) -> None:
    response = client.get(reverse("partners:partner-detail", args=["PN-PD-1"]))
    assert response.status_code in (302, 403)


def test_an_unknown_partner_is_still_a_404(
    client: Client, a_partner_with_agreements: object
) -> None:
    """The polish did not turn a missing record into an empty card."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.missing"))
    assert client.get(reverse("partners:partner-detail", args=["PN-NOPE"])).status_code == 404


def test_the_partner_card_stayed_read_only(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    No editing service exists behind this page and the view has no POST branch,
    so the polish may not have drawn an edit button — a button with no route is
    worse than no button.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pd.readonly"))

    page = _partner_card(client)

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    assert "قراءة فقط" in page, "the read-only chip states what the page is"


def test_the_partner_card_offers_the_way_back_to_its_own_register(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    It had no way back at all. The link is safe for every reader who can be on
    this page: they passed the same screen's gate to reach it.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.back"))

    page = _partner_card(client)

    href = reverse("partners:partners")
    assert f'href="{href}"' in page
    assert client.get(href).status_code == 200


def test_the_partner_card_prints_only_keys_the_projection_carried(
    client: Client, a_partner_with_agreements: object
) -> None:
    """Every value on the card is ``get_partner``'s own; nothing is derived here."""
    client.force_login(_user(Role.FINANCE_OFFICER, "pd.keys"))

    response = client.get(reverse("partners:partner-detail", args=["PN-PD-1"]))
    partner = response.context["partner"]
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for key in ("code", "name_ar", "registry_number", "contact_name", "phone", "email"):
        assert str(partner[key]) in page, key
    assert str(partner["partner_type_display"]) in page
    assert str(partner["status_display"]) in page


def test_the_partner_card_shows_no_agreement_term_of_its_own(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    The rate, the exclusions and the settlement cycle belong to the agreement
    and have their own page. A card that printed «50%» here would invite a
    reader to take a commercial figure off the wrong document.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.terms"))

    page = _partner_card(client)
    # The prose above and below the table NAMES these terms in order to send
    # the reader to the agreement, so the guarantee is about the table itself:
    # no row may carry a commercial value.
    table = page[page.index('class="tbl"') : page.index("</table>")]

    for term in ("50.0000", "40.0000", "%", "الاستثناءات", "دورة المخالصة", "حصة الشريك"):
        assert term not in table, f"the agreements table prints «{term}»"
    # …and the card says where they are, rather than leaving it to be guessed.
    assert "لا ينوب عنها" in page


def test_the_expired_agreement_is_named_without_overwriting_its_status(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    «سارية» is a recorded status; running out of term is a fact about the
    calendar. The service computes ``is_expired`` for exactly this, and the two
    are shown side by side — replacing one with the other would either hide a
    lapsed contract or contradict the record.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.expired"))

    response = client.get(reverse("partners:partner-detail", args=["PN-PD-1"]))
    rows = {a["agreement_number"]: a for a in response.context["partner"]["agreements"]}
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert rows["2020/PD-0"]["is_expired"] is True
    assert rows["2026/PD-1"]["is_expired"] is False
    # One chip, on the one row that earned it — and the stored status survives
    # on BOTH rows. Counted as chip markup: the word «سارية» also appears in a
    # fixture's title, and a substring count would be counting the wrong thing.
    table = page[page.index('class="tbl"') : page.index("</table>")]
    assert table.count("انقضت مدّتها") == 1
    assert rows["2020/PD-0"]["status"] == rows["2026/PD-1"]["status"] == "ACTIVE"
    # Scoped to the table: the partner's OWN status chip sits in the card head
    # above it, and a page-wide count would be counting that too.
    assert table.count('class="chip ok dot"') == 2, "a row lost its recorded status"
    # The template reads the flag; it does not compare dates itself.
    source = PARTNER_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    assert "a.is_expired" in source
    assert "now" not in source, "the card is comparing dates in the template"


def test_every_agreement_row_links_to_a_page_its_reader_may_open(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    The «عرض» link is guarded by AGREEMENTS VIEW, which every reader who got
    onto this card already holds — so the link opens rather than refusing.
    """
    for role, _may_create in PARTNER_READERS:
        client.force_login(_user(role, f"pd.link.{role}".lower().replace("_", ".")))
        response = client.get(reverse("partners:partner-detail", args=["PN-PD-1"]))
        page = response.content.decode("utf-8").split("</nav>", 1)[-1]
        for row in response.context["partner"]["agreements"]:
            href = reverse("partners:agreement-detail", args=[row["agreement_number"]])
            assert f'href="{href}"' in page, f"{role} · {row['agreement_number']}"
            assert client.get(href).status_code == 200, f"{role} · {href}"
        client.logout()


def test_the_partner_card_head_reads_like_every_polished_detail_page(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    A bare ``<h1>`` gained the section, the identity line and a count over the
    agreements listed — all off the projection and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.head"))

    page = _partner_card(client)

    assert 'class="eyebrow"' in page
    assert "الشركاء والمخالصات" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page
    assert "2 اتفاقيات" in page
    assert 'class="dl"' in page


def test_the_partner_card_names_its_sixth_column(
    client: Client, a_partner_with_agreements: object
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.col"))

    page = _partner_card(client)

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    assert len(re.findall(r"<th[\s>]", page)) == 6
    assert 'class="tbl-wrap"' in page


def test_a_partner_with_no_agreement_gets_a_written_empty_state(
    client: Client, three_partners: list[object]
) -> None:
    """
    «لا اتفاقيات» said nothing about what is missing or why it matters. The
    body now names what an agreement carries, and offers no action: recording
    one is the agreement editor's job, on its own screen.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.empty"))

    response = client.get(reverse("partners:partner-detail", args=["PN-UI-2"]))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.context["partner"]["agreements"] == []
    assert 'class="empty-title"' in page
    assert 'class="empty-body"' in page
    assert "دورة المخالصة" in page
    assert "empty-act" not in page


def test_a_partner_with_no_registry_number_says_so(
    client: Client, three_partners: list[object]
) -> None:
    """An unregistered partner is a real case, not a blank cell."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "pd.noreg"))

    page = _partner_card(client, code="PN-UI-2")

    assert "غير مسجَّل" in page


def test_the_partner_card_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The page reads. The help may not imply the partner is edited or deleted
    here, and it names the confusion the table invites: a recorded status is
    not a live term.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["partner-detail"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert guide.status is not None, "a read-only screen says so"
    assert "لا يصلح لمطالبة جديدة" in text
    for absent in ("حذف", "تعديل الشريك", "إنشاء اتفاقية"):
        assert absent not in text, f"the partner-card guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_may_create"), PARTNER_READERS)
def test_the_partner_card_guidance_offers_only_steps_the_reader_may_open(
    client: Client, a_partner_with_agreements: object, role: str, _may_create: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"pd.links.{role}".lower().replace("_", ".")))
    page = _partner_card(client)

    guide = GUIDES["partner-detail"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_partner_card_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PARTNER_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the partner card uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert 'class="dl"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a fact is still being alerted in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The new-partner form — page polish (Wave 1)
# ---------------------------------------------------------------------------
# §3.5/23 gives CREATE to the centre manager alone, and the view guards it on
# GET as well as POST: there is no PARTNER_NEW matrix row, so the officer and
# the auditor are refused the page rather than shown a form they could not
# submit. The screen was already the closest to polished in the wave — it had
# a title block, a boxed hint and `.form-acts` — and was missing the section,
# the sentence, the way back and any teaching.
PARTNER_NEW_TEMPLATE = Path("templates/partners/partner_new.html")


def _partner_new_page(client: Client) -> str:
    response = client.get(reverse("partners:partner-new"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "may_create"), PARTNER_READERS)
def test_the_new_partner_form_opens_for_create_and_refuses_mere_readers(
    client: Client, seeded_settings: None, role: str, may_create: bool
) -> None:
    """
    Reading the partners register is not permission to open its form. The
    officer and the auditor hold VIEW and are refused here — in both
    directions, and on GET, which is where the view puts the guard.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.CREATE in allowed_actions(role, "partners")) is may_create
    client.force_login(_user(role, f"pn.open.{role}".lower().replace("_", ".")))

    expected = 200 if may_create else 403
    assert client.get(reverse("partners:partner-new")).status_code == expected


@pytest.mark.parametrize("role", PARTNER_NON_READERS)
def test_the_new_partner_form_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    client.force_login(_user(role, f"pn.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:partner-new")).status_code == 403


def test_the_new_partner_form_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("partners:partner-new")).status_code in (302, 403)


def test_a_role_without_create_cannot_post_a_partner(client: Client, seeded_settings: None) -> None:
    """The page was never offered; the route still refuses the POST and writes nothing."""
    from apps.partners.models import Partner

    client.force_login(_user(Role.FINANCE_OFFICER, "pn.post"))

    response = client.post(
        reverse("partners:partner-new"),
        {"code": "PN-X", "name_ar": "محاولة", "partner_type": "COMPANY", "status": "ACTIVE"},
    )

    assert response.status_code == 403
    assert not Partner.objects.filter(code="PN-X").exists()


def test_the_new_partner_form_kept_every_field_it_carried(
    client: Client, seeded_settings: None
) -> None:
    """Presentation only: same fields, same names, one form, one submit."""
    client.force_login(_user(Role.CENTER_MANAGER, "pn.fields"))

    response = client.get(reverse("partners:partner-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for name in response.context["form"].fields:
        assert f'name="{name}"' in page, name
    assert page.count("<form") == 1
    assert page.count("<button") == 1
    assert "csrfmiddlewaretoken" in page
    assert 'class="form-acts"' in page


def test_the_new_partner_form_still_saves_and_lands_on_the_card(
    client: Client, seeded_settings: None
) -> None:
    """
    The polish touched no submit behaviour: a valid post still creates the
    partner through the service and redirects to its card.
    """
    from apps.partners.models import Partner

    client.force_login(_user(Role.CENTER_MANAGER, "pn.save"))

    response = client.post(
        reverse("partners:partner-new"),
        {
            "code": "PN-NEW-1",
            "name_ar": "شركة اختبار العرض",
            "partner_type": "COMPANY",
            "status": "ACTIVE",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("partners:partner-detail", args=["PN-NEW-1"])
    assert Partner.objects.filter(code="PN-NEW-1").exists()


def test_an_invalid_partner_is_refused_and_says_which_field(
    client: Client, seeded_settings: None
) -> None:
    """A refusal still renders the form with its error, and writes nothing."""
    from apps.partners.models import Partner

    client.force_login(_user(Role.CENTER_MANAGER, "pn.invalid"))

    response = client.post(reverse("partners:partner-new"), {"code": "", "name_ar": ""})
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert response.status_code == 200
    assert not Partner.objects.exists()
    assert 'class="fld has-error' in page or 'class="err"' in page
    assert response.context["form"].errors


def test_the_new_partner_head_reads_like_every_polished_form(
    client: Client, seeded_settings: None
) -> None:
    """
    The head gained the section, the sentence and the way back — and the title
    is read from the view's own ``title`` rather than retyped in the template,
    so one screen cannot end up with two names that drift apart.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pn.head"))

    response = client.get(reverse("partners:partner-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert 'class="eyebrow"' in page
    assert "الشركاء والمخالصات" in page
    assert 'class="sub"' in page
    assert f"<h1>{response.context['title']}</h1>" in page

    source = PARTNER_NEW_TEMPLATE.read_text(encoding="utf-8")
    assert "{{ title }}" in source
    assert "شريك جديد" not in source, "the screen still carries a second, hand-typed name"


def test_the_new_partner_form_offers_the_way_back_and_the_cancel_it_had(
    client: Client, seeded_settings: None
) -> None:
    """
    Two links to the same register — the head's way back and the form's
    cancel. Both are safe: whoever opened this page holds VIEW there too.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pn.back"))

    page = _partner_new_page(client)
    href = reverse("partners:partners")

    # Three links point there and each is doing a different job: the head's way
    # back, the guided help's next step, and the form's cancel. The two that
    # belong to this screen's own chrome are asserted where they sit.
    acts = page[page.index('class="page-head"') : page.index('class="card2"')]
    cancel = page[page.index('class="form-acts"') :]

    assert f'href="{href}"' in acts, "the head lost its way back"
    assert f'href="{href}"' in cancel, "the form lost its cancel"
    assert "إلغاء" in cancel
    assert client.get(href).status_code == 200


def test_the_new_partner_form_names_no_commercial_term(
    client: Client, seeded_settings: None
) -> None:
    """
    There is no rate on this form and there should be no promise of one. The
    hint and the guidance both send the reader to the agreement instead.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "pn.terms"))

    response = client.get(reverse("partners:partner-new"))
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    for field in ("percent_rate", "settlement_cycle", "exclude_registration_fee"):
        assert f'name="{field}"' not in page, field
        assert field not in response.context["form"].fields, field
    assert "شروط القسمة تُسجَّل على الاتفاقية لا هنا" in page


def test_the_new_partner_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The form records a party. The help may not imply it starts an obligation
    or carries a term — which is exactly the mistake the empty rate field
    invites, and there is no field here for a validation message to land on.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["partner-new"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "لا ينشئ التزاماً ولا يبدأ شرطاً" in text
    assert "§3.5/23" in text
    for absent in ("حذف", "مطالبة", "مخالصة نقدية", "تفعيل"):
        assert absent not in text, f"the partner-new guidance offers «{absent}»"


def test_the_new_partner_guidance_points_only_where_its_reader_may_go(
    client: Client, seeded_settings: None
) -> None:
    """
    Only the manager reaches this page, and they hold VIEW on the register —
    so the one link is drawn and it opens.
    """
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(Role.CENTER_MANAGER, "pn.links"))
    page = _partner_new_page(client)

    guide = GUIDES["partner-new"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        assert Action.VIEW in allowed_actions(Role.CENTER_MANAGER, screen)
        assert f'href="{reverse(route)}"' in page
        assert client.get(reverse(route)).status_code == 200


def test_the_new_partner_form_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = PARTNER_NEW_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the new-partner form uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="form-acts"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# The agreements register — page polish (Wave 1)
# ---------------------------------------------------------------------------
# §3.5/24 «V C E A P · — · V P · — · — · V P». Three roles read it. The create
# LINK, though, is drawn off VIEW on §3.5/25 and not off CREATE — deliberately:
# the editor opens on VIEW and submits on CREATE, so the audit account may read
# it without being able to fill it in. That asymmetry is pinned below in both
# directions, because it looks like a bug until you read the view.
AGREEMENTS_TEMPLATE = Path("templates/partners/agreements.html")

#: role, sees the «تسجيل اتفاقية موقّعة» link — VIEW on §3.5/25, not CREATE.
AGREEMENT_READERS = (
    (Role.CENTER_MANAGER, True),
    (Role.FINANCE_OFFICER, False),
    (Role.AUDIT_ACCOUNT, True),
)

#: The roles §3.5/24 leaves empty. An empty cell is an explicit deny (BR-080).
AGREEMENT_NON_READERS = (Role.REGISTRATION_OFFICER, Role.FINANCE_MANAGER, Role.CASHIER)


@pytest.fixture
def three_agreements(seeded_settings: None) -> list[object]:
    """
    One agreement of each calculation model, and one of them expired.

    Each model names a different value field, which is what the «القيمة»
    column reads — so all three shapes have to be on screen for that column to
    be worth asserting anything about.
    """
    from datetime import date
    from decimal import Decimal

    from apps.partners.models import (
        Agreement,
        AgreementStatus,
        CalculationModel,
        PartnerStatus,
        PartnerType,
    )
    from apps.partners.services import partner_service

    manager = _user(Role.CENTER_MANAGER, "ag.fixture.manager")
    partner = partner_service.create_partner(
        actor=manager,
        data={
            "code": "PN-AG-1",
            "name_ar": "شركة تناغم للتدريب",
            "partner_type": PartnerType.COMPANY,
            "status": PartnerStatus.ACTIVE,
        },
    )
    common = {
        "partner": partner,
        "signed_on": date(2026, 8, 1),
        "valid_from": date(2026, 9, 1),
        "valid_to": date(2099, 8, 31),
        "status": AgreementStatus.ACTIVE,
    }
    return [
        Agreement.objects.create(
            agreement_number="2026/AG-P",
            title_ar="اتفاقية نسبية",
            calculation_model=CalculationModel.PERCENT,
            percent_rate=Decimal("50.0000"),
            **common,
        ),
        Agreement.objects.create(
            agreement_number="2026/AG-F",
            title_ar="اتفاقية مبلغ ثابت",
            calculation_model=CalculationModel.FIXED_PER_STUDENT,
            fixed_amount_per_student=Decimal("195.000"),
            sell_price=Decimal("400.000"),
            **common,
        ),
        Agreement.objects.create(
            agreement_number="2020/AG-X",
            partner=partner,
            title_ar="اتفاقية انقضت",
            signed_on=date(2019, 8, 1),
            valid_from=date(2019, 9, 1),
            valid_to=date(2020, 8, 31),
            status=AgreementStatus.ACTIVE,
            calculation_model=CalculationModel.PERCENT,
            percent_rate=Decimal("40.0000"),
        ),
    ]


def _agreements_page(client: Client) -> str:
    response = client.get(reverse("partners:agreements"))
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</nav>", 1)[-1]


@pytest.mark.parametrize(("role", "_sees_link"), AGREEMENT_READERS)
def test_the_agreements_register_opens_exactly_where_the_matrix_says(
    client: Client, seeded_settings: None, role: str, _sees_link: bool
) -> None:
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW in allowed_actions(role, "agreements")
    client.force_login(_user(role, f"ag.open.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:agreements")).status_code == 200


@pytest.mark.parametrize("role", AGREEMENT_NON_READERS)
def test_the_agreements_register_still_refuses_the_roles_it_always_did(
    client: Client, seeded_settings: None, role: str
) -> None:
    """The polish moved no guard: an empty cell is a deny, before and after."""
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.VIEW not in allowed_actions(role, "agreements")
    client.force_login(_user(role, f"ag.deny.{role}".lower().replace("_", ".")))
    assert client.get(reverse("partners:agreements")).status_code == 403


def test_the_agreements_register_refuses_an_anonymous_visitor(
    client: Client, seeded_settings: None
) -> None:
    assert client.get(reverse("partners:agreements")).status_code in (302, 403)


def test_the_agreements_register_stayed_read_only(
    client: Client, three_agreements: list[object]
) -> None:
    """
    Activation and supersession live on the agreement's own page, each behind
    its own guard there. This screen writes nothing, so the polish may not have
    introduced a POST target — and the empty `<form>` that wrapped one link is
    gone, because a form with no field and no submit sends nothing and only
    misleads whoever reads the template next.
    """
    client.force_login(_user(Role.CENTER_MANAGER, "ag.readonly"))

    page = _agreements_page(client)

    assert "<form" not in page
    assert "<button" not in page
    assert "csrfmiddlewaretoken" not in page
    assert 'name="action"' not in page


@pytest.mark.parametrize(("role", "sees_link"), AGREEMENT_READERS)
def test_the_editor_link_follows_view_on_the_editor_not_create(
    client: Client, three_agreements: list[object], role: str, sees_link: bool
) -> None:
    """
    The link is drawn off VIEW on §3.5/25, which is why the audit account sees
    it and the finance officer does not — the auditor may READ the editor. The
    guarantee that matters is that the link always opens for whoever is shown
    it, and never for whoever is not.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert (Action.VIEW in allowed_actions(role, "agreement-new")) is sees_link
    client.force_login(_user(role, f"ag.link.{role}".lower().replace("_", ".")))

    response = client.get(reverse("partners:agreements"))
    assert response.context["can_create"] is sees_link
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    href = reverse("partners:agreement-new")
    assert (f'href="{href}"' in page) is sees_link, role
    # Shown ⇒ it opens. Hidden ⇒ the route refuses. No reader meets a refusal.
    assert client.get(href).status_code == (200 if sees_link else 403)


def test_only_the_manager_can_actually_submit_the_editor(
    client: Client, seeded_settings: None
) -> None:
    """
    Seeing the editor is not permission to fill it in. The auditor is shown the
    link, opens the page, and is still refused on POST — which is the whole
    reason the two checks differ.
    """
    from apps.people.constants import Action
    from apps.people.permissions.matrix import allowed_actions

    assert Action.CREATE in allowed_actions(Role.CENTER_MANAGER, "agreement-new")
    assert Action.CREATE not in allowed_actions(Role.AUDIT_ACCOUNT, "agreement-new")

    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.submit"))
    assert client.get(reverse("partners:agreement-new")).status_code == 200
    assert client.post(reverse("partners:agreement-new"), {}).status_code == 403


def test_every_register_row_links_to_an_agreement_its_reader_may_open(
    client: Client, three_agreements: list[object]
) -> None:
    """The «عرض» link is covered by the same VIEW that opened this register."""
    for role, _sees_link in AGREEMENT_READERS:
        client.force_login(_user(role, f"ag.row.{role}".lower().replace("_", ".")))
        response = client.get(reverse("partners:agreements"))
        page = response.content.decode("utf-8").split("</nav>", 1)[-1]
        for row in response.context["agreements"]:
            href = reverse("partners:agreement-detail", args=[row["agreement_number"]])
            assert f'href="{href}"' in page, f"{role} · {row['agreement_number']}"
            assert client.get(href).status_code == 200, f"{role} · {href}"
        client.logout()


def test_the_value_column_reads_the_field_its_model_names(
    client: Client, three_agreements: list[object]
) -> None:
    """
    A percentage agreement has no per-student amount and a per-student one has
    no rate. The column shows whichever the model names — unchanged by the
    polish — and never invents a figure for a model that has none.
    """
    from decimal import Decimal

    client.force_login(_user(Role.FINANCE_OFFICER, "ag.value"))

    response = client.get(reverse("partners:agreements"))
    rows = {a["agreement_number"]: a for a in response.context["agreements"]}
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]

    assert rows["2026/AG-P"]["percent_rate"] == Decimal("50.0000")
    assert rows["2026/AG-P"]["fixed_amount_per_student"] is None
    assert rows["2026/AG-F"]["fixed_amount_per_student"] == Decimal("195.000")
    assert rows["2026/AG-F"]["percent_rate"] is None

    # Compared against what a template really renders. The cells print the
    # Decimal directly, so USE_L10N localises it — 50.0000 reaches the page as
    # «50,0000», and `floatformat` would trim it to «50» and prove nothing.
    from django.utils.formats import localize

    table = page[page.index('class="tbl"') : page.index("</table>")]
    assert f"{localize(rows['2026/AG-P']['percent_rate'])}%" in table
    assert localize(rows["2026/AG-F"]["fixed_amount_per_student"]) in table
    # The sell price belongs to the agreement's own page, not to this column.
    assert localize(rows["2026/AG-F"]["sell_price"]) not in table


def test_the_expired_agreement_is_named_the_same_way_the_partner_card_names_it(
    client: Client, three_agreements: list[object]
) -> None:
    """
    One rule, one wording. The partner card says «انقضت مدّتها» and so does
    this register — two screens phrasing one fact two ways is how a reader
    learns two rules (polish rules §7).

    And it is no longer yellow: `.warn` is the colour of a refusal, while
    running out of term is a fact about the calendar that the service computes.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.expired"))

    response = client.get(reverse("partners:agreements"))
    rows = {a["agreement_number"]: a for a in response.context["agreements"]}
    page = response.content.decode("utf-8").split("</nav>", 1)[-1]
    table = page[page.index('class="tbl"') : page.index("</table>")]

    assert rows["2020/AG-X"]["is_expired"] is True
    assert rows["2026/AG-P"]["is_expired"] is False
    assert table.count("انقضت مدّتها") == 1
    assert "منقضية" not in page, "the two screens still word one fact two ways"
    assert 'class="chip warn"' not in table, "a calendar fact is drawn as a refusal"
    # Both screens read the service's flag; neither compares dates itself.
    assert "انقضت مدّتها" in PARTNER_DETAIL_TEMPLATE.read_text(encoding="utf-8")
    assert "a.is_expired" in AGREEMENTS_TEMPLATE.read_text(encoding="utf-8")


def test_the_agreements_head_reads_like_every_polished_register(
    client: Client, three_agreements: list[object]
) -> None:
    """
    A bare ``<h1>`` gained the section, the sentence and a count over the rows
    drawn — all off the view's own ``title`` and no new context key.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.head"))

    page = _agreements_page(client)

    assert 'class="eyebrow"' in page
    assert "الشركاء والمخالصات" in page
    assert "<h1>" in page
    assert 'class="sub"' in page
    assert 'class="count"' in page
    assert "3 اتفاقيات" in page


def test_the_agreements_table_names_its_eighth_column(
    client: Client, three_agreements: list[object]
) -> None:
    """
    Named by ``aria-label`` and not by `.sr-only`, which is
    ``position:absolute`` with no positioned ancestor and lands off the left
    edge in RTL, dragging the page sideways.
    """
    import re

    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.col"))

    page = _agreements_page(client)

    assert 'aria-label="الإجراء"' in page
    assert "sr-only" not in page
    assert len(re.findall(r"<th[\s>]", page)) == 8
    assert 'class="tbl-wrap"' in page


def test_the_three_models_rule_is_explained_and_no_longer_alerted(
    client: Client, three_agreements: list[object]
) -> None:
    """
    «ثلاثة نماذج تعاقد» was a blue `.note info` between the guided-help block
    and the table — a third framed block above the rows. Blue is an alert and
    explaining a rule is not one (polish rules §6.5).
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.rule"))

    page = _agreements_page(client)

    assert "note info" not in page
    assert '<p class="hint">' in page
    anchor = "ثلاثة نماذج تعاقد"
    assert anchor in page
    assert page.index(anchor) > page.index('class="tbl"'), "the rule left its rows behind"


def test_the_agreements_filter_parameter_is_untouched(
    client: Client, three_agreements: list[object]
) -> None:
    """
    The view has always narrowed by ``?partner=``; it has never had a control,
    and the polish added none. Pinned so a later slice knows what to keep.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "ag.filter"))

    page = _agreements_page(client)
    assert 'class="filterbar"' not in page
    assert 'name="partner"' not in page

    narrowed = client.get(reverse("partners:agreements"), {"partner": "PN-AG-1"})
    assert len(narrowed.context["agreements"]) == 3
    missed = client.get(reverse("partners:agreements"), {"partner": "PN-NOPE"})
    assert len(missed.context["agreements"]) == 0

    source = Path("apps/partners/views.py").read_text(encoding="utf-8")
    assert 'request.GET.get("partner", "").strip()' in source


def test_the_agreements_empty_state_kept_its_words_and_its_gate(
    client: Client, seeded_settings: None
) -> None:
    """
    The register already had a written empty state; the polish kept it word for
    word, and its action still follows the same flag the header link does.
    """
    from apps.partners.models import Agreement

    assert not Agreement.objects.exists()
    for role, sees_link in AGREEMENT_READERS:
        client.force_login(_user(role, f"ag.empty.{role}".lower().replace("_", ".")))
        page = _agreements_page(client)
        assert 'class="empty-title"' in page
        assert "تُوقَّع على الورق ثم تُسجَّل هنا كما وردت" in page
        assert ("empty-act" in page) is sees_link, role
        client.logout()


def test_the_agreements_guidance_invents_no_action_the_screen_lacks() -> None:
    """
    The register lists and links. The help may not imply an agreement is edited
    here — BR-042 says the opposite — and it names the three roles that read
    the screen rather than only the one that writes.
    """
    from apps.people.guidance import GUIDES

    guide = GUIDES["agreements"]
    text = " ".join(str(part) for part in (guide.what, guide.who, guide.after, guide.stops))

    assert "BR-042" in text
    assert "لا تُعدَّل اتفاقية موقّعة" in text
    assert "حساب التدقيق" in text
    for absent in ("حذف", "تعديل البنود", "استرداد"):
        assert absent not in text, f"the agreements guidance offers «{absent}»"


@pytest.mark.parametrize(("role", "_sees_link"), AGREEMENT_READERS)
def test_the_agreements_guidance_offers_only_steps_the_reader_may_open(
    client: Client, seeded_settings: None, role: str, _sees_link: bool
) -> None:
    """A next step the reader may not follow ends in a refusal and a BR-085 row."""
    from apps.people.constants import Action
    from apps.people.guidance import GUIDES
    from apps.people.permissions.matrix import allowed_actions

    client.force_login(_user(role, f"ag.links.{role}".lower().replace("_", ".")))
    page = _agreements_page(client)

    guide = GUIDES["agreements"]
    assert str(guide.what) in page
    assert str(guide.stops) in page
    assert page.index(str(guide.what)) < page.index('class="card2"')
    for screen, route, _label in guide.links:
        may_open = Action.VIEW in allowed_actions(role, screen)
        assert (f'href="{reverse(route)}"' in page) is may_open, f"{role} · {route}"
        if may_open:
            assert client.get(reverse(route)).status_code == 200, route


def test_the_agreements_register_added_no_dead_class_and_no_dependency() -> None:
    """Every class it draws with already existed; the page needed no new CSS."""
    import re

    source = AGREEMENTS_TEMPLATE.read_text(encoding="utf-8")
    css = CSS_SOURCE.read_text(encoding="utf-8")
    built = Path("static/css/app.css").read_text(encoding="utf-8")

    for dead in [*NAV_DEAD_CLASSES, "compact", "mono", "split3", "filters", "right", "tight"]:
        assert f'"{dead}"' not in source, f"the agreements register uses «{dead}»"
    assert "<script" not in source
    assert "style=" not in source
    assert "http://" not in source and "https://" not in source

    used = {
        c
        for m in re.finditer(r'class="([^"]*)"', source)
        for c in re.sub(r"{{[^}]*}}|{%[^%]*%}", " ", m.group(1)).split()
    }
    for name in used:
        assert f".{name}" in css or f".{name}" in built, f"«{name}» is defined nowhere"
    markup = source.split("{% endcomment %}", 1)[-1]
    assert 'class="tbl-wrap"' in markup
    assert "sr-only" not in markup
    for alert in ("note info", "note warn", "note danger", "note ok"):
        assert alert not in markup, f"a rule is still being explained in «{alert}»"
    for line in source.splitlines():
        assert line.count("{#") == line.count("#}"), f"a wrapped comment: {line.strip()[:60]}"
