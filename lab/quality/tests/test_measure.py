"""Метрики Результату 1: визначення коректні, чисте сховище проходить, дефектний baseline падає там, де треба."""
import yaml

from quality.measure import DEF, run


def test_definitions_cover_8_dimensions_with_thresholds():
    cfg = yaml.safe_load(open(DEF, encoding="utf-8"))
    dims = {m["dimension"] for m in cfg["metrics"]}
    assert len(cfg["metrics"]) >= 8 and len(dims) == 8
    assert all(0 < m["threshold"] <= 1 for m in cfg["metrics"])


def test_clean_lab11_has_no_failed_metric():
    res = run("lab11")
    assert res["summary"]["fail"] == 0, [m["id"] for m in res["metrics"] if m["status"] == "fail"]


def test_baseline_v2_defects_are_detected():
    res = {m["id"]: m for m in run("baseline")["metrics"]}
    for mid in ("M01", "M03", "M05", "M07", "M08", "M09", "M10", "M11"):
        assert res[mid]["status"] == "fail", mid
    assert res["M12"]["status"] == "ok" and res["M13"]["status"] == "ok"
