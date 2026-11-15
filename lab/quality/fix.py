#!/usr/bin/env python3
"""Результат 5, крок «виправлення»: усуває дефекти, які не закрили дедуплікація (Р2) і суперечності (Р3). Працює лише в data/clean.

Кожне виправлення пишеться в data/clean/fix_log.jsonl (правило, таблиця, ключ, стовпець, було, стало, метод, надійність, час): нічого не
змінюється мовчки. Значення, яких у даних немає, ІМПУТУЮТЬСЯ (медіана) і позначаються `imputed`: це оцінка, а не відновлена істина.

  F1 порожній source зв'язку      -> найчастіший source серед сусідніх зв'язків того ж рейсу   (derived)
  F2 порожній kind рейсу          -> cargo, якщо модель літака закінчується на «F» (вантажна), інакше passenger   (derived)
  F3 порожній/від'ємний seatCapacity -> медіана місць літаків тієї ж моделі   (imputed)
  F4 від'ємний flightHours        -> модуль (помилка знака)   (heuristic);  порожній -> медіана пілотів   (imputed)
  F5 minutes затримки поза [1,1440] або порожнє -> медіана затримок із тим самим видом ризику   (imputed)
  F6 level ризику поза [1,5]      -> обмеження до діапазону   (heuristic)
  F7 corpus.jsonl перебудовується з data/clean/kb (дзеркала в _quarantine не потрапляють)

    python -m quality.fix apply       # виправити data/clean і перебудувати corpus.jsonl
    python -m quality.fix validate    # перевірити правило F2 на еталонному lab11 (точність виведення kind)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from quality.store import RELS, ROOT


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def is_int(s: str) -> bool:
    return bool(s) and s.lstrip("-").isdigit()


class Fixer:
    def __init__(self, base: Path | None = None):
        self.base = base or ROOT / "data" / "clean"
        if not self.base.exists():
            raise FileNotFoundError(f"{self.base} не існує: спершу make init-clean")
        self.gdir = self.base / "graph"
        self.t = {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(self.gdir.glob("*.csv"))}
        self.log_path = self.base / "fix_log.jsonl"
        self.log = [json.loads(x) for x in open(self.log_path, encoding="utf-8")] if self.log_path.exists() else []
        self.new = 0

    def _rec(self, rule, table, key, col, old, new, method, kind):
        self.log.append({"id": f"F{len(self.log) + 1:04d}", "rule": rule, "table": table, "key": key, "column": col, "old": old, "new": new,
                         "method": method, "reliability": kind, "ts": now()})
        self.new += 1

    # ---------------------------------------------------------------- правила
    def f1_sources(self):
        for rel in RELS:
            df = self.t.get(rel)
            if df is None or "source" not in df:
                continue
            miss = df.index[df.source == ""]
            if not len(miss):
                continue
            overall = df[df.source != ""].source.mode().iat[0]
            for i in miss:
                sib = df[(df.startId == df.at[i, "startId"]) & (df.source != "")].source
                new = sib.mode().iat[0] if len(sib) else overall
                self._rec("F1", rel, f"{df.at[i, 'startId']}→{df.at[i, 'endId']}", "source", "", new, "найчастіший source серед зв'язків того ж початку", "derived")
                df.at[i, "source"] = new

    def kind_of(self, flight: str) -> str:
        ua = self.t["rel_uses_aircraft"]
        reg = ua[ua.startId == flight].endId
        models = self.t["aircraft"].set_index("registration").model
        return "cargo" if len(reg) and any(str(models.get(r, "")).endswith("F") for r in reg) else "passenger"

    def f2_kind(self):
        f = self.t["flights"]
        for i in f.index[f.kind == ""]:
            new = self.kind_of(f.at[i, "flightId"])
            self._rec("F2", "flights", f.at[i, "flightId"], "kind", "", new, "модель літака закінчується на «F» → cargo, інакше passenger", "derived")
            f.at[i, "kind"] = new

    def f3_seats(self):
        a = self.t["aircraft"]
        valid = a[a.seatCapacity.map(lambda s: is_int(s) and int(s) >= 0)]
        glob = int(statistics.median(valid.seatCapacity.astype(int)))
        for i in a.index[~a.seatCapacity.map(lambda s: is_int(s) and int(s) >= 0)]:
            same = valid[valid.model == a.at[i, "model"]].seatCapacity.astype(int)
            new = str(int(statistics.median(same))) if len(same) else str(glob)
            self._rec("F3", "aircraft", a.at[i, "registration"], "seatCapacity", a.at[i, "seatCapacity"], new, f"медіана місць моделі «{a.at[i, 'model']}»", "imputed")
            a.at[i, "seatCapacity"] = new

    def f4_hours(self):
        p = self.t["pilots"]
        ok = p[p.flightHours.map(lambda s: is_int(s) and int(s) >= 0)].flightHours.astype(int)
        med = str(int(statistics.median(ok)))
        for i in p.index:
            v = p.at[i, "flightHours"]
            if is_int(v) and int(v) < 0:
                self._rec("F4", "pilots", p.at[i, "pilotId"], "flightHours", v, str(-int(v)), "від'ємне значення = помилка знака → модуль", "heuristic")
                p.at[i, "flightHours"] = str(-int(v))
            elif not is_int(v):
                self._rec("F4", "pilots", p.at[i, "pilotId"], "flightHours", v, med, "медіана налоту пілотів", "imputed")
                p.at[i, "flightHours"] = med

    def f5_delays(self):
        d, risks, cb = self.t["delays"], self.t["risks"].set_index("riskId").kind, self.t["rel_caused_by"]
        kind_of = {r.startId: risks.get(r.endId, "") for r in cb.itertuples()}
        good = d[d.minutes.map(lambda s: is_int(s) and 1 <= int(s) <= 1440)]
        glob = int(statistics.median(good.minutes.astype(int)))
        for i in d.index.difference(good.index):
            k = kind_of.get(d.at[i, "delayId"], "")
            same = good[good.delayId.map(lambda x: kind_of.get(x, "") == k)].minutes.astype(int)
            new = str(int(statistics.median(same))) if len(same) else str(glob)
            self._rec("F5", "delays", d.at[i, "delayId"], "minutes", d.at[i, "minutes"], new, f"медіана затримок виду «{k}»", "imputed")
            d.at[i, "minutes"] = new

    def f6_risk(self):
        r = self.t["risks"]
        for i in r.index:
            v = r.at[i, "level"]
            if not is_int(v) or not 1 <= int(v) <= 5:
                new = str(min(5, max(1, int(v)))) if is_int(v) else "3"
                self._rec("F6", "risks", r.at[i, "riskId"], "level", v, new, "значення обмежено діапазоном [1,5]", "heuristic")
                r.at[i, "level"] = new

    def apply(self) -> Counter:
        for f in (self.f1_sources, self.f2_kind, self.f3_seats, self.f4_hours, self.f5_delays, self.f6_risk):
            f()
        for name, df in self.t.items():
            df.to_csv(self.gdir / f"{name}.csv", index=False, lineterminator="\n")
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in self.log:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return Counter(e["rule"] for e in self.log)


def rebuild_corpus() -> int:
    out = ROOT / "data" / "clean" / "corpus.jsonl"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_corpus.py"), "--kb", str(ROOT / "data" / "clean" / "kb"), "--out", str(out),
                    "--ref", str(ROOT / "data" / "lab11" / "corpus.jsonl"), "--quiet"], check=True, cwd=ROOT)
    return sum(1 for _ in open(out, encoding="utf-8"))


def validate_kind_rule() -> dict:
    """Правило F2 перевіряємо на еталонному lab11, де kind відомий."""
    base = ROOT / "data" / "lab11" / "graph"
    t = {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in base.glob("*.csv")}
    fx = Fixer.__new__(Fixer)
    fx.t = t
    ok = sum(fx.kind_of(r.flightId) == r.kind for r in t["flights"].itertuples())
    return {"checked": len(t["flights"]), "correct": ok, "accuracy": round(ok / len(t["flights"]), 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["apply", "validate"])
    a = ap.parse_args()
    if a.cmd == "validate":
        print(json.dumps(validate_kind_rule(), ensure_ascii=False))
        return
    fx = Fixer()
    c = fx.apply()
    n = rebuild_corpus()
    print(f"виправлень (нових {fx.new}, усього в журналі {len(fx.log)}) за правилами: {dict(c)}; corpus.jsonl перебудовано: {n} фрагментів; журнал: data/clean/fix_log.jsonl")


if __name__ == "__main__":
    main()
