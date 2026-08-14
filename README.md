# نظام إدارة مركز التعليم المستمر وخدمة المجتمع
### جامعة البترا — عمّان، الأردن

Django 5.2 LTS · MySQL 8.4 LTS · Django Templates + HTMX + Alpine.js + Tailwind CSS · عربي RTL

> **الحالة: Sprint 1 (الأساس) مكتمل.** لا شاشات عمل ولا كيانات مالية بعد — النواة والبنية التحتية فقط.
> الوثائق المعتمدة في `/Users/mohammadhabahbeh/Documents/arafa/التعليم المستمر/docs` وهي **مرجع للقراءة فقط**.

---

## 1. التشغيل المحلي (أقل من 15 دقيقة)

### 1.1 المتطلبات

| المتطلب | النسخة | ملاحظة |
|---|---|---|
| Python | **3.12+** | `python3` الافتراضي قد يكون أقدم — استخدم `python3.12` صراحةً |
| MySQL | **8.4 LTS** | ليس 9.x (مسار Innovation) ولا MariaDB |
| Node | 20+ | **وقت البناء فقط** — لا يعمل وقت التشغيل |

### 1.2 تجهيز MySQL 8.4

يعمل على المنفذ **3308** تفادياً للتصادم مع أي خادم قائم.

```bash
brew install mysql@8.4

mkdir -p /usr/local/var/mysql_ce84
# ملف الإعداد الجاهز في scripts/my_ce84.cnf — انسخه إلى /usr/local/etc/
cp scripts/my_ce84.cnf /usr/local/etc/my_ce84.cnf

/usr/local/opt/mysql@8.4/bin/mysqld \
  --defaults-file=/usr/local/etc/my_ce84.cnf --initialize-insecure --user="$(whoami)"

/usr/local/opt/mysql@8.4/bin/mysqld_safe \
  --defaults-file=/usr/local/etc/my_ce84.cnf --user="$(whoami)" &
```

**قاعدة البيانات ومستخدم التطبيق** (صلاحيات محدودة — بلا `SUPER` ولا `GRANT`):

```bash
/usr/local/opt/mysql@8.4/bin/mysql --socket=/tmp/mysql_ce84.sock -u root <<'SQL'
CREATE DATABASE continuing_education CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'ce_app'@'127.0.0.1' IDENTIFIED BY 'ضع-كلمة-مرور-قوية';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES
  ON continuing_education.* TO 'ce_app'@'127.0.0.1';
GRANT ALL PRIVILEGES ON `test_continuing_education`.* TO 'ce_app'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
```

**تحقق من الإعداد قبل المتابعة:**

```bash
/usr/local/opt/mysql@8.4/bin/mysql --socket=/tmp/mysql_ce84.sock -u root -e "
SELECT @@version, @@sql_mode, @@character_set_server,
       @@collation_server, @@transaction_isolation, @@lower_case_table_names;"
```

| المتغير | المطلوب |
|---|---|
| `version` | `8.4.x` |
| `sql_mode` | يحوي **`STRICT_ALL_TABLES`** |
| `character_set_server` | `utf8mb4` |
| `collation_server` | `utf8mb4_0900_ai_ci` |
| `transaction_isolation` | `READ-COMMITTED` |
| `lower_case_table_names` | `0` (إنتاج Linux) أو **`2`** (تطوير macOS) |

> **`STRICT_ALL_TABLES` غير قابل للتفاوض.** بدونه يبتر MySQL القيم صامتاً بدل رفعه خطأً. فحص الإقلاع **يُفشل التطبيق** لا يحذّر — وقد أثبت جدواه فعلاً أثناء البناء بالتقاط مفتاح إعداد أطول من عموده.

### 1.3 المشروع

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install "Django==5.2.*" mysqlclient python-dotenv \
    pytest pytest-django pytest-cov ruff mypy "django-stubs[compatible-mypy]"

cp .env.example .env      # ثم املأ DB_PASSWORD و DJANGO_SECRET_KEY
chmod 600 .env

npm install
npm run build:css

./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py seed_settings
./.venv/bin/python manage.py runserver
```

افتح <http://127.0.0.1:8000/> — يجب أن تظهر صفحة الحالة بالعربية RTL مع إصدار MySQL.

**توليد مفتاح سري:**
```bash
./.venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

> ⚠️ **`.env` لا يُودَع في git إطلاقاً.** وهو مُدرَج في `.gitignore` وصلاحياته `600`.

---

## 2. حماية سجل التدقيق — ثلاث طبقات

سجل التدقيق **append-only** (BR-084 · ADR-010). الحماية ثلاثية، وكلها مطلوبة:

| الطبقة | الآلية | كيف تتحقق |
|---|---|---|
| **1. التطبيق** | `save()` يرفض التعديل · `delete()` يرفض دائماً | ضمن حزمة الاختبارات |
| **2. قاعدة البيانات** | `ce_app` بلا `UPDATE`/`DELETE` على `core_auditevent` | `manage.py verify_audit_grants` |
| **3. التشفير** | سلسلة `row_hash = SHA256(prev_hash ‖ الحقول)` | `manage.py verify_audit_chain` |

### تفعيل الطبقة الثانية — **بعد كل هجرة تُنشئ جدولاً**

```bash
/usr/local/opt/mysql@8.4/bin/mysql --socket=/tmp/mysql_ce84.sock -u root \
  continuing_education < scripts/apply_audit_grants.sql
```

> **لماذا سكربت وليس `REVOKE` واحداً؟** نموذج صلاحيات MySQL **جمعي لا طرحي**: منح على مستوى جدول يُضيف إلى منح على مستوى قاعدة البيانات ولا يطرح منه. لذلك «امنع `UPDATE` على هذا الجدول وحده» يُعبَّر عنه بسحب الصلاحية عن القاعدة كلها ثم إعادة منحها لكل جدول **عدا** `core_auditevent`. والسكربت يفعل ذلك آلياً.
>
> **لهذا يجب تشغيله بعد كل هجرة تُنشئ جدولاً جديداً** — وإلا بقي الجدول الجديد بلا `UPDATE`/`DELETE` وتعطّل التطبيق.

---

## 3. أوامر الفحص

```bash
./.venv/bin/python -m pytest                     # 144 اختباراً · تغطية core ≥ 90%
./.venv/bin/ruff check .                         # lint
./.venv/bin/ruff format --check .                # التنسيق
./.venv/bin/mypy apps/                           # الأنواع
./.venv/bin/python manage.py makemigrations --check --dry-run
./.venv/bin/python manage.py check
./.venv/bin/python manage.py check --deploy      # بإعدادات production
./.venv/bin/python manage.py verify_audit_chain
./.venv/bin/python manage.py verify_audit_grants
```

---

## 4. بنية المشروع

```
config/settings/{base,local,test,production}.py   إعدادات مقسّمة
apps/core/          ⭐ النواة: الفصول · الإعدادات المؤرّخة · العدّادات
                       سجل التدقيق · المرفقات · الفترات المالية
apps/people/        نموذج User المخصّص فقط
apps/{catalog,partners,operations,billing,cashbox,
      settlements,expenses,reporting,datamigration}/
                    هياكل فارغة — النماذج في Sprints 3–9
templates/          قوالب عربية RTL
static/             Tailwind + HTMX + Alpine — كلها محلية، لا CDN
tests/              الفحوص المعمارية الثابتة
scripts/            سكربتات تشغيلية
```

---

## 5. قواعد ملزِمة

مصدرها `00-decisions/FINAL_TECHNICAL_DIRECTION.md`. مخالفتها **تُفشل البناء** لا تُناقَش:

1. **لا منطق أعمال في الـ views ولا القوالب ولا جافاسكربت** — الخدمات فقط.
2. **كل مبلغ `Decimal`** بـ `DECIMAL(12,3)`. `float` ممنوع ويُفرض بفحص ثابت.
3. **المعاملات صريحة في الخدمات** — `ATOMIC_REQUESTS = False`.
4. **لا أعمدة `ENUM`** — `VARCHAR` + `choices` + `CheckConstraint`.
5. **لا قيم معيارية في الكود** — كلها عبر `EffectiveSetting` المؤرّخ.
6. **لا CDN** — كل مورد محلي.
7. **الأسماء من `GLOSSARY.md`** — لا تُخترع مسمّيات.
8. **النصوص العربية عبر `gettext`**.
9. **كل قيد موثّق في `DATA_MODEL.md` قيد فعلي في قاعدة البيانات** لا تحقق بايثوني.
10. **فحوص الإقلاع تُفشل التطبيق** لا تحذّر.

### الإعدادات — قدرة مقابل تطبيق

| المفتاح | القيمة | المعنى |
|---|---|---|
| `deposits_supported` | `true` | **قدرة نظام** — التأمينات مستخدمة فعلاً. التطبيق بسياسة كل برنامج (Sprint 3) |
| `tax_supported` | `true` | **قدرة نظام** — الجامعة تحاسب بالضريبة |
| `default_tax_rate` | `NULL` | **«النسبة غير معروفة بعد»** لا «لا ضريبة» — حاجب قبل Sprint 4 (Q-25) |
| `partner_base_mode` | `NET` | وعاء الشريك على الصافي — افتراض بانتظار Q-28 |

> ❌ **`deposits_enabled` و`tax_enabled` مفتاحان ملغيان.** كانا يشفّران افتراضاً أثبت العميل خطأه. **لا يُبذران ولا يُشار إليهما**، ويفشل البناء عند ظهورهما.

---

## 6. انحراف بيئة التطوير المقصود

`lower_case_table_names`:

| البيئة | القيمة | السبب |
|---|---|---|
| **إنتاج (Linux)** | `0` | الهدف المعتمد |
| **تطوير (macOS)** | `2` | APFS غير حسّاس لحالة الأحرف، و`0` **غير مدعوم** من MySQL عليه |

فحص الإقلاع **يقبل `0` أو `2` ويرفض `1`**، ويُصدر تحذيراً واضحاً عند رؤية `2` يذكّر بأن الإنتاج يجب أن يكون `0`.

---

## 7. الحالة الحالية والخطوة التالية

**Sprint 1 مكتمل.** التالي **Sprint 2 (الهوية والصلاحيات)** ويحتاج جوابين قبل بدئه:

| السؤال | الأثر |
|---|---|
| **Q-12** — SSO/Active Directory أم حسابات محلية؟ | يحدد `AUTHENTICATION_BACKENDS` وسياسة الجلسات |
| **Q-14** — «المدير المالي» دور داخلي أم مرجع نصي؟ | يحدد عدد الأدوار ومصفوفة الصلاحيات كاملةً |
