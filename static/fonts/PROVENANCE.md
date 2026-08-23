# الخطوط — المصدر والرخصة

| الملف | الوزن | الحجم |
|---|---|---|
| `noto-kufi-arabic-400.woff2` | Regular | 43 KB |
| `noto-kufi-arabic-600.woff2` | SemiBold | 46 KB |
| `noto-kufi-arabic-800.woff2` | ExtraBold | 45 KB |

**الخط:** Noto Kufi Arabic — Google LLC
**الرخصة:** SIL Open Font License 1.1 — النص الكامل في `OFL.txt`
**المصدر:** حزمة npm `@fontsource/noto-kufi-arabic@5.3.0`، المجموعة الجزئية العربية
(`files/noto-kufi-arabic-arabic-{400,600,800}-normal.woff2`).

## لماذا الملفات مودَعة في المستودع بدل حزمة npm

ADR-003 / الفحص الثابت A-07 يمنعان أي مورد خارجي: النظام يعمل على شبكة
داخلية ولا يجوز أن يعتمد على الإنترنت العام. ولأن Node أداة **بناء** لا
تشغيل، فإن ربط الخط بحزمة npm يجعل ملفاً يحتاجه المتصفح وقت التشغيل رهيناً
بخطوة تثبيت. الملفات الثلاثة أصول ثابتة مثل `htmx.min.js` تماماً.

## لماذا المجموعة العربية وحدها

الأرقام والرموز اللاتينية (أرقام السندات · رموز التسجيل · بصمات الملفات)
تُترك لخط النظام: أرقامه الجدولية (`tabular-nums`) أوضح وأدق في محاذاة
المبالغ، والتمييز بين `0`/`O` و`1`/`l` فيه أصحّ. المدى في `unicode-range`
يقصر تحميل الخط على النص العربي فعلاً.

## التحديث

```bash
npm install --no-save @fontsource/noto-kufi-arabic
P=node_modules/@fontsource/noto-kufi-arabic
for w in 400 600 800; do
  cp "$P/files/noto-kufi-arabic-arabic-$w-normal.woff2" "static/fonts/noto-kufi-arabic-$w.woff2"
done
cp "$P/LICENSE" static/fonts/OFL.txt
```
