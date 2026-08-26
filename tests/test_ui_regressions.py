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
