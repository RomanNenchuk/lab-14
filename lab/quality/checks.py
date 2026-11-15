"""Результат 4: реєстр автоматичних перевірок якості сховища (20 перевірок, 4 типи інструментів).

  SHACL        S01–S09  (quality/shapes.ttl, pyshacl)
  SPARQL       Q01–Q04  (quality/sparql/*.rq, rdflib)
  pytest/pandas P01–P06 («data contract» у стилі Great Expectations: унікальність, цілісність посилань, словник, свіжість)
  JSON Schema  J01      (quality/schemas/corpus_chunk.schema.json, jsonschema)

Кожна перевірка повертає список порушень (рядки). Порожній список — перевірка пройдена.
Перевірки не читають ground truth (quality/ground_truth/).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from quality.store import AV, NODES, RELS, ROOT, build_rdf, load_corpus, load_tables

REFERENCE_DATE = dt.date(2026, 11, 14)          # як у quality/metrics_def.yml
MAX_AGE_DAYS = 365
MODEL_VOCAB = {"Embraer E190", "Airbus A220-300", "Airbus A320", "ATR 72-600", "Boeing 737-800", "Boeing 737-800F"}
SHAPES = ROOT / "quality" / "shapes.ttl"
SPARQL_DIR = ROOT / "quality" / "sparql"
SCHEMA = ROOT / "quality" / "schemas" / "corpus_chunk.schema.json"


class Ctx:
    """Дані одного варіанта сховища; RDF і SHACL-звіт обчислюються ліниво й кешуються."""

    def __init__(self, tables: dict, corpus: list[dict], name: str = "?"):
        self.t, self.corpus, self.name = tables, corpus, name
        self._g = self._shacl = None

    @classmethod
    def from_variant(cls, variant: str) -> "Ctx":
        return cls(load_tables(variant), load_corpus(variant), variant)

    @property
    def graph(self):
        if self._g is None:
            self._g = build_rdf(self.t)
        return self._g

    def shacl(self) -> dict[str, list[str]]:
        """{ 'S01': [повідомлення про порушення, ...], ... }; pyshacl запускається один раз."""
        if self._shacl is None:
            from pyshacl import validate
            from rdflib import Graph, Namespace, SH
            sg = Graph().parse(SHAPES, format="turtle")
            conforms, rg, _ = validate(self.graph, shacl_graph=sg, inference="none", abort_on_first=False)
            out: dict[str, list[str]] = {}
            for r in rg.subjects(SH.resultSeverity, None):
                shape = str(rg.value(r, SH.sourceShape))
                code = shape.rsplit("#", 1)[-1][:3]
                focus = str(rg.value(r, SH.focusNode)).replace(AV, "")
                val = rg.value(r, SH.value)
                out.setdefault(code, []).append(f"{focus}" + (f" = {val}" if val is not None else ""))
            self._shacl = out
        return self._shacl


@dataclass
class Check:
    id: str
    tool: str
    dimension: str
    title: str
    fn: Callable[[Ctx], list[str]]


def shacl_check(code: str):
    return lambda c: sorted(c.shacl().get(code, []))


def sparql_check(file: str):
    def run(c: Ctx) -> list[str]:
        q = (SPARQL_DIR / file).read_text(encoding="utf-8")
        rows = []
        for r in c.graph.query(q):
            rows.append(" · ".join(str(x).replace(AV, "") for x in r))
        return sorted(rows)
    return run


# ------------------------------------------------------------------ pandas / pytest (data contract)
def p01_unique_ids(c: Ctx) -> list[str]:
    out = []
    for tb, (_, key) in NODES.items():
        d = c.t[tb][key]
        out += [f"{tb}.{key} = {v} (×{n})" for v, n in d[d.duplicated(keep=False)].value_counts().items()]
        out += [f"{tb}: порожній {key}" for _ in range(int((d == "").sum()))]
    return out


def p02_referential_integrity(c: Ctx) -> list[str]:
    out = []
    ids = {cls: set(c.t[tb][key]) for tb, (cls, key) in NODES.items()}
    for rel, (a, _, b) in RELS.items():
        for r in c.t[rel].itertuples():
            if r.startId not in ids[a]:
                out.append(f"{rel}: startId {r.startId} не є {a}")
            if r.endId not in ids[b]:
                out.append(f"{rel}: endId {r.endId} не є {b}")
    return out


def p03_model_vocabulary(c: Ctx) -> list[str]:
    a = c.t["aircraft"]
    return [f"{r.registration}: модель «{r.model}»" for r in a.itertuples() if r.model not in MODEL_VOCAB]


def p04_no_name_duplicates(c: Ctx) -> list[str]:
    out = []
    for tb in ("passengers", "pilots", "airports"):
        names = c.t[tb]["name"].map(lambda s: re.sub(r"\s+", " ", s).strip().lower())
        for v, n in names[names.duplicated(keep=False)].value_counts().items():
            out.append(f"{tb}: ім'я «{v}» ×{n}")
    return out


def p05_unique_chunk_text(c: Ctx) -> list[str]:
    seen: dict[str, str] = {}
    out = []
    for r in c.corpus:
        k = re.sub(r"\s+", " ", r["text"]).strip().lower()
        if k in seen:
            out.append(f"{r['chunk_id']} дублює {seen[k]}")
        else:
            seen[k] = r["chunk_id"]
    return out


def p06_freshness(c: Ctx) -> list[str]:
    docs = {r["doc_id"]: r["date"] for r in c.corpus}
    out = []
    for d, ds in sorted(docs.items()):
        try:
            age = (REFERENCE_DATE - dt.date.fromisoformat(ds)).days
        except ValueError:
            out.append(f"{d}: некоректна дата «{ds}»")
            continue
        if age > MAX_AGE_DAYS:
            out.append(f"{d}: дата {ds} ({age} днів, ліміт {MAX_AGE_DAYS})")
    return out


# ------------------------------------------------------------------ JSON Schema
def j01_corpus_schema(c: Ctx) -> list[str]:
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    out = []
    for r in c.corpus:
        for e in v.iter_errors(r):
            out.append(f"{r.get('chunk_id', '?')}: {'/'.join(map(str, e.path)) or '(об\'єкт)'} — {e.message[:90]}")
    ids = [r.get("chunk_id") for r in c.corpus]
    out += [f"chunk_id {i} повторюється" for i in sorted({x for x in ids if ids.count(x) > 1})]
    return out


CHECKS: list[Check] = [
    Check("S01", "SHACL", "повнота", "Рейс має рівно один аеропорт вильоту й прильоту", shacl_check("S01")),
    Check("S02", "SHACL", "узгодженість", "Рейс має рівно одну авіакомпанію, літак і пілота", shacl_check("S02")),
    Check("S03", "SHACL", "валідність", "flightHours пілота — невід'ємне ціле", shacl_check("S03")),
    Check("S04", "SHACL", "повнота", "seatCapacity літака задано й невід'ємне", shacl_check("S04")),
    Check("S05", "SHACL", "валідність", "Тривалість затримки 1–1440 хв", shacl_check("S05")),
    Check("S06", "SHACL", "узгодженість", "Аеропорт має назву й рівно одне місто", shacl_check("S06")),
    Check("S07", "SHACL", "походження", "Кожен зв'язок має source, confidence ∈ [0,1], validFrom", shacl_check("S07")),
    Check("S08", "SHACL", "валідність", "date рейсу РРРР-ММ-ДД, kind ∈ {passenger, cargo}", shacl_check("S08")),
    Check("S09", "SHACL", "валідність", "Рівень ризику 1–5", shacl_check("S09")),
    Check("Q01", "SPARQL", "узгодженість", "Рейс не вилітає й не прилітає в один аеропорт", sparql_check("Q01_same_airport.rq")),
    Check("Q02", "SPARQL", "узгодженість", "date рейсу = дата scheduledDeparture", sparql_check("Q02_date_matches_departure.rq")),
    Check("Q03", "SPARQL", "узгодженість", "Ланцюг рейсів: приліт = наступний виліт", sparql_check("Q03_next_leg_continuity.rq")),
    Check("Q04", "SPARQL", "узгодженість", "Літак належить авіакомпанії, що виконує рейс", sparql_check("Q04_aircraft_owner_operates.rq")),
    Check("P01", "pytest/pandas", "унікальність", "Ідентифікатори унікальні в усіх таблицях вузлів", p01_unique_ids),
    Check("P02", "pytest/pandas", "узгодженість", "Цілісність посилань: кінці зв'язків існують", p02_referential_integrity),
    Check("P03", "pytest/pandas", "узгодженість", "Модель літака зі контрольованого словника", p03_model_vocabulary),
    Check("P04", "pytest/pandas", "унікальність", "Немає записів з однаковим іменем (регістр, пробіли)", p04_no_name_duplicates),
    Check("P05", "pytest/pandas", "унікальність", "Немає фрагментів корпусу з однаковим текстом", p05_unique_chunk_text),
    Check("P06", "pytest/pandas", "актуальність", "Документи корпусу не старші за 365 днів", p06_freshness),
    Check("J01", "JSON Schema", "валідність", "Фрагмент корпусу відповідає схемі (поля, формати, license/sensitivity для Вікіпедії)", j01_corpus_schema),
]
BY_ID = {c.id: c for c in CHECKS}


def run_all(ctx: Ctx, only: list[str] | None = None) -> list[dict]:
    res = []
    for ch in CHECKS:
        if only and ch.id not in only:
            continue
        v = ch.fn(ctx)
        res.append({"id": ch.id, "tool": ch.tool, "dimension": ch.dimension, "title": ch.title, "violations": len(v), "examples": v[:5], "status": "pass" if not v else "FAIL"})
    return res
