#!/usr/bin/env python3
"""Фіксує версію сховища (Результат 1, C3): версія, дата, SHA-256 кожного файлу, кількість рядків.
  python scripts/snapshot_baseline.py --variant lab11      -> quality/lab11_manifest.json     (lab11-v1, чисте сховище ЛР 11)
  python scripts/snapshot_baseline.py --variant baseline   -> quality/baseline_manifest.json  (baseline-v2, з синтетичними дефектами)
Тест tests/test_baseline_frozen.py стежить, щоб зафіксовані стани не змінили випадково. --force перезаписує."""
import argparse, datetime as dt, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VERSIONS = {"lab11": "lab11-v1", "baseline": "baseline-v2"}
SOURCES = {"lab11": "ЛР 9–11: graph/*.csv, corpus.jsonl (lab-11), kb/wiki (lab-10); без дефектів",
           "baseline": "lab11 + синтетичні дефекти: scripts/inject_defects.py, seed 42; ground truth у quality/ground_truth/"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=list(VERSIONS), default="baseline")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = ROOT / "quality" / f"{a.variant}_manifest.json"
    if out.exists() and not a.force:
        print(f"{out.relative_to(ROOT)} уже існує; --force, щоб перезаписати"); return
    base = ROOT / "data" / a.variant
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.rglob("*")) if p.is_file()}
    from quality.store import counts
    m = {"version": VERSIONS[a.variant], "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
         "source": SOURCES[a.variant], "counts": counts(a.variant), "files": files}
    out.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Збережено {out.relative_to(ROOT)}: {m['version']}, {len(files)} файлів")


if __name__ == "__main__":
    main()
