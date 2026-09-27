#!/usr/bin/env bash
#
# زرّ «رجّع الديمو من جديد».
#
#   scripts/demo_reset.sh                 # على القاعدة التي في .env
#   scripts/demo_reset.sh ce_walk         # على قاعدة بعينها
#   NOINPUT=1 scripts/demo_reset.sh       # بلا سؤال تأكيد
#
# يمحو كل صفٍّ ثم يبني القاعدة من الصفر بالمشي على مسارات النظام نفسها، فيخرج
# سجلُّ تدقيقٍ متّسق يمرّ من verify_audit_chain لا يتعارض معه.
#
# والرفض هو الحماية لا التحذير: الأمر نفسه يرفض أي قاعدة ليست مسمّاةً في
# DEMO_DATABASES داخل seed_demo_all، فلا يكفي أن يُخطئ أحدٌ في كتابة الاسم.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "لا بيئة افتراضية في ./.venv — أنشئها أولاً." >&2
  exit 1
fi

if [[ $# -gt 0 ]]; then
  export DB_NAME="$1"
fi

: "${DJANGO_SETTINGS_MODULE:=config.settings.local}"
export DJANGO_SETTINGS_MODULE
export PYTHONPATH="$PWD"

ARGS=(--reset)
if [[ "${NOINPUT:-0}" == "1" ]]; then
  ARGS+=(--noinput)
fi

echo "── الهجرات أولاً: قاعدةٌ ناقصة المخطّط تفشل في منتصف الزرع"
.venv/bin/python manage.py migrate --noinput

echo "── المحو ثم البناء"
.venv/bin/python manage.py seed_demo_all "${ARGS[@]}"

echo "── التحقّق من سلسلة سجل التدقيق"
.venv/bin/python manage.py verify_audit_chain
