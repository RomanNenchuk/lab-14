#!/usr/bin/env python3
"""Результат 4, крок 4: навмисно вносимо 3 дефекти в ЧИСТИЙ стан (lab11) і показуємо, що gate їх виявляє та блокує.

    python -m quality.ci_demo      # пише quality/ci_demo.md; код виходу 0, якщо всі 3 дефекти виявлено, а чистий стан проходить

Дефекти вносяться лише в копію в пам'яті; файли даних не змінюються.
"""
from __future__ import annotations

import copy
import sys

from quality.checks import Ctx, run_all
from quality.store import ROOT, load_corpus, load_tables

OUT = ROOT / "quality" / "ci_demo.md"


def defect_negative_hours(t, corpus):
    t["pilots"].loc[t["pilots"].pilotId == "P015", "flightHours"] = "-50"
    return "Пілот P015: flightHours = -50", ["S03"]


def defect_same_airport(t, corpus):
    f = t["rel_departs_from"]
    flight = f.iloc[0].startId
    dep = f.iloc[0].endId
    t["rel_arrives_at"].loc[t["rel_arrives_at"].startId == flight, "endId"] = dep
    return f"Рейс {flight}: приліт = виліт ({dep})", ["Q01", "Q03"]


def defect_license(t, corpus):
    corpus[0]["license"] = "own work"
    return f"Фрагмент {corpus[0]['chunk_id']} з Вікіпедії: license = «own work»", ["J01"]


DEFECTS = [defect_negative_hours, defect_same_airport, defect_license]


def run() -> tuple[list[dict], bool]:
    base_t, base_c = load_tables("lab11"), load_corpus("lab11")
    clean = run_all(Ctx(copy.deepcopy(base_t), copy.deepcopy(base_c), "lab11"))
    clean_ok = all(r["status"] == "pass" for r in clean)
    rows = []
    for fn in DEFECTS:
        t, c = copy.deepcopy(base_t), copy.deepcopy(base_c)
        what, expected = fn(t, c)
        res = run_all(Ctx(t, c, "lab11+дефект"))
        failed = [r for r in res if r["status"] == "FAIL"]
        got = [r["id"] for r in failed]
        rows.append({"defect": what, "expected": expected, "failed": got, "detected": all(e in got for e in expected), "blocked": bool(failed),
                     "examples": {r["id"]: r["examples"][:2] for r in failed}})
    return rows, clean_ok


def main() -> int:
    rows, clean_ok = run()
    lines = "\n".join(f"| {i} | {r['defect']} | {', '.join(r['expected'])} | {', '.join(r['failed'])} | {'✅ виявлено' if r['detected'] else '❌ НЕ виявлено'} | {'так (код 1)' if r['blocked'] else 'ні'} |" for i, r in enumerate(rows, 1))
    ex = "\n".join(f"- дефект {i}: " + "; ".join(f"{k}: `{v[0] if v else ''}`" for k, v in r["examples"].items()) for i, r in enumerate(rows, 1))
    OUT.write_text(f"""# Результат 4: демонстрація виявлення дефектів

Еталонний стан (`lab11`) проходить усі перевірки: {'✅ так' if clean_ok else '❌ НІ'}. У копію в пам'яті по одному вносимо дефект і запускаємо gate.

| № | Внесений дефект | Очікувані правила | Спрацювали правила | Виявлено | Gate блокує |
|---|---|---|---|---|---|
{lines}

Приклади порушень:
{ex}

Відтворити: `make ci-demo` (або `python -m quality.ci_demo`).
""", encoding="utf-8")
    for r in rows:
        print(("✅" if r["detected"] else "❌"), r["defect"], "→", r["failed"])
    print("еталон проходить:", clean_ok)
    return 0 if clean_ok and all(r["detected"] and r["blocked"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
