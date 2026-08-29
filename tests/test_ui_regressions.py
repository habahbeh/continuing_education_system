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
    anywhere. The only link the page draws is the one that clears the filter.
    """
    import re

    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("operations:cohort-detail")

    client.force_login(_user(Role.CENTER_MANAGER, "coh.links"))
    page = _cohorts(client, "?q=CO-UIC")

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    assert hrefs == {reverse("operations:cohorts")}, hrefs
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
    """Three kinds of link, and every one of them opens for the reader drawing it."""
    import re

    from apps.operations.models import MoheSubmission

    client.force_login(_user(Role.CENTER_MANAGER, "moh.links"))
    page = _mohe(client, "?q=CO-UIC")

    hrefs = set(re.findall(r'<a[^>]+href="([^"]+)"', page))
    expected = {reverse("operations:mohe"), reverse("operations:mohe-submit")} | {
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
