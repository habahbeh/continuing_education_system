"""
The one-paragraph explanation that sits above a screen (Sprint 8K-5).

The demo taught as it went: every screen carried a note saying what it was for,
who used it and what came next. The real system had the screens and not the
teaching, so a new employee met a correct form with no idea which button was
theirs.

**Content lives here, not in twenty-three templates.** One registry means the
wording can be reviewed in one sitting, and a rule quoted on two screens cannot
say two different things.

**Links are filtered, prose is not.** ``next`` links go through
``policy.is_allowed`` exactly as the sidebar does, so the guide never points a
reader at a refusal (BR-085 would log the attempt). The explanation itself is
never filtered: a registrar who may not open the till still has to know that a
payment happens and who takes it, or the guide stops guiding the people who
most need it.

**Short on purpose.** One sentence of what, one line of who and next, and a
stopping rule only where a rule genuinely refuses. Anything longer belongs on
the screen, not above it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.people.constants import Action, Screen
from apps.people.permissions import policy


@dataclass(frozen=True)
class Guide:
    """One screen's help. ``links`` are (screen, route, label) and are filtered."""

    what: Any
    who: Any
    after: Any = ""
    links: tuple[tuple[str, str, Any], ...] = ()
    #: A rule that REFUSES, in the reader's words. Absent on most screens.
    stops: Any = ""
    #: (label, chip) for a screen that is not a working form — read-only or a
    #: guide. Absent means "this screen does what it looks like it does".
    status: tuple[Any, str] | None = None


_GUIDED = (_("صفحة إرشادية"), "brand")
_READ_ONLY = (_("قراءة فقط"), "info")

#: Keyed by a stable id rather than by Screen: «مصفوفة التغطية» and «النطاق
#: المستقبلي» are both guarded by Screen.SETTINGS and are not the same screen.
GUIDES: dict[str, Guide] = {
    "dashboard": Guide(
        what=_("مؤشرات اليوم كما هي في الحركة الفعلية، لا أرقاماً تُدخَل يدوياً."),
        who=_("كل الأدوار، ولكلٍّ منها ما تسمح به صلاحيته."),
        after=_("إن كنت جديداً فابدأ من خريطة المسار."),
        links=((Screen.ENROLL_FLOW, "operations:enroll-flow", _("مسار التسجيل والدفع")),),
    ),
    "participants": Guide(
        what=_("سجل المشاركين: بحث، وفتح ملف، وتعديل بيانات."),
        who=_("موظف التسجيل ومدير المركز."),
        after=_("لتسجيل مشارك جديد ابدأ بطلب التحاق."),
        links=((Screen.STUDENT_NEW, "people:participant-new", _("طلب التحاق جديد")),),
        stops=_("الحقل الذي لا تسمح به صلاحيتك غائب عن الجدول أصلاً، لا مخفيّ (BR-101)."),
    ),
    "participant-new": Guide(
        what=_("نموذج طلب الالتحاق: بيانات المشارك وفئته ومؤهله."),
        who=_("موظف التسجيل، أو مدير المركز."),
        after=_("بعد الحفظ يُنشأ التسجيل على دفعة معتمدة."),
        links=((Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),),
        stops=_(
            "الرقم الجامعي يُولَّد مرة واحدة ولا يُصحَّح لاحقاً، ويرفض النظام توليده "
            "إن لم يكن هناك فصل نشط (BR-001)."
        ),
    ),
    "enrollments": Guide(
        what=_("تسجيلات المشاركين على الدفعات: إنشاء ومتابعة واعتماد."),
        who=_("موظف التسجيل يُنشئ ويعدّل، ومدير المركز يعتمد."),
        after=_("بعد الاعتماد تُستوفى الدفعة في الصندوق."),
        links=(
            (Screen.PAYMENT_NEW, "cashbox:payment-new", _("استيفاء دفعة")),
            (Screen.TRANSFERS, "operations:transfers", _("النقل بين الدورات")),
        ),
        stops=_(
            "لا تسجيل على دفعة لم تعتمدها الوزارة (BR-013)، ولا اعتماد قبل تسجيل الوصل (BR-018)."
        ),
    ),
    "enroll-flow": Guide(
        what=_("خريطة الرحلة كاملة، من طلب الالتحاق حتى الشهادة."),
        who=_("كل موظف جديد يبدأ من هنا."),
        after=_("افتح الشاشة المسؤولة عن كل مرحلة من زرّها."),
        status=_GUIDED,
    ),
    "payments": Guide(
        what=_("سجل سندات القبض الصادرة، بقيمها وتخصيصاتها وحالاتها."),
        who=_("الصندوق يطلب الإلغاء، والموظف المالي يعتمده."),
        after=_("في آخر اليوم يُعدّ الصندوق ويُقفل."),
        links=(
            (Screen.PAYMENT_NEW, "cashbox:payment-new", _("استيفاء دفعة")),
            (Screen.CLOSING, "cashbox:closing", _("الإقفال اليومي")),
        ),
        stops=_("لا يُعدَّل سند صادر: الإلغاء يكتب قيداً عكسياً ويُبقي الأصل (BR-025)."),
    ),
    "payment-new": Guide(
        what=_("قبض مبلغ وتوزيعه على بنود الرسوم، ثم إصدار سند القبض."),
        who=_("أمين الصندوق، أو الموظف المالي."),
        after=_("يظهر السند في سجل الدفعات."),
        links=((Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),),
        stops=_("للدبلوم حدّ أدنى للدفعة الأولى تُرفض دونه (BR-020)."),
    ),
    "transfers": Guide(
        what=_("طلبات النقل بين الدورات: فحصها واعتمادها وتنفيذها."),
        who=_("موظف التسجيل يفتح الطلب، ومدير المركز يعتمده (BR-066)."),
        after=_("بعد التنفيذ تعود المتابعة إلى التسجيلات."),
        links=((Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),),
        stops=_(
            "النقل لا يعدّل قيداً مالياً ولا يحذفه: البنود تُلغى وتُعاد، والتخصيصات "
            "تنتقل كزوج عكسي (BR-060 … BR-066)."
        ),
    ),
    "transfer-new": Guide(
        what=_("طلب نقل مشارك من دورة إلى أخرى، مع «فحص الشروط» قبل التقديم."),
        who=_("موظف التسجيل، أو مدير المركز."),
        after=_("يُعتمد الطلب ويُنفَّذ من شاشة النقل."),
        links=((Screen.TRANSFERS, "operations:transfers", _("النقل بين الدورات")),),
        stops=_(
            "«فحص الشروط» يعرض القرار وفرق الرسوم بلا أي كتابة، ومهلة النقل محدودة "
            "بعدد المحاضرات (BR-062)."
        ),
    ),
    "special-cases": Guide(
        what=_("الاستثناءات الموثّقة على مسار التسجيل: أنواعها وقواعدها وأثرها المالي."),
        who=_("مدير المركز وموظف التسجيل."),
        after=_("أثر الحالة يظهر على التسجيل وعلى براءة الذمة."),
        links=(
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.CLEARANCE, "operations:clearances", _("براءة الذمة")),
        ),
        status=_GUIDED,
    ),
    "clearance": Guide(
        what=_("إخلاء طرف المشارك، على ثلاث خطوات لا يُعاد ترتيبها."),
        who=_("المركز في الخطوتين الأولى والثالثة، والمالية في الثانية."),
        after=_("باكتمالها تُصدَر الشهادة."),
        links=((Screen.CERTIFICATES, "operations:certificates", _("الشهادات")),),
        stops=_(
            "الرصيد يجب أن يكون صفراً في الاتجاهين: الرصيد الدائن يوقف البراءة كما "
            "يوقفها الدَّين (BR-073)."
        ),
    ),
    "certificates": Guide(
        what=_("إصدار الشهادات وإعادة طباعتها بأرقامها."),
        who=_("مدير المركز."),
        after=_("بها ينتهي مسار المشارك."),
        links=((Screen.CLEARANCE, "operations:clearances", _("براءة الذمة")),),
        stops=_("لا شهادة بلا براءة ذمة مكتملة (BR-075)."),
    ),
    "partners": Guide(
        what=_("سجل الشركاء المتعاقدين مع المركز."),
        who=_("مدير المركز والموظف المالي."),
        after=_("بعد تسجيل الشريك تُسجَّل اتفاقيته."),
        links=((Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),),
    ),
    "agreements": Guide(
        what=_("اتفاقيات الشركاء: نموذج الاحتساب، والاستثناءات، ولقطة الأسعار."),
        who=_("مدير المركز يُنشئ ويعتمد."),
        after=_("على الاتفاقية تُبنى مطالبة الشريك."),
        links=((Screen.ENTITLEMENT, "settlements:entitlement", _("استحقاق الشركاء")),),
        stops=_("لا تُعدَّل اتفاقية موقّعة؛ التصحيح يكون بملحق يحلّ محلّها (BR-042)."),
    ),
    "agreement-new": Guide(
        what=_("تسجيل اتفاقية موقّعة بنموذج احتسابها واستثناءاتها."),
        who=_("مدير المركز."),
        after=_("تظهر بعدها في سجل الاتفاقيات."),
        links=((Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),),
        stops=_(
            "الاستثناءات تأتي من العقد الموقّع لا من النظام، والتأمينات مستثناة "
            "افتراضياً ما لم ينص العقد على غيرها (BR-046 · BR-092)."
        ),
    ),
    "entitlement": Guide(
        what=_("كيف يصير الشريك مستحقاً: من الاتفاقية حتى المخالصة."),
        who=_("مدير المركز والموظف المالي."),
        after=_("الحساب الفعلي يجري على شاشة المطالبات."),
        links=((Screen.CLAIMS, "settlements:claims", _("المطالبات")),),
        status=_GUIDED,
    ),
    "claims": Guide(
        what=_("بناء مطالبة الشريك عن فترة، ثم اعتمادها."),
        who=_("الموظف المالي يُنشئ، ومدير المركز يعتمد."),
        after=_("بعد الاعتماد تُخالَص الفترة."),
        links=(
            (Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات")),
            (Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
        ),
        stops=_("لا يعدّل أحد مطالبة معتمدة (BR-051)."),
    ),
    "settlements": Guide(
        what=_("مخالصة الفترة مع الشريك، والتوقيع النهائي عليها."),
        who=_("الموظف المالي يُنشئ، ومدير المركز يعتمد."),
        after=_("بها ينتهي أثر الفترة مع هذا الشريك."),
        links=((Screen.CLAIMS, "settlements:claims", _("المطالبات")),),
    ),
    "obligations": Guide(
        what=_("ما على الشريك: غرامات الغياب والدفعات المقدّمة المستردّة."),
        who=_("مدير المركز والموظف المالي."),
        after=_("تُخصم من مطالبة لاحقة، ولا يُطالَب بها بفاتورة (BR-036)."),
        links=((Screen.CLAIMS, "settlements:claims", _("المطالبات")),),
    ),
    "reports": Guide(
        what=_("التقارير السبعة، ولكل دور ما يُسمح له منها."),
        who=_("المدير والمالية وحساب التدقيق؛ والصندوق لا يرى التقارير (BR-083)."),
    ),
    "settings": Guide(
        what=_("مفاتيح النظام التي تحكم القواعد، والقرارات التي ما زالت مفتوحة."),
        who=_("مدير المركز والموظف المالي وحساب التدقيق."),
        after=_("البنود خارج النطاق الحالي موضعها صفحة النطاق المستقبلي."),
        links=((Screen.SETTINGS, "people:future", _("النطاق المستقبلي")),),
        status=_READ_ONLY,
    ),
    "coverage": Guide(
        what=_("كل بند من قائمة الديمو، مقابل عنوانه في النظام وحالته."),
        who=_("العميل وفريق المراجعة عند التسليم."),
        after=_("البنود المؤجَّلة موضعها صفحة النطاق المستقبلي."),
        links=((Screen.SETTINGS, "people:future", _("النطاق المستقبلي")),),
        status=_READ_ONLY,
    ),
    "future": Guide(
        what=_("بنود موثّقة خارج نطاق التسليم الحالي، وسبب تأجيل كل واحد منها."),
        who=_("العميل وفريق المراجعة."),
        after=_("ما هو داخل النطاق تجده في مصفوفة التغطية."),
        links=((Screen.SETTINGS, "people:coverage", _("مصفوفة تغطية المتطلبات")),),
        status=_READ_ONLY,
    ),
}


def guide_for(user: Any, key: str) -> dict[str, Any] | None:
    """
    This screen's help, with its next-step links cut to what ``user`` may open.

    Returns ``None`` for a key with no entry, so a screen without help renders
    nothing rather than an empty box.
    """
    guide = GUIDES.get(key)
    if guide is None:
        return None

    links: list[dict[str, Any]] = []
    for screen, route, label in guide.links:
        if not policy.is_allowed(user, screen, Action.VIEW):
            continue
        try:
            url = reverse(route)
        except NoReverseMatch:  # pragma: no cover - a route removed upstream
            continue
        links.append({"url": url, "label": label})

    return {
        "what": guide.what,
        "who": guide.who,
        "after": guide.after,
        "stops": guide.stops,
        "status": guide.status,
        "links": links,
    }


__all__ = ["GUIDES", "Guide", "guide_for"]
