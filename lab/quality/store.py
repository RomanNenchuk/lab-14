"""Доступ до сховища знань (підготовка до ЛР 14: «скрипти доступу до сховища»).

Сховище має три шари; усі вони лежать у data/<variant>/ :
  graph/    CSV графа авіаперевезень (ЛР 9/11): вузли (flights.csv, ...) і зв'язки (rel_*.csv з provenance)
  corpus.jsonl + kb/   текстові фрагменти для RAG (ЛР 10/11) з метаданими (date, sensitivity, source_url, license)

variant = lab11 (чисте сховище ЛР 11) | baseline (baseline-v2 з синтетичними дефектами, заморожено) | clean (робоча копія для виправлень).
Вибір за змінною KB_VARIANT або аргументом `variant`.

    from quality.store import load_tables, load_corpus, to_rdf
    t = load_tables()            # {"flights": DataFrame, ..., "rel_has_pilot": DataFrame}
    c = load_corpus()            # list[dict]
    g = to_rdf()                 # rdflib.Graph для SHACL/SPARQL (Результат 4)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
AV = "https://example.org/aviation#"          # той самий простір імен, що в ЛР 15 (semantic/shapes.ttl)
SEED = 42

# таблиця вузлів -> (клас, стовпець-ідентифікатор)
NODES = {
    "flights": ("Flight", "flightId"),
    "aircraft": ("Aircraft", "registration"),
    "airlines": ("Airline", "code"),
    "airports": ("Airport", "iata"),
    "cities": ("City", "cityId"),
    "countries": ("Country", "code"),
    "delays": ("Delay", "delayId"),
    "passengers": ("Passenger", "passengerId"),
    "pilots": ("Pilot", "pilotId"),
    "risks": ("Risk", "riskId"),
}

# файл зв'язків -> (початковий клас, предикат, кінцевий клас)
RELS = {
    "rel_affected_by": ("Flight", "affectedBy", "Risk"),
    "rel_arrives_at": ("Flight", "arrivesAt", "Airport"),
    "rel_at_airport": ("Risk", "atAirport", "Airport"),
    "rel_caused_by": ("Delay", "causedBy", "Risk"),
    "rel_departs_from": ("Flight", "departsFrom", "Airport"),
    "rel_has_delay": ("Flight", "hasDelay", "Delay"),
    "rel_has_passenger": ("Flight", "hasPassenger", "Passenger"),
    "rel_has_pilot": ("Flight", "hasPilot", "Pilot"),
    "rel_in_country": ("City", "inCountry", "Country"),
    "rel_located_in": ("Airport", "locatedIn", "City"),
    "rel_next_leg": ("Flight", "nextLeg", "Flight"),
    "rel_operated_by": ("Flight", "operatedBy", "Airline"),
    "rel_owned_by": ("Aircraft", "ownedBy", "Airline"),
    "rel_uses_aircraft": ("Flight", "usesAircraft", "Aircraft"),
}


def variant_name(variant: str | None = None) -> str:
    v = variant or os.getenv("KB_VARIANT", "baseline")
    if v not in ("lab11", "baseline", "clean"):
        raise ValueError(f"variant має бути lab11, baseline або clean, а не {v!r}")
    return v


def data_dir(variant: str | None = None) -> Path:
    d = ROOT / "data" / variant_name(variant)
    if not d.exists():
        raise FileNotFoundError(f"{d} не існує. Для clean виконай: make init-clean")
    return d


def load_tables(variant: str | None = None) -> dict[str, pd.DataFrame]:
    """Усі CSV графа; ідентифікатори читаються як рядки (щоб не губити нулі й коди на кшталт 'NA')."""
    gdir = data_dir(variant) / "graph"
    return {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(gdir.glob("*.csv"))}


def load_corpus(variant: str | None = None) -> list[dict]:
    path = data_dir(variant) / "corpus.jsonl"
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def counts(variant: str | None = None) -> dict:
    t = load_tables(variant)
    docs = load_corpus(variant)
    return {
        "nodes": {k: len(t[k]) for k in NODES if k in t},
        "relations": {k: len(t[k]) for k in RELS if k in t},
        "chunks": len(docs),
        "documents": len({d["doc_id"] for d in docs}),
    }


# ---------------------------------------------------------------- RDF
def iri(cls: str, ident: str) -> str:
    """IRI вузла містить клас, щоб однакові коди з різних таблиць (напр. код авіакомпанії й країни) не злипалися."""
    return f"{AV}{cls}/{ident}"


def to_rdf(variant: str | None = None):
    """CSV -> rdflib.Graph для варіанта сховища (див. build_rdf)."""
    return build_rdf(load_tables(variant))


def build_rdf(t: dict):
    """CSV -> rdflib.Graph. Кожен зв'язок подається двома способами:
       1) прямий тріплет  av:Flight/CA101 av:hasPilot av:Pilot/P015   (для SPARQL-інваріантів, reasoner-а)
       2) вузол av:Relation з provenance (source, confidence, validFrom)  (для SHACL: діапазон confidence, наявність source)
    """
    from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

    ns = Namespace(AV)
    g = Graph()
    g.bind("av", ns)

    for table, (cls, idcol) in NODES.items():
        for row in t[table].to_dict("records"):
            s = URIRef(iri(cls, row[idcol]))
            g.add((s, RDF.type, ns[cls]))
            for col, val in row.items():
                if val == "":
                    continue
                g.add((s, ns[col], _literal(val)))

    for rel, (c1, pred, c2) in RELS.items():
        for row in t[rel].to_dict("records"):
            a, b = URIRef(iri(c1, row["startId"])), URIRef(iri(c2, row["endId"]))
            g.add((a, ns[pred], b))
            r = URIRef(f"{AV}Relation/{pred}/{row['startId']}/{row['endId']}")
            g.add((r, RDF.type, ns.Relation))
            g.add((r, ns.relType, Literal(pred)))
            g.add((r, ns.start, a))
            g.add((r, ns.end, b))
            for col, val in row.items():
                if col in ("startId", "endId") or val == "":
                    continue
                g.add((r, ns[col], _literal(val)))
    return g


def _literal(val: str):
    from rdflib import XSD, Literal

    if val in ("true", "false"):
        return Literal(val == "true", datatype=XSD.boolean)
    try:
        return Literal(int(val), datatype=XSD.integer)
    except ValueError:
        pass
    try:
        return Literal(float(val), datatype=XSD.decimal)
    except ValueError:
        return Literal(val)


if __name__ == "__main__":
    print(json.dumps(counts(), indent=2, ensure_ascii=False))
