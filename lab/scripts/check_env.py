#!/usr/bin/env python3
"""Перевірка середовища ЛР 14.  python scripts/check_env.py [--models] [--llm]
  --models  завантажує e5 і reranker (перший запуск ~0.5 ГБ)
  --llm     1 мінімальний запит до Gemini (кілька токенів)"""
import importlib, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
bad = 0


def check(name, fn):
    global bad
    try:
        print(f"[ OK ] {name}: {fn()}")
    except Exception as e:
        bad += 1
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def libs():
    out = []
    for m in ["pandas", "rdflib", "pyshacl", "rapidfuzz", "jsonschema", "pytest", "numpy", "faiss",
              "sentence_transformers", "rank_bm25", "google.genai"]:
        mod = importlib.import_module(m)
        out.append(f"{m} {getattr(mod, '__version__', '')}".strip())
    return ", ".join(out)


def extras():
    out = []
    for m in ["great_expectations", "ragas"]:
        try:
            out.append(f"{m} {importlib.import_module(m).__version__}")
        except Exception:
            out.append(f"{m} (не встановлено; make extras)")
    return ", ".join(out)


def data():
    from quality.store import counts
    c = counts("baseline")
    return f"[baseline-v2] flights={c['nodes']['flights']}, відношень={sum(c['relations'].values())}, фрагментів={c['chunks']}, документів={c['documents']}"


def rdf():
    from quality.store import to_rdf
    return f"{len(to_rdf('baseline'))} тріплетів"


def shacl():
    from pyshacl import validate
    from rdflib import Graph
    shapes = Graph().parse(data="@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix ex: <http://e/> ."
                                "ex:S a sh:NodeShape ; sh:targetClass ex:A ; sh:property [ sh:path ex:p ; sh:minCount 1 ] .", format="turtle")
    ok, _, _ = validate(Graph().parse(data="@prefix ex: <http://e/> . ex:x a ex:A .", format="turtle"), shacl_graph=shapes)
    assert ok is False
    return "SHACL правильно знаходить порушення"


def manifest():
    out = []
    for n in ("lab11", "baseline"):
        m = json.load(open(ROOT / "quality" / f"{n}_manifest.json", encoding="utf-8"))
        out.append(f"{m['version']} від {m['created'][:10]}")
    return ", ".join(out)


def env():
    miss = [k for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "JUDGE_MODEL") if not os.getenv(k)]
    if miss:
        raise RuntimeError(f"не задано в .env: {miss} (потрібні лише для RAG/LLM-судді)")
    return f"генератор {os.getenv('GEMINI_MODEL')}, суддя {os.getenv('JUDGE_MODEL')}"


def models():
    from src.index import VectorIndex
    from sentence_transformers import CrossEncoder
    VectorIndex("e5"); CrossEncoder("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    return "e5 і reranker завантажені"


def index():
    from src.config import INDEXES
    need = [INDEXES / f"e5_hierarchical{x}" for x in (".faiss", ".json", ".npy")]
    miss = [p.name for p in need if not p.exists()]
    if miss:
        raise FileNotFoundError(f"немає {miss} у {INDEXES}; для baseline/clean: make index-baseline / make index-clean")
    return f"індекс у {INDEXES.relative_to(ROOT)}"


def llm():
    from google import genai
    r = genai.Client(api_key=os.environ["GEMINI_API_KEY"]).models.generate_content(model=os.environ["GEMINI_MODEL"], contents="Reply with the single word: ok")
    return r.text.strip()[:40]


try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
check("бібліотеки", libs)
check("додаткові (необов'язково)", extras)
check("дані baseline", data)
check("RDF із CSV", rdf)
check("pySHACL", shacl)
check("зафіксовані версії", manifest)
check("індекс (KB_VARIANT)", index)
check(".env", env)
if "--models" in sys.argv:
    check("моделі", models)
if "--llm" in sys.argv:
    check("Gemini", llm)
print("\nУсе гаразд." if not bad else f"\nПроблем: {bad}. FAIL по .env/--llm не блокують роботу з Результатами 1, 3, 4.")
sys.exit(1 if bad else 0)
