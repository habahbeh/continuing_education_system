"""
The opening-balances screen after its UX pass (BR-094 · D-24 · D-25).

Codes come from the system's yearly sequences (OB-… / PO-…); the tiles are
the stage filter; every act on a row is a dialog; the reviewer picks the
enrolment from a ranked list rather than typing it; D-24 hides the buttons
a person may not press before the service refuses them; and the archive's
link queue opens the proposal on its row.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.billing.models import OpeningBalance, OpeningBalanceDirection
from apps.billing.services import opening_balance_service as obs

pytestmark = pytest.mark.django_db

SCREEN = "billing:opening-balances"
PASSWORD = "x-pass-1234"
AS_OF = date(2025, 9, 1)


def _user(username, role):
    from apps.people.models import User

    return User.objects.create_user(username=username, password=PASSWORD, role=role, full_name_ar=username)


@pytest.fixture
def proposer(seeded_settings):
    from apps.people.models import Role

    return _user("fin.ob.a", Role.FINANCE_OFFICER)


@pytest.fixture
def reviewer(seeded_settings):
    from apps.people.models import Role

    return _user("fin.ob.b", Role.FINANCE_OFFICER)


@pytest.fixture
def approver(seeded_settings):
    from apps.people.models import Role

    return _user("mgr.ob", Role.CENTER_MANAGER)


@pytest.fixture
def live_enrollment(cohort_with_agreement, make_paid_enrollment):
    return make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")


def _cl(client, user):
    client.force_login(user)
    return client


def _propose(client, **extra):
    data = {
        "action": "propose",
        "source_row_id": "",
        "legacy_number": "2024/5/0012",
        "direction": "RECEIVABLE",
        "amount": "150.000",
        "as_of": "2025-09-01",
        "description_ar": "رصيد رسوم 2024",
    }
    data.update(extra)
    return client.post(reverse(SCREEN), data)


def test_codes_come_from_the_sequence_and_the_form_asks_for_none(client, proposer) -> None:
    assert _propose(_cl(client, proposer)).status_code == 302
    assert _propose(client, amount="10").status_code == 302
    assert sorted(OpeningBalance.objects.values_list("code", flat=True)) == [
        "OB-2025-00001",
        "OB-2025-00002",
    ]
    page = client.get(reverse(SCREEN)).content.decode("utf-8").split("</nav>", 1)[-1]
    assert 'name="code"' not in page.replace('name="code" value=', "")
    assert 'name="source_row_id"' in page and 'type="hidden" name="source_row_id"' in page
    assert 'x-ref="confirm"' in page and "alert(" not in page and "confirm(" not in page


def test_the_buttons_follow_d24_before_the_service_does(
    client, proposer, reviewer, approver, live_enrollment
) -> None:
    _propose(_cl(client, proposer))
    code = OpeningBalance.objects.get().code

    page = client.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="rv-{code}"' not in page and "اقترحته أنت" in page

    page = _cl(client, reviewer).get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="rv-{code}"' in page
    assert f'value="{live_enrollment.code}"' in page  # the reviewer picks, never types

    # A review without a note reopens its dialog and changes nothing.
    refused = client.post(
        reverse(SCREEN), {"action": "review", "code": code, "enrollment_code": "", "note_ar": " "}
    )
    assert refused.status_code == 200 and "$el.showModal()" in refused.content.decode("utf-8")
    assert client.post(
        reverse(SCREEN),
        {"action": "review", "code": code, "enrollment_code": live_enrollment.code, "note_ar": "قوبل"},
    ).status_code == 302

    # The reviewer holds no APPROVE; the approver who is a third person sees both dialogs.
    page = _cl(client, approver).get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="ap-{code}"' in page and f'id="rj-{code}"' in page and "fin.ob.b" in page
    assert client.post(reverse(SCREEN), {"action": "approve", "code": code, "note_ar": ""}).status_code == 302

    page = client.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="po-{code}"' in page and "سيُنشأ بند رسم" in page
    assert client.post(reverse(SCREEN), {"action": "post", "code": code}).status_code == 302
    assert OpeningBalance.objects.get().status == "POSTED"


def test_the_credit_path_runs_through_its_dialogs(
    client, proposer, reviewer, approver, live_enrollment
) -> None:
    _propose(_cl(client, proposer), direction="CREDIT", amount="30.000")
    code = OpeningBalance.objects.get().code
    _cl(client, reviewer).post(
        reverse(SCREEN),
        {"action": "review", "code": code, "enrollment_code": live_enrollment.code, "note_ar": "قوبل"},
    )
    _cl(client, approver).post(reverse(SCREEN), {"action": "approve", "code": code, "note_ar": ""})

    page = client.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="cf-{code}"' in page and f'id="rd-{code}"' in page
    assert client.post(
        reverse(SCREEN), {"action": "refund-due", "code": code, "note_ar": "لا تسجيل لاحق"}
    ).status_code == 302

    page = _cl(client, reviewer).get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="py-{code}"' in page and 'name="payout_code"' not in page
    response = client.post(
        reverse(SCREEN),
        {
            "action": "pay-refund",
            "code": code,
            "payment_method": "CASH",
            "paid_on": "2026-09-20",
            "external_reference": "PV-9",
            "payee_name_ar": "ليلى",
            "note_ar": "",
        },
    )
    assert response.status_code == 302
    balance = OpeningBalance.objects.get()
    assert balance.status == "REFUNDED"
    assert list(balance.refund_payouts.values_list("code", flat=True)) == ["PO-2026-00001"]

    page = client.get(reverse(SCREEN)).content.decode("utf-8")
    assert f'id="rx-{code}"' in page
    assert client.post(
        reverse(SCREEN),
        {"action": "reverse-refund", "code": code, "reason_ar": "سند مكرر", "reversal_reference": "", "reversed_on": "2026-09-20"},
    ).status_code == 302
    assert OpeningBalance.objects.get().status == "REFUND_DUE"


def test_tiles_and_search_follow_the_rows(client, proposer, reviewer, live_enrollment) -> None:
    _propose(_cl(client, proposer))
    _propose(client, legacy_number="2024/5/0099", description_ar="آخر")
    first = OpeningBalance.objects.order_by("id").first()
    obs.review(actor=reviewer, balance=first, enrollment=live_enrollment, note_ar="قوبل")

    response = client.get(reverse(SCREEN))
    tiles = {t["key"]: t["value"] for t in response.context["tiles"]}
    assert tiles["DRAFT"] == 1 and tiles["REVIEWED"] == 1
    drafts = client.get(reverse(SCREEN), {"status": "DRAFT"})
    assert [b["status"] for b in drafts.context["balances"]] == ["DRAFT"]
    by_legacy = client.get(reverse(SCREEN), {"q": "0099"})
    assert len(by_legacy.context["balances"]) == 1
    miss = client.get(reverse(SCREEN), {"q": "zzz"})
    assert miss.context["balances"] == [] and "لا أرصدة تطابق التصفية" in miss.content.decode("utf-8")
    assert 'hx-get="' + reverse(SCREEN) in response.content.decode("utf-8")


def test_the_enrolment_options_rank_the_attached_participant_first(
    proposer, reviewer, live_enrollment
) -> None:
    balance = obs.propose_manually(
        actor=proposer,
        direction=OpeningBalanceDirection.RECEIVABLE,
        amount=Decimal("10"),
        as_of=AS_OF,
        description_ar="x",
        legacy_number=live_enrollment.participant.participant_number,
    )
    options = obs.enrollment_options(actor=reviewer, balance=balance)
    assert options and options[0]["code"] == live_enrollment.code and options[0]["rank"] == 1


def test_readers_without_create_get_no_form(client, approver) -> None:
    response = _cl(client, approver).get(reverse(SCREEN))
    assert response.status_code == 200 and response.context["form"] is None
    assert 'id="propose-form"' not in response.content.decode("utf-8")
