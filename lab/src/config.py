"""Єдине місце для налаштувань. SEED фіксуємо для відтворюваності."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:  # ключі Gemini з .env (файл у .gitignore); змінні, задані в оточенні (напр. KB_VARIANT з Makefile), не перезаписуються
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
# ЛР 14: один код, три стани сховища. KB_VARIANT=lab11 (чисте сховище ЛР 11, еталон) | baseline (baseline-v2: ЛР 11 + синтетичні дефекти) | clean (після очищення)
VARIANT = os.getenv("KB_VARIANT", "baseline")
if VARIANT not in ("lab11", "baseline", "clean"):
    raise SystemExit(f"KB_VARIANT має бути lab11, baseline або clean, а не {VARIANT!r}")
DATA = ROOT / "data" / VARIANT
KB = DATA / "kb"
INDEXES = ROOT / "indexes" / VARIANT
EVAL = ROOT / "eval"
LLM_LOG = ROOT / "llm_log"
CLAIMS = ROOT / "claims" / "claims.csv"

SEED = 42

# Дві моделі embeddings для Результату 3 (обидві багатомовні, працюють на CPU)
EMB_MODELS = {
    "e5": "intfloat/multilingual-e5-small",
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}
# Для e5 потрібні префікси "query: " та "passage: "
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "

# Cross-encoder для reranking (багатомовний)
RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

# HNSW
HNSW_M = 32
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 64

RRF_K = 60
