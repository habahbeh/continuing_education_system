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
        what=_("نموذج طلب الالتحاق في خمسة أقسام: الفئة، والبيانات، والاتصال، والمؤهل، والتعهّد."),
        who=_("موظف التسجيل، أو مدير المركز."),
        after=_("بعد الحفظ يُنشأ التسجيل على دفعة معتمدة."),
        links=((Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),),
        stops=_("لا حفظ بلا الإقرار بالتعهّد، ولا رقم جامعي بلا فصل دراسي نشط (BR-001 · BR-003)."),
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
    "receipt-detail": Guide(
        what=_("سند قبض واحد: قيمته وطريقته وتخصيصاته على بنود الرسوم، وما جرى عليه من طلب إلغاء."),
        # Same split, same words as the register's guide: the rule is one rule,
        # and two screens saying it two ways is how a reader learns two rules.
        who=_("أمين الصندوق يطلب الإلغاء، والموظف المالي يعتمده، وبقية الأدوار تقرأ."),
        after=_("السند يظهر في سجل الدفعات، ويدخل إقفال يوم الصندوق."),
        links=(
            (Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),
            (Screen.CLOSING, "cashbox:closing", _("الإقفال اليومي")),
        ),
        stops=_(
            "لا يُعدَّل سند صادر ولا يُحذف: الإلغاء يكتب قيداً عكسياً ويُبقي الأصل برقمه "
            "(BR-025). والتصحيح بسند جديد أو بإلغاء، لا بتعديل هذا السند ولا ببيانات "
            "المشارك ولا برسومه."
        ),
    ),
    "cashbox-closing": Guide(
        what=_("إقفال يوم الصندوق: الإجمالي النظامي والمعدود والفرق بينهما، وحالة كل إقفال."),
        who=_("أمين الصندوق يفتح إقفال يومه ويُدخل المعدود، والموظف المالي أو مدير المركز يعتمده."),
        after=_("السندات التي يضمّها الإقفال تُقرأ في سجل الدفعات."),
        links=((Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),),
        # BR-027 is printed on the page itself one line below this block, so it
        # is not repeated here. BR-028 is said nowhere on the screen — the
        # approve button is simply absent for the cashier — and a rule a reader
        # meets only as a missing button is a rule they never learn.
        stops=_(
            "لا يعتمد أمين الصندوق إقفال يومه: الاعتماد لغيره (BR-028). والتصحيح يكون على "
            "السند نفسه في سجل الدفعات، لا على ملخّص الإقفال."
        ),
    ),
    "discounts": Guide(
        what=_("الخصومات على الرسوم الدراسية: مبلغ كل خصم ووعاؤه وحصّة الجامعة والشريك منه."),
        who=_("مدير المركز يمنح الخصم، ويعتمده مديرٌ آخر؛ الموظف المالي وحساب التدقيق يقرآن."),
        after=_("أثر الخصم يظهر في رصيد التسجيل، ويُقبض المتبقّي في الصندوق."),
        links=(
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),
        ),
        # Three refusals, and each one is met as a message the operator cannot
        # predict from the form: the president's reference is a plain text
        # field, the ordering rule is invisible until a payment exists, and
        # D-18 hides the button rather than explaining it.
        stops=_(
            "الخصم على الرسوم الدراسية وحدها ولا يمسّ رسم التسجيل (§5.1 · BR-029)، ولا "
            "يُمنح بلا رقم موافقة رئيس الجامعة (BR-030 · D-31)، ولا بعد قبض مبلغ عليها. "
            "ولا يعتمد الخصمَ من أنشأه (D-18)."
        ),
    ),
    "refunds": Guide(
        what=_("حركتان لا واحدة: الاسترداد الاستثنائي بوثائقه ومراحله، وردّ الرصيد الدائن المستقل."),
        who=_("الموظف المالي يطلب الاسترداد وينفّذه، ومدير المركز يعتمده أو يرفضه."),
        after=_("أثر التنفيذ يظهر في رصيد التسجيل، وحصّة الشريك تعود في مطالباته."),
        links=(
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.CLAIMS, "settlements:claims", _("مطالبات الشركاء")),
        ),
        # The screen's own separation, said as a rule rather than left to be
        # inferred from two tables: the light case must not inherit the heavy
        # process, which is the mistake one merged form would invite.
        stops=_(
            "الأصل لا استرداد (§5.3): الاستثناء يحتاج كتاباً رسمياً وموافقة رئيس الجامعة "
            "معاً (BR-034)، ولا يعتمده من طلبه (D-18)، ولا يُنفَّذ قبل اعتماده. أمّا ردّ "
            "الرصيد الدائن فلا يحتاج شيئاً من ذلك (BR-071)."
        ),
    ),
    "extra-fees": Guide(
        what=_("الرسوم المحمَّلة خارج رسوم البرنامج: نوع كل رسم ومبلغه وبنده في حساب المشارك."),
        who=_("مدير المركز والموظف المالي يحمّلان الرسم، وموظف التسجيل وحساب التدقيق يقرآن."),
        after=_("الرسم يصير بنداً في حساب المشارك، ويُقبض مع بقية رسومه في الصندوق."),
        links=(
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.PAYMENTS, "cashbox:payments", _("الدفعات وسندات القبض")),
        ),
        # Who shares a fee is §5.5's answer and not the person's who enters it,
        # which the form's own «قابل للقسمة» field makes easy to misread as a
        # choice. Said here rather than left to the refusal message.
        stops=_(
            "رسم الامتحان الدولي لا يُحمَّل بلا اتفاق مسبق مع المشارك (BR-040 · §5.5)، "
            "والرسم من نوع «أخرى» يحتاج قراراً صريحاً بقابليته للقسمة. أمّا بقية "
            "الأنواع فقسمتها تقرّرها القاعدة لا مُدخِل الرسم."
        ),
    ),
    "expenses": Guide(
        what=_("ما أنفقه المركز، مقيَّداً بتصنيفه وتاريخه ودفعته، وحالة اعتماد كل قيد."),
        who=_("الموظف المالي يقيّد المصروف، ومدير المركز يعتمده أو يرفضه."),
        after=_("المعتمَد وحده يُطرح من صافي دخل المركز في تقارير الدخل."),
        links=(
            (Screen.REPORTS, "reporting:reports", _("التقارير")),
            (Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
        ),
        # What an expense is NOT is the rule people get wrong here: a partner's
        # obligation and a consumable charged to a participant both look like
        # money and are neither of them a cost to subtract.
        stops=_(
            "لا يُخصم من صافي الدخل إلا المصروف المعتمَد، ولا يعتمد المصروفَ من قيّده "
            "(D-18). والمصروف ليس التزام شريك ولا مستهلكات محمَّلة على المشارك — تلك "
            "إيراد لا تكلفة."
        ),
    ),
    "opening-balances": Guide(
        what=_("أرصدة الأرشيف التاريخي في طريقها إلى الدفتر، وما بلغ الترحيل منها فعلاً."),
        who=_("الموظف المالي يقترح ويراجع ويصرف، ومدير المركز يعتمد ويقرّر مصير الرصيد الدائن."),
        after=_("المُرحَّل وحده يصير بنداً في حساب المشارك، والذمة القديمة تُقرأ في براءة الذمة."),
        links=(
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
            (Screen.CLEARANCE, "operations:clearances", _("براءة الذمة")),
        ),
        # The mistake this screen invites is reading an approval as a posting:
        # three of the four hands move nothing at all, and the screen shows
        # them side by side precisely so that stays visible.
        stops=_(
            "لا يُنشَأ بند رسم إلا بالترحيل: الاقتراح والمراجعة والاعتماد لا تُحرّك في "
            "الدفتر شيئاً (BR-094). ولا يجمع شخص واحد دورين، ولا يُقبل صرف ولا عكسه "
            "بتاريخ في فترة مقفلة ولا في المستقبل."
        ),
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
    # --- §3.3 البرامج والأسعار ---------------------------------------------
    # Three rows of the matrix that differ only by programme type, so three
    # guides that differ only where the type actually changes the rule. None of
    # them is a form: the catalogue has no data-entry service behind it, which
    # is what `_READ_ONLY` says out loud.
    "programs": Guide(
        what=_("الدبلومات المعرَّفة في الكتالوج: رموزها وساعاتها ومجالاتها وحالة تفعيلها."),
        who=_("مدير المركز يعرّفها، وبقية الأدوار تقرأ."),
        after=_("السعر يُقرأ من قائمة الأسعار السارية، والتشغيل يبدأ بدفعة."),
        links=(
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
        ),
        stops=_("مجموع أسعار مواد الدبلوم يجب أن يطابق رسوم الدورة قبل الاعتماد (BR-006)."),
        status=_READ_ONLY,
    ),
    "short-courses": Guide(
        what=_("الدورات القصيرة المعرَّفة في الكتالوج: رموزها وساعاتها ومجال كل دورة."),
        who=_("مدير المركز يعرّفها، وبقية الأدوار تقرأ."),
        after=_("السعر يُقرأ من قائمة الأسعار السارية، والتشغيل يبدأ بدفعة."),
        links=(
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
        ),
        stops=_("مجال الدورة يحدّ النقل: لا نقل خارج المجال إلا باستثناء (BR-061)."),
        status=_READ_ONLY,
    ),
    "online-courses": Guide(
        what=_("الدورات الأونلاين المعرَّفة في الكتالوج: رموزها وساعاتها وحالة تفعيلها."),
        who=_("مدير المركز يعرّفها، وبقية الأدوار تقرأ."),
        after=_("السعر يُقرأ من قائمة الأسعار السارية، والتشغيل يبدأ بدفعة."),
        links=(
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
        ),
        status=_READ_ONLY,
    ),
    # --- §3.3 الأسعار والتشغيل واعتماد الوزارة ------------------------------
    # The programme card, the dated price list, the cohort that opens on it and
    # the ministry file that unlocks enrolment on it: one chain, and each guide
    # hands the reader on to the next link of it. Each guide says
    # what its own screen decides and, more usefully, what it does not: which
    # list applies, whether a blank deposit is a zero, and whether a file may
    # be sent are all answered elsewhere, and saying so is the whole teaching.
    "program-detail": Guide(
        what=_("بطاقة برنامج واحد في الكتالوج: بياناته الأساسية، ومواده حين تكون له مواد."),
        who=_("مدير المركز والموظف المالي وموظف التسجيل، قراءةً."),
        after=_(
            "التسعير في قوائم الأسعار المؤرّخة، والتشغيل يبدأ بدفعة لا يُسجَّل عليها "
            "قبل اعتماد الوزارة (BR-013)."
        ),
        links=(
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
            (Screen.MOHE, "operations:mohe", _("اعتماد الوزارة")),
        ),
        stops=_(
            "لا تعديل من هذه الشاشة ولا فتح دفعة ولا ملف وزاري. والسعر الذي يُطبَّق تحسمه "
            "خدمة التسعير من قائمة الأسعار بتاريخ الواقعة (BR-012)، والمواد المعروضة لا "
            "تُصدر بذاتها حكماً مالياً."
        ),
        status=_READ_ONLY,
    ),
    "pricelists": Guide(
        what=_("سجل قوائم الأسعار المؤرّخة: أي قائمة، لأي فصل، صدرت متى وتسري متى."),
        who=_("مدير المركز يعرّفها، وبقية الأدوار تقرأ."),
        after=_("بنود كل قائمة — رسوم الدورة والتأمين ورسم التسجيل — في صفحة القائمة نفسها."),
        links=(
            (Screen.PROGRAMS, "catalog:programs", _("الدبلومات")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
        ),
        stops=_(
            "القائمة المعتمدة لا تُعدَّل؛ التغيير بإصدار قائمة جديدة (BR-008). وهذه الشاشة "
            "تؤرّخ ولا تحسم أيّ قائمة تنطبق: ذلك يُحسم بتاريخ الواقعة في خدمة التسعير (BR-012)."
        ),
        status=_READ_ONLY,
    ),
    "pricelist-detail": Guide(
        what=_("بنود قائمة أسعار واحدة: ما يُسعَّر به كل برنامج، وما عليه من تأمين ومن رسم تسجيل."),
        who=_("مدير المركز والموظف المالي وموظف التسجيل، قراءةً."),
        after=_("بقية القوائم وتأريخها في سجل قوائم الأسعار."),
        links=(
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
            (Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),
        ),
        stops=_(
            "الفراغ ليس صفراً: تأمين فارغ يعني ألّا تأمين على البرنامج (BR-096)، ورسم فارغ "
            "يعني ألّا رسم تسجيل يُستوفى (BR-009). ولا تُقارَن هنا أسعار المواد برسوم الدورة."
        ),
        status=_READ_ONLY,
    ),
    "cohorts": Guide(
        what=_("الدفعات المُشغّلة على برامج الكتالوج: فترتها ومقاعدها وحالتها ومَن يشغّلها."),
        who=_("مدير المركز يفتح الدفعة، وبقية الأدوار تقرأ."),
        after=_("الدفعة تُفتح مخطَّطة، ثم يُفتح لها ملف وزاري."),
        links=(
            (Screen.MOHE, "operations:mohe", _("اعتماد الوزارة")),
            (Screen.PRICELISTS, "catalog:pricelists", _("قوائم الأسعار المؤرّخة")),
        ),
        stops=_(
            "لا يُسجَّل مشارك على دفعة لم تعتمدها الوزارة (BR-013)، والسعر يُقرأ من قائمة "
            "الأسعار السارية لا من هذا السجل."
        ),
    ),
    "mohe": Guide(
        what=_(
            "سجل الملفات الوزارية للدفعات: ما زال مسودةً، وما أُرسل، وما اعتمدته الوزارة أو ردّته."
        ),
        who=_("مدير المركز وموظف التسجيل يفتحان الملفات، وحساب التدقيق يقرأ."),
        after=_("الإرفاق والإرسال وتسجيل القرار كلّها على صفحة الملف الواحد."),
        # No link to «فتح ملف وزاري» here. The register draws that button off
        # CREATE on §3.3/15 and the audit account has VIEW without it — a link
        # filtered on VIEW would hand the reader a page they may open and not
        # use, and put back the very offer the register's polish removed.
        links=((Screen.COHORTS, "operations:cohorts", _("الدفعات المُشغّلة")),),
        stops=_(
            "لا تسجيل على دفعة لم تُعتمد (BR-013)، ولا إرسال قبل استكمال محتوى الملف "
            "ومرفقيه الإلزاميين (BR-016). والقرار يُسجَّل كما ورد من الوزارة."
        ),
    ),
    "mohe-detail": Guide(
        what=_(
            "ملف دفعة واحدة لدى الوزارة: محتواه، وما يطلبه من مرفقات وما رُفع منها وما ينقص، "
            "وما جرى عليه."
        ),
        who=_("مدير المركز يرسل ويسجّل القرار، وموظف التسجيل يرفق ويهيّئ، وبقية الأدوار تقرأ."),
        after=_("باعتماد الوزارة تُفتح الدفعة للتسجيل."),
        links=(
            (Screen.MOHE, "operations:mohe", _("اعتماد الوزارة")),
            (Screen.ENROLLMENTS, "operations:enrollments", _("التسجيلات")),
        ),
        stops=_(
            "قبول الإرسال يأتي محسوباً من الخدمة لا من خلوّ قائمة النقص، ولا تستنتج الصفحة "
            "نقصاً من عندها. وسبب الرفض نصّ الوزارة كما ورد (BR-016)."
        ),
    ),
    "mohe-submit": Guide(
        what=_("فتح ملف وزاري لدفعة قائمة: يُحفظ مسودةً باسم المركز."),
        who=_("مدير المركز وموظف التسجيل."),
        after=_("المرفقان الإلزاميان والإرسال من صفحة الملف بعد الحفظ."),
        links=((Screen.MOHE, "operations:mohe", _("اعتماد الوزارة")),),
        stops=_(
            "الدفعات المعروضة هي القابلة للتقديم وحدها، وإن لم تكن هناك دفعة فلا نموذج. "
            "والحقول السبعة نصّ النموذج الوزاري لا ملاحظات داخلية، وحفظ المسودة لا يعني "
            "أن الملف صار جاهزاً للإرسال (BR-014 · BR-016)."
        ),
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
        what=_("الأطراف المتعاقدة كما ترد في الاتفاقيات الموقّعة، وكم اتفاقية يحمل كلٌّ منها."),
        # §3.5/23 gives CREATE to the manager alone; the officer and the audit
        # account read. Naming the reader as well keeps the line true for the
        # three roles that actually open the screen.
        who=_("مدير المركز يسجّل الشريك، والموظف المالي وحساب التدقيق يقرآن."),
        after=_("بعد تسجيل الشريك تُسجَّل اتفاقيته الموقّعة، وعندها تبدأ الشروط."),
        links=((Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),),
        # The record is thin on purpose, and a reader looking here for the
        # share or the exclusions needs to be told where they actually live.
        # The screen's own `.hint` says the paper rule; saying it here too
        # would print one sentence twice on one page (polish rules §7.3).
        stops=_(
            "سجل الشريك اسمٌ ونوعٌ وبيانات تعريف؛ لا نسبة فيه ولا استثناء ولا دورة "
            "مخالصة — تلك كلّها على الاتفاقية، ولا تبدأ قبل سريانها."
        ),
    ),
    "partner-new": Guide(
        what=_("تسجيل الطرف المتعاقد باسمه وبياناته التعريفية، لا بشروطه المالية."),
        who=_("مدير المركز وحده — §3.5/23 لا تمنح غيره الإنشاء."),
        after=_("تُفتح بطاقة الشريك، ومنها تُسجَّل اتفاقيته الموقّعة."),
        links=((Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون")),),
        # The mistake this form invites is looking for the rate on it. Saying
        # where the terms live is more use than any validation message would
        # be, because there is no field here to refuse.
        stops=_(
            "لا نسبة على هذا النموذج ولا استثناء ولا دورة مخالصة: تسجيل الشريك لا "
            "ينشئ التزاماً ولا يبدأ شرطاً — الاتفاقية الموقّعة وحدها تفعل ذلك."
        ),
    ),
    "agreement-detail": Guide(
        what=_("بنود العقد كما وُقّعت: نموذج الاحتساب والاستثناءات والتوقيت وصيغة تقاسم الخصم."),
        who=_("مدير المركز يعتمد سريان المسودة، والموظف المالي وحساب التدقيق يقرآن."),
        after=_("على البنود السارية يُحتسب استحقاق الشريك وتُبنى مطالبته."),
        links=(
            (Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),
            (Screen.ENTITLEMENT, "settlements:entitlement", _("استحقاق الشركاء")),
        ),
        # The one act on this page is activation, and the mistake it invites is
        # reading a draft as a contract. D-15 is the reason the two are not the
        # same thing: activation is what freezes the terms.
        stops=_(
            "المسودة ليست عقداً: لا تُحتسب عليها استحقاقات ولا تُعرض على الدفعات حتى "
            "تسري، والسريان يجمّد بنودها (D-15). ولا تُعدَّل بعد ذلك — التصحيح بملحق "
            "يحلّ محلّها (BR-042)."
        ),
    ),
    "partner-detail": Guide(
        what=_("بطاقة الطرف المتعاقد: بياناته التعريفية والاتفاقيات المعقودة معه."),
        who=_("مدير المركز والموظف المالي وحساب التدقيق يقرؤونها."),
        after=_("بنود كل اتفاقية — النسبة والاستثناءات ودورة المخالصة — في صفحتها."),
        links=(
            (Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),
            (Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون")),
        ),
        # Read-only, and the chip says so: §3.5/23 grants the manager EDIT but
        # no editing service exists behind this page, so the screen draws no
        # edit button and the guide may not promise one.
        status=_READ_ONLY,
        # «سارية» is a recorded status and «انقضت مدّتها» is a fact about the
        # calendar. A reader who takes the first for the second raises a claim
        # under a contract that ended.
        stops=_(
            "حالة الاتفاقية المسجَّلة شيء وانقضاء مدّتها شيء آخر: العقد المنقضي يبقى "
            "معروضاً لأنه العقد الذي قامت عليه مطالبات سابقة، ولا يصلح لمطالبة جديدة."
        ),
    ),
    "agreements": Guide(
        what=_("اتفاقيات الشركاء: نموذج الاحتساب، والاستثناءات، ولقطة الأسعار."),
        # §3.5/24 gives the manager C E A and leaves the officer and the audit
        # account reading. Naming them keeps the line true for the three roles
        # that actually open the screen.
        who=_("مدير المركز يُنشئ ويعتمد، والموظف المالي وحساب التدقيق يقرآن."),
        after=_("على الاتفاقية تُبنى مطالبة الشريك."),
        links=(
            (Screen.PARTNERS, "partners:partners", _("الشركاء المتعاقدون")),
            (Screen.ENTITLEMENT, "settlements:entitlement", _("استحقاق الشركاء")),
        ),
        stops=_("لا تُعدَّل اتفاقية موقّعة؛ التصحيح يكون بملحق يحلّ محلّها (BR-042)."),
    ),
    "agreement-new": Guide(
        what=_("تسجيل اتفاقية موقّعة بنموذج احتسابها واستثناءاتها."),
        # §3.5/25 is «V C E» for the manager and «V» for the audit account, so
        # the auditor opens this screen too and needs to know it reads to them.
        who=_("مدير المركز يسجّلها، وحساب التدقيق يطّلع على النموذج ولا يملؤه."),
        after=_("تظهر بعدها في سجل الاتفاقيات."),
        links=((Screen.AGREEMENTS, "partners:agreements", _("الاتفاقيات")),),
        stops=_(
            "الاستثناءات تأتي من العقد الموقّع لا من النظام، والتأمينات مستثناة "
            "افتراضياً ما لم ينص العقد على غيرها (BR-046 · BR-092)."
        ),
    ),
    "entitlement": Guide(
        what=_("كيف يصير الشريك مستحقاً: من الاتفاقية حتى المخالصة."),
        # §3.5/26 gives V P to the manager, the finance officer AND the audit
        # account. The line named two of the three, so the reader it omitted
        # met a page that did not admit he was one of its readers.
        who=_("مدير المركز والموظف المالي، وحساب التدقيق يقرأ."),
        after=_("الحساب الفعلي يجري على شاشة المطالبات."),
        links=((Screen.CLAIMS, "settlements:claims", _("المطالبات")),),
        status=_GUIDED,
    ),
    "claims": Guide(
        what=_("بناء مطالبة الشريك عن فترة، ثم اعتمادها."),
        # §3.5/27 gives V P to the audit account too, and it reads the register
        # like the other two. The line named the two that act and left out the
        # one that only reads.
        who=_("الموظف المالي يُنشئ، ومدير المركز يعتمد، وحساب التدقيق يقرأ."),
        after=_("بعد الاعتماد تُخالَص الفترة."),
        links=(
            (Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات")),
            (Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
        ),
        stops=_("لا يعدّل أحد مطالبة معتمدة (BR-051)."),
    ),
    "claim-detail": Guide(
        what=_("مطالبة واحدة: كيف تكوّن وعاؤها، ومن دخل فيها ومن لم يدخل، وما حُسم عليها."),
        who=_("الموظف المالي يطبّق المقاصّات، ومدير المركز يعتمد، وحساب التدقيق يقرأ."),
        after=_("بعد الاعتماد تدخل المطالبة مخالصة الفترة."),
        links=(
            (Screen.CLAIMS, "settlements:claims", _("المطالبات")),
            (Screen.OBLIGATIONS, "settlements:obligations", _("التزامات الشركاء")),
        ),
        # Two rules refuse on this page and only one of them was ever said out
        # loud, to the approver alone. D-18 belongs to every reader: it is why
        # the button is missing for the person who built the claim.
        stops=_(
            "لا يعتمد أحد مطالبة أنشأها بنفسه (D-18)، ولا تُعدَّل مطالبة بعد "
            "اعتمادها — التصحيح يكون بحسم على مطالبة لاحقة (BR-051 · BR-036)."
        ),
    ),
    "settlements": Guide(
        what=_("مخالصة الفترة مع الشريك، والتوقيع النهائي عليها."),
        # §3.5/28 gives V P to the audit account as well.
        who=_("الموظف المالي يُنشئ، ومدير المركز يعتمد، وحساب التدقيق يقرأ."),
        after=_("بها ينتهي أثر الفترة مع هذا الشريك."),
        links=((Screen.CLAIMS, "settlements:claims", _("المطالبات")),),
        # The nil-balance rule genuinely refuses, and it was said on the card
        # inside the approver's own branch — so the officer who records the
        # payment that clears the balance never read the reason for it.
        stops=_("لا تُوقَّع مخالصة ورصيدها غير صفري، ولا تُفتح ثانيةٌ على اتفاقية لها مخالصة مفتوحة."),
    ),
    "settlement-detail": Guide(
        what=_("مخالصة واحدة: ما ضُمّ إليها من مطالبات، وما دُفع عليها، وما بقي قبل التوقيع."),
        who=_("الموظف المالي يضمّ ويسجّل الدفع، ومدير المركز يوقّع، وحساب التدقيق يقرأ."),
        after=_("بالتوقيع ينتهي أثر الفترة مع هذا الشريك."),
        links=(
            (Screen.SETTLEMENTS, "settlements:settlements", _("المخالصات")),
            (Screen.CLAIMS, "settlements:claims", _("المطالبات")),
        ),
        # The order is the rule: attach, pay, then sign. Stated once here so
        # the officer who clears the balance reads why he is clearing it.
        stops=_(
            "لا تُضمّ مطالبة غير معتمدة، ولا يُوقَّع على رصيد غير صفري — الضمّ ثم "
            "الدفع ثم التوقيع، وبعد التوقيع لا يُسجَّل على الفترة شيء (BR-053)."
        ),
    ),
    "obligations": Guide(
        what=_("ما على الشريك: غرامات الغياب والدفعات المقدّمة المستردّة."),
        # §3.5/29 gives V P to the audit account as well.
        who=_("مدير المركز والموظف المالي يقيّدان، وحساب التدقيق يقرأ."),
        after=_("تُخصم من مطالبة لاحقة، ولا يُطالَب بها بفاتورة (BR-036)."),
        links=(
            (Screen.CLAIMS, "settlements:claims", _("المطالبات")),
            (Screen.OBLIGATIONS, "settlements:absences", _("غيابات المدربين")),
        ),
        # Two refusals live behind this form and neither was ever said on the
        # screen: the statement reference IS the evidence of the debt, and the
        # absence penalty is a formula that must show its inputs (BR-057).
        stops=_(
            "لا يُقيَّد التزام بلا مرجع كشف من المركز، ولا تُقيَّد غرامة الغياب باليد — "
            "تُحتسب من سجل الغيابات (BR-057)."
        ),
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
