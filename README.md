# Bloodborne-Arabic-Localization

مشروع تعريب مجتمعي للعبة Bloodborne على نسخة bbport.
الترجمة الآلية بتشتغل على دفعات CSV. ترجماته بتوصل لـ `needs_review` إذا ما فيها أخطاء، وإذا فيها أخطاء بتضل `draft`. كل سطر لازم يراجعه إنسان ويعتمده باسمه قبل ما يدخل المود.

## بنية المجلدات

| المجلد | شو فيه | الحالة |
|---|---|---|
| `common` | أدوات مشتركة: المسارات، كشف الأكواد والمتغيرات، معالجة العربي (ربط الحروف + RTL) | ✅ |
| `database` | قاعدة SQLite (`schema.sql` + `db.py`): النصوص، الحالات، السجل، القاموس | ✅ |
| `glossary` | `glossary.csv` (القاموس) و`style_guide_ar.md` (دليل الأسلوب) | ✅ |
| `validation` | المدقق: الأكواد، القاموس، الطول، اليونيكود، الفراغات والتكرار | ✅ |
| `fonts` | فحص دعم الخط للعربي + صور معاينة PNG. حطي هون خطوط OFL | ✅ |
| `tests` | اختبارات + عينات أصلية من تأليفنا (`tests/samples`) | ✅ |
| `translation` | دفعات الترجمة (CSV): `batch.py` و`TRANSLATOR.md` و`batches/out` و`batches/in` | ✅ |
| `extraction` | استخراج النصوص من ملفات اللعبة | ⏳ بعد التحقق من نسخة رسمية |
| `mod_builder` | بناء ملفات المود | ⏳ لاحقاً |
| `logs` | تقارير التدقيق والمعاينات | |
| `backups` / `documentation` | نسخ احتياطية / توثيق | |

## حالات الترجمة

```
pending ──(ترجمة)──> draft ──(review)──> needs_review ──(approve + اسم مراجع)──> approved
                       ▲                        │                                   │
                       └──────(reject / تعديل النص)──────────────────────────────────┘
```

- `pending`: ما في ترجمة.
- `draft`: في ترجمة، بس فيها أخطاء من المدقق، أو ما حدا جهّزها للمراجعة لسا.
- `needs_review`: ما فيها أخطاء، وناطرة مراجع. ترجمات الدفعات بتوصل هون إذا ما فيها أخطاء.
- `approved`: مراجع بشري اعتمدها باسمه، وما فيها أخطاء تدقيق. **بس هدول بيدخلوا المود.**

قاعدة البيانات نفسها بتمنع `approved` بلا اسم مراجع. استيراد CSV ما بيعمل `approved` أبداً. وأي تعديل على نص معتمد بيرجعه `draft`.

## الأوامر (من مجلد المشروع)

```
.venv\Scripts\python cli.py init
.venv\Scripts\python cli.py glossary-load
.venv\Scripts\python cli.py import tests\samples\samples_original.csv
.venv\Scripts\python cli.py status
.venv\Scripts\python cli.py validate
.venv\Scripts\python cli.py preview --status draft
.venv\Scripts\python cli.py review sample:ui:stat_insight --by Dana
.venv\Scripts\python cli.py approve sample:ui:stat_insight --reviewer Dana
.venv\Scripts\python cli.py export translation\export.csv
```

### نسخ الذكر والأنثى

النص بيضل `shared` (ترجمة وحدة)، إلا إذا الصياغة العربية بتتغير حسب جنس شخصية اللاعب، ولو بالتشكيل بس. وقتها بيصير إله نسختين `male` و`female` بنفس المعرّف، وكل نسخة بتنراجع وبتنعتمد لحالها.

```
.venv\Scripts\python cli.py show demo:system:received
.venv\Scripts\python cli.py gender-split demo:system:received --male "تسلّمتَ %s." --female "تسلّمتِ %s." --by Dana
.venv\Scripts\python cli.py review demo:system:received --gender female --by Dana
.venv\Scripts\python cli.py approve demo:system:received --gender female --reviewer Dana
.venv\Scripts\python cli.py select-gender female
```
- النص اللي إله نسختين، أي أمر عليه بدون `--gender` بينرفض.
- بالدفعات، عمود `translation_female` هو اللي بيعمل التقسيم. الشرح كامل بـ `translation/TRANSLATOR.md`.
- الجنس النشط بينحفظ بقاعدة البيانات، وما بيتغير إلا بأمر `select-gender`.
- `gender-merge` (رجعة النسختين لترجمة وحدة) **معطّل مؤقتاً** لحد المخطط v3، لأنو كان بيعدّل سجل التاريخ (القاعدة 9). الأمر بيرفض قبل ما يلمس قاعدة البيانات.

### دفعات الترجمة

```
.venv\Scripts\python cli.py sources-import my_texts.csv --dry-run
.venv\Scripts\python cli.py sources-import my_texts.csv
.venv\Scripts\python cli.py batch-export --limit 20
.venv\Scripts\python cli.py batch-import translation\batches\in\batch_XXXX.csv --dry-run
.venv\Scripts\python cli.py batch-import translation\batches\in\batch_XXXX.csv
.venv\Scripts\python cli.py review-export
.venv\Scripts\python cli.py batch-list
.venv\Scripts\python cli.py batch-cancel batch_XXXX --by Dana --reason "file lost"
```
- ملف الدفعة بيطلع بـ `translation\batches\out\`، ومنرجّعه مترجم لـ `translation\batches\in\`. التفاصيل بـ `translation/README.md`، وتعليمات المترجم بـ `translation/TRANSLATOR.md`.
- `--dry-run` بيفحص وبيكتب التقرير بس، وقاعدة البيانات بتضل متل ما هي.
- الاستيراد ما بيعتمد شي، وما بيغيّر نص معتمد أبداً. لتغيير نص معتمد، المراجع بيعمل `reject` أول.
- `review-export` بيكتب ورقة مراجعة CSV للقراءة بس. الاعتماد بيضل نص نص بأمر `approve`.

الاختبارات: `powershell -ExecutionPolicy Bypass -File tools\run_tests.ps1`. بتشغّل `tools\run_tests_isolated.py`، وهو بيتأكد إنو الاختبارات ما غيّرت `logs\` ولا `translation\batches\` ولا `backups\` ولا `database\`، وإنها نضّفت ملفاتها المؤقتة.

## قواعد المشروع

- ملفات اللعبة الأصلية ما بتنعدل أبداً. المود بيتبنى بمجلد منفصل.
- ما بننشر ولا بنوزع أي ملف من ملفات اللعبة (`.gitignore` بيمنعهم).
- قاعدة البيانات بتخزن العربي بشكله **المنطقي** (حروف عادية). ربط الحروف وقلب الاتجاه بيصيروا بس وقت المعاينة والبناء.
- العينات بالمشروع من تأليفنا، مش منسوخة من اللعبة.

## أدوات مساعدة

تم استعمال Claude وCodex بالمساعدة بالكود والترجمة والمراجعة. كل ترجمة بتنراجع وبتنعتمد من إنسان.
