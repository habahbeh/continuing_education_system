"""
The admission form as a SCREEN — who may save, what it suggests, and what it
says when it refuses.

The permission proofs for this route live in ``test_participant_views.py``
(T-284 reads them from the matrix). What is under test here is the promise the
page makes to the person in front of it — and the one that mattered most was a
promise it had no right to make: the audit account, whose whole grant is
«قراءة فقط لجميع الحركات» (requirements §8), was handed a fillable form and a
save button, and the save was refused by the service and written to the audit
trail as a DENIED_ATTEMPT the screen itself had invited.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Semester
from apps.people.models import IdDocumentType, Participant, Role, User
from apps.people.services import reference_data

pytestmark = pytest.mark.django_db

PASSWORD = "admission-probe-1234"

#: A payload the form accepts. Tests narrow it rather than rebuild it.
VALID = {
    "category": "UNIVERSITY",
    "name_ar": "نور سامي عبدالكريم الحياري",
    "name_en": "Noor Sami Abdulkarim Al-Hiyari",
    "id_document_type": IdDocumentType.NATIONAL_ID,
    "id_document_number": "9971234567",
    "nationality": "الأردن",
    "gender": "FEMALE",
    "date_of_birth": "1997-04-02",
    "qualification": "",
    "city": "",
    "phone": "0791111222",
    "po_box": "",
    "email": "",
    "employer": "",
    "registered_on": "2026-09-20",
    "no_refund_pledge_accepted": "on",
    "is_exempt": "",
    "exemption_approval_ref": "",
    "exemption_approval_date": "",
    "duplicate_override_reason": "",
}


def _user(role: str, username: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


# ---------------------------------------------------------------------------
# Who may save — requirements §8, polish rules §3.4
# ---------------------------------------------------------------------------
def test_the_audit_account_is_offered_no_save_button(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    «حساب التدقيق: قراءة فقط لجميع الحركات» — and a screen that offers it a
    save button is a screen that walks it into a refusal it then records.
    """
    client.force_login(_user(Role.AUDIT_ACCOUNT, "adm.auditor"))

    response = client.get(reverse("people:participant-new"))
    body = response.content.decode()

    assert response.status_code == 200  # it may LOOK at the form
    assert 'class="btn2 primary"' not in body  # …and may not save it
    assert "نموذج للاطّلاع" in body
    assert "<fieldset disabled" in body


def test_the_registrar_is_offered_the_save_button(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """The control case — otherwise a blank page would pass the test above."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.reg"))

    body = client.get(reverse("people:participant-new")).content.decode()

    assert 'class="btn2 primary"' in body
    assert "نموذج للاطّلاع" not in body
    assert "<fieldset disabled" not in body


def test_a_post_from_the_audit_account_creates_nothing(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """Hiding the button is not the guard; the guard is that the POST is inert."""
    client.force_login(_user(Role.AUDIT_ACCOUNT, "adm.auditor.post"))

    response = client.post(reverse("people:participant-new"), VALID)

    assert response.status_code == 200
    assert Participant.objects.count() == 0


# ---------------------------------------------------------------------------
# What the form suggests
# ---------------------------------------------------------------------------
def test_the_registration_date_is_offered_as_today(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """Required, known to the system, and typed by hand on every application."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.date"))

    body = client.get(reverse("people:participant-new")).content.decode()

    assert f'value="{timezone.localdate().isoformat()}"' in body


@pytest.mark.parametrize("field", ["registered_on", "date_of_birth", "exemption_approval_date"])
def test_no_date_control_offers_tomorrow(
    client: Client, seeded_settings: None, active_semester: Semester, field: str
) -> None:
    """A birth date in 2099 was a legal answer in a registry sent to the ministry."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, f"adm.max.{field}"))

    body = client.get(reverse("people:participant-new")).content.decode()

    control = re.search(rf'<input[^>]*name="{field}"[^>]*>', body)
    assert control, f"الحقل «{field}» غير مرسوم"
    assert f'max="{timezone.localdate().isoformat()}"' in control.group(0)


def test_the_nationality_is_suggested_and_not_imposed(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    A default is the common answer; a choice list would be a vocabulary. The
    client's requirements enumerate the qualification and never the
    nationality, so the field stays free text.
    """
    from apps.people.participant_forms import ParticipantForm

    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.nat"))
    body = client.get(reverse("people:participant-new")).content.decode()

    assert 'name="nationality" value="الأردن"' in body
    assert not hasattr(ParticipantForm().fields["nationality"], "choices")

    # …and it is overwritable, not enforced.
    client.post(reverse("people:participant-new"), dict(VALID, nationality="سورية"))
    assert Participant.objects.get().nationality == "سورية"


# ---------------------------------------------------------------------------
# What it says when it refuses
# ---------------------------------------------------------------------------
def test_a_refusal_counts_and_names_every_field_at_the_top(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    Six errors across five cards on a page two and a half screens tall: the
    reader used to have to scroll for the red.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.summary"))

    body = client.post(reverse("people:participant-new"), dict.fromkeys(VALID, "")).content.decode()

    assert 'id="err-summary"' in body
    assert "حقول تحتاج تصحيحاً قبل الحفظ" in body
    for anchor in ("#id_category", "#id_name_ar", "#id_registered_on"):
        assert f'href="{anchor}"' in body
    assert Participant.objects.count() == 0


def test_a_successful_page_carries_no_error_summary(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.clean"))

    body = client.get(reverse("people:participant-new")).content.decode()

    assert 'id="err-summary"' not in body


def test_a_duplicate_document_offers_the_matching_file_as_a_link(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    BR-005 names a participant number. A number alone means copying it and
    searching for it; the record itself is one read away.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.dup"))
    client.post(reverse("people:participant-new"), VALID)
    existing = Participant.objects.get()

    body = client.post(
        reverse("people:participant-new"),
        dict(VALID, name_ar="شخص آخر بنفس الوثيقة تماماً"),
    ).content.decode()

    assert reverse("people:participant-detail", args=[existing.participant_number]) in body
    assert existing.name_ar in body
    # …and the reason field appears now, where the question is actually asked.
    assert body.count('name="duplicate_override_reason"') == 1
    assert Participant.objects.count() == 1  # …and nothing was created


def test_the_duplicate_warning_survives_a_refusal_about_another_field(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    The clerk is refused for the duplicate, types the documented reason, then
    leaves the pledge unticked and saves again. ``clean()`` now rejects the
    form BEFORE the service is reached, so no duplicate is reported by it —
    and the page used to answer that by dropping the banner, the field and the
    sentence the clerk had written, without a word. Nothing about the duplicate
    had changed; only which field complained first.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.dup.keep"))
    client.post(reverse("people:participant-new"), VALID)
    existing = Participant.objects.get()

    response = client.post(
        reverse("people:participant-new"),
        dict(
            VALID,
            name_ar="شخص آخر بنفس الوثيقة تماماً",
            duplicate_override_reason="أرشيف ورقي — وثيقة مكرّرة موثّقة",
            no_refund_pledge_accepted="",  # الرفض يأتي من هنا، لا من الوثيقة
        ),
    )
    body = response.content.decode()

    assert response.context["form"].errors == {
        "no_refund_pledge_accepted": ["يجب الإقرار بالتعهّد قبل الحفظ"]
    }
    assert "وثيقة الهوية مسجَّلة سلفاً" in body  # the banner stays…
    assert reverse("people:participant-detail", args=[existing.participant_number]) in body
    assert body.count('name="duplicate_override_reason"') == 1  # …so does its field…
    assert "أرشيف ورقي — وثيقة مكرّرة موثّقة" in body  # …and what was typed in it
    assert Participant.objects.count() == 1


def test_the_matching_file_opens_without_costing_the_filled_form(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    Checking the duplicate is a read, and it must not cost twenty filled
    fields: the link leaves this tab where it is.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.dup.tab"))
    client.post(reverse("people:participant-new"), VALID)
    existing = Participant.objects.get()

    body = client.post(
        reverse("people:participant-new"),
        dict(VALID, name_ar="شخص آخر بنفس الوثيقة تماماً"),
    ).content.decode()

    file_url = reverse("people:participant-detail", args=[existing.participant_number])
    link = re.search(rf'<a href="{file_url}"[^>]*>', body)
    assert link, "the matching file is not linked at all"
    assert 'target="_blank"' in link.group(0)
    assert 'rel="noopener"' in link.group(0)


def test_a_jump_leaves_the_sticky_section_strip_its_own_room(
    client: Client, seeded_settings: None
) -> None:
    """
    The strip is 48px tall and sticks to the top, so without a scroll margin
    the section it jumps to lands at 0 — the strip covering the very heading
    that was clicked — and a field reached from the error summary lands at -23,
    hiding its label and its error message together (71px of it). Measured in
    a headless browser; asserted here because the rule is invisible until
    something is actually clicked.
    """
    css = (Path(settings.BASE_DIR) / "static" / "src" / "input.css").read_text(encoding="utf-8")
    block = css.split("--- طلب التحاق جديد:", 1)[1].split("/* --- ", 1)[0]

    assert block.count("scroll-margin-block-start") == 2
    assert '.card2[id^="sec-"]' in block


def test_the_duplicate_may_still_be_saved_with_a_reason(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """BR-005 WARN: the link is an aid, not a block."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.dup.ok"))
    client.post(reverse("people:participant-new"), VALID)

    client.post(
        reverse("people:participant-new"),
        dict(
            VALID,
            name_ar="توأم يحمل الوثيقة ذاتها",
            duplicate_override_reason="أرشيف ورقي — وثيقة مكرّرة موثّقة",
        ),
    )

    assert Participant.objects.count() == 2


# ---------------------------------------------------------------------------
# BR-001 — the screen knows before the clerk does
# ---------------------------------------------------------------------------
def test_the_form_says_up_front_that_no_semester_is_active(
    client: Client, seeded_settings: None
) -> None:
    """Twenty-two fields, then an apology, was the old order of events."""
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.nosem"))

    body = client.get(reverse("people:participant-new")).content.decode()

    assert "لا يمكن حفظ طلب التحاق الآن" in body
    assert "لا يوجد فصل دراسي نشط" in body


def test_the_active_semester_is_named_when_there_is_one(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.sem"))

    body = client.get(reverse("people:participant-new")).content.decode()

    assert active_semester.code in body
    assert "لا يمكن حفظ طلب التحاق الآن" not in body


# ---------------------------------------------------------------------------
# The five sections, and an empty POST that used to say nothing at all
# ---------------------------------------------------------------------------
def test_every_field_is_drawn_exactly_once(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    The exemption fields are revealed by a checkbox rather than rendered twice:
    two copies would mean two controls of the same name posting together.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.sections"))

    body = client.get(reverse("people:participant-new")).content.decode()

    for name in VALID:
        if name == "duplicate_override_reason":
            # BR-005 — it is not asked before anything has been repeated; it
            # is drawn beside the warning that asks for it (see the duplicate
            # test below), and nowhere else.
            assert body.count(f'name="{name}"') == 0
            continue
        assert body.count(f'name="{name}"') == 1, f"«{name}» مرسوم أكثر من مرة"
    for heading in (
        "الفئة وتاريخ التسجيل",
        "البيانات الشخصية ووثيقة الهوية",
        "الاتصال والعنوان",
        "المؤهل والعمل",
        "الإعفاء والتعهّد",
    ):
        assert heading in body


def test_an_empty_post_is_still_a_submission(
    client: Client, seeded_settings: None, active_semester: Semester
) -> None:
    """
    ``request.POST or None`` read an empty body as "not submitted", so the page
    came back pristine and silent instead of saying what was missing.
    """
    client.force_login(_user(Role.REGISTRATION_OFFICER, "adm.empty"))

    body = client.post(reverse("people:participant-new"), {}).content.decode()

    assert 'id="err-summary"' in body


# ---------------------------------------------------------------------------
# Q-31 — the reference list against the client's own document
# ---------------------------------------------------------------------------
def test_the_qualification_list_holds_the_six_levels_the_client_wrote_down(
    seeded_settings: None,
) -> None:
    """
    requirements.md §2.2 enumerates them: أقل من الثانوية · الثانوية · دبلوم ·
    بكالوريوس · ماجستير · دكتوراه. The first was missing, so a participant who
    had not finished secondary school had no level a clerk could pick.

    «دبلوم عالٍ» is not in that list and is not removed: a stored row may carry
    it, and deleting a choice is not a correction (polish rules §1).
    """
    labels = [label for _code, label in reference_data.qualifications()]

    for level in ("أقل من الثانوية", "دبلوم", "بكالوريوس", "ماجستير", "دكتوراه"):
        assert level in labels, f"«{level}» غائب عن قائمة المؤهلات"
    assert any("الثانوية" in label for label in labels)


def test_refreshing_a_reference_list_opens_a_period_and_closes_the_old_one(
    seeded_settings: None,
) -> None:
    """
    A corrected list reaches an already-seeded database as a new effective
    period, never as an edit: a row entered last month is still read against
    the list that was in force the day it was entered.
    """
    from django.core.management import call_command

    from apps.core.models import EffectiveSetting
    from apps.core.services.settings_service import set_setting

    EffectiveSetting.objects.filter(key="participant_qualifications").update(
        effective_to=date(2026, 1, 1)
    )
    set_setting(
        "participant_qualifications",
        '[["OLD","قائمة قديمة"]]',
        value_type="JSON",
        effective_from=date(2026, 1, 2),
        note="قائمة سابقة لغرض الاختبار",
    )
    assert [label for _c, label in reference_data.qualifications()] == ["قائمة قديمة"]

    call_command("seed_settings", "--refresh-lists", verbosity=0)

    labels = [label for _c, label in reference_data.qualifications()]
    assert "أقل من الثانوية" in labels
    # The old period is closed, not deleted — yesterday still reads as yesterday.
    yesterday = timezone.localdate() - timedelta(days=1)
    assert reference_data.qualifications(as_of=yesterday) == [("OLD", "قائمة قديمة")]
