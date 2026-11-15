#!/usr/bin/env python3
"""Результат 2: дедуплікація (entity resolution) сховища знань.

Конвеєр:  нормалізація -> blocking -> порівняння (рядки + embeddings + атрибути) -> рішення -> [LLM-суддя на спірних] -> кластери -> злиття.

Типи сутностей: passenger, pilot, airport, aircraft (граф) і документи корпусу (дзеркала статей).
ВАЖЛИВО: детектор бачить лише імена й атрибути записів. Ідентифікатори, джерело зв'язків (source) і ground truth він НЕ використовує;
ground truth читається лише в командах оцінювання (eval, docs-eval).

    python -m quality.dedup tune                 # поріг на dev-частині pairs_gold.csv -> quality/dedup_config.json
    python -m quality.dedup eval [--judge]       # precision/recall на test (+ blocking, + наскрізна оцінка) -> quality/dedup_report.{json,md}
    python -m quality.dedup run   [--variant V]  # знайти кластери дублікатів у сховищі
    python -m quality.dedup apply                # злити знайдені кластери в data/clean (з provenance, журнал merge_log.jsonl)
    python -m quality.dedup demo                 # злити й скасувати злиття на тимчасовій копії; показати, що таблиці збіглися
    python -m quality.dedup docs-eval            # дублікати документів корпусу: оцінка проти ground truth
Embeddings: DEDUP_EMBEDDER=e5 (за замовчуванням, multilingual-e5-small) або hash (легкий запасний варіант без завантаження моделі).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from quality.store import ROOT, load_tables, variant_name

CONFIG = ROOT / "quality" / "dedup_config.json"
GOLD = ROOT / "quality" / "pairs_gold.csv"
REPORT_JSON, REPORT_MD = ROOT / "quality" / "dedup_report.json", ROOT / "quality" / "dedup_report.md"
JUDGE_LOG = ROOT / "llm_log" / "dedup_judge.jsonl"
ETYPES = ["passenger", "pilot", "airport", "aircraft"]
W_STR, W_EMB = 0.6, 0.4
DEFAULTS = {"threshold": 0.8, "uncertain_delta": 0.08, "weights": {"string": W_STR, "embedding": W_EMB}}
GENERIC_AIRPORT_WORDS = {"international", "airport", "intl"}


# ============================================================================ записи й нормалізація
@dataclass
class Rec:
    id: str
    etype: str
    name: str
    attrs: dict = field(default_factory=dict)
    norm: str = ""
    tokens: list = field(default_factory=list)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.replace("'", ""))).strip()


def normalize(etype: str, name: str) -> tuple[str, list[str]]:
    """Нормалізація: без діакритики, регістр, пунктуація; для аеропортів прибираємо службові слова; для літаків лише літери й цифри."""
    if etype == "aircraft":
        n = re.sub(r"[^a-z0-9]", "", name.lower())
        return n, [n]
    toks = fold(name).split()
    if etype == "airport":
        toks = [t for t in toks if t not in GENERIC_AIRPORT_WORDS]
    return " ".join(toks), toks


def load_records(variant: str | None = None) -> dict[str, list[Rec]]:
    t = load_tables(variant)
    grp = lambda rel, a, b: t[rel].groupby(a)[b].apply(set).to_dict()
    pax, pil = grp("rel_has_passenger", "endId", "startId"), grp("rel_has_pilot", "endId", "startId")
    dep, arr, uses = grp("rel_departs_from", "endId", "startId"), grp("rel_arrives_at", "endId", "startId"), grp("rel_uses_aircraft", "endId", "startId")
    loc = dict(zip(t["rel_located_in"].startId, t["rel_located_in"].endId))
    ctry = dict(zip(t["rel_in_country"].startId, t["rel_in_country"].endId))
    owner = dict(zip(t["rel_owned_by"].startId, t["rel_owned_by"].endId))
    out: dict[str, list[Rec]] = {k: [] for k in ETYPES}
    for r in t["passengers"].itertuples():
        out["passenger"].append(Rec(r.passengerId, "passenger", r.name, {"flights": pax.get(r.passengerId, set())}))
    for r in t["pilots"].itertuples():
        out["pilot"].append(Rec(r.pilotId, "pilot", r.name, {"hours": int(r.flightHours) if r.flightHours.lstrip("-").isdigit() else None, "flights": pil.get(r.pilotId, set())}))
    for r in t["airports"].itertuples():
        c = loc.get(r.iata)
        out["airport"].append(Rec(r.iata, "airport", r.name, {"city": c, "country": ctry.get(c), "flights": dep.get(r.iata, set()) | arr.get(r.iata, set())}))
    for r in t["aircraft"].itertuples():
        out["aircraft"].append(Rec(r.registration, "aircraft", r.registration, {"model": re.sub(r"[^a-z0-9]", "", r.model.lower().replace("airbus", "").replace("boeing", "")),
                                                                                  "seats": int(r.seatCapacity) if r.seatCapacity.isdigit() else None,
                                                                                  "airline": owner.get(r.registration), "flights": uses.get(r.registration, set())}))
    for recs in out.values():
        for r in recs:
            r.norm, r.tokens = normalize(r.etype, r.name)
    return out


# ============================================================================ blocking
def block_keys(r: Rec) -> set[str]:
    """Кілька ключів на запис (множинний blocking), щоб друкарська помилка чи ініціал не розривали пару."""
    if r.etype == "aircraft":
        digits = re.sub(r"\D", "", r.norm)
        return {r.norm, f"d:{digits}"} if digits else {r.norm}
    keys = set()
    for t in r.tokens:
        if len(t) >= 3:
            keys.add("p:" + t[:3])
            keys.add("s:" + t[-3:])
    return keys


def candidate_pairs(recs: list[Rec]) -> set[tuple[str, str]]:
    blocks: dict[str, list[str]] = {}
    for r in recs:
        for k in block_keys(r):
            blocks.setdefault(k, []).append(r.id)
    pairs = set()
    for ids in blocks.values():
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            pairs.add((a, b))
    return pairs


# ============================================================================ embeddings
class Embedder:
    """e5 (за замовчуванням) або hash: символьні 3-грами в хеш-простір (без завантаження моделі; для тестів)."""

    def __init__(self, kind: str | None = None):
        self.kind = kind or os.getenv("DEDUP_EMBEDDER", "e5")
        self.cache: dict[str, np.ndarray] = {}
        self.model = None

    def _encode_hash(self, texts):
        out = np.zeros((len(texts), 512), dtype=np.float32)
        for i, t in enumerate(texts):
            s = f"  {t}  "
            for j in range(len(s) - 2):
                out[i, zlib.crc32(s[j:j + 3].encode()) % 512] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

    def _encode_e5(self, texts):
        if self.model is None:
            from sentence_transformers import SentenceTransformer
            from src.config import EMB_MODELS
            self.model = SentenceTransformer(EMB_MODELS["e5"], device="cpu")
        return self.model.encode([f"query: {t}" for t in texts], normalize_embeddings=True, show_progress_bar=False)

    def vec(self, texts: list[str]) -> np.ndarray:
        miss = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if miss:
            for t, v in zip(miss, (self._encode_hash if self.kind == "hash" else self._encode_e5)(miss)):
                self.cache[t] = v
        return np.stack([self.cache[t] for t in texts])

    def cos(self, a: str, b: str) -> float:
        va, vb = self.vec([a, b])
        return float(va @ vb)

    def scale(self, c: float) -> float:
        """e5-small дає косинус ≈ 0.8–1.0 навіть для різних рядків, тому розтягуємо цей діапазон у 0..1."""
        return float(np.clip((c - 0.8) / 0.2, 0, 1)) if self.kind == "e5" else float(np.clip(c, 0, 1))


# ============================================================================ порівняння
def tok_sim(a: str, b: str) -> float:
    """Схожість двох токенів імені; ініціал сумісний із повним токеном на ту саму літеру."""
    if len(a) == 1 and len(b) == 1:
        return 1.0 if a == b else 0.0
    if len(a) == 1 or len(b) == 1:
        s, f = (a, b) if len(a) == 1 else (b, a)
        return 1.0 if f.startswith(s) else 0.0
    return max(fuzz.ratio(a, b), JaroWinkler.similarity(a, b) * 100) / 100


def person_sim(ta: list[str], tb: list[str]) -> float:
    """Найкраще вирівнювання токенів за всіма перестановками (порядок «ім'я прізвище» чи «прізвище ім'я»)."""
    n = max(len(ta), len(tb))
    ta, tb = ta + [""] * (n - len(ta)), tb + [""] * (n - len(tb))
    best = 0.0
    for perm in itertools.permutations(range(n)):
        s = sum(tok_sim(ta[i], tb[j]) if ta[i] and tb[j] else 0.0 for i, j in zip(range(n), perm)) / n
        best = max(best, s)
    return best


def string_sim(a: Rec, b: Rec) -> float:
    if a.etype in ("passenger", "pilot"):
        return person_sim(a.tokens, b.tokens)
    x, y = " ".join(sorted(a.tokens)), " ".join(sorted(b.tokens))
    if a.etype == "aircraft":
        return 1.0 if x == y else fuzz.ratio(x, y) / 100
    return (fuzz.token_sort_ratio(x, y) + JaroWinkler.similarity(x, y) * 100) / 200


def attr_rule(a: Rec, b: Rec) -> tuple[float, bool, str]:
    """Атрибути: (корекція оцінки, veto, пояснення). Veto означає «це точно різні сутності»."""
    if a.etype == "passenger" and a.attrs["flights"] & b.attrs["flights"]:
        return 0.0, True, "обидва записи пасажирів на одному рейсі: це різні особи"
    if a.etype == "pilot":
        ha, hb = a.attrs["hours"], b.attrs["hours"]
        if ha and hb and ha > 0 and hb > 0:
            d = abs(ha - hb) / max(ha, hb)
            return (0.03, False, f"години нальоту близькі ({d:.0%})") if d <= 0.15 else (-0.10, False, f"години нальоту різняться ({d:.0%})")
    if a.etype == "airport" and a.attrs["city"] and a.attrs["city"] == b.attrs["city"]:
        return 0.05, False, "те саме місто"
    if a.etype == "aircraft":
        if a.attrs["airline"] and b.attrs["airline"] and a.attrs["airline"] != b.attrs["airline"]:
            return 0.0, True, "різні власники"
        if a.attrs["model"] == b.attrs["model"] and a.attrs["airline"] == b.attrs["airline"]:
            return 0.05, False, "та сама модель і власник"
    return 0.0, False, ""


def score_pair(a: Rec, b: Rec, emb: Embedder, w=(W_STR, W_EMB)) -> dict:
    fs = string_sim(a, b)
    fe = emb.scale(emb.cos(a.norm, b.norm))
    adj, veto, why = attr_rule(a, b)
    s = 0.0 if veto else float(np.clip(w[0] * fs + w[1] * fe + adj, 0, 1))
    return {"score": s, "string": round(fs, 4), "embedding": round(fe, 4), "attr_adj": adj, "veto": veto, "why": why}


# ============================================================================ конфіг, оцінка
def load_config() -> dict:
    if CONFIG.exists():
        return json.load(open(CONFIG, encoding="utf-8"))
    return dict(DEFAULTS)


def decide(score: float, cfg: dict) -> str:
    if score >= cfg["threshold"]:
        return "match"
    return "uncertain" if score >= cfg["threshold"] - cfg["uncertain_delta"] else "non-match"


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0, "tp": tp, "fp": fp, "fn": fn}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(float(max(0, c - h)), 3), round(float(min(1, c + h)), 3)]


def read_gold(split: str | None = None) -> pd.DataFrame:
    g = pd.read_csv(GOLD, dtype=str, keep_default_na=False)
    g["label"] = g["label"].astype(int)
    return g if split is None else g[g.split == split]


def score_gold(g: pd.DataFrame, recs_by_type: dict, emb: Embedder, w) -> pd.DataFrame:
    idx = {t: {r.id: r for r in rs} for t, rs in recs_by_type.items()}
    rows = []
    for p in g.itertuples():
        d = score_pair(idx[p.entity_type][p.id_a], idx[p.entity_type][p.id_b], emb, w)
        rows.append({"pair_id": p.pair_id, "entity_type": p.entity_type, "label": p.label, "pair_kind": p.pair_kind, **d})
    return pd.DataFrame(rows)


# ============================================================================ tune / eval
def cmd_tune(args):
    emb = Embedder()
    dev = read_gold("dev")
    sc = score_gold(dev, load_records(args.variant), emb, (W_STR, W_EMB))
    best = None
    for th in np.arange(0.30, 0.9801, 0.005):
        pred = sc.score >= th
        tp, fp, fn = int((pred & (sc.label == 1)).sum()), int((pred & (sc.label == 0)).sum()), int((~pred & (sc.label == 1)).sum())
        m = prf(tp, fp, fn)
        key = (m["f1"], th)                                   # за рівного F1 обираємо вищий поріг (обережніше зливати)
        if best is None or key > best[0]:
            best = (key, round(float(th), 3), m)
    cfg = {"threshold": best[1], "uncertain_delta": 0.08, "weights": {"string": W_STR, "embedding": W_EMB}, "embedder": emb.kind,
           "tuned_on": f"dev, {len(dev)} пар", "dev_metrics": best[2]}
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"поріг = {cfg['threshold']} (embedder {emb.kind}); dev: {best[2]}")


def end_to_end(variant, cfg, emb, truth: set[frozenset]):
    recs = load_records(variant)
    found, n_cand = set(), 0
    pairs_all = sum(len(r) * (len(r) - 1) // 2 for r in recs.values())
    for et, rs in recs.items():
        idx = {r.id: r for r in rs}
        cands = candidate_pairs(rs)
        n_cand += len(cands)
        for a, b in cands:
            if decide(score_pair(idx[a], idx[b], emb)["score"], cfg) == "match":
                found.add(frozenset((a, b)))
    tp = len(found & truth)
    return {**prf(tp, len(found - truth), len(truth - found)), "candidates": n_cand, "all_pairs": pairs_all,
            "reduction": round(1 - n_cand / pairs_all, 4), "missed": [sorted(x) for x in truth - found], "false_pos": [sorted(x) for x in found - truth]}


def llm_review(pairs: list[dict], sleep: float = 3.0) -> dict[str, dict]:
    """LLM-суддя для спірних пар (Gemini, temperature=0). Кожен виклик журналюється в llm_log/dedup_judge.jsonl; кеш за хешем запиту."""
    import hashlib
    import time
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types
    load_dotenv(ROOT / ".env")
    JUDGE_LOG.parent.mkdir(exist_ok=True)
    cache = {}
    if JUDGE_LOG.exists():
        for line in open(JUDGE_LOG, encoding="utf-8"):
            e = json.loads(line)
            cache[e["prompt_sha"]] = e
    model, client = os.environ["GEMINI_MODEL"], genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    sysmsg = ("You decide whether two database records describe the SAME real-world entity (an airline passenger, a pilot, an airport or an aircraft). "
              "Spelling variants, transliteration, initials, swapped name order and typos are possible; two different people may share a surname or first name. "
              "Answer with JSON only: {\"same\": true|false, \"confidence\": 0..1, \"reason\": \"<one sentence>\"}.")
    out = {}
    for p in pairs:
        user = f"Entity type: {p['etype']}\nRecord A: {p['a']}\nRecord B: {p['b']}"
        sha = hashlib.sha256((sysmsg + user + model).encode()).hexdigest()[:16]
        if sha not in cache:
            r = client.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(system_instruction=sysmsg, temperature=0, seed=42))
            m = re.search(r"\{.*\}", r.text or "", re.S)
            try:
                ans = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                ans = {}
            cache[sha] = {"prompt_sha": sha, "ts": pd.Timestamp.now().isoformat(timespec="seconds"), "model": model, "params": {"temperature": 0, "seed": 42},
                          "prompt_version": "dedup-judge-v1", "etype": p["etype"], "input": user, "raw_output": r.text, "answer": ans, "human_decision": None}
            with open(JUDGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(cache[sha], ensure_ascii=False) + "\n")
            time.sleep(sleep)
        out[p["pair_id"]] = cache[sha]["answer"]
    return out


def describe(r: Rec) -> str:
    a = {k: (sorted(v)[:3] if isinstance(v, set) else v) for k, v in r.attrs.items() if v not in (None, set())}
    if "flights" in a:
        a["n_flights"] = len(r.attrs["flights"])
        a.pop("flights")
    return f"{r.name} {json.dumps(a, ensure_ascii=False)}"


def cmd_eval(args):
    cfg, emb = load_config(), Embedder()
    if cfg.get("embedder") not in (None, emb.kind):
        print(f"УВАГА: поріг підібрано для embedder={cfg['embedder']}, а зараз {emb.kind}; перезапусти tune", file=sys.stderr)
    recs = load_records(args.variant)
    test = read_gold("test")
    sc = score_gold(test, recs, emb, (cfg["weights"]["string"], cfg["weights"]["embedding"]))
    sc["decision"] = sc.score.map(lambda s: decide(s, cfg))
    pos = sc.label == 1
    base = prf(int(((sc.decision == "match") & pos).sum()), int(((sc.decision == "match") & ~pos).sum()), int(((sc.decision != "match") & pos).sum()))
    res = {"embedder": emb.kind, "threshold": cfg["threshold"], "test_pairs": len(sc), "test_positive": int(pos.sum()), "test": base,
           "test_precision_ci95": wilson(base["tp"], base["tp"] + base["fp"]), "test_recall_ci95": wilson(base["tp"], base["tp"] + base["fn"]),
           "by_type": {et: prf(int(((g.decision == "match") & (g.label == 1)).sum()), int(((g.decision == "match") & (g.label == 0)).sum()), int(((g.decision != "match") & (g.label == 1)).sum()))
                       for et, g in sc.groupby("entity_type")},
           "uncertain_pairs": int((sc.decision == "uncertain").sum()), "errors": []}
    idx = {t: {r.id: r for r in rs} for t, rs in recs.items()}
    g = test.set_index("pair_id")
    for row in sc[(sc.decision == "match") != pos].itertuples():
        res["errors"].append({"pair": row.pair_id, "type": row.entity_type, "label": int(row.label), "decision": row.decision, "score": round(row.score, 3),
                              "names": [g.loc[row.pair_id, "name_a"], g.loc[row.pair_id, "name_b"]], "kind": row.pair_kind})
    # blocking: чи потрапили позитивні пари тесту в кандидати
    cand = {t: candidate_pairs(rs) for t, rs in recs.items()}
    pos_pairs = test[test.label == 1]
    kept = sum((min(p.id_a, p.id_b), max(p.id_a, p.id_b)) in cand[p.entity_type] for p in pos_pairs.itertuples())
    res["blocking"] = {"positives_in_test": len(pos_pairs), "kept_by_blocking": kept, "recall": round(kept / max(1, len(pos_pairs)), 3)}
    # LLM-суддя на спірних
    if args.judge:
        unc = sc[sc.decision == "uncertain"]
        req = [{"pair_id": r.pair_id, "etype": r.entity_type, "a": describe(idx[r.entity_type][g.loc[r.pair_id, "id_a"]]), "b": describe(idx[r.entity_type][g.loc[r.pair_id, "id_b"]])} for r in unc.itertuples()]
        ans = llm_review(req)
        sc["final"] = sc.apply(lambda r: ("match" if ans.get(r.pair_id, {}).get("same") else "non-match") if r.decision == "uncertain" else r.decision, axis=1)
        res["with_llm_judge"] = {"judged": len(req), **prf(int(((sc.final == "match") & pos).sum()), int(((sc.final == "match") & ~pos).sum()), int(((sc.final != "match") & pos).sum()))}
    # наскрізна оцінка на всьому сховищі проти ground truth (лише оцінювання)
    gt = json.load(open(ROOT / "quality" / "ground_truth" / "injected_defects.json", encoding="utf-8"))
    truth = {frozenset((p["a"], p["b"])) for p in gt["duplicate_pairs"]}
    res["end_to_end_vs_ground_truth"] = end_to_end(args.variant, cfg, emb, truth)
    REPORT_JSON.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report_md(res, cfg)
    print(json.dumps({k: v for k, v in res.items() if k not in ("errors",)}, indent=1, ensure_ascii=False))
    print(f"помилок на test: {len(res['errors'])}; звіт: {REPORT_MD.relative_to(ROOT)}")


def write_report_md(res, cfg):
    t, e = res["test"], res["end_to_end_vs_ground_truth"]
    rows = "\n".join(f"| {k} | {v['precision']} | {v['recall']} | {v['f1']} | {v['tp']} / {v['fp']} / {v['fn']} |" for k, v in res["by_type"].items())
    err = "\n".join(f"- `{x['pair']}` ({x['type']}, {x['kind']}): «{x['names'][0]}» ~ «{x['names'][1]}», еталон {x['label']}, рішення {x['decision']}, оцінка {x['score']}" for x in res["errors"]) or "- немає"
    judge = ""
    if "with_llm_judge" in res:
        j = res["with_llm_judge"]
        judge = f"\n**З LLM-суддею на спірних парах** ({j['judged']} пар): precision {j['precision']}, recall {j['recall']}, F1 {j['f1']}.\n"
    REPORT_MD.write_text(f"""# Результат 2: оцінка дедуплікації

Embeddings: `{res['embedder']}`. Поріг {cfg['threshold']} підібрано на dev ({cfg.get('tuned_on')}); спірна зона: [{cfg['threshold'] - cfg['uncertain_delta']:.2f}, {cfg['threshold']}).

## Test ({res['test_pairs']} пар, з них дублікатів {res['test_positive']})
| Метрика | Значення | 95% інтервал Вілсона |
|---|---|---|
| precision | {t['precision']} | {res['test_precision_ci95']} |
| recall | {t['recall']} | {res['test_recall_ci95']} |
| F1 | {t['f1']} | — |

tp / fp / fn = {t['tp']} / {t['fp']} / {t['fn']}. Спірних пар: {res['uncertain_pairs']}.{judge}

| Тип | precision | recall | F1 | tp / fp / fn |
|---|---|---|---|---|
{rows}

## Blocking
Позитивних пар у test: {res['blocking']['positives_in_test']}, збережено blocking-ом: {res['blocking']['kept_by_blocking']} (recall blocking {res['blocking']['recall']}).

## Наскрізна оцінка на всьому сховищі (проти ground truth, лише для оцінювання)
Кандидатних пар {e['candidates']} із {e['all_pairs']} можливих (скорочення {e['reduction']:.1%}). precision {e['precision']}, recall {e['recall']}, F1 {e['f1']} (tp/fp/fn = {e['tp']}/{e['fp']}/{e['fn']}).
Пропущені: {e['missed']}. Хибні: {e['false_pos']}.

## Помилки на test
{err}
""", encoding="utf-8")


# ============================================================================ кластери, run, apply, demo
def find_clusters(variant, cfg, emb):
    recs = load_records(variant)
    clusters, uncertain = [], []
    for et, rs in recs.items():
        idx = {r.id: r for r in rs}
        parent = {r.id: r.id for r in rs}
        find = lambda x: x if parent[x] == x else find(parent[x])
        scored = {}
        for a, b in candidate_pairs(rs):
            d = score_pair(idx[a], idx[b], emb, (cfg["weights"]["string"], cfg["weights"]["embedding"]))
            dec = decide(d["score"], cfg)
            if dec == "match":
                parent[find(a)] = find(b)
                scored[(a, b)] = d["score"]
            elif dec == "uncertain":
                uncertain.append({"type": et, "a": a, "b": b, "score": round(d["score"], 3)})
        groups: dict[str, list[str]] = {}
        for r in rs:
            groups.setdefault(find(r.id), []).append(r.id)
        for ids in groups.values():
            if len(ids) > 1:
                sc = [v for (a, b), v in scored.items() if a in ids and b in ids]
                clusters.append({"type": et, "ids": sorted(ids), "score": round(min(sc), 3) if sc else None})
    return clusters, uncertain


def cmd_run(args):
    cfg, emb = load_config(), Embedder()
    clusters, unc = find_clusters(args.variant, cfg, emb)
    by = {}
    for c in clusters:
        by[c["type"]] = by.get(c["type"], 0) + 1
    print(f"кластерів дублікатів: {len(clusters)} {by}; спірних пар: {len(unc)}")
    for c in clusters[:15]:
        print(" ", c["type"], c["ids"], c["score"])


def cmd_apply(args):
    """Злиття знайдених кластерів у data/clean. --judge: спірні пари (поріг−0.08…поріг) віддаються LLM-судді (Gemini);
    --manual тип:id1,id2 : рішення власника (повторюваний аргумент) — для пар, яких не знайшли ні правила, ні суддя."""
    from quality.merge import GraphMerger
    cfg, emb = load_config(), Embedder()
    clusters, uncertain = find_clusters("clean", cfg, emb)
    m = GraphMerger("clean")
    for c in clusters:
        m.merge(c["type"], c["ids"], decided_by="rule", score=c["score"])
    extra = []
    if args.judge and uncertain:
        recs = load_records("clean")
        idx = {t: {r.id: r for r in rs} for t, rs in recs.items()}
        live = [u for u in uncertain if u["a"] in idx[u["type"]] and u["b"] in idx[u["type"]]]
        req = [{"pair_id": f"{u['a']}|{u['b']}", "etype": u["type"], "a": describe(idx[u["type"]][u["a"]]), "b": describe(idx[u["type"]][u["b"]])} for u in live]
        ans = llm_review(req)
        for u in live:
            if ans.get(f"{u['a']}|{u['b']}", {}).get("same"):
                extra.append((u["type"], [u["a"], u["b"]], f"llm-judge:{os.environ.get('GEMINI_MODEL', '?')}", u["score"]))
    for spec in args.manual or []:
        et, ids = spec.split(":", 1)
        extra.append((et, ids.split(","), f"owner:{args.owner}", None))
    done = 0
    for et, ids, who, sc in extra:
        try:
            m.merge(et, ids, decided_by=who, score=sc)
            done += 1
        except KeyError as e:
            print(f"пропущено {ids}: {e} (вже злито або немає такого запису)")
    m.save()
    from quality.merge import DocMerger
    dm, pairs = DocMerger("clean"), doc_pairs("clean")[0]
    for p in pairs:
        dm.merge(p["duplicate"], p["keep"], reason=f"дзеркало документа, схожість тексту {p['similarity']}", score=p["similarity"])
    print(f"злито документів-дзеркал: {len(pairs)} (kb/_quarantine/, журнал doc_merge_log.jsonl)")
    print(f"злито кластерів за правилами: {len(clusters)}; додатково (суддя/власник): {done}; журнал: data/clean/merge_log.jsonl; псевдоніми: data/clean/graph/entity_aliases.csv")


def cmd_demo(args):
    """Демонстрація C4: злиття зі збереженням provenance і unmerge на тимчасовій копії clean (або baseline, якщо clean ще не створено)."""
    from quality.merge import GraphMerger
    src = ROOT / "data" / ("clean" if (ROOT / "data" / "clean").exists() else "baseline")
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(src, tmp / "d")
    m = GraphMerger("clean", root=tmp / "d")
    before = m.snapshot()
    cfg, emb = load_config(), Embedder()
    clusters, _ = find_clusters(src.name, cfg, emb)
    cl = next(c for c in clusters if c["type"] == "passenger")
    mid = m.merge(cl["type"], cl["ids"], decided_by="demo", score=cl["score"])
    e = m.log[-1]
    print(f"злито {cl['ids']} -> {e['survivor']} ({mid}); змінено зв'язків: {len(e['rel_changes'])}, відкинуто дублів: {len(e['rel_dropped'])}")
    print("псевдоніми:\n", m.t["entity_aliases"].to_string(index=False))
    print("зв'язки survivor зберегли власні source/confidence/validFrom:")
    rel = m.t["rel_has_passenger"]
    print(rel[rel.endId == e["survivor"]].to_string(index=False))
    after = m.snapshot()
    assert after != before, "злиття нічого не змінило"
    m.unmerge(mid)
    assert m.snapshot() == before, "після unmerge таблиці не збіглися з початковими"
    print(f"unmerge {mid}: усі {len(before)} таблиць збігаються з початковим станом ✅")
    shutil.rmtree(tmp)


# ============================================================================ документи корпусу
def doc_texts(variant):
    from src.chunking import load_docs
    kb = ROOT / "data" / variant_name(variant) / "kb"
    docs = load_docs(kb)
    return {d["doc_id"]: {"title": d["title"], "date": d["meta"].get("date", ""), "url": d["meta"].get("source_url", ""), "text": re.sub(r"\s+", " ", d["body"]).strip()} for d in docs}


def doc_pairs(variant, th=0.9):
    """Дублікати документів: blocking за назвою; порівняння 1) текст (rapidfuzz), 2) метадані (назва збігається)."""
    d = doc_texts(variant)
    by_title: dict[str, list[str]] = {}
    for k, v in d.items():
        by_title.setdefault(fold(v["title"]), []).append(k)
    out = []
    for ids in by_title.values():
        for a, b in itertools.combinations(sorted(ids), 2):
            sim = fuzz.ratio(d[a]["text"][:6000], d[b]["text"][:6000]) / 100
            if sim >= th:
                # залишаємо новіший документ; за рівної дати — той, що з вікіпедійним джерелом
                keep, dup = sorted((a, b), key=lambda x: (d[x]["date"], "wikipedia.org" in d[x]["url"]), reverse=True)
                out.append({"keep": keep, "duplicate": dup, "similarity": round(sim, 3), "dates": [d[keep]["date"], d[dup]["date"]]})
    return out, len(d)


def cmd_docs_eval(args):
    pairs, n = doc_pairs(args.variant)
    gt = json.load(open(ROOT / "quality" / "ground_truth" / "injected_defects.json", encoding="utf-8"))
    truth = {frozenset((m["original"], m["mirror"])) for m in gt["mirrors"]}
    found = {frozenset((p["keep"], p["duplicate"])) for p in pairs}
    tp = len(found & truth)
    m = prf(tp, len(found - truth), len(truth - found))
    print(f"документів {n}, можливих пар {n * (n - 1) // 2}; знайдено дублікатів {len(pairs)}; {m}")
    for p in pairs:
        print(" ", p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tune", "eval", "run", "apply", "demo", "docs-eval"])
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--manual", action="append", help="apply: рішення власника, формат тип:id1,id2 (напр. airport:PRG,prg_b)")
    ap.add_argument("--owner", default="Roman Nenchuk")
    a = ap.parse_args()
    {"tune": cmd_tune, "eval": cmd_eval, "run": cmd_run, "apply": cmd_apply, "demo": cmd_demo, "docs-eval": cmd_docs_eval}[a.cmd](a)


if __name__ == "__main__":
    main()
