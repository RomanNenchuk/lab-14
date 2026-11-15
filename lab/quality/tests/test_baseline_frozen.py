"""Страховка: зафіксовані стани (lab11, baseline) не можна змінювати; усі правки — у data/clean. Порівнюємо з quality/*_manifest.json."""
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_states_unchanged():
    manifests = sorted((ROOT / "quality").glob("*_manifest.json"))
    assert {p.name for p in manifests} >= {"baseline_manifest.json", "lab11_manifest.json"}
    for mp in manifests:
        m = json.load(open(mp, encoding="utf-8"))
        changed = [f for f, h in m["files"].items() if hashlib.sha256((ROOT / f).read_bytes()).hexdigest() != h]
        assert not changed, f"{m['version']} змінено: {changed[:5]}"
        assert m["version"] and m["created"]
