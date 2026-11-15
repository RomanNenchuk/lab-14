import shutil
import pandas as pd
import pytest
from quality.conflicts import ConflictManager, reasoner_inconsistent, disjoint_inconsistent, same_model
from quality.store import ROOT


@pytest.fixture()
def mgr(tmp_path):
    shutil.copytree(ROOT / "data" / "baseline", tmp_path / "d")
    return ConflictManager("clean", root=tmp_path / "d")


def test_reasoner_detects_functional_violation():
    assert reasoner_inconsistent("usesAircraft", "F1", ["A", "B"])
    assert not reasoner_inconsistent("usesAircraft", "F1", ["A"])
    assert disjoint_inconsistent("F1", "BUD")


def test_model_variants():
    assert same_model("Airbus A320", "A320") and same_model("Airbus A220-300", "Airbus A220")
    assert same_model("Boeing 737-800", "B737-800") and same_model("Embraer E190", "Embraer 190")
    assert not same_model("Boeing 737-800", "Boeing 737-800F")


def test_detects_four_types_on_baseline(mgr):
    types = {c["type"] for c in mgr.detect()}
    assert {"temporal", "value", "logical", "terminology"} <= types


def test_refuses_frozen_variant():
    with pytest.raises(ValueError):
        ConflictManager("baseline")


def test_apply_keeps_alternatives_and_restore_roundtrip(mgr, tmp_path):
    before = mgr.snapshot()
    st = mgr.apply(owner="tester", owner_file=tmp_path / "none.csv")
    assert st.get("temporal") == 3 and st.get("terminology") == 5
    alts = mgr.t["conflict_alternatives"]
    assert {"accepted", "rejected"} <= set(alts.status) and (alts.decidedBy != "").all()
    assert all(c["type"] not in ("temporal", "terminology") for c in mgr.detect())
    for e in [x for x in mgr.log if not x["reverted"]]:
        mgr.restore(e["id"])
    assert mgr.snapshot() == before
    assert len(mgr.t["conflict_alternatives"]) == 0


def test_owner_value_outside_candidates(mgr, tmp_path):
    f = tmp_path / "o.csv"
    pd.DataFrame([{"conflict_id": "C-departsArrivesSame-BC116-20261103", "subject": "BC116-20261103", "predicate": "arrivesAt", "options": "HEL",
                   "chosen": "RIX", "confirmed": "yes", "note": ""}]).to_csv(f, index=False)
    mgr.apply(owner="tester", owner_file=f)
    arr = mgr.t["rel_arrives_at"]
    assert arr[arr.startId == "BC116-20261103"].endId.tolist() == ["RIX"]
    mgr.restore("C-departsArrivesSame-BC116-20261103")
    assert mgr.t["rel_arrives_at"].query("startId=='BC116-20261103'").endId.tolist() == ["HEL"]
