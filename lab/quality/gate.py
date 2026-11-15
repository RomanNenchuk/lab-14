#!/usr/bin/env python3
"""Результат 4: «gate» якості сховища. Запускає всі перевірки з quality/checks.py і блокує (код виходу 1), якщо є порушення.

    python -m quality.gate --variant lab11            # має пройти (еталон)
    python -m quality.gate --variant baseline         # очікувано падає: показує порушення за правилами
    python -m quality.gate --variant clean            # після Результату 5 має пройти
    python -m quality.gate --variant clean --only S03 Q01

Побічні ефекти (вимкнути --no-history): рядок у quality/ci_history.jsonl (тренд), quality/ci_report.md (звіт із порушеннями за правилами),
quality/ci_trend.csv, лог запуску quality/ci_logs/<час>_<варіант>.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
from contextlib import redirect_stdout

from quality.checks import CHECKS, Ctx, run_all
from quality.store import ROOT

HIST = ROOT / "quality" / "ci_history.jsonl"
REPORT = ROOT / "quality" / "ci_report.md"
TREND_CSV = ROOT / "quality" / "ci_trend.csv"
LOGS = ROOT / "quality" / "ci_logs"


def read_history() -> list[dict]:
    return [json.loads(x) for x in open(HIST, encoding="utf-8")] if HIST.exists() else []


def write_report(res: list[dict], variant: str, ts: str) -> None:
    hist = read_history()
    failed = [r for r in res if r["status"] == "FAIL"]
    tools = {}
    for r in res:
        d = tools.setdefault(r["tool"], [0, 0])
        d[0] += 1
        d[1] += r["status"] == "FAIL"
    rows = "\n".join(f"| {r['id']} | {r['tool']} | {r['dimension']} | {r['title']} | {r['violations']} | {'✅' if r['status'] == 'pass' else '❌'} |" for r in res)
    detail = "\n\n".join(f"**{r['id']}** — {r['title']} ({r['violations']}):\n" + "\n".join(f"- `{e}`" for e in r["examples"]) + (f"\n- … ще {r['violations'] - len(r['examples'])}" if r["violations"] > len(r["examples"]) else "") for r in failed) or "Порушень немає."
    trend = "\n".join(f"| {h['ts'][:19].replace('T', ' ')} | {h['variant']} | {h['checks_failed']} / {h['checks_total']} | {h['violations']} | {'✅' if h['passed'] else '❌'} |" for h in hist[-15:])
    tl = "\n".join(f"| {k} | {v[0]} | {v[1]} |" for k, v in tools.items())
    REPORT.write_text(f"""# Результат 4: звіт автоматичних перевірок

Останній запуск: **{variant}**, {ts[:19].replace('T', ' ')}. Перевірок: {len(res)}, не пройдено: {len(failed)}, порушень: {sum(r['violations'] for r in res)}. Gate: {'✅ пройдено' if not failed else '❌ ЗАБЛОКОВАНО'}.

## Перевірки за інструментами
| Інструмент | Перевірок | Не пройдено |
|---|---|---|
{tl}

## Порушення за правилами
| ID | Інструмент | Вимір | Правило | Порушень | Стан |
|---|---|---|---|---|---|
{rows}

## Приклади порушень
{detail}

## Тренд (останні запуски gate)
| Час | Варіант | Не пройдено перевірок | Порушень | Gate |
|---|---|---|---|---|
{trend}

Повна історія: `quality/ci_history.jsonl`, `quality/ci_trend.csv`; журнали запусків: `quality/ci_logs/`.
""", encoding="utf-8")
    TREND_CSV.write_text("ts,variant,checks_failed,checks_total,violations,passed\n" + "\n".join(f"{h['ts']},{h['variant']},{h['checks_failed']},{h['checks_total']},{h['violations']},{h['passed']}" for h in hist) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="lab11", choices=["lab11", "baseline", "clean"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-history", action="store_true")
    a = ap.parse_args()
    ts = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    buf = io.StringIO()
    with redirect_stdout(buf):
        res = run_all(Ctx.from_variant(a.variant), a.only)
        print(f"GATE {a.variant} {ts}")
        print(f"{'ID':4} {'інструмент':14} {'порушень':>8}  правило")
        for r in res:
            print(f"{r['id']:4} {r['tool']:14} {r['violations']:>8}  {'✅' if r['status'] == 'pass' else '❌'} {r['title']}")
        failed = [r for r in res if r["status"] == "FAIL"]
        print(f"\nперевірок {len(res)}, не пройдено {len(failed)}, порушень {sum(r['violations'] for r in res)} → {'GATE: ПРОЙДЕНО' if not failed else 'GATE: ЗАБЛОКОВАНО (код виходу 1)'}")
    out = buf.getvalue()
    print(out, end="")
    if not a.no_history and not a.only:
        rec = {"ts": ts, "variant": a.variant, "checks_total": len(res), "checks_failed": len(failed), "violations": sum(r["violations"] for r in res),
               "passed": not failed, "failed_ids": [r["id"] for r in failed]}
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        LOGS.mkdir(exist_ok=True)
        (LOGS / f"{ts[:19].replace(':', '-')}_{a.variant}.log").write_text(out, encoding="utf-8")
        write_report(res, a.variant, ts)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
