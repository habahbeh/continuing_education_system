"""
أمر زرع الحركة التي تُنطق التقارير (Sprint 8L-12).

يُختبر هنا في قاعدة الاختبار ويُترك للمستخدم ليشغّله على قاعدته: الزرع يكتب
صفوفاً مالية حقيقية، وقاعدة العرض على العميل ليست موضع تجربة.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles(seeded_settings):  # type: ignore[no-untyped-def]
    from apps.people.models import Role, User

    return (
        User.objects.create_user(username="seed.fin", password="x", role=Role.FINANCE_OFFICER),
        User.objects.create_user(username="seed.mgr", password="x", role=Role.CENTER_MANAGER),
    )


def _run(**kwargs) -> str:  # type: ignore[no-untyped-def]
    out = StringIO()
    call_command("seed_reports_demo", stdout=out, **kwargs)
    return out.getvalue()


def test_it_refuses_to_seed_without_the_two_roles(seeded_settings) -> None:
    """
    زرعٌ بفاعلٍ خاطئ يكتب في سجل التدقيق أن المدير فعل ما لا يفعله المدير.

    الموظف المالي يبني ويدفع، ومدير المركز يعتمد ويوقّع — وفصلُ الأدوار قاعدةٌ
    لا تُخرق لأجل بيانات عرض.
    """
    assert "لا مستخدم بدور" in _run()


def test_the_dry_run_writes_nothing(roles) -> None:
    from apps.expenses.models import Expense

    output = _run(dry_run=True)

    assert "تشغيل تجريبي" in output
    assert not Expense.objects.exists()


def test_it_seeds_four_expense_categories_not_one(roles) -> None:
    """تقريرٌ بتصنيف واحد يُظهر شريطاً بنسبة ١٠٠٪، وهو رسمٌ لا معلومة."""
    from apps.expenses.models import Expense

    _run()

    assert Expense.objects.count() == 5
    assert Expense.objects.values("category").distinct().count() == 4


def test_it_leaves_one_expense_awaiting_approval_on_purpose(roles) -> None:
    """التقرير السابع يفصل المعتمَد عن المنتظر، وشاشةٌ كلّها معتمدة لا تُظهر الفرق."""
    from apps.expenses.models import Expense

    _run()

    statuses = set(Expense.objects.values_list("status", flat=True))
    assert len(statuses) > 1, f"كلها بحالة واحدة: {statuses}"


def test_running_it_twice_does_not_double_anything(roles) -> None:
    from apps.expenses.models import Expense

    _run()
    first = Expense.objects.count()
    second_output = _run()

    assert Expense.objects.count() == first
    assert "تُرك 5" in second_output


def test_it_says_what_is_missing_instead_of_seeding_half(roles) -> None:
    """بلا دفعةٍ على اتفاقية لا مطالبة — ويقولها بدل أن يصمت."""
    output = _run()

    assert "لا دفعة مرتبطة باتفاقية" in output or "المطالبات" in output


def test_it_fills_the_third_report_which_was_the_empty_one(
    roles, cohort_with_agreement, make_paid_enrollment
) -> None:
    """
    التقرير الثالث كان يُفتح على لا شيء في قاعدة الديمو — صفر مطالبات وصفر
    مخالصات وصفر التزامات — وهو التقرير الذي يشرح نموذج العمل كلّه لمن يُعرض
    عليه النظام.
    """
    from datetime import date

    from apps.reporting.services import report_service
    from apps.settlements.models import PartnerClaim, PartnerSettlement

    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    _run()

    assert PartnerClaim.objects.exists(), "لا مطالبة — والتقرير الثالث يبقى فارغاً"
    assert PartnerSettlement.objects.exists()

    finance, _manager = roles
    report = report_service.partner_dues_report(actor=finance)
    assert report["partners"], "صفّ لكل شريك"
    assert report["claims_total"] > 0


def test_the_claim_is_built_through_the_service_so_the_audit_names_a_real_actor(
    roles, cohort_with_agreement, make_paid_enrollment
) -> None:
    """زرعٌ يدور حول الخدمات يزرع صفوفاً لم ترَها قاعدةٌ قطّ."""
    from apps.core.models import AuditEvent
    from apps.settlements.models import ClaimStatus, PartnerClaim

    make_paid_enrollment(cohort_with_agreement, index=1, amount="270.000")
    finance, manager = roles
    _run()

    claim = PartnerClaim.objects.first()
    assert claim is not None
    assert claim.status in (ClaimStatus.APPROVED, ClaimStatus.PAID), "تُعتمد لا تُترك مسودّة"
    assert claim.created_by_id == finance.pk, "الموظف المالي يبني"
    assert AuditEvent.objects.filter(entity_type__icontains="Claim").exists()
