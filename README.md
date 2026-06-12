# recoo

**Modular, controllable reconnaissance automation for authorized bug-bounty
and pentest engagements.**

أداة أتمتة استطلاع (Recon) منظّمة وقويّة. تعطيها دومين (أو ملف فيه دومينات) وهي
تنفّذ خطوات الميثودولوجي كاملة — من اكتشاف الجذور حتى تحويل النتائج إلى
attack vectors — مع الميزة الأهم: **تحكّم كامل في تشغيل/إيقاف كل أداة على حدة**.

> ⚠️ For use only against targets you are explicitly authorized to test
> (bug-bounty programs in scope, signed pentest engagements, your own
> assets). You are responsible for staying within the rules of engagement.

---

## لماذا recoo؟ / Why recoo

معظم سكربتات الـ recon "كل شيء أو لا شيء". recoo مبنيّ على فكرة واحدة: **كل
أداة هي صف واحد في ملف إعدادات، تشغّلها أو توقفها كيفما شئت** — من الملف أو من
سطر الأوامر — دون تعديل أي كود.

* 🎛️ **تحكّم بالأدوات** — `--only` / `--enable` / `--disable` / `--stages` /
  `--skip-stages` / `--exclude-tags`، أو `enabled: true|false` في الإعدادات.
* 🧩 **مبني بالبيانات** — كل الأدوات معرّفة في `recoo/tools.yaml`؛ أضف أداتك
  بصف واحد.
* 🔗 **خط أنابيب متّصل** — مخرجات كل مرحلة تغذّي التي بعدها تلقائيًا
  (seeds → subs → resolved → live → urls → js → params → findings).
* 🛟 **تخطّي آمن** — أي أداة غير مثبّتة على النظام يتم تجاوزها تلقائيًا.
* 📁 **مخرجات منظّمة** — شجرة مجلدات نظيفة + دمج وإزالة تكرار (`anew`) + ملخّص.
* 🔍 **معاينة قبل التنفيذ** — `--dry-run` يطبع الأوامر الفعلية دون تشغيلها.

---

## التثبيت / Install

```bash
git clone https://github.com/carbo0on/recoo
cd recoo

# 1) اعتماد recoo الوحيد (Python)
pip3 install -r requirements.txt

# 2) أدوات الـ recon الخارجية (اختياري — ثبّت ما تحتاجه فقط)
chmod +x install.sh
./install.sh            # كل الأدوات | --go للأدوات الـ Go فقط | --python لأدوات pip

# 3) تأكّد ماذا يرى recoo
python3 recoo.py --list-tools
```

recoo يعمل بـ Python 3.8+ و PyYAML فقط. باقي العمل تقوم به أدوات معروفة
(subfinder, httpx, dnsx, katana, nuclei, gau, jsluice, arjun, gf …) ويتم
تخطّي أي أداة غير موجودة.

---

## الاستخدام السريع / Quick start

```bash
# دومين واحد
python3 recoo.py example.com

# ملف فيه عدّة دومينات، ومجلد مخرجات مخصّص
python3 recoo.py domains.txt -o acme

# دومين واحد صراحةً
python3 recoo.py -d example.com -o acme
```

### التحكّم في الأدوات (الميزة الأساسية)

```bash
# شغّل أدوات محدّدة فقط
python3 recoo.py -d example.com --only subfinder,httpx,katana,nuclei

# شغّل كل الافتراضي إلا أدوات معيّنة
python3 recoo.py -d example.com --disable nuclei,amass_passive

# فعّل أدوات متوقفة افتراضيًا
python3 recoo.py -d example.com --enable naabu,permutations,cloud_enum

# نفّذ مراحل بعينها فقط
python3 recoo.py -d example.com --stages subdomains,resolve,probe

# استثنِ الأدوات البطيئة/المزعجة/التي تحتاج مفاتيح
python3 recoo.py -d example.com --exclude-tags slow,noisy,needs-key

# عاين الأوامر دون تنفيذ
python3 recoo.py -d example.com --dry-run -v
```

### أنماط العمق / Depth profiles

تقسيمة إضافية حسب العمق (محور مختلف عن المراحل والأدوات): اختر نمطًا واحدًا فيشغّل
مجموعة أدوات منتقاة، ثم نقّحه بـ `--enable/--disable` أو الواجهة التفاعلية.

```bash
python3 recoo.py -d example.com --profile fast     # سريع: passive + resolve + probe + triage
python3 recoo.py -d example.com --profile medium   # متوسط: + crawl + JS + params + screenshots + nuclei
python3 recoo.py -d example.com --profile deep      # عميق جدًا: كل شيء (brute, ports, cloud, OSINT)
python3 recoo.py --list-profiles                    # اعرض الأنماط الثلاثة وأدواتها
```

### الواجهة التفاعلية / Interactive checklist

أضف `-i` لتظهر أمامك **تشيك ليست** بالأدوات المختارة، فتزيل أو تضيف أيًّا منها قبل
التشغيل (أسهم ↑/↓، مسافة للتبديل، Enter للتشغيل — أو أرقام في الوضع البديل):

```bash
python3 recoo.py -d example.com --profile medium -i
```

### تصنيف روابط الحقن / Injection candidate classification

أداة `injection_classify` (مدمجة، بلا اعتماديات) تستخرج الروابط المرشّحة للإصابة
بثغرات الحقن وتصنّفها حسب نوع الثغرة:

```
findings/injection_candidates.txt        # تقرير موحّد موسوم
findings/injection/sqli.txt              # ملف لكل صنف:
findings/injection/{xss,ssrf,lfi,rce,ssti,redirect,idor}.txt
```

### السكرين شوت / Screenshots

مرحلة `screenshots` تلتقط صورة لكل live host/port عبر `gowitness` (أو `aquatone`)
إلى مجلد `screenshots/`، وتظهر في تقرير الـ HTML كمعرض صور.

### تقرير HTML / HTML report

يُنشأ تلقائيًا في نهاية كل تشغيل: `report.html` — صفحة واحدة منظّمة (dark theme)
فيها البطاقات الإحصائية، جدول الـ live hosts، مرشّحات الحقن مصنّفة، معرض السكرين
شوت، وملخّص الأدوات. لإعادة توليده من نتائج موجودة: `--report-only`. لتعطيله:
`--no-html`.

### الفحص / Inspect

```bash
python3 recoo.py --list-tools     # كل أداة وحالتها (مفعّلة/متوقفة) + الوسوم
python3 recoo.py --list-stages    # مراحل الـ pipeline وعدد الأدوات المفعّلة بكلٍّ
python3 recoo.py --list-profiles  # أنماط العمق (fast / medium / deep)
```

---

## المراحل / Pipeline stages

| # | Stage | يفعل |
|---|-------|------|
| 1 | `seeds` | اكتشاف الجذور: ASN، CIDR، apex domains |
| 2 | `subdomains` | حصر الـ subdomains (passive + active + brute) |
| 3 | `permutations` | توليد وحلّ التباديل (api-staging, dev2 …) |
| 4 | `resolve` | حلّ DNS + مرشّحات الـ takeover |
| 5 | `probe` | HTTP probing + بصمة التقنيات (httpx) |
| 6 | `ports` | فحص المنافذ على الأصول المباشرة |
| 7 | `screenshots` | التقاط صورة لكل live host/port (gowitness/aquatone) |
| 8 | `crawl` | زحف حيّ (JS-aware) + URLs تاريخية |
| 9 | `urls` | تصنيف الـ URLs (clean / params / js) |
| 10 | `js` | تحليل JS عميق: endpoints + secrets + source maps |
| 11 | `params` | اكتشاف parameters مخفيّة |
| 12 | `apis` | أسطح API حديثة (REST/GraphQL/docs) |
| 13 | `cloud` | تخزين سحابي / edge / buckets |
| 14 | `osint` | OSINT و secrets و code leaks |
| 15 | `triage` | تصنيف روابط الحقن + gf + nuclei |
| 16 | `monitoring` | استطلاع مستمر: diff + notify |

---

## شجرة المخرجات / Output layout

```
<output>/
├── seeds/        apex domains, ASNs, CIDRs
├── subs/         raw + resolved subdomains  (all.txt, resolved.txt)
├── hosts/        httpx.json, live.txt, ports.txt
├── urls/         all.txt, clean.txt, with_params.txt
├── js/           js_urls.txt + extracted endpoints
├── params/       discovered parameters
├── screenshots/  gowitness/aquatone captures
├── findings/     takeovers, gf_*, nuclei, secrets, api_surface, source_maps
│   ├── injection_candidates.txt        # روابط الحقن مصنّفة (موحّد)
│   └── injection/{sqli,xss,ssrf,...}.txt
├── monitoring/   snapshots + diffs over time
├── report.html   تقرير HTML منظّم (يُنشأ تلقائيًا)
└── .recoo/       run.json metadata
```

---

## التحكّم عبر ملف الإعدادات / Config file

انسخ `config.example.yaml` إلى `config.yaml` ومرّره بـ `-c`:

```bash
python3 recoo.py -d example.com -c config.yaml
```

في الملف يمكنك ضبط الإعدادات العامة (threads, timeout, wordlists, tokens)
وتشغيل/إيقاف أي أداة، أو حتى تغيير أمرها:

```yaml
settings:
  threads: 50
  resolvers: wordlists/resolvers.txt

tools:
  amass_passive: false          # اختصار: أوقفها
  naabu:                        # أو عدّل أي حقل
    enabled: true
    timeout: 600
  nuclei:
    cmd: "nuclei -l {input} -t cves/ -severity critical -silent"
```

---

## إضافة أداتك الخاصة / Add your own tool

أضف صفًا في `recoo/tools.yaml` (أو في `config.yaml`):

```yaml
  my_tool:
    stage: subdomains
    desc: "أداتي الخاصة"
    bin: mytool
    input: seeds          # الـ artifact الذي تقرأ منه
    cmd: "mytool -dL {input} -silent"
    output: "subs/mytool.txt"
    produces: subs_all    # الـ artifact الذي تدمج فيه نتائجها
    enabled: true
```

**Placeholders المتاحة:** `{input}` `{item}` `{output}` `{domain}`
`{threads}` `{resolvers}` `{wordlist_dns}` `{wordlist_content}`
`{wordlist_params}` `{wordlist_perms}` `{github_token}` `{workspace}`.

**أنماط التشغيل (`mode`):** `list` (يمرّر ملف الإدخال)، `each` (يكرّر لكل سطر
عبر `{item}`)، `none` (مرّة واحدة بلا إدخال).

---

## ملاحظة أخلاقية / Ethics

كل ما سبق مخصّص للاستخدام داخل برامج مصرّح بها وبحسابات تملكها، دون التأثير على
بيانات أو خدمات المستخدمين الحقيقيين، ووفق الـ Rules of Engagement.
