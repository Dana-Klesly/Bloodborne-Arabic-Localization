# تسليم مسودة تعريب Bloodborne للدمج التجريبي

طلب المستخدم إنجاز مسودة عربية واسعة ثم تسليمها لكلود لوضعها داخل اللعبة. ستتم المراجعة أثناء اللعب. جميع الترجمات مسودات، ولم يُسجّل اعتماد رسمي أو اسم مراجع.

## الملفات

- المسودة `translation/arabic_player_draft.csv`
- المصدر `extraction/output/bb_engus_messages.csv`
- المراجعة السابقة `translation/arabic_review_handoff_20261116.csv`
- القاموس `glossary/glossary.csv`
- التسليم الأقدم `translation/CHAT_HANDOFF_AR.md`

تعليمات المستخدم الأحدث تستبدل اشتراط عرض كل دفعة وانتظار الموافقة في التسليم الأقدم. أذن المستخدم باستكمال الدفعات تلقائياً وإعداد مسودة للتجربة داخل اللعبة.

## حالة الملف

| الحالة | العدد | المعنى |
|---|---:|---|
| `draft` | 40,168 | ترجمة عربية مسودة |
| `preserved` | 2,124 | رموز أو أرقام أو علامات محفوظة حرفياً |
| `deferred_internal` | 488 | نص اختبار أو معرّف داخلي أو وسم صريح بعدم الحاجة |
| `deferred_foreign_source` | 7 | نص ياباني يصف حقلاً أو سبب موت، بقي دون تخمين مقابل إنجليزي |

الإجمالي 42,787 سجلاً بترتيب المصدر ومعرّفاته. عدد `pending` يساوي 0. تشمل المسودات 14,512 نصاً إنجليزياً مختلفاً، والبقية تكرارات واختلافات قيم رقمية.

## تنسيق المسودة

احتُفظ بجميع أعمدة المصدر دون تغيير، وأُضيفت أعمدة الترجمة.

- `id` مفتاح الربط الثابت، ويتضمن نوع النص والحزمة ومعرّف الإدخال.
- `source` الإنجليزية الأصلية كما هي، بما فيها الأسطر والرموز.
- `context`, `type`, `source_file`, `fmg_name`, `bnd_entry_id`, `entry_id` بيانات المصدر الأصلية.
- `translation` العربية بترتيب Unicode المنطقي الطبيعي.
- `translation_female` موجود لكنه فارغ. لم تُنشأ نسخ إضافية فيه. بقيت إدخالات المصدر المنفصلة كما هي، ومنها المخاطبة الأنثوية الموجودة في المصدر.
- `status` حالة العمل.
- `translator_note` ملاحظات الالتباس والمصطلحات، خارج النص المعروض للاعب.
- `draft_batch` يحدد سجلات كل دفعة بدقة.

الملف UTF-8 مع BOM. استخدم محلّل CSV يدعم الحقول المقتبسة متعددة الأسطر. عدد السطور المادية أكبر من عدد سجلات CSV.

الملف مسودة مستقلة ولا يحتوي `batch_id` أو `source_hash` الخاصين بسير عمل `batch-import`. اقرأ مسار الدمج الموجود واربط باستخدام `id` وبيانات FMG الأصلية، مع التحقق من تطابق `source`.

استخدم `translation` لسجلات `draft`، واحفظ الرموز في `preserved`. أبقِ النص الأصلي في سجلات التأجيل. توجد نصوص قديمة تشير صراحة إلى Dark Souls وPlayStation®3 والأرواح وعهود قديمة. تُرجمت المتبقية منها مع ملاحظة قدمها، لكن تحديد الملفات المستخدمة فعلياً يخص مرحلة الدمج.

## المصطلحات والأسلوب

- Great Ones تعني العظماء دائماً. المفرد عظيم أو أحد العظماء وفق السياق.
- Blood Echoes تعني أصداء الدم.
- Quicksilver Bullets تعني رصاصات فضية.
- Notebook تعني كتاب الملاحظات.
- Iosefka تعني يوسيفكا.
- Oil Urn تعني قارورة الزيت.
- Visceral attacks صيغت هجمات حاسمة، بدلاً من الترجمة الحرفية التي رفضها المستخدم.
- اختير رمز كاريل في الدفعات التي راجعها المستخدم، مع وجود نقش كاريل مقترحاً في القاموس الأقدم.
- قرارات المستخدم الأحدث تسبق مقترحات القاموس، وخصوصاً الرصاصات الفضية.
- العربية فصحى طبيعية مفهومة، تحفظ المعنى والغموض والنبرة، وتتجنب الألفاظ النادرة والتراكيب الحرفية.

لا تُدرج الشروح الاختيارية من ملف المراجعة السابق في اللعبة. الأوصاف المطوّلة في المسودة الحالية ترجمات لنصوص المصدر نفسها. توجد ملاحظات قصيرة عند المواضع الملتبسة، مثل Communion وScales وlumenwood.

## مرحلة الدمج

المطلوب إعداد نسخة تعريب تجريبية لنسخة اللعبة الموجودة، بفحص أدوات المشروع والملفات المستهدفة أولاً. حافظ على الملفات الأصلية وضع الناتج التجريبي في مسار مستقل.

تصف وثائق المشروع الأقدم تصدير المعتمد فقط. طلب المستخدم الحالي تجربة المسودات أثناء اللعب، لذا يلزم مسار تجريبي يستخدم CSV مع إبقاء حالاته مسودات، دون اعتماد رسمي أو اختلاق مراجع.

افحص دعم الخط وتشكيل الحروف والاتجاه في محرك اللعبة ومسار البناء. العربية في CSV بترتيبها المنطقي. تجنّب تطبيق التشكيل أو ترتيب العرض مرتين. تحقّق داخل اللعبة من القوائم والحوارات والأوصاف متعددة الأسطر، والنصوص المختلطة بالأرقام والأزرار والمتغيرات، والقصّ وحدود العرض.

خلال إعداد الترجمة لم تتغير الشيفرة أو قاعدة البيانات أو إعدادات المشروع أو ملفات اللعبة الأصلية. لم تُنفّذ عمليات Git أو استخراج جديد أو اختبارات المشروع. لم تُبنَ حزمة للعبة ولم تُجرَ تجربة داخل اللعبة.

## مطابقة الملف المنجزة

طوبق كل حقل من حقول المصدر وكل معرّف وترتيب سجل مع CSV الأصلي. تطابقت الرموز التقنية والأرقام وعدد فواصل الأسطر ومواضع الأسطر الفارغة في جميع الترجمات والسجلات المحفوظة. لم تظهر اختلافات في هذا الفحص. تجربة العرض داخل اللعبة ما زالت مطلوبة.

وُزّعت فواصل أسطر الأوصاف المطوّلة على العربية مع الحفاظ على عددها ومواضع الفقرات الفارغة. يلزم التحقق من عرضها بالخط الفعلي.

## السجلات اليابانية المؤجّلة

أُزيلت النصوص اليابانية المنقولة من اللعبة من هذه النسخة المرفوعة، وبقيت المعرّفات. النص الكامل في ملف المصدر المحلي غير المرفوع.

- `bb:engus:system:bnd4:0` — وسم داخلي يطلب عدم الحذف
- `bb:engus:system:bnd4:1` — سبب موت عام غير محدد
- `bb:engus:system:bnd4:10` — سبب موت: السقوط
- `bb:engus:system:bnd4:100` — سبب موت: القتل على يد لاعب/عدو، فيه الرمز `<?murdererName?>`
- `bb:engus:system:bnd4:200` — سبب موت: السم
- `bb:engus:system:bnd4:1000` — سبب موت: هجوم من `<?murdererName?>`، فيه الرمز `<?murdererName?>`
- `bb:engus:system:bnd4:106000` — سبب موت مرتبط بعدو يمتص الدماغ

## حدود الدفعات

هذه أول وآخر معرّفات كل دفعة بترتيب المصدر. عمود `draft_batch` يحدد عضويتها الفعلية.

| الدفعة | السجلات | البداية | النهاية |
|---|---:|---|---|
| `carryover_01` | 126 | `bb:engus:item_name:bnd10:100` | `bb:engus:item_desc:bnd20:1612` |
| `ui_01` | 390 | `bb:engus:ui:bnd2:32005` | `bb:engus:system:bnd203:1` |
| `places_ui_02` | 365 | `bb:engus:place:bnd18:1000` | `bb:engus:ui:bnd202:400005` |
| `menus_03` | 318 | `bb:engus:ui:bnd2:35024` | `bb:engus:system:bnd203:20001001` |
| `notes_04` | 4,634 | `bb:engus:ui:bnd2:10000` | `bb:engus:ui:bnd2:1195050` |
| `controls_repeat_05` | 4,043 | `bb:engus:item_name:bnd10:1020` | `bb:engus:ui:bnd200:426361` |
| `main_menu_06` | 177 | `bb:engus:ui:bnd200:301016` | `bb:engus:ui:bnd200:430242` |
| `dialogue_07` | 229 | `bb:engus:dialogue:bnd1:400000` | `bb:engus:dialogue:bnd1:10500200` |
| `effects_08` | 12,584 | `bb:engus:item_desc:bnd35:13101` | `bb:engus:item_desc:bnd35:3143408` |
| `gem_descriptions_09` | 7,026 | `bb:engus:item_desc:bnd32:0` | `bb:engus:item_desc:bnd33:6105006` |
| `item_names_10` | 4,356 | `bb:engus:item_name:bnd10:200` | `bb:engus:item_name:bnd31:16010001` |
| `appearance_11` | 231 | `bb:engus:place:bnd19:90900` | `bb:engus:ui:bnd200:522007` |
| `stat_help_12` | 89 | `bb:engus:ui:bnd200:30200` | `bb:engus:ui:bnd200:39002` |
| `item_short_13` | 112 | `bb:engus:item_desc:bnd20:200` | `bb:engus:item_desc:bnd20:4906` |
| `weapon_descriptions_14` | 1,093 | `bb:engus:item_desc:bnd25:2000000` | `bb:engus:item_desc:bnd25:38021000` |
| `attire_runes_15` | 153 | `bb:engus:item_name:bnd12:10000` | `bb:engus:item_name:bnd31:12000009` |
| `opening_materials_16` | 85 | `bb:engus:item_desc:bnd20:4902` | `bb:engus:ui:bnd202:1001` |
| `dialogue_17` | 178 | `bb:engus:dialogue:bnd1:700402` | `bb:engus:dialogue:bnd1:1100404` |
| `dialogue_18` | 220 | `bb:engus:dialogue:bnd1:1200000` | `bb:engus:dialogue:bnd1:1400059` |
| `game_messages_19` | 290 | `bb:engus:dialogue:bnd30:14000` | `bb:engus:dialogue:bnd30:70010210` |
| `dialogue_20` | 265 | `bb:engus:dialogue:bnd1:1400100` | `bb:engus:dialogue:bnd1:2700202` |
| `dialogue_21` | 294 | `bb:engus:dialogue:bnd1:1401300` | `bb:engus:dialogue:bnd1:10601700` |
| `dialogue_22` | 259 | `bb:engus:dialogue:bnd1:1900104` | `bb:engus:dialogue:bnd1:2300450` |
| `dialogue_23` | 263 | `bb:engus:dialogue:bnd1:2300406` | `bb:engus:dialogue:bnd1:2502053` |
| `dialogue_24` | 313 | `bb:engus:dialogue:bnd1:2502100` | `bb:engus:dialogue:bnd30:14000210` |
| `dialogue_25` | 323 | `bb:engus:dialogue:bnd1:10200402` | `bb:engus:dialogue:bnd1:12000517` |
| `dialogue_26` | 84 | `bb:engus:dialogue:bnd1:10700600` | `bb:engus:dialogue:bnd1:12000514` |
| `game_messages_27` | 312 | `bb:engus:dialogue:bnd30:10010713` | `bb:engus:dialogue:bnd204:302210` |
| `system_messages_28` | 236 | `bb:engus:dialogue:bnd204:15` | `bb:engus:dialogue:bnd204:506002` |
| `item_lore_29` | 35 | `bb:engus:item_desc:bnd20:4903` | `bb:engus:item_desc:bnd24:1260` |
| `item_lore_30` | 74 | `bb:engus:item_desc:bnd24:1270` | `bb:engus:item_desc:bnd24:1684` |
| `item_lore_31` | 36 | `bb:engus:item_desc:bnd24:1700` | `bb:engus:item_desc:bnd24:4103` |
| `item_lore_32` | 42 | `bb:engus:item_desc:bnd24:4104` | `bb:engus:item_desc:bnd24:6300` |
| `ritual_lore_33` | 61 | `bb:engus:item_desc:bnd24:6109` | `bb:engus:item_desc:bnd24:7200` |
| `firearms_lore_34` | 226 | `bb:engus:item_desc:bnd25:6000000` | `bb:engus:item_desc:bnd25:36001000` |
| `attire_lore_35` | 54 | `bb:engus:item_desc:bnd26:10000` | `bb:engus:item_desc:bnd26:151000` |
| `attire_lore_36` | 51 | `bb:engus:item_desc:bnd26:152000` | `bb:engus:item_desc:bnd26:352000` |
| `attire_runes_37` | 55 | `bb:engus:item_desc:bnd26:353000` | `bb:engus:item_desc:bnd32:16010001` |
| `rune_lore_38` | 34 | `bb:engus:item_desc:bnd33:11000000` | `bb:engus:item_desc:bnd33:16010001` |
| `legacy_controls_39` | 150 | `bb:engus:dialogue:bnd30:70002901` | `bb:engus:ui:bnd91:10006` |
| `legacy_messages_40` | 138 | `bb:engus:dialogue:bnd78:5` | `bb:engus:system:bnd92:20001015` |
| `legacy_ui_41` | 164 | `bb:engus:ui:bnd70:114110` | `bb:engus:ui:bnd76:305001` |

## بصمات الملفات وقت التسليم

- `extraction/output/bb_engus_messages.csv` SHA-256 `156b78be1d74ac58ef6cdbfc7a62c9321ee3da5915fef46e2beb9f4093378397`
- `translation/arabic_player_draft.csv` SHA-256 `be3e562712797684ce239e14f44dabfe4c92c034293ffc0c0fc2aacf1257ac51`
