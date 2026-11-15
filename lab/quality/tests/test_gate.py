"""Gate як pytest: чистий стан lab11 проходить усі перевірки; baseline-v2 їх порушує; навмисні дефекти виявляються."""
import pytest

from quality import ci_demo
from quality.checks import CHECKS, Ctx, run_all


@pytest.fixture(scope="module")
def lab11():
    return Ctx.from_variant("lab11")


def test_at_least_10_checks_from_3_tools():
    assert len(CHECKS) >= 10 and len({c.tool for c in CHECKS}) >= 3


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.id)
def test_lab11_passes(check, lab11):
    v = check.fn(lab11)
    assert not v, f"{check.id} {check.title}: {v[:3]}"


def test_baseline_is_blocked():
    res = run_all(Ctx.from_variant("baseline"))
    failed = {r["id"] for r in res if r["status"] == "FAIL"}
    assert len(failed) >= 10 and {"S03", "Q01", "P05"} <= failed


def test_three_deliberate_defects_detected():
    rows, clean_ok = ci_demo.run()
    assert clean_ok
    assert len(rows) == 3 and all(r["detected"] and r["blocked"] for r in rows)
