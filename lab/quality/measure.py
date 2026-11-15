#!/usr/bin/env python3
"""Результат 1, Крок 2–3: вимірювання метрик якості сховища за quality/metrics_def.yml.

    python -m quality.measure                        # baseline-v2 -> quality/metrics.json
    python -m quality.measure --variant lab11        # еталон (чисте сховище ЛР 11) -> quality/metrics_lab11.json
    python -m quality.measure --report               # + оновити автоматичну частину quality/profile_baseline.md
    python -m quality.measure --validate             # + звірити грубі правила M08/M09 із ground truth (лише ОЦІНЮВАННЯ правил)

Ground truth (quality/ground_truth/) читається ТІЛЬКИ з --validate; самі метрики його не використовують.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from rapidfuzz import fuzz, process

from quality.store import NODES, RELS, ROOT, load_corpus, load_tables, variant_name

DEF = ROOT / "quality" / "metrics_def.yml"
SAMPLE = ROOT / "quality" / "accuracy_sample.csv"
START, END = "<!-- auto:start -->", "<!-- auto:end -->"


# ----------------------------------------------------------------- допоміжне
def share(good: int, total: int) -> float:
    return good / total if total else 1.0


def ex(items, k=6):
    items = list(items)
    return [str(x) for x in items[:k]]


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0, c - h), 3), round(min(1, c + h), 3)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9' ]+", " ", s).replace("'", "")


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

    def redundant(self):
        roots = {}
        for i in range(len(self.p)):
            roots.setdefault(self.find(i), []).append(i)
        return sum(len(v) - 1 for v in roots.values())


# ----------------------------------------------------------------- спільні дані
class Ctx:
    def __init__(self, variant, cfg):
        self.variant, self.cfg = variant, cfg
        self.t = load_tables(variant)
        self.corpus = load_corpus(variant)
        self.ref = dt.date.fromisoformat(cfg["reference_date"])
        self.ids = {cls: set(self.t[table][col]) for table, (cls, col) in NODES.items()}


# ----------------------------------------------------------------- метрики
def m01(c: Ctx, d):
    tables = ["flights", "aircraft", "pilots", "passengers", "airports", "delays", "risks"]
    total = bad = 0
    miss = []
    for tb in tables:
        df = c.t[tb]
        empty = (df == "").to_numpy()
        total += empty.size
        bad += int(empty.sum())
        for r, col in zip(*np.where(empty)):
            miss.append(f"{tb}.{df.columns[col]}[{df.iloc[r, 0]}]")
    return share(total - bad, total), total, bad, miss, "порожні комірки"


def m02(c: Ctx, d):
    total = bad = 0
    miss = []
    for rel in RELS:
        df = c.t[rel]
        ok = (df[["source", "confidence", "validFrom"]] != "").all(axis=1)
        total += len(df)
        bad += int((~ok).sum())
        miss += [f"{rel}:{a}->{b}" for a, b in zip(df.startId[~ok], df.endId[~ok])]
    return share(total - bad, total), total, bad, miss, "зв'язки без source/confidence/validFrom"


def m03(c: Ctx, d):
    f = c.t["flights"].set_index("flightId")
    dep = c.t["rel_departs_from"].groupby("startId").endId.first()
    arr = c.t["rel_arrives_at"].groupby("startId").endId.first()
    bad = {}
    for fid, row in f.iterrows():
        if dep.get(fid) is not None and dep.get(fid) == arr.get(fid):
            bad.setdefault(fid, []).append("відправлення = призначення")
        if row["date"] != row["scheduledDeparture"][:10]:
            bad.setdefault(fid, []).append("date ≠ дата scheduledDeparture")
    for a, b in zip(c.t["rel_next_leg"].startId, c.t["rel_next_leg"].endId):
        if a in f.index and b in f.index and f.loc[b, "scheduledDeparture"] <= f.loc[a, "scheduledDeparture"]:
            bad.setdefault(a, []).append("nextLeg не пізніший")
    return share(len(f) - len(bad), len(f)), len(f), len(bad), [f"{k}: {'; '.join(v)}" for k, v in bad.items()], "рейси з логічними порушеннями"


FUNCTIONAL = ["rel_operated_by", "rel_uses_aircraft", "rel_departs_from", "rel_arrives_at", "rel_owned_by", "rel_located_in", "rel_in_country"]


def m04(c: Ctx, d):
    total = bad = 0
    ex_ = []
    for rel in FUNCTIONAL:
        g = c.t[rel].groupby("startId").endId.nunique()
        total += len(g)
        multi = g[g > 1]
        bad += len(multi)
        ex_ += [f"{rel}:{k} ({v} значень)" for k, v in multi.items()]
    return share(total - bad, total), total, bad, ex_, "вузли з кількома різними значеннями функціонального зв'язку"


def m05(c: Ctx, d):
    vocab = set(d["vocabulary_aircraft_models"])
    df = c.t["aircraft"]
    bad = df[~df.model.isin(vocab)]
    return share(len(df) - len(bad), len(df)), len(df), len(bad), [f"{r}: «{m}»" for r, m in zip(bad.registration, bad.model)], "моделі поза словником"


def m06(c: Ctx, d):
    """Точність: потрібна експертна розмітка quality/accuracy_sample.csv (колонка correct = 1/0)."""
    if c.variant != "baseline":
        return "n/a", 0, 0, [], "вибірку взято з baseline-v2; для інших станів потрібна власна розмітка"
    if not SAMPLE.exists():
        return "pending", 0, 0, [], "немає quality/accuracy_sample.csv (make accuracy-sample)"
    df = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
    lab = df[df["correct"].isin(["1", "0"])]
    n = len(lab)
    if n < d["min_sample"]:
        return "pending", n, 0, [], f"розмічено {n} із {len(df)}; потрібно ≥ {d['min_sample']}"
    k = int((lab["correct"] == "1").sum())
    return k / n, n, n - k, lab[lab["correct"] == "0"].sample_id.tolist(), f"95% інтервал Вілсона {wilson(k, n)}"


def m07(c: Ctx, d):
    cut = c.ref - dt.timedelta(days=d["max_age_days"])
    old = [r for r in c.corpus if dt.date.fromisoformat(r["date"]) < cut]
    docs = sorted({r["doc_id"] for r in old})
    return share(len(c.corpus) - len(old), len(c.corpus)), len(c.corpus), len(old), docs, f"застарілі фрагменти (старші за {d['max_age_days']} днів), документів: {len(docs)}"


def person_pairs(names: dict[str, str], thr=90):
    keys = list(names)
    norm = [" ".join(sorted(fold(names[k]).split())) for k in keys]
    sim = process.cdist(norm, norm, scorer=fuzz.token_sort_ratio, dtype=np.uint8)
    pairs = set()
    toks = [fold(names[k]).split() for k in keys]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ok = sim[i, j] >= thr
            if not ok:                                   # ініціал + те саме прізвище
                a, b = toks[i], toks[j]
                short = [x for x in a if len(x) == 1] and a or ([x for x in b if len(x) == 1] and b) or None
                if short is not None:
                    other = b if short is a else a
                    ini = [x for x in short if len(x) == 1][0]
                    rest = [x for x in short if len(x) > 1]
                    ok = bool(rest) and rest[0] in other and any(x.startswith(ini) and len(x) > 1 for x in other)
            if ok:
                pairs.add(frozenset((keys[i], keys[j])))
    return pairs


def airport_pairs(names: dict[str, str], thr=90):
    stop = {"international", "airport"}
    keys = list(names)
    norm = [" ".join(sorted(w for w in fold(names[k]).split() if w not in stop)) for k in keys]
    sim = process.cdist(norm, norm, scorer=fuzz.token_sort_ratio, dtype=np.uint8)
    return {frozenset((keys[i], keys[j])) for i in range(len(keys)) for j in range(i + 1, len(keys)) if sim[i, j] >= thr}


def aircraft_pairs(regs: list[str]):
    key = {}
    for r in regs:
        key.setdefault(re.sub(r"[^A-Z0-9]", "", r.upper()), []).append(r)
    return {frozenset((a, b)) for v in key.values() for i, a in enumerate(v) for b in v[i + 1:]}


def entity_pairs(c: Ctx):
    t = c.t
    out = {
        "passenger": person_pairs(dict(zip(t["passengers"].passengerId, t["passengers"].name))),
        "pilot": person_pairs(dict(zip(t["pilots"].pilotId, t["pilots"].name))),
        "airport": airport_pairs(dict(zip(t["airports"].iata, t["airports"].name))),
        "aircraft": aircraft_pairs(list(t["aircraft"].registration)),
    }
    n = sum(len(t[x]) for x in ("passengers", "pilots", "airports", "aircraft"))
    return out, n


def m08(c: Ctx, d):
    pairs, n = entity_pairs(c)
    ids = sorted({x for ps in pairs.values() for p in ps for x in p})
    uf_index = {x: i for i, x in enumerate(ids)}
    uf = UF(len(ids))
    for ps in pairs.values():
        for p in ps:
            a, b = tuple(p)
            uf.union(uf_index[a], uf_index[b])
    red = uf.redundant()
    exs = [f"{a} ~ {b}" for ps in pairs.values() for a, b in (sorted(p) for p in ps)]
    detail = {k: len(v) for k, v in pairs.items()}
    return share(n - red, n), n, red, exs, f"кандидатних пар за типами: {detail}"


def chunk_pairs(c: Ctx, thr=90):
    tx = [r["text"] for r in c.corpus]
    sim = process.cdist(tx, tx, scorer=fuzz.token_set_ratio, dtype=np.uint8, workers=-1)
    np.fill_diagonal(sim, 0)
    i, j = np.where(np.triu(sim) >= thr)
    docs = [r["doc_id"] for r in c.corpus]
    return [(a, b) for a, b in zip(i, j) if docs[a] != docs[b]]


def m09(c: Ctx, d):
    pairs = chunk_pairs(c)
    uf = UF(len(c.corpus))
    for a, b in pairs:
        uf.union(a, b)
    red = uf.redundant()
    dp = sorted({tuple(sorted((c.corpus[a]["doc_id"], c.corpus[b]["doc_id"]))) for a, b in pairs})
    return share(len(c.corpus) - red, len(c.corpus)), len(c.corpus), red, [f"{a} ~ {b}" for a, b in dp], f"кандидатних пар фрагментів: {len(pairs)}, пар документів: {len(dp)}"


def m10(c: Ctx, d):
    bad = [r for r in c.corpus if r["license"] == "own work" or ("wikipedia.org" in r["source_url"] and r["license"] != "CC BY-SA 4.0")]
    docs = sorted({r["doc_id"] for r in bad})
    return share(len(c.corpus) - len(bad), len(c.corpus)), len(c.corpus), len(bad), docs, f"фрагменти з неузгодженою ліцензією/джерелом; документів: {len(docs)}"


def m11(c: Ctx, d):
    checks = []  # (опис, series, перевірка)
    def rng(tb, col, lo, hi):
        s = c.t[tb][col]
        s = s[s != ""]
        v = pd.to_numeric(s, errors="coerce")
        bad = s[(v.isna()) | (v < lo) | (v > hi)]
        return len(s), [f"{tb}.{col}[{c.t[tb].iloc[i, 0]}]={x}" for i, x in bad.items()]
    def seats():
        df = c.t["aircraft"]
        df = df[df.seatCapacity != ""]
        v = pd.to_numeric(df.seatCapacity, errors="coerce")
        cargo = df.model.str.contains(r"\d[A-Z]*F$|freighter", case=False, regex=True)      # вантажні (…F): 0 місць допустимо
        ok = (v >= 1) & (v <= 900) | (cargo & (v == 0))
        return len(df), [f"aircraft.seatCapacity[{r}]={x}" for r, x in zip(df.registration[~ok], df.seatCapacity[~ok])]

    def iso(tb, col):
        s = c.t[tb][col]
        s = s[s != ""]
        pat = r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$"
        bad = s[~s.str.match(pat)]
        return len(s), [f"{tb}.{col}[{c.t[tb].iloc[i, 0]}]={x}" for i, x in bad.items()]
    parts = [rng("pilots", "flightHours", 0, 40000), rng("delays", "minutes", 0, 720), rng("risks", "level", 1, 5),
             seats(), iso("flights", "date"), iso("flights", "scheduledDeparture")]
    parts += [iso(rel, "validFrom") for rel in RELS]
    total = sum(p[0] for p in parts)
    bad = [x for p in parts for x in p[1]]
    return share(total - len(bad), total), total, len(bad), bad, "значення поза межами/форматом"


def m12(c: Ctx, d):
    total = bad = 0
    exs = []
    for rel, (c1, _, c2) in RELS.items():
        df = c.t[rel]
        total += len(df)
        ok = df.startId.isin(c.ids[c1]) & df.endId.isin(c.ids[c2])
        bad += int((~ok).sum())
        exs += [f"{rel}:{a}->{b}" for a, b in zip(df.startId[~ok], df.endId[~ok])]
    return share(total - bad, total), total, bad, exs, "зв'язки з неіснуючим кінцем чи кінцем не того класу"


def m13(c: Ctx, d):
    ok_s = {"public", "internal", "restricted"}
    bad = [r["chunk_id"] for r in c.corpus if r["sensitivity"] not in ok_s or not re.match(r"^https?://", r["source_url"]) or not r["text"].strip()]
    return share(len(c.corpus) - len(bad), len(c.corpus)), len(c.corpus), len(bad), bad, "фрагменти без коректних sensitivity/source_url/тексту"


def m14(c: Ctx, d):
    p = ROOT / "indexes" / c.variant / "e5_hierarchical.json"
    if not p.exists():
        return "n/a", 0, 0, [], f"індекс {p.relative_to(ROOT)} не побудовано (make index-{c.variant})"
    idx_docs = {x["doc_id"] for x in json.load(open(p, encoding="utf-8"))["chunks"]}
    docs = {r["doc_id"] for r in c.corpus}
    miss = sorted(docs - idx_docs)
    return share(len(docs) - len(miss), len(docs)), len(docs), len(miss), miss, "документи корпусу, яких немає в індексі"


FUNCS = {f"M{i:02d}": f for i, f in enumerate([m01, m02, m03, m04, m05, m06, m07, m08, m09, m10, m11, m12, m13, m14], 1)}


# ----------------------------------------------------------------- валідація правил (лише оцінювання)
def validate(c: Ctx):
    gt = json.load(open(ROOT / "quality" / "ground_truth" / "injected_defects.json", encoding="utf-8"))
    truth = {frozenset((p["a"], p["b"])) for p in gt["duplicate_pairs"]}
    pairs, _ = entity_pairs(c)
    found = {p for ps in pairs.values() for p in ps}
    tp = len(found & truth)
    ent = {"precision": round(tp / len(found), 3) if found else None, "recall": round(tp / len(truth), 3), "tp": tp, "found": len(found), "truth": len(truth)}
    decoys = {frozenset((p["a"], p["b"])) for p in gt["decoy_pairs"]}
    ent["decoys_flagged"] = len(found & decoys)
    mirror_docs = {m["mirror"] for m in gt["mirrors"]} | {m["original"] for m in gt["mirrors"]}
    cp = chunk_pairs(c)
    mir_pairs = [(a, b) for a, b in cp if {c.corpus[a]["doc_id"], c.corpus[b]["doc_id"]} <= mirror_docs]
    chunk = {"pairs_found": len(cp), "pairs_inside_mirror_docs": len(mir_pairs),
             "precision": round(len(mir_pairs) / len(cp), 3) if cp else None}
    return {"entity_rule_vs_truth": ent, "chunk_rule_vs_truth": chunk}


# ----------------------------------------------------------------- запуск
def run(variant, do_validate=False):
    cfg = yaml.safe_load(open(DEF, encoding="utf-8"))
    c = Ctx(variant, cfg)
    out = []
    for d in cfg["metrics"]:
        value, n, bad, examples, note = FUNCS[d["id"]](c, d)
        if isinstance(value, str):
            status, val = value, None
        else:
            val = round(value, 4)
            status = "ok" if value >= d["threshold"] else "fail"
        out.append({"id": d["id"], "name": d["name"], "dimension": d["dimension"], "value": val, "threshold": d["threshold"], "status": status,
                    "n_checked": n, "n_bad": bad, "examples": ex(examples), "note": note})
    man = ROOT / "quality" / f"{variant}_manifest.json"
    meta = json.load(open(man, encoding="utf-8")) if man.exists() else {}
    res = {"variant": variant, "store_version": meta.get("version"), "store_created": meta.get("created"),
           "measured": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "manifest_sha256": hashlib.sha256(man.read_bytes()).hexdigest() if man.exists() else None,
           "reference_date": cfg["reference_date"], "counts": meta.get("counts"),
           "summary": {s: sum(m["status"] == s for m in out) for s in ("ok", "fail", "pending", "n/a")}, "metrics": out}
    if do_validate:
        res["validation"] = validate(c)
    return res


ICON = {"ok": "✅", "fail": "❌", "pending": "⏳", "n/a": "—"}


def table(res):
    rows = ["| ID | Вимір | Метрика | Значення | Поріг | Статус | Перевірено | Проблем |", "|---|---|---|---|---|---|---|---|"]
    for m in res["metrics"]:
        v = "—" if m["value"] is None else f"{m['value']:.4f}"
        rows.append(f"| {m['id']} | {m['dimension']} | `{m['name']}` | {v} | ≥ {m['threshold']} | {ICON[m['status']]} | {m['n_checked']} | {m['n_bad']} |")
    return "\n".join(rows)


def write_report(res):
    p = ROOT / "quality" / "profile_baseline.md"
    block = (f"{START}\n_Згенеровано `python -m quality.measure --report`. Версія сховища: **{res['store_version']}** "
             f"(зафіксовано {res['store_created']}), виміряно {res['measured']}._\n\n{table(res)}\n\n"
             f"Підсумок: ✅ {res['summary']['ok']} · ❌ {res['summary']['fail']} · ⏳ {res['summary']['pending']} · — {res['summary']['n/a']}\n{END}")
    if p.exists() and START in p.read_text(encoding="utf-8"):
        s = p.read_text(encoding="utf-8")
        s = s[:s.index(START)] + block + s[s.index(END) + len(END):]
    else:
        s = f"# Профіль якості сховища (baseline)\n\n{block}\n"
    p.write_text(s, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    v = variant_name(a.variant)
    res = run(v, a.validate)
    out = ROOT / "quality" / ("metrics.json" if v == "baseline" else f"metrics_{v}.json")
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(table(res))
    print(f"\n{out.relative_to(ROOT)}: {res['summary']}")
    if a.validate:
        print(json.dumps(res["validation"], indent=1, ensure_ascii=False))
    if a.report and v == "baseline":
        write_report(res)
        print("оновлено quality/profile_baseline.md")


if __name__ == "__main__":
    main()
