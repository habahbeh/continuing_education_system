#!/usr/bin/env bash
#
# بوابة الشاشة — تُشغَّل بعد كل شاشة، قبل السويت الكاملة لا بدلاً منها.
#
# السويت الكاملة ٥٤٤١ اختباراً (~٣ دقائق)، وتشغيلها بعد كل تعديل يجعل البوابة
# شيئاً يُتخطّى. وملفُّ الشاشة وحده لا يكفي: الشاشة تقرأ خدمةً يقرأها غيرها،
# وتُبنى على `app.css` تشترك فيه كل الشاشات. فالبوابة هنا = ملفّات الشاشة
# التي تمرّرها أنت + ثلاثة حرّاس يمسكون ما يتسرّب من شاشة إلى أخرى.
#
#   tests/test_architecture.py    — عقود الخدمات: أيّ مرشّح جديد على أيّ
#                                   `list_*` يجب أن يُصنَّف قبل أن يمرّ.
#   tests/test_ui_regressions.py  — الانحدارات المعروفة عبر كل الشاشات.
#   tests/test_demo_readiness.py  — الأصناف غير المعرَّفة، والروابط التي
#                                   توقع قارئاً في رفض.
#
# ومصفوفة الصلاحيات (٢٦٧١ اختباراً وحدها) تُضاف تلقائياً — وفقط — إن كان
# `apps/people/permissions/` بين ما غيّرته، لأنها لا تقرأ الشاشات أصلاً.
#
# الاستعمال:
#   scripts/screen_gate.sh apps/people/tests/test_participants_screen.py \
#                          apps/people/tests/test_participant_service.py
#
# وقبل الـ commit، وحينها فقط:
#   pytest -q
#
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
    echo "الاستعمال: scripts/screen_gate.sh <ملف اختبار الشاشة> [ملفات أخرى…]" >&2
    exit 2
fi

GUARDS=(
    tests/test_architecture.py
    tests/test_ui_regressions.py
    tests/test_demo_readiness.py
    apps/people/tests/test_navigation.py
)

# المصفوفة لا تُقرأ من الشاشات، فلا تُشغَّل إلا إن مُسّت.
if git diff --name-only HEAD -- apps/people/permissions/ | grep -q .; then
    echo "» مُسَّت مصفوفة الصلاحيات — تُضاف إلى البوابة."
    GUARDS+=(apps/people/tests/test_permission_matrix.py)
fi

# التغطية تُقاس على السويت الكاملة لا على بوابة شاشة؛ إسقاطها هنا وقتٌ صافٍ.
echo "» pytest: $* + ${#GUARDS[@]} حُرّاس"
.venv/bin/python -m pytest -q --reuse-db --no-cov "$@" "${GUARDS[@]}"

# ruff على ما غيّرته أنت فقط: المشروع يحمل مخالفات سابقة، وبوابةٌ تفشل دائماً
# ليست بوابة.
CHANGED_PY="$(git diff --name-only HEAD -- '*.py'; git ls-files --others --exclude-standard -- '*.py')"
if [ -n "$CHANGED_PY" ]; then
    # `--force-exclude` وإلّا تجاهل ruff استثناءات pyproject حين يُمرَّر الملف
    # صراحةً — فأول هجرة جديدة تُكتب تُسقط البوابة بأسطر ولّدها Django نفسه،
    # وpyproject يستثني `*/migrations/*` أصلاً.
    echo "» ruff على الملفات المتغيّرة"
    # shellcheck disable=SC2086
    .venv/bin/ruff check --force-exclude $CHANGED_PY
    # shellcheck disable=SC2086
    .venv/bin/ruff format --check --force-exclude $CHANGED_PY
    echo "» mypy"
    .venv/bin/mypy apps config | tail -1
fi

echo "» makemigrations --check"
.venv/bin/python manage.py makemigrations --check --dry-run >/dev/null
echo "» git diff --check"
git diff --check

echo "✓ بوابة الشاشة اجتازت. السويت الكاملة (pytest -q) تبقى قبل الـ commit."
