# Bloodborne-Arabic-Localization

مشروع تعريب مجتمعي للعبة Bloodborne على نسخة bbport.
المترجم الأساسي هو Claude، وبيشتغل على دفعات CSV. ترجماته بتوصل لـ `needs_review` إذا ما فيها أخطاء، وإذا فيها أخطاء بتضل `draft`. كل سطر لازم يراجعه إنسان ويعتمده باسمه قبل ما يدخل المود.

## بنية المجلدات

| المجلد | شو فيه | الحالة |
|---|---|---|
| `common` | أدوات مشتركة: المسارات، كشف الأكواد والمتغيرات، معالجة العربي (ربط الحروف + RTL) | ✅ |
| `database` | قاعدة SQLite (`schema.sql` + `db.py`): النصوص، الحالات، السجل، القاموس | ✅ |
| `glossary` | `glossary.csv` (القاموس) و`style_guide_ar.md` (دليل الأسلوب) | ✅ |
| `validation` | المدقق: الأكواد، القاموس، الطول، اليونيكود، الفراغات والتكرار | ✅ |
| `fonts` | فحص دعم الخط للعربي + صور معاينة PNG. حطي هون خطوط OFL | ✅ |
| `tests` | اختبارات + عينات أصلية من تأليفنا (`tests/samples`) | ✅ |
| `translation` | دفعات الترجمة (CSV): `batch.py` و`CLAUDE_TRANSLATOR.md` و`batches/out` و`batches/in` | ✅ |
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

### دفعات الترجمة

```
.venv\Scripts\python cli.py sources-import my_texts.csv
.venv\Scripts\python cli.py batch-export --limit 40
.venv\Scripts\python cli.py batch-import translation\batches\in\batch_XXXX.csv
.venv\Scripts\python cli.py batch-list
```
ملف الدفعة بيطلع بـ `translation\batches\out\`، ومنرجّعه مترجم لـ `translation\batches\in\`. التفاصيل بـ `translation/README.md`، وتعليمات المترجم بـ `translation/CLAUDE_TRANSLATOR.md`.

الاختبارات: `powershell -ExecutionPolicy Bypass -File tools\run_tests.ps1`

## قواعد المشروع

- ملفات اللعبة الأصلية ما بتنعدل أبداً. المود بيتبنى بمجلد منفصل.
- ما بننشر ولا بنوزع أي ملف من ملفات اللعبة (`.gitignore` بيمنعهم).
- قاعدة البيانات بتخزن العربي بشكله **المنطقي** (حروف عادية). ربط الحروف وقلب الاتجاه بيصيروا بس وقت المعاينة والبناء.
- العينات بالمشروع من تأليفنا، مش منسوخة من اللعبة.
