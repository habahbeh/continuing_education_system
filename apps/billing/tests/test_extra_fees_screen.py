"""
The extra-fees screen after its UX pass (§5.5 · BR-037 … BR-040).

The type cards carry the seeded amount and the sharing rule the service
charges with; only the fields the chosen type needs are asked; the
international exam cannot be posted without the prior agreement; «أخرى»
needs a yes/no share decision, never «مجهول»; the register carries tiles,
live search and who charged each fee; and charging goes through a dialog.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.billing.models import ExtraFee
from apps.billing.services import extra_fee_service

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 20)


@pytest.fixture
def enrollment(make_enrollment):
    return make_enrollment()[0]


def _post(client, enrollment, **fields):
    data = {
        "enrollment_code": enrollment.code,
        "fee_type": "SUBJECT_REPEAT",
        "amount": "",
        "subject_name": "",
        "charged_on": "2026-09-20",
        "is_partner_shareable": "",
    }
    data.update(fields)
    return client.post(reverse("billing:extra-fees"), data)


def test_the_type_cards_carry_the_seeded_amount_and_the_sharing_rule(client, finance) -> None:
    cards = {c["code"]: c for c in extra_fee_service.fee_type_cards(as_of=DAY)}
    assert cards["SUBJECT_REPEAT"]["default_amount"] == Decimal("75.000")
    assert cards["SUBJECT_REPEAT"]["shareable"] is True and cards["SUBJECT_REPEAT"]["subject"]
    assert cards["CERTIFICATE_REPLACEMENT"]["default_amount"] == Decimal("15.000")
    assert cards["INTERNATIONAL_EXAM"]["default_amount"] is None
    assert cards["INTERNATIONAL_EXAM"]["agreement"] and cards["OTHER"]["decision"]

    client.force_login(finance)
    page = client.get(reverse("billing:extra-fees")).content.decode("utf-8")
    assert 'data-amount="75.000"' in page and 'data-share="1"' in page
    assert "مجهول" not in page
    assert "alert(" not in page and "confirm(" not in page and 'x-ref="confirm"' in page


def test_a_repeat_fee_takes_the_seeded_amount_and_names_its_subject(
    client, finance, enrollment
) -> None:
    client.force_login(finance)
    refused = _post(client, enrollment, subject_name="")
    assert refused.status_code == 200 and not ExtraFee.objects.exists()
    assert "يسمّي المادة" in refused.content.decode("utf-8")

    assert _post(client, enrollment, subject_name="شبكات 2").status_code == 302
    fee = ExtraFee.objects.get()
    assert fee.amount == Decimal("75.000") and fee.is_partner_shareable is True
    assert fee.subject_name == "شبكات 2"


def test_an_international_exam_needs_the_prior_agreement_before_posting(
    client, finance, enrollment
) -> None:
    client.force_login(finance)
    refused = _post(client, enrollment, fee_type="INTERNATIONAL_EXAM", amount="120")
    assert refused.status_code == 200 and not ExtraFee.objects.exists()
    assert "BR-040" in refused.content.decode("utf-8")

    ok = _post(
        client,
        enrollment,
        fee_type="INTERNATIONAL_EXAM",
        amount="120",
        prior_agreement_with_participant="on",
    )
    assert ok.status_code == 302
    fee = ExtraFee.objects.get()
    assert fee.prior_agreement_with_participant and fee.is_partner_shareable is False


def test_other_needs_a_plain_share_decision(client, finance, enrollment) -> None:
    client.force_login(finance)
    refused = _post(client, enrollment, fee_type="OTHER", amount="10")
    assert refused.status_code == 200 and not ExtraFee.objects.exists()
    assert "قرّر" in refused.content.decode("utf-8")

    assert _post(client, enrollment, fee_type="OTHER", amount="10", is_partner_shareable="false").status_code == 302
    assert ExtraFee.objects.get().is_partner_shareable is False


def test_tiles_search_and_type_filter_follow_the_rows(client, finance, enrollment) -> None:
    client.force_login(finance)
    _post(client, enrollment, subject_name="شبكات 2")
    _post(client, enrollment, fee_type="CERTIFICATE_REPLACEMENT")

    response = client.get(reverse("billing:extra-fees"))
    tiles = {t["key"]: (t["value"], t["foot"]) for t in response.context["tiles"]}
    assert tiles["SUBJECT_REPEAT"] == (1, Decimal("75.000"))
    assert tiles["CERTIFICATE_REPLACEMENT"] == (1, Decimal("15.000"))

    only = client.get(reverse("billing:extra-fees"), {"type": "SUBJECT_REPEAT"})
    assert [f["fee_type"] for f in only.context["fees"]] == ["SUBJECT_REPEAT"]
    by_subject = client.get(reverse("billing:extra-fees"), {"q": "شبكات"})
    assert len(by_subject.context["fees"]) == 1
    miss = client.get(reverse("billing:extra-fees"), {"q": "zzz"})
    assert miss.context["fees"] == [] and "لا رسوم تطابق التصفية" in miss.content.decode("utf-8")

    page = response.content.decode("utf-8")
    assert 'hx-get="' + reverse("billing:extra-fees") in page
    assert reverse("operations:account", args=[enrollment.code]) in page


def test_a_cancelled_enrolment_is_not_offered(client, finance, enrollment) -> None:
    assert [r["code"] for r in extra_fee_service.chargeable_rows(actor=finance)] == [enrollment.code]
    enrollment.status = "CANCELLED"
    enrollment.save(update_fields=["status"])
    assert extra_fee_service.chargeable_rows(actor=finance) == []


def test_the_account_page_opens_the_form_on_its_enrolment(client, finance, enrollment) -> None:
    client.force_login(finance)
    account = client.get(reverse("operations:account", args=[enrollment.code])).content.decode("utf-8")
    assert f"?enrollment={enrollment.code}#charge" in account
    response = client.get(reverse("billing:extra-fees"), {"enrollment": enrollment.code, "type": "OTHER"})
    assert response.context["form"]["enrollment_code"].value() == enrollment.code
    assert response.context["form"]["fee_type"].value() == "OTHER"
    assert response.context["form"]["charged_on"].value() == date.today()


def test_readers_without_create_get_no_form(client, registrar, enrollment) -> None:
    client.force_login(registrar)
    response = client.get(reverse("billing:extra-fees"))
    assert response.status_code == 200 and response.context["candidates"] == []
    assert 'id="fee-form"' not in response.content.decode("utf-8")
    assert client.post(reverse("billing:extra-fees"), {"enrollment_code": enrollment.code}).status_code == 403
