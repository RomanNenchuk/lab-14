"""Офлайн-перевірка, що доступ до сховища працює (ключі, мережа й моделі не потрібні)."""
from quality.store import NODES, RELS, counts, load_corpus, load_tables, to_rdf


def test_tables_present():
    t = load_tables("baseline")
    assert set(NODES) <= set(t) and set(RELS) <= set(t)


def test_counts_nonzero():
    c = counts("baseline")
    assert c["nodes"]["passengers"] > counts("lab11")["nodes"]["passengers"]   # дублікати й двійники додано
    assert c["nodes"]["flights"] > 0 and c["chunks"] > 0 and c["documents"] > 0


def test_corpus_has_metadata():
    for r in load_corpus("baseline"):
        assert {"chunk_id", "doc_id", "text", "sensitivity", "source_url"} <= set(r)


def test_rdf_builds_and_lab11_relations_have_provenance():
    g = to_rdf("lab11")      # у baseline-v2 частину source навмисно стерто (дефект provenance)
    q = """PREFIX av: <https://example.org/aviation#>
           SELECT (COUNT(?r) AS ?n) WHERE { ?r a av:Relation . FILTER NOT EXISTS { ?r av:source ?s } }"""
    assert int(next(iter(g.query(q)))[0]) == 0


def test_injection_ground_truth_matches_data():
    import json
    from quality.store import ROOT
    gt = json.load(open(ROOT / "quality" / "ground_truth" / "injected_defects.json", encoding="utf-8"))
    ids = {r["passengerId"] for r in load_tables("baseline")["passengers"].to_dict("records")}
    assert all(p["a"] in ids and p["b"] in ids for p in gt["duplicate_pairs"] if p["entity"] == "passenger")
    assert gt["summary"]["defects"] == len(gt["defects"]) and gt["version"] == "baseline-v2"
