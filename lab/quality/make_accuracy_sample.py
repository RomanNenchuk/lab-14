#!/usr/bin/env python3
"""Результат 1, Крок 2: експертна вибірка для метрики точності (M06). 40 записів, випадково й стратифіковано (seed 42).
Вибірка НЕ знає про ін'єковані дефекти: вона випадкова, тож оцінка точності відображає їх реальну частку.

    python -m quality.make_accuracy_sample          # створює quality/accuracy_sample.csv (не перезаписує розмічене)
Експерт заповнює колонку correct: 1 (правильно) / 0 (хибно) і, за потреби, comment. Далі: python -m quality.measure --report
"""
import csv
import random
import re
import sys

from quality.store import ROOT, load_corpus, load_tables

OUT = ROOT / "quality" / "accuracy_sample.csv"
rng = random.Random(42)
VARIANT = "baseline"
t, corpus = load_tables(VARIANT), load_corpus(VARIANT)
city = dict(zip(t["cities"].cityId, t["cities"].name))
country = dict(zip(t["countries"].code, t["countries"].name))
airline = dict(zip(t["airlines"].code, t["airlines"].name))
airport = dict(zip(t["airports"].iata, t["airports"].name))
aircraft = t["aircraft"].set_index("registration").to_dict("index")
first = lambda rel: t[rel].groupby("startId").endId.first()
located, in_country = first("rel_located_in"), first("rel_in_country")
dep, arr, op, uses, owner = (first(r) for r in ("rel_departs_from", "rel_arrives_at", "rel_operated_by", "rel_uses_aircraft", "rel_owned_by"))

rows = []


def add(stratum, ref, statement, how, url=""):
    rows.append({"sample_id": f"S{len(rows) + 1:02d}", "stratum": stratum, "record_ref": ref, "statement": statement, "how_to_check": how, "check_url": url, "correct": "", "comment": ""})


# A. аеропорти: факти реального світу (довідник IATA)
for iata in rng.sample(sorted(t["rel_located_in"].startId), 10):
    c = located[iata]
    add("граф: аеропорт→місто→країна", iata, f"Аеропорт «{airport[iata]}» ({iata}) розташований у місті {city[c]}, країна {country.get(in_country.get(c), '?')}.",
        "Перевірити за довідником IATA/Вікіпедією: код, місто й країна відповідають реальному аеропорту", f"https://en.wikipedia.org/wiki/{airport[iata].replace(' ', '_')}")
# B. літаки: місткість правдоподібна для типу
for reg in rng.sample([r for r, v in aircraft.items() if v["seatCapacity"] != ""], 6):
    a = aircraft[reg]
    add("граф: літак", reg, f"Літак {reg}: модель {a['model']}, кількість місць {a['seatCapacity']}.",
        "Модель — існуючий тип; місткість правдоподібна для одного салону (для вантажного …F допустимо 0). Прийнятна різниця ±10 місць від типової", "")
# C. рейси: внутрішня несуперечність
for fid in rng.sample(list(t["flights"].flightId), 10):
    f = t["flights"].set_index("flightId").loc[fid]
    reg, al = uses.get(fid), op.get(fid)
    add("граф: рейс", fid, f"Рейс {f['flightNumber']} {f['date']} (вильот {f['scheduledDeparture']}): {airport.get(dep.get(fid), '?')} → {airport.get(arr.get(fid), '?')}; "
        f"оператор {airline.get(al, '?')}; літак {reg}, що належить {airline.get(owner.get(reg), '?')}.",
        "Усі частини узгоджені: пункти відправлення й призначення різні; дата збігається з датою вильоту; літак належить оператору (або це кодшер)", "")
# D/E. корпус: речення з числами проти джерела
sent = re.compile(r"(?<=[.!?])\s+")
def pick(chunks, n, stratum):
    rng.shuffle(chunks)
    used = 0
    for r in chunks:
        cand = [s.strip() for s in sent.split(r["text"]) if re.search(r"\d", s) and 50 <= len(s.strip()) <= 320 and "\n" not in s.strip()]
        if not cand:
            continue
        orig = r["doc_id"].removesuffix("_mirror")
        add(stratum, r["chunk_id"], rng.choice(cand), f"Знайти це твердження в статті Вікіпедії «{r['title']}»; число/факт має збігатися з поточною версією статті",
            f"https://en.wikipedia.org/wiki/{r['title'].replace(' ', '_')}")
        used += 1
        if used == n:
            return
orig_chunks = [r for r in corpus if not r["doc_id"].endswith("_mirror")]
mirror_chunks = [r for r in corpus if r["doc_id"].endswith("_mirror")]
pick(orig_chunks, 8, "корпус: оригінал")
pick(mirror_chunks, 6, "корпус: дзеркало")

if OUT.exists() and any(r["correct"] for r in csv.DictReader(open(OUT, encoding="utf-8"))) and "--force" not in sys.argv:
    sys.exit(f"{OUT.name} уже має розмітку; --force перезапише її")
with open(OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
print(f"Збережено {OUT.relative_to(ROOT)}: {len(rows)} записів")
