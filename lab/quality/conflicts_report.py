"""Генерація quality/conflicts.md з поточного стану data/clean (журнал рішень + відкриті суперечності)."""
from __future__ import annotations

import json
from collections import Counter

from quality.conflicts import ConflictManager, eval_gt, ROOT

TYPE_UA = {"logical": "логічна (reasoner / інваріант)", "value": "значень (різні джерела)", "temporal": "часова", "terminology": "термінологічна"}
STRAT_UA = {"trust": "довіра до джерела", "date": "дата (новіше)", "consensus": "консенсус", "owner": "рішення власника"}
FIX = {"disjoint": "`arrivesAt` замінено на підтверджений аеропорт; помилкове значення збережено в альтернативах", "flightdate": "`flights.date` узгоджено зі `scheduledDeparture`; старе значення збережено в альтернативах", "functional": "залишено запис-переможець у `rel_*`; решту винесено в альтернативи", "terminology": "`aircraft.model` уніфіковано; стару назву збережено в альтернативах",
       "attribute": "значення атрибута вибрано; друге збережено в альтернативах", "document": "дзеркало переміщено в `kb/_quarantine/`; обидва значення збережено в альтернативах"}


def cell(x: dict) -> str:
    return f"{str(x['value'])[:60]} [{x['source']}{', ' + x['confidence'] if x['confidence'] else ''}{', ' + x['validFrom'] if x['validFrom'] else ''}]"


def write_report():
    m = ConflictManager("clean")
    res = [e for e in m.log if not e.get("reverted")]
    opn = m.detect()
    n_all = len(res) + len(opn)
    by = Counter(e["type"] for e in res) + Counter(c["type"] for c in opn)
    rows = []
    for e in res:
        w = e["candidates"][e["winner"]]
        rows.append(f"| {e['id']} | {TYPE_UA[e['type']]} | {e['subject']} · {e['predicate']} | {' ⟷ '.join(cell(x) for x in e['candidates'])} | {STRAT_UA[e['strategy']]} | {FIX[e['kind']]} | розв'язано: {str(w['value'])[:40]} |")
    for c in opn:
        rows.append(f"| {c['id']} | {TYPE_UA[c['type']]} | {c['subject']} · {c['predicate']} | {' ⟷ '.join(cell(x) for x in c['candidates'])} | рішення власника | очікує `quality/owner_decisions.csv` (confirmed=yes) | **відкрито** |")
    log = [f"| {e['id']} | {e['decided_by']} | {e['approved_by']} | {e['ts'][:19].replace('T', ' ')} | {e['why']} |" for e in res]
    ca = m.t["conflict_alternatives"]
    ex = {}
    for e in res:
        ex.setdefault(e["kind"], e)
    samples = []
    for kind, e in ex.items():
        a = ca[ca.conflictId == e["id"]]
        tab = "\n".join(f"| {str(r.value)[:70]} | {r.source} | {r.confidence} | {r.validFrom} | {r.status} | {r.strategy} |" for r in a.itertuples())
        samples.append(f"**{e['id']}** ({kind}):\n\n| value | source | confidence | validFrom | status | strategy |\n|---|---|---|---|---|---|\n{tab}")
    gt = eval_gt()
    ev = "\n".join(f"| {k} | {v['injected']} | {v['found']} |" for k, v in gt.items())
    out = f"""# Результат 3: суперечності та їх розв'язання

Станом на стан `data/clean`. Усі суперечності штучні (їх вставлено скриптом `scripts/inject_defects.py`, див. `data/baseline/INJECTION.md`); детектор ground truth не читає.

**Всього суперечностей: {n_all}** (розв'язано {len(res)}, відкрито {len(opn)}); за типами: {dict(by)}.

## 1. Як шукаємо
| Тип | Метод |
|---|---|
| логічна | OWL-reasoner (`owlrl`): (1) `owl:FunctionalProperty` + `owl:differentFrom` — у функціональної властивості два різні значення; (2) `owl:propertyDisjointWith` для `departsFrom`/`arrivesAt` — рейс «вилітає й прилітає» в один аеропорт; reasoner повідомляє про неузгодженість (ErrorMessage). Плюс інваріант `date` = дата `scheduledDeparture` |
| значень | зв'язки з різним `source` (напр. `codeshare-feed` проти основного); атрибути, що розійшлися при злитті дублікатів (години нальоту); числа в дзеркалах документів |
| часова | два значення одного зв'язку з різним `validFrom` (старий `legacy-roster` проти нового запису) |
| термінологічна | група назв моделі, що збігаються після нормалізації (`Airbus A320` ~ `A320`); вантажний `737-800F` — окремий тип |

Частина логічних суперечностей (місто аеропорту) **виникає лише після дедуплікації**: поки дублікат аеропорту окремий запис, його зв'язки не конфліктують.

## 2. Стратегії
| Стратегія | Коли застосовуємо |
|---|---|
| довіра (trust) | різні джерела: перемагає вище `confidence` |
| дата (date) | різний `validFrom` або дата документа: перемагає новіше |
| консенсус (consensus) | кілька назв одного поняття: перемагає найчастіша |
| рішення власника (owner) | довіра й дати рівні (напр. години нальоту дубліката): вибір пише власник у `quality/owner_decisions.csv` |

## 3. Каталог суперечностей і виправлень
| ID | Тип | Суб'єкт · властивість | Суперечні значення [джерело, довіра, дата] | Стратегія | Виправлення | Статус |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 4. Збереження альтернатив з provenance
Відхилене значення не видаляється: воно переходить у `data/clean/graph/conflict_alternatives.csv` (conflictId, subject, predicate, value, source, confidence, validFrom, status, strategy, decidedBy, approvedBy, decidedAt, restore).
`python -m quality.conflicts restore <ID>` повертає відхилене значення на місце (перевірено тестом). Приклади (по одному на тип суперечності):

{(chr(10) * 2).join(samples)}

## 5. Журнал рішень (хто, коли, чому)
| Суперечність | Хто вирішив | Хто затвердив | Коли | Чому |
|---|---|---|---|---|
{chr(10).join(log)}

## 6. Оцінка виявлення проти ground truth (лише для оцінювання)
| Тип вставлених дефектів | Вставлено | Виявлено |
|---|---|---|
{ev}
"""
    (ROOT / "quality" / "conflicts.md").write_text(out, encoding="utf-8")
    (ROOT / "quality" / "conflicts.json").write_text(json.dumps({"resolved": res, "open": opn, "ground_truth_eval": gt}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"quality/conflicts.md: {n_all} суперечностей (розв'язано {len(res)}, відкрито {len(opn)})")
