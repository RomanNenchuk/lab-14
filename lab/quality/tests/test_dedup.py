import os, shutil
os.environ["DEDUP_EMBEDDER"] = "hash"
import pytest
from quality import dedup
from quality.merge import GraphMerger
from quality.store import ROOT

def test_normalize_and_person_sim():
    assert dedup.normalize("passenger", "Olehh  Ivanenko")[1] == ["olehh", "ivanenko"]
    assert dedup.person_sim(["t", "schmidt"], ["thomas", "schmidt"]) > 0.9
    assert dedup.person_sim(["taras", "melnyk"], ["daryna", "melnyk"]) < 0.9

def test_gold_split_balanced():
    g = dedup.read_gold()
    assert len(g) >= 100 and set(g.split) == {"dev", "test"}
    assert g.groupby("split").label.sum().min() >= 20

def test_docs_mirrors_found():
    pairs, _ = dedup.doc_pairs("baseline")
    assert len(pairs) == 6

def test_merge_unmerge_roundtrip(tmp_path):
    shutil.copytree(ROOT / "data" / "baseline", tmp_path / "d")
    m = GraphMerger("clean", root=tmp_path / "d")
    before = m.snapshot()
    recs = dedup.load_records("baseline")["passenger"]
    cl = dedup.find_clusters("baseline", {"threshold": 0.875, "uncertain_delta": 0.08, "weights": {"string": .6, "embedding": .4}}, dedup.Embedder("hash"))[0]
    c = next(c for c in cl if c["type"] == "passenger")
    mid = m.merge("passenger", c["ids"])
    assert m.snapshot() != before and len(m.t["entity_aliases"]) == len(c["ids"]) - 1
    m.unmerge(mid)
    assert m.snapshot() == before

def test_merge_refuses_frozen_variant():
    with pytest.raises(ValueError):
        GraphMerger("baseline")
