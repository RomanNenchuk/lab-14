#!/usr/bin/env python3
"""Результат 3: суперечності та їх розв'язання.

Типи суперечностей, які шукаємо (детектор НЕ читає ground truth; той використовується лише в `eval`):
  логічна      функціональна властивість має два різні значення; суперечність підтверджує OWL-reasoner (owlrl): functional + differentFrom
  значень      різні джерела дають різне значення (зв'язки з різним source; атрибути, що розійшлися при злитті дублікатів; числа в дзеркалах документів)
  часова       два значення того самого зв'язку з різним validFrom (старе джерело проти нового)
  термінологічна  одна й та сама модель літака названа по-різному («Airbus A320» / «A320»)

Стратегії: trust (довіра до джерела за confidence), date (новіше перемагає), consensus (більшість), owner (рішення власника даних).
Альтернативи: відхилені значення не губляться, а переходять у data/clean/graph/conflict_alternatives.csv з source/confidence/validFrom
(і з рядком для повернення). Рішення пишуться в data/clean/conflict_decisions.jsonl (хто, коли, чому). `restore` скасовує рішення.

    python -m quality.conflicts detect                 # лише знайти (нічого не міняє)
    python -m quality.conflicts propose-owner          # заготовка quality/owner_decisions.csv для рішень власника
    python -m quality.conflicts apply [--owner "Ім'я"] # розв'язати в data/clean
    python -m quality.conflicts report                 # quality/conflicts.md + conflicts.json
    python -m quality.conflicts restore C-...          # скасувати рішення
    python -m quality.conflicts eval                   # recall проти ground truth (лише оцінювання)
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from quality.store import ROOT

FUNCTIONAL = ["rel_uses_aircraft", "rel_operated_by", "rel_located_in", "rel_departs_from", "rel_arrives_at", "rel_has_pilot", "rel_owned_by", "rel_in_country"]
AV = "https://example.org/aviation#"
ALT_COLS = ["conflictId", "type", "subject", "predicate", "value", "source", "confidence", "validFrom", "status", "strategy", "decidedBy", "approvedBy", "decidedAt", "restore"]
OWNER_FILE = ROOT / "quality" / "owner_decisions.csv"
OWNER_DEFAULT = "Roman Nenchuk"
NUM = re.compile(r"\d[\d,.]*\d|\d")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ============================================================================ reasoner
def reasoner_inconsistent(pred: str, subject: str, values: list[str]) -> bool:
    """Будуємо мінімальну онтологію: pred — owl:FunctionalProperty, різні значення — owl:differentFrom; OWL RL-замикання (owlrl).
    Суб'єкт із двома значеннями => owl:sameAs для них => разом із differentFrom це суперечність (ErrorMessage)."""
    import owlrl
    from rdflib import Graph, Namespace, OWL, RDF
    ns = Namespace(AV)
    g = Graph()
    g.add((ns[pred], RDF.type, OWL.FunctionalProperty))
    vs = sorted(set(values))
    for i, a in enumerate(vs):
        for b in vs[i + 1:]:
            g.add((ns[a], OWL.differentFrom, ns[b]))
    for v in vs:
        g.add((ns[subject], ns[pred], ns[v]))
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, rdfs_closure=False, axiomatic_triples=False, datatype_axioms=False).expand(g)
    return any(str(o).endswith("ErrorMessage") for _, _, o in g.triples((None, RDF.type, None)))


# ============================================================================ термінологія моделей
def disjoint_inconsistent(flight: str, airport: str) -> bool:
    import owlrl
    from rdflib import Graph, Namespace, OWL, RDF
    ns = Namespace(AV)
    g = Graph()
    g.add((ns.departsFrom, OWL.propertyDisjointWith, ns.arrivesAt))
    g.add((ns[flight], ns.departsFrom, ns[airport]))
    g.add((ns[flight], ns.arrivesAt, ns[airport]))
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, rdfs_closure=False, axiomatic_triples=False, datatype_axioms=False).expand(g)
    return any(str(o).endswith("ErrorMessage") for _, _, o in g.triples((None, RDF.type, None)))


def model_key(m: str) -> str:
    s = re.sub(r"airbus|boeing|embraer", "", m.lower())
    s = re.sub(r"[^a-z0-9]", "", s)
    return re.sub(r"^[a-z](?=\d)", "", s)          # a320 -> 320, b737800 -> 737800, e190 -> 190


def same_model(a: str, b: str) -> bool:
    ka, kb = model_key(a), model_key(b)
    if ka == kb:
        return True
    s, l = sorted((ka, kb), key=len)
    return l.startswith(s) and l[len(s):].isdigit()  # 220 ~ 220300, але 737800 !~ 737800f (вантажний варіант)


# ============================================================================ документи
def read_docs(base: Path) -> dict[str, dict]:
    from src.chunking import parse_doc
    out = {}
    for sub in ("wiki", "_quarantine"):
        d = base / "kb" / sub
        for p in sorted(d.glob("*.md")) if d.exists() else []:
            meta, body = parse_doc(p)
            out[p.stem] = {"meta": meta, "body": body, "quarantined": sub == "_quarantine"}
    return out


def sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if len(s.strip()) > 25]


def doc_value_conflicts(docs: dict[str, dict]) -> list[dict]:
    res = []
    titles: dict[str, list[str]] = {}
    for k, v in docs.items():
        titles.setdefault(re.sub(r"\W+", " ", str(v["meta"].get("title", "")).lower()).strip(), []).append(k)
    for ids in titles.values():
        for a in sorted(ids):
            for b in sorted(ids):
                if a >= b:
                    continue
                sa, sb = sentences(docs[a]["body"]), sentences(docs[b]["body"])
                setb = set(sb)
                if fuzz.ratio(" ".join(sa)[:4000], " ".join(sb)[:4000]) < 90:
                    continue
                for s in sa:
                    if s in setb:
                        continue
                    best = max(sb, key=lambda x: fuzz.ratio(s, x))
                    if fuzz.ratio(s, best) >= 85 and NUM.findall(s) != NUM.findall(best):
                        res.append({"docs": [a, b], "sentence_a": s, "sentence_b": best, "numbers": [NUM.findall(s), NUM.findall(best)]})
    return res


# ============================================================================ менеджер
class ConflictManager:
    def __init__(self, variant: str = "clean", root: Path | None = None):
        if variant != "clean" and root is None:
            raise ValueError("Розв'язання суперечностей дозволено лише в data/clean")
        self.base = root or ROOT / "data" / variant
        self.gdir = self.base / "graph"
        self.t = {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(self.gdir.glob("*.csv"))}
        self.t.setdefault("conflict_alternatives", pd.DataFrame(columns=ALT_COLS, dtype=str))
        self.log_path = self.base / "conflict_decisions.jsonl"
        self.log = [json.loads(x) for x in open(self.log_path, encoding="utf-8")] if self.log_path.exists() else []
        self.done = {e["id"] for e in self.log if not e.get("reverted")}

    # ------------------------------------------------------------------ виявлення
    def detect(self) -> list[dict]:
        out = self._functional() + self._disjoint() + self._dates() + self._terminology() + self._attr_values() + self._docs()
        return [c for c in out if c["id"] not in self.done]

    def _functional(self) -> list[dict]:
        out = []
        for rel in FUNCTIONAL:
            if rel not in self.t:
                continue
            df = self.t[rel]
            pred = re.sub(r"^rel_", "", rel)
            pred = re.sub(r"_(\w)", lambda m: m.group(1).upper(), pred)
            g = df.groupby("startId").endId.nunique()
            for sid in g[g > 1].index:
                rows = df[df.startId == sid]
                if not reasoner_inconsistent(pred, sid, list(rows.endId)):
                    continue
                cands = [{"value": r.endId, "source": r.source or "(немає)", "confidence": r.confidence, "validFrom": r.validFrom} for r in rows.itertuples()]
                kind = self._classify(rows)
                out.append({"id": f"C-{pred}-{sid}", "kind": "functional", "type": kind, "rel": rel, "predicate": pred, "subject": sid, "candidates": cands,
                            "reasoner": "inconsistent"})
        return out

    def _disjoint(self) -> list[dict]:
        """departsFrom і arrivesAt — owl:propertyDisjointWith: рейс не може вилітати й прилітати в один аеропорт (reasoner підтверджує)."""
        dep, arr = self.t["rel_departs_from"], self.t["rel_arrives_at"]
        nxt = self.t["rel_next_leg"] if "rel_next_leg" in self.t else pd.DataFrame(columns=["startId", "endId"])
        dep_of = dict(zip(dep.startId, dep.endId))
        out = []
        for r in arr.itertuples():
            if dep_of.get(r.startId) == r.endId and disjoint_inconsistent(r.startId, r.endId):
                cands = [{"value": r.endId, "source": r.source or "(немає)", "confidence": r.confidence, "validFrom": r.validFrom}]
                nl = nxt[nxt.startId == r.startId]
                if len(nl) and dep_of.get(nl.iloc[0].endId):
                    cands.append({"value": dep_of[nl.iloc[0].endId], "source": f"ланцюг рейсів: наступний рейс {nl.iloc[0].endId} вилітає звідси", "confidence": "", "validFrom": ""})
                out.append({"id": f"C-departsArrivesSame-{r.startId}", "kind": "disjoint", "type": "logical", "rel": "rel_arrives_at", "predicate": "arrivesAt", "subject": r.startId, "candidates": cands})
        return out

    def _dates(self) -> list[dict]:
        """Інваріант: flights.date збігається з датою в scheduledDeparture (і з датою в flightId)."""
        out = []
        for r in self.t["flights"].itertuples():
            sd = r.scheduledDeparture[:10]
            if sd and r.date and sd != r.date:
                out.append({"id": f"C-flightDate-{r.flightId}", "kind": "flightdate", "type": "logical", "subject": r.flightId, "predicate": "date",
                            "candidates": [{"value": sd, "source": "scheduledDeparture (і flightId) — 2 із 3 джерел", "confidence": "", "validFrom": ""},
                                           {"value": r.date, "source": "поле date", "confidence": "", "validFrom": ""}]})
        return out

    @staticmethod
    def _classify(rows: pd.DataFrame) -> str:
        """temporal: дати різні, і новіший запис ще й довіреніший (старий roster застарів); value: та сама дата, різні джерела;
        logical: дата й довіра вказують на різних переможців (це бачить лише reasoner: суперечність у самих фактах)."""
        conf = rows.confidence.astype(float)
        if rows.validFrom.nunique() > 1:
            newest = rows.validFrom.idxmax() if False else rows.index[rows.validFrom.values.argmax()]
            return "temporal" if conf.loc[newest] == conf.max() and (conf == conf.max()).sum() == 1 else "logical"
        return "value" if rows.source.nunique() > 1 else "logical"

    def _terminology(self) -> list[dict]:
        a = self.t["aircraft"]
        forms = Counter(a.model)
        models = list(forms)
        groups: list[list[str]] = []
        for m in models:
            for g in groups:
                if any(same_model(m, x) for x in g):
                    g.append(m)
                    break
            else:
                groups.append([m])
        out = []
        for g in groups:
            if len(g) < 2:
                continue
            canon = sorted(g, key=lambda m: (-forms[m], -len(m)))[0]        # консенсус: найчастіша форма; за рівності — повніша
            for m in g:
                if m == canon:
                    continue
                for r in a[a.model == m].itertuples():
                    out.append({"id": f"C-model-{r.registration}", "kind": "terminology", "type": "terminology", "subject": r.registration, "predicate": "model",
                                "candidates": [{"value": canon, "source": f"консенсус ({forms[canon]} з {sum(forms[x] for x in g)} записів)", "confidence": "", "validFrom": ""},
                                               {"value": m, "source": "aircraft.csv", "confidence": "", "validFrom": ""}], "canonical": canon})
        return out

    def _attr_values(self) -> list[dict]:
        if "merge_alternatives" not in self.t:
            return []
        out = []
        for r in self.t["merge_alternatives"].itertuples():
            if r.attribute == "name" or not r.otherValue:
                continue
            out.append({"id": f"C-val-{r.canonicalId}-{r.attribute}", "kind": "attribute", "type": "value", "subject": r.canonicalId, "predicate": r.attribute, "merge_id": r.mergeId,
                        "candidates": [{"value": r.keptValue, "source": f"запис {r.canonicalId}", "confidence": "", "validFrom": ""},
                                       {"value": r.otherValue, "source": f"запис {r.otherFrom} (дублікат)", "confidence": "0.8", "validFrom": ""}]})
        return out

    def _docs(self) -> list[dict]:
        docs = read_docs(self.base)
        out = []
        for c in doc_value_conflicts(docs):
            a, b = c["docs"]
            da, db = str(docs[a]["meta"].get("date", "")), str(docs[b]["meta"].get("date", ""))
            out.append({"id": f"C-doc-{b}", "kind": "document", "type": "value", "subject": b, "predicate": "числове значення в тексті", "docs": c["docs"],
                        "candidates": [{"value": f"{', '.join(c['numbers'][0])} — «{c['sentence_a'][:110]}»", "source": a, "confidence": "", "validFrom": da},
                                       {"value": f"{', '.join(c['numbers'][1])} — «{c['sentence_b'][:110]}»", "source": b, "confidence": "", "validFrom": db}]})
        return out

    # ------------------------------------------------------------------ стратегії
    @staticmethod
    def pick(c: dict) -> tuple[int | None, str, str]:
        """Повертає (індекс переможця, стратегія, чому). None => потрібне рішення власника."""
        cs = c["candidates"]
        conf = lambda x: float(x["confidence"] or 0)
        if c["kind"] == "terminology":
            return 0, "consensus", "найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах"
        if c["kind"] == "flightdate":
            return 0, "consensus", "дві з трьох ознак (scheduledDeparture, flightId) дають одну дату; поле date — самотнє"
        if c["kind"] == "disjoint":
            return None, "owner", "правильний аеропорт прильоту невідомий із самих даних; пропозиція береться з ланцюга рейсів (потрібне підтвердження власника)"
        if c["kind"] == "document":
            i = max(range(2), key=lambda k: cs[k]["validFrom"])
            return i, "date", f"новіший документ ({cs[i]['validFrom']}) переважає; застарілий дубль-дзеркало виводимо з обігу"
        if c["kind"] == "attribute":
            return None, "owner", "джерел із довірою не вистачає: значення залежить від того, який запис точніший"
        if c["type"] == "temporal":
            top = max(x["validFrom"] for x in cs)
            win = [i for i, x in enumerate(cs) if x["validFrom"] == top]
            if len(win) == 1:
                return win[0], "date", f"запис із validFrom {top} новіший і довіреніший; старий ({min(x['validFrom'] for x in cs)}, {cs[1 - win[0]]['source']}) — застарілий"
        confs = sorted((conf(x) for x in cs), reverse=True)
        if confs[0] > confs[1]:
            i = max(range(len(cs)), key=lambda k: conf(cs[k]))
            return i, "trust", f"джерело «{cs[i]['source']}» має вищу довіру ({cs[i]['confidence']}) за альтернативи"
        return None, "owner", "довіра й дати рівні: потрібне рішення власника"

    # ------------------------------------------------------------------ застосування
    def apply(self, owner: str = OWNER_DEFAULT, owner_file: Path = OWNER_FILE) -> dict:
        stats = Counter()
        decisions = {}
        if owner_file.exists():
            for r in pd.read_csv(owner_file, dtype=str, keep_default_na=False).itertuples():
                if r.confirmed.lower() == "yes":
                    decisions[r.conflict_id] = r.chosen
        for c in self.detect():
            i, strat, why = self.pick(c)
            if i is None and c["id"] in decisions:
                val = decisions[c["id"]]
                i = next((k for k, x in enumerate(c["candidates"]) if x["value"] == val), None)
                if i is None and val:                      # власник вказав значення, якого не було серед кандидатів: додаємо як нового кандидата
                    c["candidates"].append({"value": val, "source": f"рішення власника ({owner})", "confidence": "", "validFrom": ""})
                    i = len(c["candidates"]) - 1
                strat, why = "owner", f"рішення власника ({owner}): значення {val}"
            if i is None:
                stats["open"] += 1
                continue
            self._resolve(c, i, strat, why, owner)
            stats[c["type"]] += 1
        return dict(stats)

    def _resolve(self, c: dict, win: int, strat: str, why: str, owner: str) -> None:
        ts = now()
        decided = "rule:" + strat if strat != "owner" else f"owner:{owner}"
        restore = {}
        if c["kind"] == "functional":
            df = self.t[c["rel"]]
            for k, x in enumerate(c["candidates"]):
                if k == win:
                    continue
                m = (df.startId == c["subject"]) & (df.endId == x["value"]) & (df.source == (x["source"] if x["source"] != "(немає)" else ""))
                restore[k] = df[m].iloc[0].to_dict()
                df = df[~m]
            self.t[c["rel"]] = df
        elif c["kind"] == "disjoint":
            df = self.t["rel_arrives_at"]
            m = df.startId == c["subject"]
            restore[0] = df[m].iloc[0].to_dict()
            df.loc[m, "endId"] = c["candidates"][win]["value"]
            df.loc[m, "source"] = "owner-decision"
        elif c["kind"] == "flightdate":
            f = self.t["flights"]
            f.loc[f.flightId == c["subject"], "date"] = c["candidates"][win]["value"]
            restore[1] = {"table": "flights", "key": "flightId", "id": c["subject"], "col": "date", "old": c["candidates"][1]["value"]}
        elif c["kind"] == "terminology":
            a = self.t["aircraft"]
            a.loc[a.registration == c["subject"], "model"] = c["candidates"][win]["value"]
            restore[1] = {"registration": c["subject"], "model": c["candidates"][1]["value"]}
        elif c["kind"] == "attribute":
            tb, key = ("pilots", "pilotId") if c["subject"] in set(self.t["pilots"].pilotId) else ("aircraft", "registration")
            self.t[tb].loc[self.t[tb][key] == c["subject"], c["predicate"]] = c["candidates"][win]["value"]
            restore[1 - win] = {"table": tb, "key": key, "id": c["subject"], "col": c["predicate"], "old": c["candidates"][0]["value"]}
        elif c["kind"] == "document":
            from quality.merge import DocMerger
            dm = DocMerger("clean", root=self.base)
            dup = c["candidates"][1 - win]["source"]
            if (self.base / "kb" / "wiki" / f"{dup}.md").exists():
                dm.merge(dup, c["candidates"][win]["source"], reason="суперечність значень: " + why)
            restore[1 - win] = {"doc": dup}
        alts = [[c["id"], c["type"], c["subject"], c["predicate"], x["value"], x["source"], x["confidence"], x["validFrom"], "accepted" if k == win else "rejected",
                 strat, decided, owner, ts, json.dumps(restore[k], ensure_ascii=False) if k != win and k in restore else ""] for k, x in enumerate(c["candidates"])]
        ca = self.t["conflict_alternatives"]
        for a in alts:
            ca.loc[len(ca)] = a
        self.log.append({"id": c["id"], "type": c["type"], "kind": c["kind"], "subject": c["subject"], "predicate": c["predicate"], "candidates": c["candidates"], "winner": win,
                         "strategy": strat, "decided_by": decided, "approved_by": owner, "why": why, "ts": ts, "reverted": False})
        self.done.add(c["id"])

    def restore(self, cid: str) -> None:
        e = next((x for x in self.log if x["id"] == cid and not x["reverted"]), None)
        if e is None:
            raise ValueError(f"{cid}: немає активного рішення")
        ca = self.t["conflict_alternatives"]
        rej = ca[(ca.conflictId == cid) & (ca.status == "rejected")]
        for r in rej.itertuples():
            info = json.loads(r.restore) if r.restore else {}
            if e["kind"] == "functional" and info:
                rel = self._rel_of(cid)
                self.t[rel] = pd.concat([self.t[rel], pd.DataFrame([info])], ignore_index=True)
            elif e["kind"] == "terminology":
                a = self.t["aircraft"]
                a.loc[a.registration == info["registration"], "model"] = info["model"]
            elif e["kind"] == "disjoint" and info:
                df = self.t["rel_arrives_at"]
                self.t["rel_arrives_at"] = pd.concat([df[df.startId != e["subject"]], pd.DataFrame([info])], ignore_index=True)
            elif e["kind"] in ("attribute", "flightdate"):
                t = self.t[info["table"]]
                t.loc[t[info["key"]] == info["id"], info["col"]] = info["old"]
            elif e["kind"] == "document":
                from quality.merge import DocMerger
                dm = DocMerger("clean", root=self.base)
                m = next((x for x in dm.log if x["duplicate"] == info["doc"] and not x["reverted"]), None)
                if m:
                    dm.unmerge(m["merge_id"])
        self.t["conflict_alternatives"] = ca[ca.conflictId != cid].reset_index(drop=True)
        e["reverted"], e["reverted_at"] = True, now()
        self.done.discard(cid)

    def _rel_of(self, cid: str) -> str:
        pred = cid.split("-")[1]
        return "rel_" + re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), pred)

    def save(self) -> None:
        for name, df in self.t.items():
            df.to_csv(self.gdir / f"{name}.csv", index=False, lineterminator="\n")
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in self.log:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        return {n: sorted(tuple(r) for r in df.itertuples(index=False, name=None)) for n, df in self.t.items() if n != "conflict_alternatives"}


# ============================================================================ CLI
def cmd_detect(a):
    m = ConflictManager("clean")
    cs = m.detect()
    cnt = Counter(c["type"] for c in cs)
    print(f"знайдено суперечностей: {len(cs)} за типами {dict(cnt)}")
    for c in cs[:40]:
        i, s, w = m.pick(c)
        print(f"  {c['id']:34} {c['type']:12} стратегія={s:9} {[(x['value'], x['source']) for x in c['candidates']]}")


def cmd_propose(a):
    m = ConflictManager("clean")
    rows = []
    for c in m.detect():
        i, s, w = m.pick(c)
        if i is None:
            vals = [x["value"] for x in c["candidates"]]
            num = [float(v) for v in vals if re.fullmatch(r"-?\d+(\.\d+)?", v)]
            prop = str(int(max(num))) if len(num) == len(vals) else (vals[-1] if len(vals) > 1 else "")
            rows.append({"conflict_id": c["id"], "subject": c["subject"], "predicate": c["predicate"], "options": " | ".join(vals), "chosen": prop,
                         "confirmed": "", "note": "наліт накопичувальний: пропонуємо більше значення (лише пропозиція, перевір)" if num else "пропозиція з ланцюга рейсів (перевір)"})
    pd.DataFrame(rows).to_csv(OWNER_FILE, index=False)
    print(f"заготовка рішень власника: {OWNER_FILE.relative_to(ROOT)} ({len(rows)} рядків); заповни confirmed=yes, де згоден")


def cmd_apply(a):
    m = ConflictManager("clean")
    st = m.apply(owner=a.owner)
    m.save()
    print("розв'язано:", st, "| журнал: data/clean/conflict_decisions.jsonl; альтернативи: data/clean/graph/conflict_alternatives.csv")


def cmd_restore(a):
    m = ConflictManager("clean")
    m.restore(a.cid)
    m.save()
    print("скасовано", a.cid)


def eval_gt() -> dict:
    gt = json.load(open(ROOT / "quality" / "ground_truth" / "injected_defects.json", encoding="utf-8"))
    m = ConflictManager("clean")
    alias = m.t.get("entity_aliases", pd.DataFrame(columns=["aliasId", "canonicalId"]))
    seen = set()
    canon_aliases = alias.groupby("canonicalId").aliasId.apply(set).to_dict() if len(alias) else {}
    for c in m.detect() + [{"subject": e["subject"], "candidates": e["candidates"], "id": e["id"]} for e in m.log if not e.get("reverted")]:
        seen |= {c["subject"], c["id"].split("-", 2)[-1]} | canon_aliases.get(c["subject"], set())
        for x in c["candidates"]:
            seen.add(x["value"])
            seen |= canon_aliases.get(x["value"], set())
        if "docs" in c:
            seen |= set(c["docs"])
    res = {}
    for typ in ("logical_conflict", "temporal_conflict", "terminology_conflict", "value_conflict"):
        ds = [d for d in gt["defects"] if d["type"] == typ]
        hit = [d for d in ds if any(r in seen for r in d["refs"])]
        res[typ] = {"injected": len(ds), "found": len(hit), "missed": [d["detail"][:70] for d in ds if d not in hit][:5]}
    return res


def cmd_eval(a):
    print(json.dumps(eval_gt(), indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["detect", "propose-owner", "apply", "report", "restore", "eval"])
    ap.add_argument("cid", nargs="?")
    ap.add_argument("--owner", default=OWNER_DEFAULT)
    a = ap.parse_args()
    if a.cmd == "report":
        from quality.conflicts_report import write_report
        return write_report()
    {"detect": cmd_detect, "propose-owner": cmd_propose, "apply": cmd_apply, "restore": cmd_restore, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    main()
