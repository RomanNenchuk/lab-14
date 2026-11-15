"""Результат 2, Крок 4: злиття дублікатів зі збереженням provenance і можливістю unmerge.

Принципи:
  * працюємо лише з data/clean (baseline і lab11 заморожені);
  * нічого не видаляється безслідно: кожне злиття записує в `merge_log.jsonl` повні рядки «до» й «після»,
    тож `unmerge(merge_id)` повертає таблиці у попередній стан;
  * походження: `entity_aliases.csv` (псевдонім -> канонічний запис, джерело рішення, оцінка),
    `merge_alternatives.csv` (значення атрибутів, що розійшлися; виживає значення survivor),
    рядки зв'язків зберігають власні source/confidence/validFrom.

Дублікати документів корпусу не видаляються, а переносяться в kb/_quarantine/ (і повертаються назад при unmerge).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pandas as pd

from quality.store import ROOT

# тип сутності -> (таблиця, ключ, [(таблиця зв'язків, колонка кінця, що вказує на сутність)])
ENT = {
    "passenger": ("passengers", "passengerId", [("rel_has_passenger", "endId")]),
    "pilot": ("pilots", "pilotId", [("rel_has_pilot", "endId")]),
    "airport": ("airports", "iata", [("rel_departs_from", "endId"), ("rel_arrives_at", "endId"), ("rel_located_in", "startId"), ("rel_at_airport", "endId")]),
    "aircraft": ("aircraft", "registration", [("rel_uses_aircraft", "endId"), ("rel_owned_by", "startId")]),
}
ALIAS_COLS = ["aliasId", "canonicalId", "entityType", "aliasName", "mergeId", "decidedBy", "score", "mergedAt"]
ALT_COLS = ["mergeId", "canonicalId", "attribute", "keptValue", "otherValue", "otherFrom"]


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _rows(df: pd.DataFrame):
    return [tuple(r) for r in df.itertuples(index=False, name=None)]


class GraphMerger:
    """Злиття сутностей графа в data/<variant>/graph (за замовчуванням clean)."""

    def __init__(self, variant: str = "clean", root: Path | None = None):
        if variant != "clean" and root is None:
            raise ValueError("Злиття дозволено лише в data/clean (baseline і lab11 заморожені)")
        self.base = (root or ROOT / "data" / variant)
        self.gdir = self.base / "graph"
        self.log_path = self.base / "merge_log.jsonl"
        self.t = {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(self.gdir.glob("*.csv"))}
        self.t.setdefault("entity_aliases", pd.DataFrame(columns=ALIAS_COLS, dtype=str))
        self.t.setdefault("merge_alternatives", pd.DataFrame(columns=ALT_COLS, dtype=str))
        self.log = [json.loads(l) for l in open(self.log_path, encoding="utf-8")] if self.log_path.exists() else []

    # ------------------------------------------------------------- допоміжне
    def degree(self, etype: str, eid: str) -> int:
        return sum(int((self.t[rel][col] == eid).sum()) for rel, col in ENT[etype][2])

    def pick_survivor(self, etype: str, ids: list[str]) -> str:
        """Канонічний запис: з найбільшою кількістю зв'язків; за рівності — менший ідентифікатор."""
        return sorted(ids, key=lambda i: (-self.degree(etype, i), i))[0]

    def _entity_row(self, etype, eid):
        tb, key, _ = ENT[etype]
        df = self.t[tb]
        m = df[df[key] == eid]
        if m.empty:
            raise KeyError(f"{etype} {eid} не знайдено в {tb}")
        return m.index[0], m.iloc[0].to_dict()

    # ------------------------------------------------------------- merge
    def merge(self, etype: str, ids: list[str], decided_by: str = "rule", score: float | None = None, survivor: str | None = None) -> str:
        assert len(set(ids)) >= 2, "для злиття потрібно щонайменше 2 записи"
        survivor = survivor or self.pick_survivor(etype, ids)
        aliases = [i for i in ids if i != survivor]
        tb, key, rels = ENT[etype]
        mid = f"M{len(self.log) + 1:04d}"
        idx_s, srow_before = self._entity_row(etype, survivor)
        entry = {"merge_id": mid, "type": etype, "survivor": survivor, "aliases": aliases, "survivor_before": srow_before,
                 "alias_rows": {}, "rel_changes": [], "rel_dropped": [], "alternatives": [], "decided_by": decided_by, "score": score,
                 "ts": now(), "reverted": False}
        for a in aliases:
            idx_a, arow = self._entity_row(etype, a)
            entry["alias_rows"][a] = arow
            for col, val in arow.items():
                if col == key:
                    continue
                cur = self.t[tb].at[idx_s, col]
                if val and not cur:
                    self.t[tb].at[idx_s, col] = val                                     # survivor порожній: беремо значення псевдоніма
                    entry["alternatives"].append({"attribute": col, "kept": val, "other": "", "other_from": a, "filled": True})
                elif val and cur and val != cur:
                    entry["alternatives"].append({"attribute": col, "kept": cur, "other": val, "other_from": a, "filled": False})
            for rel, col in rels:
                df = self.t[rel]
                for i in df.index[df[col] == a]:
                    before = df.loc[i].to_dict()
                    df.at[i, col] = survivor
                    entry["rel_changes"].append({"rel": rel, "before": before, "after": df.loc[i].to_dict()})
            self.t[tb] = self.t[tb].drop(index=idx_a)
            self.t["entity_aliases"].loc[len(self.t["entity_aliases"])] = [a, survivor, etype, arow.get("name", a), mid, decided_by, "" if score is None else f"{score:.3f}", entry["ts"]]
        for alt in entry["alternatives"]:
            self.t["merge_alternatives"].loc[len(self.t["merge_alternatives"])] = [mid, survivor, alt["attribute"], alt["kept"], alt["other"], alt["other_from"]]
        # однакові (start, end) після перенаправлення: залишаємо перший, решту записуємо в журнал
        for rel in {r for r, _ in rels}:
            df = self.t[rel]
            dup = df.duplicated(subset=["startId", "endId"], keep="first")
            for i in df.index[dup]:
                entry["rel_dropped"].append({"rel": rel, "row": df.loc[i].to_dict()})
            self.t[rel] = df[~dup]
        self.log.append(entry)
        return mid

    # ------------------------------------------------------------- unmerge
    def unmerge(self, merge_id: str, force: bool = False) -> None:
        e = next((x for x in self.log if x["merge_id"] == merge_id), None)
        if e is None or e["reverted"]:
            raise ValueError(f"{merge_id}: немає такого злиття або його вже скасовано")
        later = [x for x in self.log if x["merge_id"] > merge_id and not x["reverted"] and x["type"] == e["type"]
                 and (x["survivor"] == e["survivor"] or e["survivor"] in x["aliases"])]
        if later and not force:
            raise ValueError(f"спершу скасуй пізніші злиття: {[x['merge_id'] for x in later]}")
        tb, key, rels = ENT[e["type"]]
        # 1) рядки зв'язків, що були відкинуті як дублі, повертаються
        for d in e["rel_dropped"]:
            self.t[d["rel"]] = pd.concat([self.t[d["rel"]], pd.DataFrame([d["row"]])], ignore_index=True)
        # 2) змінені зв'язки повертаються на старий кінець
        for ch in e["rel_changes"]:
            df, cols = self.t[ch["rel"]], list(ch["after"])
            hit = (df[cols] == pd.Series(ch["after"])).all(axis=1)
            if not hit.any():
                raise RuntimeError(f"не знайдено рядок {ch['after']} у {ch['rel']}")
            df.loc[hit[hit].index[0]] = pd.Series(ch["before"])
        # 3) сутності
        idx_s, _ = self._entity_row(e["type"], e["survivor"])
        for col, val in e["survivor_before"].items():
            self.t[tb].at[idx_s, col] = val
        self.t[tb] = pd.concat([self.t[tb], pd.DataFrame(list(e["alias_rows"].values()))], ignore_index=True)
        # 4) прибираємо провенанс злиття
        for name in ("entity_aliases", "merge_alternatives"):
            self.t[name] = self.t[name][self.t[name]["mergeId"] != merge_id].reset_index(drop=True)
        e["reverted"] = True
        e["reverted_at"] = now()

    # ------------------------------------------------------------- збереження / стан
    def save(self) -> None:
        for name, df in self.t.items():
            df.to_csv(self.gdir / f"{name}.csv", index=False, lineterminator="\n")
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in self.log:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        """Порядково-незалежний знімок усіх таблиць (для перевірки unmerge)."""
        return {n: sorted(_rows(df.reset_index(drop=True))) for n, df in self.t.items() if n not in ("entity_aliases", "merge_alternatives")}


# ============================================================================ документи корпусу
class DocMerger:
    """Дубль документа переноситься в kb/_quarantine/ (нічого не видаляється); unmerge повертає файл."""

    def __init__(self, variant: str = "clean", root: Path | None = None):
        if variant != "clean" and root is None:
            raise ValueError("Злиття дозволено лише в data/clean")
        self.base = root or ROOT / "data" / variant
        self.wiki = self.base / "kb" / "wiki"
        self.qdir = self.base / "kb" / "_quarantine"
        self.log_path = self.base / "doc_merge_log.jsonl"
        self.log = [json.loads(l) for l in open(self.log_path, encoding="utf-8")] if self.log_path.exists() else []

    def merge(self, duplicate: str, keep: str, reason: str, score: float | None = None) -> str:
        src = self.wiki / f"{duplicate}.md"
        if not src.exists():
            raise FileNotFoundError(src)
        self.qdir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(self.qdir / src.name))
        mid = f"DM{len(self.log) + 1:03d}"
        self.log.append({"merge_id": mid, "duplicate": duplicate, "kept": keep, "reason": reason, "score": score, "ts": now(), "reverted": False})
        self._save()
        return mid

    def unmerge(self, merge_id: str) -> None:
        e = next(x for x in self.log if x["merge_id"] == merge_id and not x["reverted"])
        shutil.move(str(self.qdir / f"{e['duplicate']}.md"), str(self.wiki / f"{e['duplicate']}.md"))
        e["reverted"], e["reverted_at"] = True, now()
        self._save()

    def _save(self) -> None:
        with open(self.log_path, "w", encoding="utf-8") as f:
            for e in self.log:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
