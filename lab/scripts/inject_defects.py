#!/usr/bin/env python3
"""Контрольована ін'єкція СИНТЕТИЧНИХ дефектів: data/lab11 (чисте сховище ЛР 11) -> data/baseline (baseline-v2).

Навіщо: сховище ЛР 11 майже ідеально чисте (немає дублікатів, суперечностей), тож ЛР 14 нічого було б вимірювати й виправляти.
Тут імітуємо «другий потік даних» (feed-b-import, дзеркала статей), який вносить типові проблеми.
Усі дефекти ЗАДОКУМЕНТОВАНО й мають відомий ground truth: quality/ground_truth/injected_defects.json.
  * ground truth призначено ЛИШЕ для оцінювання (precision/recall) і перевірки виправлень;
    dedup.py та інші виявлячі не мають його читати;
  * у звіті й декларації ШІ це описується як синтетичні дефекти, а не реальні помилки джерел.

Запуск:  python scripts/inject_defects.py        (детермінований, seed 42; перезаписує data/baseline)
"""
import csv, json, random, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, DST = ROOT / "data" / "lab11", ROOT / "data" / "baseline"
GT_PATH = ROOT / "quality" / "ground_truth" / "injected_defects.json"
SEED = 42
rng = random.Random(SEED)
FEED_B, LEGACY, CODESHARE = "feed-b-import", "legacy-roster", "codeshare-feed"

# ----------------------------------------------------------------- CSV
def read(name):
    with open(SRC / "graph" / f"{name}.csv", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def write(name, fields, rows):
    with open(DST / "graph" / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


T = {}
for n in ["flights", "aircraft", "airlines", "airports", "cities", "countries", "delays", "passengers", "pilots", "risks",
          "rel_affected_by", "rel_arrives_at", "rel_at_airport", "rel_caused_by", "rel_departs_from", "rel_has_delay",
          "rel_has_passenger", "rel_has_pilot", "rel_in_country", "rel_located_in", "rel_next_leg", "rel_operated_by",
          "rel_owned_by", "rel_uses_aircraft"]:
    T[n] = read(n)
rows = lambda n: T[n][1]

DEFECTS, DUP_PAIRS, DECOYS, MIRRORS = [], [], [], []


def defect(dtype, dim, refs, detail):
    d = {"id": f"D{len(DEFECTS) + 1:03d}", "type": dtype, "dimension": dim, "refs": refs, "detail": detail}
    DEFECTS.append(d)
    return d["id"]


# ----------------------------------------------------------------- варіанти рядків
KEYB = {"a": "qs", "e": "wr", "o": "ip", "i": "uo", "n": "bm", "s": "ad", "r": "et", "l": "k", "t": "ry", "k": "jl", "m": "n", "u": "yi", "y": "tu", "h": "gj"}
# Лише реальні українські варіанти транслітерації (КМУ 2010 / стара паспортна 1996 / ISO 9 / англомовне написання); жодних російських форм.
TRANSLIT = [("Andrii", "Andriy"), ("Sofiia", "Sofiya"), ("Yulia", "Yuliia"), ("Hanna", "Ganna"), ("Kyiv", "Kyyiv"), ("Lviv", "L'viv"),
            ("Kharkiv", "Harkiv"), ("Zhuliany", "Zhulyany"), ("Halytskyi", "Halytskyj"), ("kh", "h")]


def v_typo(s):
    i = rng.choice([k for k, c in enumerate(s) if c.isalpha() and 0 < k < len(s) - 1])
    op = rng.choice(["swap", "delete", "dup", "kb"])
    if op == "swap" and s[i + 1].isalpha():
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op == "delete":
        return s[:i] + s[i + 1:]
    if op == "dup":
        return s[:i] + s[i] + s[i:]
    if s[i].lower() in KEYB:
        c = rng.choice(KEYB[s[i].lower()])
        return s[:i] + (c.upper() if s[i].isupper() else c) + s[i + 1:]
    return s[:i] + s[i + 1:]


def v_translit(s):
    for a, b in TRANSLIT:
        if a in s:
            return s.replace(a, b, 1)
    return None


def v_initial(s):
    p = s.split()
    return f"{p[0][0]}. {' '.join(p[1:])}" if len(p) >= 2 else None


def v_order(s):
    p = s.split()
    return f"{' '.join(p[1:])} {p[0]}" if len(p) >= 2 else None


def v_suffix(s):
    return s + " International Airport" if "Airport" not in s else s.replace(" Airport", "")


PERSON_VARIANTS = [("typo", v_typo), ("translit", v_translit), ("initial", v_initial), ("order", v_order), ("case", lambda s: s.upper())]
AIRPORT_VARIANTS = [("translit", v_translit), ("suffix", v_suffix), ("typo", v_typo)]


def variant(name, options):
    opts = options[:]
    rng.shuffle(opts)
    for kind, fn in opts:
        out = fn(name)
        if out and out != name:
            return kind, out
    return "typo", v_typo(name)


def next_id(prefix, existing, width=3):
    n = max(int(re.sub(r"\D", "", e) or 0) for e in existing if e.startswith(prefix)) + 1
    return f"{prefix}{n:0{width}d}"


def reassign_half(rel, col, old, new, source=FEED_B, conf="0.8"):
    """Половину зв'язків, що вказують на `old`, переносимо на дубль `new` (джерело feed-b)."""
    hit = [r for r in rows(rel) if r[col] == old]
    k = max(1, len(hit) // 2) if hit else 0
    for r in rng.sample(hit, k):
        r[col] = new
        r["source"], r["confidence"] = source, conf
    return k


# ===================================================================== ГРАФ
used = set()
def pick(table, idcol, n, exclude=()):
    pool = [r for r in rows(table) if r[idcol] not in used and r[idcol] not in exclude]
    chosen = rng.sample(pool, n)
    used.update(r[idcol] for r in chosen)
    return chosen


# --- A. Дублікати сутностей ---------------------------------------------------------------
# пілоти
pilot_ids = [r["pilotId"] for r in rows("pilots")]
for orig in pick("pilots", "pilotId", 10):
    kind, nm = variant(orig["name"], PERSON_VARIANTS)
    new = next_id("P", pilot_ids + [x["pilotId"] for x in rows("pilots")], 3)
    hours = int(int(orig["flightHours"]) * (1 + rng.choice([-1, 1]) * rng.uniform(0.04, 0.12)))
    rows("pilots").append({"pilotId": new, "name": nm, "flightHours": str(hours)})
    k = reassign_half("rel_has_pilot", "endId", orig["pilotId"], new)
    DUP_PAIRS.append({"entity": "pilot", "a": orig["pilotId"], "b": new, "variant": kind, "names": [orig["name"], nm]})
    defect("duplicate", "унікальність", [orig["pilotId"], new], f"дубль пілота ({kind}): «{orig['name']}» ~ «{nm}»; {k} рейс(ів) перенесено на дубль")
    defect("value_conflict", "узгодженість", [orig["pilotId"], new], f"flightHours: {orig['flightHours']} vs {hours}")

# пасажири
for orig in pick("passengers", "passengerId", 30):
    kind, nm = variant(orig["name"], PERSON_VARIANTS)
    new = next_id("X", [x["passengerId"] for x in rows("passengers")], 3)
    rows("passengers").append({"passengerId": new, "name": nm})
    k = reassign_half("rel_has_passenger", "endId", orig["passengerId"], new)
    DUP_PAIRS.append({"entity": "passenger", "a": orig["passengerId"], "b": new, "variant": kind, "names": [orig["name"], nm]})
    defect("duplicate", "унікальність", [orig["passengerId"], new], f"дубль пасажира ({kind}): «{orig['name']}» ~ «{nm}»; {k} рейс(ів) перенесено на дубль")

# аеропорти (дубль має інший код: у feed-b внутрішній ідентифікатор)
city_ids = [r["cityId"] for r in rows("cities")]
airport_dups = pick("airports", "iata", 8)
for n, orig in enumerate(airport_dups):
    kind, nm = variant(orig["name"], AIRPORT_VARIANTS)
    new = orig["iata"].lower() + "_b"
    rows("airports").append({"iata": new, "name": nm})
    own_city = next(r["endId"] for r in rows("rel_located_in") if r["startId"] == orig["iata"])
    city = own_city
    if n < 3:                                        # 3 дублі з іншим містом -> логічна суперечність (locatedIn функціональний)
        city = rng.choice([c for c in city_ids if c != own_city])
        defect("logical_conflict", "узгодженість", [orig["iata"], new], f"locatedIn: {own_city} vs {city} для одного аеропорту")
    rows("rel_located_in").append({"startId": new, "endId": city, "source": FEED_B, "confidence": "0.8", "validFrom": "2026-10-20"})
    reassign_half("rel_departs_from", "endId", orig["iata"], new)
    reassign_half("rel_arrives_at", "endId", orig["iata"], new)
    DUP_PAIRS.append({"entity": "airport", "a": orig["iata"], "b": new, "variant": kind, "names": [orig["name"], nm]})
    defect("duplicate", "унікальність", [orig["iata"], new], f"дубль аеропорту ({kind}): «{orig['name']}» ~ «{nm}»")

# літаки: інший формат реєстрації; для 2 — суперечливі seatCapacity
for n, orig in enumerate(pick("aircraft", "registration", 4)):
    reg = orig["registration"]
    new = reg.replace("-", "") if n % 2 == 0 else reg.lower()
    seats = orig["seatCapacity"]
    if n < 2:
        seats = str(int(seats) + rng.choice([-6, -4, 4, 6, 8]))
        defect("value_conflict", "узгодженість", [reg, new], f"seatCapacity: {orig['seatCapacity']} vs {seats}")
    rows("aircraft").append({"registration": new, "model": orig["model"], "seatCapacity": seats})
    reassign_half("rel_uses_aircraft", "endId", reg, new)
    airline = next(r["endId"] for r in rows("rel_owned_by") if r["startId"] == reg)
    rows("rel_owned_by").append({"startId": new, "endId": airline, "source": FEED_B, "confidence": "0.8", "validFrom": "2026-10-20"})
    DUP_PAIRS.append({"entity": "aircraft", "a": reg, "b": new, "variant": "format", "names": [reg, new]})
    defect("duplicate", "унікальність", [reg, new], f"дубль літака (формат реєстрації): {reg} ~ {new}")

# --- B. Двійники-приманки (схожі, але РІЗНІ особи) — складні негативи для дедуплікації ------------
def first_last(s):
    p = s.split()
    return p[0], " ".join(p[1:])

from rapidfuzz import fuzz
for table, idcol, prefix, rel in (("passengers", "passengerId", "X", "rel_has_passenger"), ("pilots", "pilotId", "P", "rel_has_pilot")):
    n_decoys = 10 if table == "passengers" else 4
    base = [r for r in read(table)[1] if r[idcol] not in used]   # лише оригінали (до ін'єкції), не ті, що вже мають дубль
    firsts = sorted({first_last(r["name"])[0] for r in base})
    for orig in rng.sample(base, n_decoys):
        f, l = first_last(orig["name"])
        taken = {x["name"].lower() for x in rows(table)}      # ім'я двійника не має збігатися з наявним: інакше розмітка «різні особи» недостовірна
        cand = next(x for x in sorted((x for x in firsts if x != f), key=lambda x: -fuzz.ratio(x, f)) if f"{x} {l}".lower() not in taken)
        nm = f"{cand} {l}"
        new = next_id(prefix, [x[idcol] for x in rows(table)], 3)
        row = {idcol: new, "name": nm}
        if table == "pilots":
            row["flightHours"] = str(rng.randint(2000, 12000))
        rows(table).append(row)
        if table == "passengers":
            for fl in rng.sample([r["flightId"] for r in rows("flights")], 2):
                rows(rel).append({"startId": fl, "endId": new, "source": "synthetic-generator-v1", "confidence": "0.9", "validFrom": fl.split("-")[-1][:4] + "-" + fl.split("-")[-1][4:6] + "-" + fl.split("-")[-1][6:]})
        DECOYS.append({"entity": table[:-1], "a": orig[idcol], "b": new, "names": [orig["name"], nm]})

# --- C. Інші суперечності ----------------------------------------------------------------
fl_pool = [r["flightId"] for r in rows("flights")]
ac_pool = [r["registration"] for r in read("aircraft")[1]]
al_pool = [r["code"] for r in rows("airlines")]
rng.shuffle(fl_pool)
fl_iter = iter(fl_pool)

# C1 часова: застарілий зв'язок «рейс–літак» з legacy-roster (старіша validFrom, інший літак)
for _ in range(3):
    fl = next(fl_iter)
    cur = next(r for r in rows("rel_uses_aircraft") if r["startId"] == fl)
    other = rng.choice([a for a in ac_pool if a != cur["endId"]])
    rows("rel_uses_aircraft").append({"startId": fl, "endId": other, "source": LEGACY, "confidence": "0.7", "validFrom": "2026-10-20"})
    defect("temporal_conflict", "актуальність", [fl], f"usesAircraft: {cur['endId']} (validFrom {cur['validFrom']}) vs {other} (legacy-roster, validFrom 2026-10-20)")

# C2 значення з різних джерел: другий оператор рейсу (codeshare-feed)
for _ in range(3):
    fl = next(fl_iter)
    cur = next(r for r in rows("rel_operated_by") if r["startId"] == fl)
    other = rng.choice([a for a in al_pool if a != cur["endId"]])
    rows("rel_operated_by").append({"startId": fl, "endId": other, "source": CODESHARE, "confidence": "0.6", "validFrom": cur["validFrom"]})
    defect("value_conflict", "узгодженість", [fl], f"operatedBy: {cur['endId']} (generator, 1.0) vs {other} (codeshare-feed, 0.6)")

# C3 логічна: рейс вилітає і прилітає в один аеропорт
for _ in range(3):
    fl = next(fl_iter)
    dep = next(r for r in rows("rel_departs_from") if r["startId"] == fl)["endId"]
    arr = next(r for r in rows("rel_arrives_at") if r["startId"] == fl)
    defect("logical_conflict", "точність", [fl], f"departsFrom = arrivesAt = {dep} (було arrivesAt {arr['endId']})")
    arr["endId"] = dep

# C4 дата рейсу не збігається з scheduledDeparture
for _ in range(4):
    fl = next(fl_iter)
    r = next(x for x in rows("flights") if x["flightId"] == fl)
    old = r["date"]
    y, m, d = map(int, old.split("-"))
    r["date"] = f"{y:04d}-{m:02d}-{min(d + 1, 28):02d}" if d < 28 else f"{y:04d}-{m:02d}-{d - 1:02d}"
    defect("logical_conflict", "узгодженість", [fl], f"date {r['date']} ≠ дата в scheduledDeparture {r['scheduledDeparture'][:10]}")

# C5 термінологічна: різні назви однієї моделі літака
TERMS = {"Airbus A320": "A320", "Boeing 737-800": "B737-800", "Embraer E190": "Embraer 190", "ATR 72-600": "ATR72-600", "Airbus A220-300": "Airbus A220"}
dup_new = {p["b"] for p in DUP_PAIRS}
free = [r for r in rows("aircraft") if r["registration"] not in used and r["registration"] not in dup_new and r["model"] in TERMS]
seen = set()
for r in free:
    if r["model"] in seen or len(seen) >= 5:
        continue
    seen.add(r["model"])
    defect("terminology_conflict", "узгодженість", [r["registration"]], f"model: «{r['model']}» → «{TERMS[r['model']]}» (інша назва того ж типу)")
    r["model"] = TERMS[r["model"]]
    used.add(r["registration"])

# --- D. Повнота, валідність, походження ---------------------------------------------------
for r in pick("aircraft", "registration", 6):
    defect("missing_value", "повнота", [r["registration"]], f"seatCapacity порожнє (було {r['seatCapacity']})"); r["seatCapacity"] = ""
for r in pick("pilots", "pilotId", 4):
    defect("missing_value", "повнота", [r["pilotId"]], f"flightHours порожнє (було {r['flightHours']})"); r["flightHours"] = ""
for r in pick("flights", "flightId", 4):
    defect("missing_value", "повнота", [r["flightId"]], f"kind порожнє (було {r['kind']})"); r["kind"] = ""
for r in pick("pilots", "pilotId", 2):
    defect("invalid_value", "валідність", [r["pilotId"]], f"flightHours = -{abs(int(r['flightHours'] or 0)) or 50} (було {r['flightHours']})"); r["flightHours"] = f"-{abs(int(r['flightHours'] or 0)) or 50}"
for r in pick("delays", "delayId", 2):
    defect("invalid_value", "валідність", [r["delayId"]], f"minutes = 9999 (було {r['minutes']})"); r["minutes"] = "9999"
for r in pick("risks", "riskId", 1):
    defect("invalid_value", "валідність", [r["riskId"]], f"level = 7 поза діапазоном 1–5 (було {r['level']})"); r["level"] = "7"
for r in rng.sample(rows("rel_has_passenger"), 12):
    r["source"] = ""
defect("missing_provenance", "походження", ["rel_has_passenger"], "12 зв'язків без source")

# ----------------------------------------------------------------- запис графа
shutil.rmtree(DST, ignore_errors=True)
(DST / "graph").mkdir(parents=True)
for n, (fields, rs) in T.items():
    write(n, fields, rs)

# ===================================================================== КОРПУС
EVAL = [json.loads(l) for l in open(ROOT / "eval" / "rag_eval.jsonl", encoding="utf-8")]
gold_docs = sorted({d for t in EVAL if t["category"] == "answerable" for d in t["gold_doc_ids"]})
evidence = {}
for t in EVAL:
    if t["category"] == "answerable":
        for d in t["gold_doc_ids"]:
            evidence.setdefault(d, []).extend(t["evidence"])
SYN = {"also": "additionally", "used": "employed", "major": "large", "often": "frequently", "However": "But", "approximately": "about",
       "e.g.": "for example", "—": " - ", "the United States": "the U.S.", "begin": "start", "different": "various"}
NUM = re.compile(r"\b(1[89]\d\d|20[0-2]\d|\d{2,3})\b")


def tweak_line(line):
    if line.startswith("#") or not line.strip():
        return line
    for a, b in SYN.items():
        pat = re.compile(r"(?<!\w)" + re.escape(a) + r"(?!\w)")     # лише цілі слова: інакше «focused» -> «focemployed»
        if pat.search(line) and rng.random() < 0.35:
            line = pat.sub(b, line, count=1)
    return re.sub(r"  +", " ", line) if rng.random() < 0.5 else line.replace(". ", ".  ", 1) if rng.random() < 0.3 else line


def change_number(sentence):
    m = NUM.search(sentence)
    v = int(m.group(1))
    new = v + rng.choice([-3, -2, 2, 3, 5]) if 1800 <= v <= 2030 else max(1, int(round(v * rng.choice([0.5, 1.5, 2]))))
    return sentence[:m.start()] + str(new) + sentence[m.end():], v, new


(DST / "kb" / "wiki").mkdir(parents=True)
for f in (SRC / "kb" / "wiki").glob("*.md"):
    shutil.copy2(f, DST / "kb" / "wiki" / f.name)
shutil.copy2(SRC / "sensitivity_overrides.json", DST / "sensitivity_overrides.json")

mirror_docs = rng.sample(gold_docs, 6)
for n, doc in enumerate(sorted(mirror_docs)):
    raw = (SRC / "kb" / "wiki" / f"{doc}.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    meta = dict(l.split(": ", 1) for l in m.group(1).splitlines() if ": " in l)
    body = raw[m.end():]
    conflict = None
    if n < 4:                                                    # 4 дзеркала з перекрученим числом
        ev = [e for e in evidence.get(doc, []) if NUM.search(e)]
        cands = [s for s in re.split(r"(?<=[.!?])\s+", body) if NUM.search(s) and len(s) > 40]
        target = next((e for e in ev if e in body), None) or (rng.choice(cands) if cands else None)
        if target:
            new_s, old_v, new_v = change_number(target)
            body = body.replace(target, new_s, 1)
            conflict = {"original": target, "mirror": new_s, "old": old_v, "new": new_v}
    body = "\n".join(tweak_line(l) for l in body.split("\n"))
    mid = f"{doc}_mirror"
    mmeta = {"doc_id": mid, "title": meta["title"], "lang": "en", "type": meta.get("type", "overview"), "date": "2025-03-12",
             "source_url": f"https://wikimirror.example.org/wiki/{meta['title'].replace(' ', '_')}"}
    if n < 4:
        mmeta["license"] = "CC BY-SA 4.0"
        mmeta["sensitivity"] = "public"                          # у 2 дзеркал license і sensitivity не задано -> build_corpus підставить дефолти
    (DST / "kb" / "wiki" / f"{mid}.md").write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in mmeta.items()) + "\n---\n" + body, encoding="utf-8")
    MIRRORS.append({"original": doc, "mirror": mid, "value_conflict": conflict})
    defect("duplicate", "унікальність", [doc, mid], "дзеркало статті (стара копія 2025-03-12, дрібні редагування)")
    defect("stale", "актуальність", [mid], "date 2025-03-12 (оригінал 2026-10-31)")
    if conflict:
        defect("value_conflict", "точність", [doc, mid], f"число {conflict['old']} → {conflict['new']}: «{conflict['original'][:90]}…»")
    if n >= 4:
        defect("inconsistent_metadata", "походження", [mid], "немає license/sensitivity у front matter; дефолти build_corpus: «own work» / internal")

import subprocess
subprocess.run([sys.executable, str(ROOT / "scripts" / "build_corpus.py"), "--kb", str(DST / "kb"), "--out", str(DST / "corpus.jsonl"),
                "--ref", str(SRC / "corpus.jsonl"), "--quiet"], check=True)

# ===================================================================== ground truth + опис
GT_PATH.parent.mkdir(parents=True, exist_ok=True)
by = {}
for d in DEFECTS:
    by[d["type"]] = by.get(d["type"], 0) + 1
gt = {"version": "baseline-v2", "seed": SEED, "note": "СИНТЕТИЧНІ дефекти; лише для оцінювання. Виявлячі (dedup.py тощо) цей файл не читають.",
      "summary": {"defects": len(DEFECTS), "by_type": by, "duplicate_pairs": len(DUP_PAIRS), "decoy_pairs": len(DECOYS), "mirror_docs": len(MIRRORS)},
      "duplicate_pairs": DUP_PAIRS, "decoy_pairs": DECOYS, "mirrors": MIRRORS, "defects": DEFECTS}
GT_PATH.write_text(json.dumps(gt, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
(DST / "INJECTION.md").write_text(
    "# baseline-v2: сховище ЛР 11 + синтетичні дефекти\n\nЗгенеровано `scripts/inject_defects.py` (seed 42) з `data/lab11`. "
    "Імітує другий потік даних (feed-b-import, legacy-roster, codeshare-feed) і дзеркала статей.\n\n"
    f"Усього дефектів: {len(DEFECTS)}; за типами: {by}.\nПар-дублікатів сутностей: {len(DUP_PAIRS)}; двійників-приманок: {len(DECOYS)}; дзеркальних документів: {len(MIRRORS)}.\n\n"
    "Ground truth (для оцінювання): `quality/ground_truth/injected_defects.json`.\n", encoding="utf-8")
print(json.dumps(gt["summary"], ensure_ascii=False, indent=1))
