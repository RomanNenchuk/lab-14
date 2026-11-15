# Тестовий набір і метрики RAG (ЛР 11, Результат 4)

_Згенеровано `python -m src.rag_eval --report`; дані: `eval/rag_eval.jsonl`, `eval/rag_eval_results.jsonl`._

## 1. Набір тестів (37)
18 із відповіддю, 10 без відповіді (gold-набір ЛР 10, усі verified), 5 суперечливих джерел і 4 prompt injection (**синтетичні фікстури**, передаються як додаткові джерела; позначені в `note`).
Прогін на рівні доступу restricted (крім одного тесту на public). Генератор: `gemini-3.5-flash-lite-1`, суддя: `gemini-3.5-flash-lite`.

## 2. Метрики
| Метрика | Значення | Як обчислено |
|---|---|---|
| Faithfulness (середня) | 1.00 | суддя розбиває відповідь на твердження; частка підтверджених джерелами |
| Faithfulness = 1.0 | 25/25 (100%) | відповіді, де всі твердження підтверджені |
| Context recall (документний) | 0.89 | частка gold-документів серед знайдених, 18 питань |
| Context recall (за evidence) | 0.92 | частка evidence-речень, що дослівно є в контексті LLM |
| Citation accuracy: валідність | 25/25 (100%) | відповіді без цитат поза наданим контекстом |
| Citation accuracy: точність | 0.75 | частка цитат, що вказують на gold-документ |
| Abstention rate (загальна) | 12/37 (32%) | відмов серед усіх тестів |
| Правильна відмова | 10/10 (100%) | відмов серед питань без відповіді |
| Хибна відмова | 1/18 (6%) | відмов серед питань із відповіддю |

Проходження тестів за категоріями:
| Категорія | Пройдено |
|---|---|
| answerable | 15/18 (83%) |
| unanswerable | 10/10 (100%) |
| conflict | 4/5 (80%) |
| injection | 4/4 (100%) |

Затримка: p50 = 1198 мс, p95 = 1706 мс; prompt-токенів у середньому 1583, усього 1640; вартість/запит: тариф безкоштовний або ціни не задано (PRICE_IN_PER_M, PRICE_OUT_PER_M).

## 3. Ручна перевірка і узгодженість із суддею
Ще не виконано: `--sheet`, заповнити `eval/human_sheet.csv`, `--kappa`.

## 4. Провали та причини (автоматична розмітка; клас і план виправлення заповнює автор)
| id | категорія | питання | що сталося | причина (авто) | клас | план виправлення |
|---|---|---|---|---|---|---|
| t02 | answerable | Why do Delta Air Lines and American Airlines use LaGuardia Airport as a hub in New York Ci | Delta Air Lines and American Airlines use LaGuardia Airport as a domestic hub in New York City due to capacity and slot restrictions at thei | генерація (цитування/відповідь) | TODO | TODO |
| t10 | answerable | aircraft cabin cleaning services | Aircraft cabin cleaning services include cleaning seats, galleys, and lavatories, as well as resetting the cabin by folding seatbelts and ma | пошук (gold-документ не в контексті) | TODO | TODO |
| t16 | answerable | How do low-cost carriers discourage unused reservations as an alternative to overbooking,  | відмова | пошук (gold-документ не в контексті) | TODO | TODO |
| t32 | conflict | What is the maximum flight duty period for a two-pilot crew? | The maximum flight duty period for a two-pilot crew is 13 hours according to the 2023 regulations [fx:duty-2023], and 12 hours according to  | політика/промпт (конфлікт джерел не позначено) | TODO | TODO |

## 5. Обмеження
- Тести конфліктів та ін'єкцій синтетичні; 37 тестів — мала вибірка, відсотки нестабільні.
- Faithfulness оцінює LLM-суддя (інша модель), що має власні похибки; для цього є ручна вибірка і κ.
- Citation accuracy перевіряє, що цитата вказує на gold-документ, а не що саме це речення підтверджується цим фрагментом.
