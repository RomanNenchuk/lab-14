#!/usr/bin/env python3
"""Створює робочу копію data/clean/ з data/baseline/ (усі виправлення Результатів 2–3 робимо в clean).
Baseline не змінюється. Індекс clean треба перебудувати після виправлень: make index-clean."""
import shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src, dst = ROOT / "data" / "baseline", ROOT / "data" / "clean"
if dst.exists() and "--force" not in sys.argv:
    sys.exit("data/clean вже існує (втратиш виправлення). Щоб почати заново: python scripts/init_clean.py --force")
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)
print(f"Скопійовано {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
