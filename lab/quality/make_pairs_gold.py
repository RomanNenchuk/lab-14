#!/usr/bin/env python3
"""Результат 2: еталонна розмітка пар для дедуплікації -> quality/pairs_gold.csv (120 пар, split dev/test 60/60).

Позитиви й decoy беруться з ground truth ін'єкції (label_source=injection), «важкі» негативи — природні пари з найвищою
схожістю імен, що не є дублями (label_source=auto-hard), легкі негативи — випадкові (auto-random).
Людина перевіряє колонку `label` (human_check: заповнити ok/змінено). Детектор цього файлу не бачить, поки не запущено tune/eval."""
import json, random, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quality.dedup import load_records, candidate_pairs, Embedder, score_pair, ROOT

rng = random.Random(42)
gt = json.load(open(ROOT / "quality/ground_truth/injected_defects.json", encoding="utf-8"))
recs = load_records("baseline")
idx = {t: {r.id: r for r in rs} for t, rs in recs.items()}
true = {frozenset((p["a"], p["b"])) for p in gt["duplicate_pairs"]}
rows = []
def add(t, a, b, label, kind, src):
    rows.append(dict(entity_type=t, id_a=a, id_b=b, name_a=idx[t][a].name, name_b=idx[t][b].name, label=label, pair_kind=kind, label_source=src))
for p in gt["duplicate_pairs"]:
    add(p["entity"], p["a"], p["b"], 1, "duplicate-" + p["variant"], "injection")
for p in gt["decoy_pairs"]:
    add(p["entity"], p["a"], p["b"], 0, "decoy", "injection")
emb = Embedder("hash")
taken = {frozenset((r["id_a"], r["id_b"])) for r in rows}
hard = []
for t, rs in recs.items():
    for a, b in candidate_pairs(rs):
        k = frozenset((a, b))
        if k in true or k in taken: continue
        hard.append((score_pair(idx[t][a], idx[t][b], emb)["string"], t, a, b))
hard.sort(reverse=True)
want = {"passenger": 10, "pilot": 5, "airport": 6, "aircraft": 3}
for s, t, a, b in hard:
    if want[t] > 0:
        add(t, a, b, 0, "hard-natural", "auto-hard"); want[t] -= 1
n_easy = 120 - len(rows)
for _ in range(n_easy):
    t = rng.choices(list(recs), weights=[4, 2, 2, 2])[0]
    a, b = rng.sample(sorted(idx[t]), 2)
    if frozenset((a, b)) in true: continue
    add(t, a, b, 0, "random", "auto-random")
df = pd.DataFrame(rows)
df["split"] = ""
for (t, l), g in df.groupby(["entity_type", "label"]):
    ids = list(g.index); rng.shuffle(ids)
    for i, ix in enumerate(ids): df.at[ix, "split"] = "dev" if i % 2 == 0 else "test"
df = df.sample(frac=1, random_state=42).reset_index(drop=True)
df.insert(0, "pair_id", [f"G{i+1:03d}" for i in range(len(df))])
df["human_check"] = ""
df.to_csv(ROOT / "quality/pairs_gold.csv", index=False)
print(len(df), df.groupby(["split", "label"]).size().to_dict(), df.pair_kind.value_counts().to_dict())
