# مراقب البريد (MailWatch)

أداة بلا خادم (Serverless) تعمل مجانًا على GitHub Actions:

- تسحب نسخة من كل رسائل البريد (القديمة والجديدة) وتحفظها داخل مستودع خاص على GitHub.
- تدعم عددًا غير محدود من الحسابات: Gmail، Yahoo، Outlook، iCloud، Zoho، وأي دومين خاص عبر IMAP.
- لكل حساب إعداداته الخاصة: الاتصال، المجلدات، الفلاتر (مرسل / مستلم / عنوان / نص / أي مكان)، التنبيهات.
- تنبيهات فورية عبر تليجرام عند وصول رسالة مطابقة للفلاتر (أو لكل رسالة).
- أوامر من تليجرام: رد، تعليم كمقروءة، تمييز، أرشفة، مهملات، حالة الحسابات.
- لوحة تحكم عربية حديثة ومتجاوبة للموبايل: تصفح الرسائل والمرفقات، الرد والإرسال، إدارة الحسابات والفلاتر، السجل.

## كيف تعمل؟

```
كل 5 دقائق ─► GitHub Actions ─► IMAP (كل الحسابات بالتوازي)
                     │                 ├─ رسائل جديدة ─► فلترة ─► حفظ ─► تنبيه تليجرام
                     │                 └─ شريحة من الأرشيف القديم ─► حفظ (تدريجيًا حتى يكتمل)
                     ├─ تنفيذ أوامر تليجرام المعلّقة
                     └─ commit + push إلى المستودع
لوحة التحكم (صفحة HTML ثابتة) ─► تقرأ الملفات من GitHub API بتوكن محفوظ في متصفحك
                               └─► ترسل الأوامر (رد/أرشفة/...) عبر repository_dispatch ─► Actions ينفذها خلال دقيقة
```

البنية داخل مجلد البيانات:

```
data/
  accounts.json                  ملخص الحسابات وحالتها (للوحة التحكم و /status)
  state/<account>.json           حالة السحب لكل حساب
  index/<account>/<YYYY-MM>.json فهرس شهري مختصر
  messages/<account>/<id>.json   الرسالة كاملة (نص + HTML + بيانات المرفقات)
  raw/<account>/<id>.eml         النسخة الأصلية الخام
  attachments/<account>/<id>/    المرفقات
  actions/log.json               سجل تنفيذ الأوامر
```

## الإعداد خطوة بخطوة

### 1) المستودع

1. أنشئ مستودعًا **خاصًا** على GitHub وارفع إليه محتويات هذا المجلد.
2. Settings ← Actions ← General ← Workflow permissions ← اختر **Read and write permissions**.

### 2) بوت تليجرام

1. افتح [@BotFather](https://t.me/BotFather) ← `/newbot` ← انسخ التوكن.
2. أرسل أي رسالة للبوت الجديد، ثم افتح `https://api.telegram.org/bot<TOKEN>/getUpdates` وانسخ `chat.id`
   (أو استخدم [@userinfobot](https://t.me/userinfobot)). يمكن استخدام مجموعة أو قناة أيضًا.

### 3) الأسرار والمتغيرات

Settings ← Secrets and variables ← Actions:

| النوع | الاسم | الوصف |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` | توكن البوت |
| Secret | `TELEGRAM_CHAT_ID` | معرّف المحادثة الافتراضي (يمكن أكثر من واحد بفاصلة، الأول هو الافتراضي والبقية مسموح لها بإرسال الأوامر) |
| Secret | `ACCOUNT_PASSWORDS` | اختياري: JSON بكلمات المرور `{"hamza_gmail":"xxxx","work":"yyyy"}` بدل وضعها في الملف |
| Variable | `DASHBOARD_URL` | اختياري: رابط لوحة التحكم ليُضاف زر "فتح في اللوحة" في التنبيهات |
| Variable | `BACKFILL_BATCH` | اختياري: عدد الرسائل القديمة لكل مجلد في كل دورة (افتراضي 100) |
| Variable | `TIME_BUDGET_MIN` | اختياري: الحد الزمني لسحب الأرشيف القديم بالدقائق (افتراضي 8) |
| Variable | `WORKERS` | اختياري: عدد الحسابات التي تُفحص بالتوازي (افتراضي 8) |

### 4) الحسابات

انسخ `config/accounts.example.yaml` إلى `config/accounts.yaml` وعدّله، أو أضف الحسابات من لوحة التحكم لاحقًا.

```yaml
defaults:
  folders: [INBOX]
  archive: all        # all = حفظ كل الرسائل | matched = المطابقة للفلاتر فقط
  notify: matched     # matched | all | none
accounts:
  - id: hamza_gmail
    name: حمزة - Gmail
    provider: gmail
    email: example@gmail.com
    password: "xxxx xxxx xxxx xxxx"
    filters:
      - name: البنك
        from: ["@bank.com"]
        subject: ["تحويل", "حوالة"]
      - name: عميل مهم
        anywhere: ["شركة النور"]
```

كلمات مرور التطبيقات:

- **Gmail**: فعّل التحقق بخطوتين ← [App passwords](https://myaccount.google.com/apppasswords).
- **Yahoo**: Account Security ← Generate app password.
- **iCloud**: [appleid.apple.com](https://appleid.apple.com) ← App-Specific Passwords.
- **Outlook/Hotmail الشخصي**: مايكروسوفت أوقفت كلمات المرور في IMAP، يلزم OAuth2:
  `python tools/oauth_setup.py microsoft --client-id <ID>` (التعليمات داخل الملف).
- **Gmail عبر OAuth2** (بديل عن App password): `python tools/oauth_setup.py google --client-id ... --client-secret ...`
- **دومين خاص**: `provider: custom` مع `custom.imap_host` و `custom.smtp_host`.

### 5) التشغيل الأول

Actions ← **فحص البريد** ← Run workflow. التشغيلة الأولى تسجل عدد الرسائل الموجودة في كل حساب وتبدأ سحبها
تدريجيًا (100 رسالة لكل مجلد في كل دورة). لتسريع السحب: Actions ← **سحب الأرشيف القديم** ← Run workflow
(يعيد تشغيل نفسه حتى يكتمل).

### 6) لوحة التحكم

اللوحة ملف واحد `dashboard/index.html` لا يحتوي على أي بيانات، ويقرأ كل شيء من GitHub بتوكن يُحفظ في متصفحك فقط.

1. أنشئ توكن: GitHub ← Settings ← Developer settings ← Personal access tokens ← **Fine-grained** ←
   Repository access: مستودعك ← Permissions: **Contents: Read and write** و **Actions: Read and write**.
2. افتح اللوحة بإحدى الطرق:
   - ارفع `dashboard/index.html` إلى مستودع **عام** منفصل وفعّل GitHub Pages (الملف لا يحوي بيانات، فهذا آمن)، أو
   - افتحه من أي استضافة ثابتة (Cloudflare Pages / Netlify)، أو
   - افتحه محليًا بتشغيل `python -m http.server 8765` داخل المستودع ثم `http://localhost:8765/dashboard/`.
3. الإعدادات ← مستودع البيانات `owner/repo` ← الفرع ← التوكن ← حفظ.
4. ضع رابط اللوحة في المتغير `DASHBOARD_URL` ليظهر زر "فتح في لوحة التحكم" في تنبيهات تليجرام.

## وضع المستودعين (الإعداد الحالي)

الدقائق المجانية لـ Actions في المستودعات **الخاصة** 2000 دقيقة شهريًا، وكل تشغيلة تُحسب بدقيقة كاملة على الأقل،
بينما الدقائق في المستودعات **العامة** غير محدودة. لذلك يُقسَم المشروع إلى:

1. **مستودع الكود (عام)**، وهو هذا المستودع: الكود والـ workflows ولوحة التحكم. لا يحتوي أي بيانات أو كلمات مرور،
   و `.gitignore` يمنع رفع `data/` و `config/accounts.yaml` إليه.
2. **مستودع البيانات (خاص)**: `config/accounts.yaml` و `data/` (الرسائل والفهارس والحالة والمرفقات).

الـ workflows تجلب مستودع البيانات إلى `data_repo/` وتحفظ التغييرات فيه. الإعدادات في مستودع الكود:

| النوع | الاسم | القيمة |
|---|---|---|
| Variable (اختياري) | `DATA_REPO` | اسم مستودع البيانات `owner/repo` (الافتراضي مكتوب داخل ملفات الـ workflows) |
| Secret | `DATA_REPO_TOKEN` | Fine-grained token له **Contents: Read and write** على مستودع البيانات |
| Secret | `TELEGRAM_BOT_TOKEN` و `TELEGRAM_CHAT_ID` و `ACCOUNT_PASSWORDS` | كما في الأعلى |

حماية الخصوصية في المستودع العام:

- سجلات التشغيل تستبدل أي عنوان بريد بـ `<email>` (`PUBLIC_LOGS=1`).
- أوامر لوحة التحكم (الرد، الإرسال، الأرشفة...) تُكتب في `data/actions/queue/` داخل مستودع البيانات الخاص،
  ثم تطلب اللوحة تشغيل workflow "تنفيذ أمر" دون أي محتوى، فلا يظهر نص الرد أو العناوين في السجلات العامة.
- لا تشغّل workflow "تنفيذ أمر" يدويًا بأمر مكتوب في الخانة، لأن مدخلات التشغيل اليدوي تظهر للعامة.

لوحة التحكم: مستودع البيانات = الخاص، مستودع الكود = العام، مجلد البيانات = `data`.
توكن اللوحة يحتاج الوصول إلى المستودعين معًا (Contents و Actions: Read and write).

للعمل بمستودع خاص واحد بدلًا من ذلك: عيّن المتغير `DATA_REPO` إلى اسم المستودع نفسه، وغيّر الجدولة في
`.github/workflows/poll.yml` إلى كل 30 دقيقة (`*/30 * * * *`) حتى لا تتجاوز الدقائق المجانية.

## أوامر تليجرام

| الأمر | الوظيفة |
|---|---|
| `/status` | حالة الحسابات وعدد الرسائل والأخطاء |
| `/read حساب:معرف` | تعليم كمقروءة |
| `/unread حساب:معرف` | تعليم كغير مقروءة |
| `/flag حساب:معرف` | تمييز |
| `/archive حساب:معرف` | أرشفة |
| `/trash حساب:معرف` | نقل إلى المهملات |
| `/reply حساب:معرف نص الرد` | إرسال رد |

المعرف يصلك مع كل تنبيه. تُنفذ الأوامر في دورة الفحص التالية (عادةً خلال 5 دقائق)، أما أوامر لوحة التحكم فتُنفذ خلال دقيقة تقريبًا.

## التشغيل والاختبار محليًا

```bash
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=...      # في PowerShell: $env:TELEGRAM_BOT_TOKEN="..."
python -m mailwatch check --connect     # التحقق من الإعدادات واختبار الاتصال بكل حساب
python -m mailwatch telegram-test       # رسالة اختبار
python -m mailwatch poll                # دورة فحص كاملة (تكتب في data/)
python -m mailwatch backfill --time-budget 10
python -m mailwatch action --payload "{\"action\":\"mark_read\",\"account\":\"hamza_gmail\",\"id\":\"ab12cd34ef56\"}"
```

## ملاحظات مهمة

- **التأخير**: الجدولة كل 5 دقائق (أقل مدة يسمح بها GitHub)، وقد يؤخرها GitHub عدة دقائق إضافية وقت الضغط.
- **الخصوصية**: الرسائل تُحفظ في مستودع خاص على GitHub، وكلمات المرور في الملف أو في Secrets. التوكن في اللوحة يُحفظ في متصفحك فقط.
- **الحجم**: المرفقات الأكبر من 90 م.ب لا تُحفظ (حد GitHub للملف 100 م.ب). يُفضَّل إبقاء المستودع تحت بضعة جيجابايت.
- **الجدولة**: GitHub يوقف الجدولة بعد 60 يومًا بلا نشاط؛ الـ workflow يعيد تفعيل نفسه تلقائيًا في كل دورة.
- **Gmail**: أرشفة رسالة من INBOX تعني إزالتها من الوارد وبقاءها في All Mail (سلوك Gmail الطبيعي).
- **الرسائل القديمة** تُحفظ دون تنبيهات؛ التنبيهات للرسائل الجديدة فقط.
- **تغيير الفلاتر** يُطبَّق على الرسائل الجديدة فقط، ولا يعيد تصنيف الرسائل المحفوظة.
