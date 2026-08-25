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
