#!/usr/bin/env python3
"""Будує HNSW-індекс e5 для обраного KB_VARIANT (потрібен після змін у data/clean/kb).
    KB_VARIANT=clean python scripts/build_index.py
Перший запуск качає multilingual-e5-small (~0.5 ГБ) у кеш HuggingFace."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import VARIANT, INDEXES
from src.index import VectorIndex

vi = VectorIndex("e5", "hierarchical")
n = vi.build()
vi.save()
print(f"[{VARIANT}] проіндексовано {n} фрагментів -> {INDEXES}")
