"""
Printed documents — clearance form and certificate (Sprint 8C-1).

⚠️ **These test requirements-based output, not an official form.** The centre's
blank ``CS Fm 7.18 Rev A`` and its certificate were not in the client folder,
so what is asserted is that the document carries what §6.4 and §7 SAY it
records — never that its layout matches the centre's paper. The
``document_mode`` marker is itself under test, because a document that stopped
admitting it was unverified would be the actual failure here.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

pytestmark = pytest.mark.django_db

TERM_START = date(2026, 9, 20)
PASSWORD = "probe-password-1234"


@pytest.fixture
def signed_in(client):
    def _in(user):
        client.force_login(user)
        return client

    return _in


@pytest.fixture
def finance_manager(seeded_settings):
    from apps.people.models import Role, User

    return User.objects.create_user(
        username="fim.doc", password=PASSWORD, role=Role.FINANCE_MANAGER
    )


@pytest.fixture
def settled(make_cohort, approve_cohort, make_enrollment, charge_and_pay):
    def _make(index: int = 1, code: str = "CO-DOC", paid: str = "270.000"):
        cohort = make_cohort("SC-NET", code=code)
        approve_cohort(cohort, course_number=f"M-{code}")
        enrollment = make_enrollment(cohort, index=index)
        charge_and_pay(enrollment, amount=paid)
        enrollment.status = "COMPLETED"
        enrollment.save(update_fields=["status"])
        return enrollment

    return _make


def _url(code: str) -> str:
    return reverse("operations:clearance-detail", args=[code])


def _open(client, enrollment, code="CLR-DOC-1"):
    return client.post(
        reverse("operations:clearances"),
        {
            "action": "open",
            "enrollment_code": enrollment.code,
            "case_type": "GRADUATION",
            "code": code,
            "opened_on": TERM_START.isoformat(),
        },
        follow=True,
    )


def _custody(client, code, items="هوية المركز\nبطاقة المواصلات"):
    return client.post(_url(code), {"action": "custody", "items": items}, follow=True)


def _through_finance(signed_in, manager, finance, finance_manager, enrollment, code="CLR-DOC-1"):
    _open(signed_in(manager), enrollment, code=code)
    _custody(signed_in(manager), code)
    signed_in(finance).post(_url(code), {"action": "certify"}, follow=True)
    signed_in(finance_manager).post(_url(code), {"action": "second-certify"}, follow=True)


# ---------------------------------------------------------------------------
# The standard custody list — Sprint 7's silent hole, closed here
# ---------------------------------------------------------------------------
def test_the_standard_custody_list_is_offered_from_settings(signed_in, manager, settled) -> None:
    """§6.4 names «هوية المركز» and «بطاقة المواصلات» — seeded, not hard-coded."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    page = signed_in(manager).get(_url("CLR-DOC-1"))
    offered = page.context["custody_form"].initial["items"]

    assert "هوية المركز" in offered
    assert "بطاقة المواصلات" in offered


def test_an_empty_custody_list_is_now_refused(signed_in, manager, settled) -> None:
    """
    🐞 Sprint 7 accepted ``custody_items=[]`` silently, so step 1 could be
    completed by recovering nothing at all — on a form whose entire first
    section is the property being handed back.
    """
    from apps.operations.services import clearance_service

    enrollment = settled()
    _open(signed_in(manager), enrollment)
    clearance = clearance_service.clearance_instance(actor=manager, code="CLR-DOC-1")

    with pytest.raises(ValidationError, match="فارغة"):
        clearance_service.complete_custody_step(
            actor=manager, clearance=clearance, custody_items=[]
        )


# ---------------------------------------------------------------------------
# The participant's acknowledgement — §6.4 step 3's second signature
# ---------------------------------------------------------------------------
def test_handover_without_the_receivers_name_is_refused(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """
    §6.4 — «تسليم الشهادة ← توقيع المشارك ومدير المركز».

    A handover attested only by the centre proves that the centre says it
    happened.
    """
    from apps.operations.services import clearance_service

    enrollment = settled()
    _through_finance(signed_in, manager, finance, finance_manager, enrollment)
    clearance = clearance_service.clearance_instance(actor=manager, code="CLR-DOC-1")

    with pytest.raises(clearance_service.ParticipantAcknowledgementRequiredError):
        clearance_service.complete_handover_step(
            actor=manager, clearance=clearance, participant_ack_name="  "
        )


def test_the_acknowledgement_is_recorded_and_audited(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    from apps.core.models import AuditEvent

    enrollment = settled()
    _through_finance(signed_in, manager, finance, finance_manager, enrollment)

    signed_in(manager).post(
        _url("CLR-DOC-1"),
        {"action": "handover", "participant_ack_name": "سالم أحمد سالم العمري"},
        follow=True,
    )

    page = signed_in(manager).get(_url("CLR-DOC-1"))
    step3 = page.context["clearance"]["steps"][2]
    assert step3["is_done"] is True

    event = (
        AuditEvent.objects.filter(
            entity_type="operations.ClearanceStep", reference="CLR-DOC-1", action="APPROVE"
        )
        .order_by("-id")
        .first()
    )
    assert event is not None
    changes = event.changes or {}
    assert changes["participant_ack_name"] == "سالم أحمد سالم العمري"


# ---------------------------------------------------------------------------
# The clearance form
# ---------------------------------------------------------------------------
def test_the_clearance_form_carries_what_6_4_says_it_records(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """
    NOT a claim of layout match — a claim that the CONTENT §6.4 specifies is
    present: the three cases, the three steps, the custody items, the balance
    at the financial check, and both financial signatures.
    """
    enrollment = settled()
    _through_finance(signed_in, manager, finance, finance_manager, enrollment)
    signed_in(manager).post(
        _url("CLR-DOC-1"),
        {"action": "handover", "participant_ack_name": "سالم العمري"},
        follow=True,
    )

    page = signed_in(manager).get(reverse("operations:clearance-print", args=["CLR-DOC-1"]))
    body = page.content.decode()

    assert page.status_code == 200
    assert enrollment.participant.name_ar in body
    assert "هوية المركز" in body
    assert "سالم العمري" in body
    for step_name in ("استرجاع العُهد", "التحقق المالي", "تسليم الشهادة"):
        assert step_name in body


def test_the_form_admits_it_is_not_the_official_layout(signed_in, manager, settled) -> None:
    """
    The honesty marker, and the point of this test.

    ``document_mode`` ships REQUIREMENTS_BASED because the centre's blank form
    was not available. A document that quietly stopped saying so would be
    presenting itself as something nobody has verified it is.
    """
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    body = (
        signed_in(manager)
        .get(reverse("operations:clearance-print", args=["CLR-DOC-1"]))
        .content.decode()
    )

    assert "لم يُقابَل بعد بالنموذج الورقي" in body
    assert "CS Fm 7.18 Rev A" in body  # the code §6.4 names is still shown


def test_marking_the_layout_verified_removes_the_notice(signed_in, manager, settled) -> None:
    """Flipping the setting is what a real verification looks like."""
    from apps.core.models import EffectiveSetting, SettingValueType
    from apps.core.services.settings_service import close_setting, set_setting

    if EffectiveSetting.objects.filter(key="document_mode").exists():
        close_setting("document_mode", effective_to=date(2026, 1, 1))
    set_setting(
        "document_mode",
        "OFFICIAL",
        value_type=SettingValueType.STRING,
        effective_from=date(2026, 1, 2),
        note="اختبار — بعد مقابلة المخرَج بالنموذج",
    )

    enrollment = settled()
    _open(signed_in(manager), enrollment)
    body = (
        signed_in(manager)
        .get(reverse("operations:clearance-print", args=["CLR-DOC-1"]))
        .content.decode()
    )

    assert "لم يُقابَل بعد بالنموذج الورقي" not in body


def test_the_form_is_printable_before_completion(signed_in, manager, settled) -> None:
    """
    WORKFLOWS §6.3 — the form is a checklist handed over the counter.

    A participant standing there is entitled to see what is still outstanding,
    so printing does not wait for completion.
    """
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    page = signed_in(manager).get(reverse("operations:clearance-print", args=["CLR-DOC-1"]))
    assert page.status_code == 200
    assert "غير مكتملة" in page.content.decode()


def test_the_cashier_cannot_print_a_clearance(signed_in, cashier, manager, settled) -> None:
    """The print route runs the same check as the screen it prints."""
    enrollment = settled()
    _open(signed_in(manager), enrollment)

    response = signed_in(cashier).get(reverse("operations:clearance-print", args=["CLR-DOC-1"]))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# The certificate
# ---------------------------------------------------------------------------
def _issue(signed_in, manager, finance, finance_manager, settled, grade="EXCELLENT"):
    enrollment = settled()
    _through_finance(signed_in, manager, finance, finance_manager, enrollment)
    signed_in(manager).post(
        _url("CLR-DOC-1"),
        {"action": "handover", "participant_ack_name": "سالم العمري"},
        follow=True,
    )
    signed_in(manager).post(_url("CLR-DOC-1"), {"action": "close"}, follow=True)
    issued = signed_in(manager).post(
        reverse("operations:certificates"),
        {
            "action": "issue",
            "enrollment_code": enrollment.code,
            "grade": grade,
            "issued_on": TERM_START.isoformat(),
            "duration_text": "20 أيلول — 20 كانون الأول 2026",
            "training_hours": "40",
        },
        follow=True,
    )
    return issued.context["certificates"][0]["certificate_number"]


def test_the_certificate_carries_all_seven_fields_7_names(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """
    §7 — «اسم المشارك · اسم الدورة · مدة الدورة · عدد الساعات · التقدير ·
    الرقم · التاريخ». All seven, and the stamps as labelled empty space
    because §7 says they are applied by hand.
    """
    number = _issue(signed_in, manager, finance, finance_manager, settled)

    body = (
        signed_in(manager)
        .get(reverse("operations:certificate-print", args=[number]))
        .content.decode()
    )

    assert number in body
    assert "40" in body
    assert "20 أيلول" in body
    assert "ختم وزارة التعليم العالي" in body
    assert "stamp-ring" in body  # the placeholder, not a drawn stamp


def test_a_replacement_prints_as_a_replacement(
    signed_in, manager, finance, finance_manager, settled, cashier, cash_method
) -> None:
    """
    BR-038 · C-08 — a replacement names the original it replaces.

    The fee is charged and COLLECTED first, because ``issue_replacement``
    refuses otherwise — this sprint does not loosen that.
    """
    from apps.billing.services import extra_fee_service
    from apps.cashbox.services import payment_service
    from apps.operations.services import enrollment_service

    number = _issue(signed_in, manager, finance, finance_manager, settled, grade="GOOD")
    enrollment = enrollment_service.get_enrollment(actor=manager, code="EN-OPS-1")

    extra_fee_service.charge_extra_fee(
        actor=manager,
        enrollment=enrollment,
        fee_type="CERTIFICATE_REPLACEMENT",
        charged_on=TERM_START,
    )
    fee_amount = extra_fee_service.default_amount_for("CERTIFICATE_REPLACEMENT", as_of=TERM_START)
    assert fee_amount is not None  # BR-038's 15 dinars, seeded in Sprint 1
    payment_service.take_payment(
        actor=cashier,
        enrollment=enrollment,
        amount=fee_amount,
        payment_method=cash_method,
        received_on=TERM_START,
    )

    replaced = signed_in(manager).post(
        reverse("operations:certificates"),
        {"action": "replace", "number": number, "on_date": TERM_START.isoformat()},
        follow=True,
    )
    new_number = next(
        c["certificate_number"] for c in replaced.context["certificates"] if c["is_replacement"]
    )

    body = (
        signed_in(manager)
        .get(reverse("operations:certificate-print", args=[new_number]))
        .content.decode()
    )

    assert "بدل فاقد" in body
    assert number in body  # names the original
    assert new_number in body
    assert new_number != number


def test_printing_a_certificate_that_does_not_exist_is_a_404(signed_in, manager) -> None:
    """
    BR-075 needs no second check on this route.

    A Certificate row exists only because ``issue_certificate`` found a
    COMPLETED clearance. Printing reads what that refusal already permitted —
    it cannot conjure one that was never issued.
    """
    response = signed_in(manager).get(reverse("operations:certificate-print", args=["2026999999"]))
    assert response.status_code == 404


def test_the_cashier_cannot_print_a_certificate(
    signed_in, cashier, manager, finance, finance_manager, settled
) -> None:
    number = _issue(signed_in, manager, finance, finance_manager, settled)

    response = signed_in(cashier).get(reverse("operations:certificate-print", args=[number]))
    assert response.status_code == 403


def test_print_chrome_hides_the_navigation(
    signed_in, manager, finance, finance_manager, settled
) -> None:
    """The document is the page — the menu is not part of it."""
    number = _issue(signed_in, manager, finance, finance_manager, settled)
    body = (
        signed_in(manager)
        .get(reverse("operations:certificate-print", args=[number]))
        .content.decode()
    )

    assert 'class="no-print"' in body
    assert 'class="doc"' in body
