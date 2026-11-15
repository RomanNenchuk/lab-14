import shutil
import pytest
from quality.checks import BY_ID, Ctx
from quality.fix import Fixer, validate_kind_rule
from quality.store import ROOT
import pandas as pd

RULES = ["S03", "S04", "S05", "S07", "S08", "S09"]


def test_kind_rule_is_exact_on_lab11():
    assert validate_kind_rule()["accuracy"] == 1.0


def test_fix_clears_value_rules_and_logs(tmp_path):
    shutil.copytree(ROOT / "data" / "baseline", tmp_path / "d")
    fx = Fixer(tmp_path / "d")
    before = Ctx(fx.t, [], "x")
    assert sum(len(BY_ID[r].fn(before)) for r in RULES) > 0
    fx.apply()
    after = Ctx({p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in (tmp_path / "d" / "graph").glob("*.csv")}, [], "x")
    assert all(not BY_ID[r].fn(after) for r in RULES)
    log = [__import__("json").loads(x) for x in open(tmp_path / "d" / "fix_log.jsonl", encoding="utf-8")]
    assert log and {e["reliability"] for e in log} <= {"derived", "imputed", "heuristic"} and all(e["old"] != e["new"] for e in log)
