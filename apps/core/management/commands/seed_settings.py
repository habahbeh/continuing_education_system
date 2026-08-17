"""
Seed the initial effective-dated settings (ADR-009, BR-086).

Idempotent: running it twice creates nothing new and NEVER overwrites a value
an administrator has since changed. Adding a key later and re-running seeds
only the missing key.

Capability settings reflect the REVISED decisions of 2026-08-14
(CLIENT_FEEDBACK_2026-08-14.md):

* Deposits ARE used. There is no `deposits_enabled` kill switch; the capability
  stays on and applicability is decided per program by a DepositPolicy
  (Sprint 3). `deposits_enabled` is RETIRED and must not appear anywhere.

* Tax IS applied — "the university accounts for its revenue from everyone with
  tax". There is no `tax_enabled` kill switch either. `default_tax_rate` is
  seeded as NULL meaning "rate not yet known" (Q-25), NOT "no tax". Sprint 4
  must raise TaxRateNotConfigured when a taxable charge is created while the
  rate is unset — never silently compute zero, because a silent zero turns a
  known blocking question into an invisible data error.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import EffectiveSetting, SettingValueType

D = SettingValueType.DECIMAL
I = SettingValueType.INTEGER  # noqa: E741 - reads better aligned in the table below
B = SettingValueType.BOOLEAN
S = SettingValueType.STRING

#: (key, value, type, note)
#: `value is None` means "not configured yet" — distinct from zero.
SEED: list[tuple[str, Any, str, str]] = [
    # --- Capability settings (revised 2026-08-14) ------------------------
    (
        "deposits_supported",
        True,
        B,
        "Q-01 (مُراجَع): التأمينات مستخدمة فعلاً — قدرة نظام تبقى true. "
        "التطبيق يُقرَّر بسياسة كل برنامج لا بمفتاح عالمي (BR-096).",
    ),
    (
        "default_deposit_required",
        False,
        B,
        "Q-01 (مُراجَع): الافتراض لا تأمين ما لم تُعرَّف سياسة للبرنامج.",
    ),
    ("tax_supported", True, B, "Q-05 (مُراجَع): الضريبة مطبَّقة فعلاً — قدرة نظام تبقى true."),
    (
        "default_tax_rate",
        None,
        D,
        "Q-05 (مُراجَع): NULL تعني «النسبة غير معروفة بعد» لا «لا ضريبة». "
        "حاجب قبل Sprint 4 — Q-25. إنشاء بند خاضع بلا نسبة يجب أن يرفع "
        "TaxRateNotConfigured لا أن يحتسب صفراً.",
    ),
    (
        "tax_details_required_before_sprint_4",
        True,
        B,
        "علامة حجب صريحة: Q-25 · Q-26 · Q-27 · Q-28 يجب حسمها قبل Sprint 4.",
    ),
    (
        "partner_base_mode",
        "NET",
        S,
        "Q-28 (مفتوح): وعاء قسمة الشريك على الصافي قبل الضريبة — الافتراض المهني "
        "الموصى به. إعداد مفرد لا معادلة مضمّنة، فتغييره قبل Sprint 5 إعداد.",
    ),
    (
        "partner_offset_scope",
        "PARTNER",
        S,
        "Q-08 (مفتوح): المقاصّة على مستوى الشريك افتراضاً — التزام ناشئ عن اتفاقية "
        "يُستردّ من مطالبة اتفاقية أخرى للشريك نفسه. تحويلها إلى AGREEMENT يحصر كل "
        "مقاصّة باتفاقيتها. أي التزام مفرد يبقى قابلاً للتقييد باتفاقيته وحدها عبر "
        "restricted_to_agreement، وهو يغلب الإعداد دائماً.",
    ),
    # --- Money and receipts ----------------------------------------------
    ("money_display_dp", 2, I, "Q-04: التخزين DECIMAL(12,3) والعرض بخانتين."),
    (
        "external_receipt_required",
        False,
        B,
        "Q-03: رقم السند الخارجي اختياري؛ تحويله إلى true يجعله إلزامياً بلا هجرة.",
    ),
    # --- Business thresholds from the demo -------------------------------
    ("diploma_minimum_first_payment", "400.000", D, "BR-020: 300 تسجيل + 100 أول مادة."),
    ("transfer_lecture_limit", 3, I, "BR-062: مهلة النقل ثلاث محاضرات."),
    ("subject_repeat_fee", "75.000", D, "BR-037: رسم إعادة المادة، يُقسم 50/50."),
    ("certificate_replacement_fee", "15.000", D, "BR-038: بدل فاقد الشهادة، للمركز بالكامل."),
    ("dismissal_fail_limit", 3, I, "BR-067: الرسوب في أكثر من 3 مواد ⇒ الفصل."),
    ("trainer_absence_replace_limit", 4, I, "BR-058: استبدال المدرب بعد 4 غيابات."),
    ("trainer_absence_multiplier", 3, I, "BR-057: الغرامة 3 أضعاف نفقة المحاضرة."),
    ("mohe_deadline_alert_days", 15, I, "BR-015: التنبيه قبل انتهاء المهلة الوزارية."),
    ("payment_overdue_days", 30, I, "Q-16 (مفتوح): مهلة اعتبار المشارك متأخراً."),
    ("registration_fee_center_default", "50.000", D, "BR-009: رسم تسجيل طالب المركز."),
    ("registration_fee_university_default", "15.000", D, "BR-009: رسم تسجيل الطالب الجامعي."),
    # --- Local authentication policy (Q-12, Sprint 2A) --------------------
    # The thresholds live here rather than in settings.py because they are
    # business decisions, not deployment configuration: raising the attempt
    # limit is a security decision someone must own and date (BR-086).
    (
        "session_idle_timeout_minutes",
        30,
        I,
        "Q-12: خمول 30 دقيقة يُنهي الجلسة. تُقرأ من IdleSessionMiddleware لا من الكود.",
    ),
    (
        "login_max_failed_attempts",
        5,
        I,
        "Q-12: قفل الحساب بعد 5 محاولات فاشلة.",
    ),
    (
        "login_lockout_requires_admin_unlock",
        True,
        B,
        "Q-12: فكّ القفل من SYSTEM_ADMINISTRATOR بسبب موثّق — لا فكّ تلقائي بمرور الوقت.",
    ),
    # --- Participants (Sprint 2B) ----------------------------------------
    (
        "identity_document_uniqueness_mode",
        "WARN",
        S,
        "BR-005: WARN يعرض السجل المطابق ويسمح بالمتابعة بسبب موثّق · BLOCK يمنع. "
        "WARN في v1 لأن أرشيف Sprint 8 سيحوي تكرارات مشروعة يجب أن تبقى قابلة "
        "للأرشفة؛ ولذلك قيد التفرد غير مُفعَّل في قاعدة البيانات (DATA_MODEL §4.2).",
    ),
    (
        "participant_qualifications",
        (
            '[["HIGH_SCHOOL","الثانوية العامة"],["DIPLOMA","دبلوم"],'
            '["BACHELOR","بكالوريوس"],["HIGHER_DIPLOMA","دبلوم عالٍ"],'
            '["MASTER","ماجستير"],["PHD","دكتوراه"]]'
        ),
        S,
        "🟠 Q-31 — قائمة مبدئية غير معتمدة. SPEC §6 يذكر «6 مستويات» بلا تعدادها. "
        "تُخزَّن كإعداد لا كثوابت في الكود، فتصحيحها بعد جواب العميل إدخال بيانات "
        "لا هجرة. ولا قيد CHECK على الحقل لهذا السبب.",
    ),
    (
        "participant_cities",
        (
            '[["AMMAN","عمّان"],["IRBID","إربد"],["ZARQA","الزرقاء"],'
            '["BALQA","البلقاء"],["MAFRAQ","المفرق"],["KARAK","الكرك"],'
            '["JERASH","جرش"],["MADABA","مأدبا"],["AJLOUN","عجلون"],'
            '["AQABA","العقبة"],["MAAN","معان"],["TAFILAH","الطفيلة"]]'
        ),
        S,
        "🟠 Q-31 — قائمة مبدئية غير معتمدة (محافظات الأردن الاثنتا عشرة). "
        "غير المقيم خارجها يحتاج قراراً — انظر Q-31.",
    ),
    (
        "certificate_grades",
        '[["EXCELLENT","ممتاز"],["VERY_GOOD","جيد جداً"],["GOOD","جيد"],["PASS","مقبول"]]',
        S,
        "BR-078 — التقدير يدوي ولا يوجد نموذج درجات ولا امتحانات. التقديرات الأربعة "
        "من نص القاعدة، وتُخزَّن إعداداً لا ثوابت: إضافة تقدير أو تعديل تسميته "
        "إدخال بيانات لا هجرة، ولا قيد CHECK على الحقل — القيمة يملكها المركز "
        "(سابقة Q-31 · جواب العميل 2026-08-15).",
    ),
]

#: Keys that must never be seeded again. Kept so the command actively reports
#: their presence rather than leaving a stale row unnoticed.
RETIRED_KEYS = ["deposits_enabled", "tax_enabled"]


class Command(BaseCommand):
    help = "Seed initial effective-dated settings. Idempotent and non-destructive."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--effective-from",
            type=date.fromisoformat,
            default=date(2026, 1, 1),
            help="Effective-from date for seeded rows (default 2026-01-01).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        effective_from: date = options["effective_from"]
        created = skipped = 0

        for key, value, value_type, note in SEED:
            if EffectiveSetting.objects.filter(key=key).exists():
                skipped += 1
                self.stdout.write(f"  = {key:<40} موجود — لم يُمسّ")
                continue

            EffectiveSetting.objects.create(
                key=key,
                value=None if value is None else str(value),
                value_type=value_type,
                effective_from=effective_from,
                effective_to=None,
                note=note,
            )
            created += 1
            shown = "NULL (غير محدَّدة بعد)" if value is None else value
            self.stdout.write(self.style.SUCCESS(f"  + {key:<40} {shown}"))

        for key in RETIRED_KEYS:
            if EffectiveSetting.objects.filter(key=key).exists():
                self.stdout.write(
                    self.style.ERROR(
                        f"  ! {key} موجود في قاعدة البيانات — مفتاح ملغى. احذفه يدوياً بعد المراجعة."
                    )
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"تم: {created} مُنشأ · {skipped} قائم لم يُمسّ."))
        self.stdout.write(
            "ملاحظة: default_tax_rate = NULL تعني «غير محدَّدة بعد» لا «لا ضريبة» (Q-25)."
        )
