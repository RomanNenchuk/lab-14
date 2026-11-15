# Результат 2: оцінка дедуплікації

Embeddings: `e5`. Поріг 0.83 підібрано на dev (dev, 61 пар); спірна зона: [0.75, 0.83).

## Test (59 пар, з них дублікатів 26)
| Метрика | Значення | 95% інтервал Вілсона |
|---|---|---|
| precision | 0.96 | [0.805, 0.993] |
| recall | 0.923 | [0.759, 0.979] |
| F1 | 0.941 | — |

tp / fp / fn = 24 / 1 / 2. Спірних пар: 5.
**З LLM-суддею на спірних парах** (5 пар): precision 0.962, recall 0.962, F1 0.962.


| Тип | precision | recall | F1 | tp / fp / fn |
|---|---|---|---|---|
| aircraft | 1.0 | 1.0 | 1.0 | 2 / 0 / 0 |
| airport | 1.0 | 0.75 | 0.857 | 3 / 0 / 1 |
| passenger | 0.933 | 0.933 | 0.933 | 14 / 1 / 1 |
| pilot | 1.0 | 1.0 | 1.0 | 5 / 0 / 0 |

## Blocking
Позитивних пар у test: 26, збережено blocking-ом: 26 (recall blocking 1.0).

## Наскрізна оцінка на всьому сховищі (проти ground truth, лише для оцінювання)
Кандидатних пар 1990 із 12471 можливих (скорочення 84.0%). precision 0.909, recall 0.962, F1 0.935 (tp/fp/fn = 50/5/2).
Пропущені: [['X049', 'X098'], ['PRG', 'prg_b']]. Хибні: [['X069', 'X073'], ['X008', 'X090'], ['X050', 'X122'], ['X062', 'X073'], ['X063', 'X083']].

## Помилки на test
- `G025` (airport, duplicate-typo): «Prague Vaclav Havel» ~ «Prague aclav Havel», еталон 1, рішення non-match, оцінка 0.743
- `G054` (passenger, hard-natural): «Julia Shevchenko» ~ «Yulia Shevchenko», еталон 0, рішення match, оцінка 0.906
- `G070` (passenger, duplicate-initial): «Daryna Schmidt» ~ «D. Schmidt», еталон 1, рішення uncertain, оцінка 0.797
