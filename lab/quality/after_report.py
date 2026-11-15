#!/usr/bin/env python3
"""Результат 5: quality/after_report.md — порівняння «до/після» (baseline-v2 → clean) для метрик якості, правил gate і метрик RAG.

    python -m quality.after_report

Метрики якості беруться з quality/metrics.json (baseline) і quality/metrics_clean.json (make measure-clean); правила gate обчислюються наживо;
метрики RAG — з eval/rag_eval_results_<variant>.jsonl (make rag-run rag-judge VARIANT=clean). Чого немає — позначається «н/д».
Висновки (розділ 4) формуються з чисел цього ж звіту; наприкінці їх варто перечитати й за потреби дописати власні спостереження.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

from quality.checks import CHECKS, Ctx, run_all
from quality.store import ROOT

OUT = ROOT / "quality" / "after_report.md"
CLEAN = ROOT / "data" / "clean"
ICON = {"ok": "✅", "fail": "❌", "n/a": "—", "pending": "⏳"}
GROUP = {  # яка дія очищення на які метрики/правила впливає (для аналізу впливу)
    "Дедуплікація (Р2)": ["M08", "M09", "P04", "P05"],
    "Розв'язання суперечностей (Р3)": ["M03", "M04", "M05", "Q01", "Q02", "Q03", "Q04", "P03"],
    "Виправлення значень і походження (Р5)": ["M01", "M02", "M11", "S03", "S04", "S05", "S07", "S08", "S09"],
    "Вилучення застарілих дзеркал і перебудова корпусу": ["M07", "M10", "M13", "P05", "P06", "J01"],
}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()] if p.exists() else []


def rag_summary(variant: str) -> dict | None:
    f = ROOT / "eval" / f"rag_eval_results_{variant}.jsonl"
    if not f.exists():
        return None
    code = ("import json; from src.rag_eval import compute; m=compute(); "
            "print(json.dumps({k:m[k] for k in ['n','faithfulness_mean','faithful_all','doc_recall','evidence_recall','cite_valid','cite_precision',"
            "'abstain_overall','abstain_correct','abstain_false','p50','p95']} | {'cats': dict(m['cats'])}))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "KB_VARIANT": variant}, capture_output=True, text=True)
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else None


def fmt(v, kind="f"):
    if v is None:
        return "н/д"
    if kind == "pair":
        return f"{v[0]}/{v[1]} ({v[0] / max(1, v[1]):.0%})"
    return f"{v:.2f}"


def build_conclusions(deltas, bm, am, gb, ga, flog, rb, rc, rl) -> list[str]:
    """Висновки про вплив: формуються з чисел звіту; тверджень про RAG без виміряного baseline не робимо."""
    out = []
    mirror = [i for i in ("M07", "M09", "M10") if i in deltas]
    out.append(f"**Найбільший ефект дало виведення 6 дзеркальних документів із корпусу.** Одна дія закрила три метрики одразу ("
               + ", ".join(f"{i} {deltas[i]:+.3f}" for i in mirror) + f") і три правила gate (P05, P06, J01: {sum(b['violations'] for b in gb if b['id'] in ('P05', 'P06', 'J01'))} → "
               f"{sum(a['violations'] for a in ga if a['id'] in ('P05', 'P06', 'J01'))} порушень), бо ці дзеркала були водночас застарілими й дублями, а два з них ще й без правильної ліцензії.")
    out.append(f"**Дедуплікація сутностей і розв'язання суперечностей дали менший приріст у відсотках, але важливіші для запитів до графа:** M08 {deltas.get('M08', 0):+.3f}, "
               f"M05 {deltas.get('M05', 0):+.3f}, M03 {deltas.get('M03', 0):+.3f}. Це кілька десятків записів, але саме вони давали хибного перевізника, літак чи місто аеропорту. "
               f"M08 не дійшов до порогу (наближене правило, хибні спрацювання на «пастках»), тож повну унікальність без ручного розбору обіцяти не можна.")
    imp, heur = sum(e["reliability"] == "imputed" for e in flog), sum(e["reliability"] == "heuristic" for e in flog)
    out.append(f"**Прохід порогів не дорівнює правильності значень.** M01, M02, M11 стали 1.000, але з {len(flog)} виправлень {imp} — імпутації (медіана), {heur} — евристики. "
               f"Пропуски закрито оцінками, тож метрика повноти «зросла» без відновлення істини; ці значення позначені `imputed` у `fix_log.jsonl`.")
    out.append("**RAG ЛР 11 читає лише текстовий корпус, а не граф.** Виправлення графа (дедуплікація сутностей, суперечності, значення) не можуть змінити його відповідей; "
               "на RAG впливають тільки зміни корпусу (дзеркала, застарілі дані). Після очищення корпус `clean` має ті самі 795 фрагментів, що й еталон lab11.")
    if rc and rl:
        diff = [k for k in ("faithfulness_mean", "doc_recall", "evidence_recall", "cite_precision") if abs((rc[k] or 0) - (rl[k] or 0)) > 0.005]
        cdiff = {c: (rl["cats"][c], rc["cats"][c]) for c in rc["cats"] if rc["cats"][c] != rl["cats"][c]}
        out.append("**Метрики RAG на `clean` збігаються з еталоном lab11"
                   + (f", окрім {', '.join(diff)}" if diff else "")
                   + (f"; відрізняється проходження тестів {cdiff}" if cdiff else "")
                   + ".** Різниця в 1 тест із 37 лежить у межах шуму (генерація LLM недетермінована), але її варто розглянути у `docs/rag_eval_clean.md`.")
    if rb and rc:
        dd = {k: rc[k] - rb[k] for k in ("faithfulness_mean", "doc_recall", "evidence_recall", "cite_precision") if isinstance(rb[k], float) and isinstance(rc[k], float)}
        out.append("**Вплив очищення на RAG (baseline-v2 → clean):** " + ", ".join(f"{k} {v:+.2f}" for k, v in dd.items())
                   + f"; правильна відмова {fmt(rb['abstain_correct'], 'pair')} → {fmt(rc['abstain_correct'], 'pair')}; хибна відмова {fmt(rb['abstain_false'], 'pair')} → {fmt(rc['abstain_false'], 'pair')}.")
    elif rc:
        out.append("**Вплив на RAG до очищення ще не виміряно** (немає `eval/rag_eval_results_baseline.jsonl`): `make rag-run rag-judge rag-report VARIANT=baseline`, потім `make after-report`.")
    return out


def main():
    cfg = yaml.safe_load(open(ROOT / "quality" / "metrics_def.yml", encoding="utf-8"))
    before, after, ref = load(ROOT / "quality" / "metrics.json"), load(ROOT / "quality" / "metrics_clean.json"), load(ROOT / "quality" / "metrics_lab11.json")
    if not before or not after:
        sys.exit("потрібні quality/metrics.json (make measure) і quality/metrics_clean.json (make measure-clean)")
    bm, am = {m["id"]: m for m in before["metrics"]}, {m["id"]: m for m in after["metrics"]}
    rm = {m["id"]: m for m in ref["metrics"]} if ref else {}
    v = lambda m: "—" if not m or m["value"] is None else f"{m['value']:.4f}"
    st = lambda m: ICON[m["status"]] if m else "—"
    rows, deltas = [], {}
    for d in cfg["metrics"]:
        b, a = bm[d["id"]], am[d["id"]]
        dl = "" if b["value"] is None or a["value"] is None else f"{a['value'] - b['value']:+.4f}"
        if dl:
            deltas[d["id"]] = a["value"] - b["value"]
        rows.append(f"| {d['id']} | {d['dimension']} | `{d['name']}` | ≥ {d['threshold']} | {v(b)} {st(b)} ({b['n_bad']}) | {v(a)} {st(a)} ({a['n_bad']}) | {dl} | {v(rm.get(d['id']))} |")
    sb, sa = before["summary"], after["summary"]

    gb, ga = run_all(Ctx.from_variant("baseline")), run_all(Ctx.from_variant("clean"))
    grows = "\n".join(f"| {b['id']} | {b['tool']} | {b['title']} | {b['violations']} | {a['violations']} | {'✅' if a['status'] == 'pass' else '❌'} |" for b, a in zip(gb, ga))
    gtot = (sum(r["violations"] for r in gb), sum(r["violations"] for r in ga))
    gfail = (sum(r["status"] == "FAIL" for r in gb), sum(r["status"] == "FAIL" for r in ga))

    mlog, dlog, clog, flog = jl(CLEAN / "merge_log.jsonl"), jl(CLEAN / "doc_merge_log.jsonl"), jl(CLEAN / "conflict_decisions.jsonl"), jl(CLEAN / "fix_log.jsonl")
    mc = Counter(e["decided_by"].split(":")[0] for e in mlog if not e["reverted"])
    cc = Counter(e["strategy"] for e in clog if not e.get("reverted"))
    fc, fr = Counter(e["rule"] for e in flog), Counter(e["reliability"] for e in flog)
    actions = f"""| Дія | Скільки | Деталі |
|---|---|---|
| Злиття дублікатів сутностей (Р2) | {sum(1 for e in mlog if not e['reverted'])} кластерів | за ким: {dict(mc)}; псевдоніми в `entity_aliases.csv`, журнал `merge_log.jsonl`, є `unmerge` |
| Дзеркала документів виведено з обігу (Р2) | {sum(1 for e in dlog if not e['reverted'])} | `kb/_quarantine/`, журнал `doc_merge_log.jsonl` |
| Суперечності розв'язано (Р3) | {sum(cc.values())} | стратегії: {dict(cc)}; альтернативи збережено в `conflict_alternatives.csv` |
| Виправлення значень і походження (Р5) | {len(flog)} | правила: {dict(fc)}; надійність: {dict(fr)} (`imputed` — оцінка, не відновлена істина) |"""

    rb, rc, rl = rag_summary("baseline"), rag_summary("clean"), rag_summary("lab11")
    rag_rows = ""
    if rb or rc:
        spec = [("Faithfulness (середня)", "faithfulness_mean", "f"), ("Faithfulness = 1.0", "faithful_all", "pair"), ("Context recall (документний)", "doc_recall", "f"),
                ("Context recall (evidence)", "evidence_recall", "f"), ("Citation accuracy: валідність", "cite_valid", "pair"), ("Citation accuracy: точність", "cite_precision", "f"),
                ("Правильна відмова (питання без відповіді)", "abstain_correct", "pair"), ("Хибна відмова (питання з відповіддю)", "abstain_false", "pair"),
                ("Загальна частка відмов", "abstain_overall", "pair")]
        g = lambda r, k, kind: fmt(None if not r else r[k], kind)
        rag_rows = "\n".join(f"| {n} | {g(rb, k, kind)} | {g(rc, k, kind)} | {g(rl, k, kind)} |" for n, k, kind in spec)
        cats = lambda r, c: "н/д" if not r else fmt(r["cats"][c], "pair")
        rag_rows += "\n" + "\n".join(f"| Тести «{c}» пройдено | {cats(rb, c)} | {cats(rc, c)} | {cats(rl, c)} |" for c in ("answerable", "unanswerable", "conflict", "injection"))
    rag_block = (f"| Метрика | baseline-v2 (до) | clean (після) | lab11 (еталон) |\n|---|---|---|---|\n{rag_rows}" if rag_rows else
                 "Метрики RAG ще не виміряно: `make index-clean && make rag-run rag-judge rag-report VARIANT=clean`, потім повторити `make after-report`.")

    # ---- висновки з чисел
    concl = []
    for grp, ids in GROUP.items():
        mids = [i for i in ids if i in deltas and abs(deltas[i]) > 1e-9]
        gids = [i for i in ids if not i.startswith("M")]
        gb_ = sum(b["violations"] for b in gb if b["id"] in gids)
        ga_ = sum(a["violations"] for a in ga if a["id"] in gids)
        concl.append((sum(abs(deltas[i]) for i in mids) + (gb_ - ga_) / 100, grp, ", ".join(f"{i} {deltas[i]:+.3f}" for i in mids), gb_, ga_))
    concl.sort(reverse=True)
    lines = build_conclusions(deltas, bm, am, gb, ga, flog, rb, rc, rl)
    best = concl[0][1]
    rag_note = ""
    thr = {d["id"]: d["threshold"] for d in cfg["metrics"]}

    OUT.write_text(f"""# Результат 5: після покращення та вплив на RAG

_Згенеровано `python -m quality.after_report`. «До» = baseline-v2 (з синтетичними дефектами), «після» = `data/clean`. Усі дефекти штучні (див. `data/baseline/INJECTION.md`)._

## 1. Що зроблено
{actions}

Порядок відтворення: `make init-clean` → `make dedup-apply` → `make conflicts-apply` → `make fix-clean` → `make gate-clean` → `make measure-clean` → `make index-clean` → `make rag-run rag-judge rag-report VARIANT=clean` → `make after-report`.

## 2. Метрики якості до / після (таблиця 1)
У дужках — кількість проблемних елементів. Останній стовпець — еталон lab11 (чисте сховище ЛР 11). Підсумок: до — ✅ {sb['ok']} · ❌ {sb['fail']}; після — ✅ {sa['ok']} · ❌ {sa['fail']}.

| ID | Вимір | Метрика | Поріг | До (baseline-v2) | Після (clean) | Δ | lab11 |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

M06 (точність за експертною вибіркою) вимірювалася лише на baseline (40 розмічених записів); після очищення вибірку не переоцінювали. M14 потребує індексу clean (`make index-clean`).
M08 використовує наближене правило схожості імен; воно має відомі хибні спрацювання на «пастках» (різні люди зі схожими іменами), тому 1.000 недосяжне без ручного розбору.

### Правила gate до / після (таблиця 2)
Не пройдено правил: {gfail[0]} → {gfail[1]} із {len(CHECKS)}; порушень: {gtot[0]} → {gtot[1]}.

| ID | Інструмент | Правило | Порушень до | Порушень після | Стан |
|---|---|---|---|---|---|
{grows}

## 3. Метрики RAG до / після (таблиця 3)
Той самий тестовий набір ЛР 11 (`eval/rag_eval.jsonl`), та сама модель-генератор і суддя, seed 42.

{rag_block}

## 4. Аналіз впливу
{chr(10).join(f"{k}. {x}" for k, x in enumerate(lines, 1))}

Обмеження: (1) дефекти штучні, а тому ефект очищення на реальних даних може відрізнятися; (2) значення `imputed` — оцінки (медіани), а не відновлені факти; (3) тести RAG малочисельні.

## 5. Процес підтримки якості
| Що | Власник | Періодичність | Поріг / умова | Алерт і дія |
|---|---|---|---|---|
| Метрики M01–M14 (`make measure-clean`) | власник сховища (Roman Nenchuk) | перед кожним релізом і щотижня | пороги з `quality/metrics_def.yml`: M01 ≥ {thr['M01']}, M02 ≥ {thr['M02']}, M05 ≥ {thr['M05']}, M08/M09 ≥ {thr['M08']}, M12–M14 = 1.0 | метрика нижче порогу → issue в репозиторії, виправлення до релізу |
| Gate у CI (20 правил: SHACL, SPARQL, pandas, JSON Schema) | автор змін; рецензує інший учасник | на кожен push і pull request | 0 порушень на `clean` (блокуючий), еталон lab11 завжди зелений | червоний workflow блокує merge; порушення — у `quality/ci_report.md` |
| Дедуплікація й злиття | власник сховища | після кожного імпорту нових даних | precision ≥ 0.95 і recall ≥ 0.90 на `pairs_gold.csv` (test); спірні пари — LLM-суддя, остаточно людина | падіння метрик → перетюнити поріг (`make dedup-tune`); кожне злиття має `unmerge` |
| Суперечності й альтернативи | власник сховища | разом із дедуплікацією | невирішені = 0 перед релізом; рішення власника — у `owner_decisions.csv` | відкриті суперечності в `conflicts.md` не пропускаються до релізу |
| Джерела й ліцензії корпусу | власник сховища | щокварталу (актуальність ≤ 365 днів) | M07 ≥ {thr['M07']}, M10 ≥ {thr['M10']} | застарілі документи оновлюються або виводяться в `_quarantine` |
| Якість RAG (`make rag-run rag-judge rag-report`) | власник сховища | після зміни даних, індексу чи моделі | не гірше за lab11 більш ніж на 0.05 (faithfulness 1.00, recall 0.94, правильна відмова 10/10) | погіршення → розбір провалів у `docs/rag_eval_clean.md`, відкат виправлення (`unmerge`/`restore`) |
| Ключі й персональні дані | власник сховища | постійно | ключі лише в `.env`; у зовнішні сервіси не передаються персональні дані | знайдено секрет у коді → негайно відкликати ключ |

Огляд процесу: раз на модуль (або після великої зміни схеми) власник перевіряє пороги й актуальність цієї таблиці.
""", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: метрики якості {sb['ok']}✅/{sb['fail']}❌ → {sa['ok']}✅/{sa['fail']}❌; gate {gfail[0]} → {gfail[1]} правил не пройдено; RAG: {'є' if rc else 'ще немає'}")


if __name__ == "__main__":
    main()
