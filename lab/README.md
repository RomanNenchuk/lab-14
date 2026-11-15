# ЛР 14. Якість, очищення та оцінювання сховища знань

Курс «Сховища знань та управління знаннями», ЛНУ ім. І. Франка. Гілка: `lab14`. Предметна область: управління авіаційними перевезеннями.
Це **стартовий скелет**: середовище, три стани сховища, доступ до сховища, RAG з ЛР 11 і офлайн-тести. Самі результати 1–5 ще треба виконати.

## Запуск з нуля (Ubuntu, Python 3.10+, без Docker)
```bash
git switch -c lab14                       # у корені репозиторію kms
cd lab-14/lab                             # (у тебе каталог lab-14/lab; workflow лежить у корені: .github/workflows/quality.yml)
make setup && source .venv/bin/activate   # venv + torch (CPU) + залежності
cp .env.example .env                      # впиши GEMINI_API_KEY (потрібен для Результатів 2 і 5)
make test                                 # 12 офлайн-тестів, ключ і мережа не потрібні
make index-baseline                       # ОДИН РАЗ: індекс e5 для baseline-v2 (перший запуск качає модель ~0.5 ГБ)
python scripts/check_env.py               # що працює, а що ні   (--models --llm: глибша перевірка)
```
Необов'язково, коли дійдемо до Результатів 4–5: `make extras` (Great Expectations, RAGAS).
**seed = 42.** Ключі лише в `.env` (у `.gitignore`). Граф синтетичний, корпус з англійської Вікіпедії (CC BY-SA), персональних даних немає.

## Три стани сховища (`KB_VARIANT`)
| Стан | Де | Що це |
|---|---|---|
| `lab11` | `data/lab11/`, `indexes/lab11/` | чисте сховище з ЛР 11 (версія `lab11-v1`), еталон «ідеального» стану. Метрики RAG: `docs/rag_eval_lab11.md` |
| `baseline` | `data/baseline/`, `indexes/baseline/` | **baseline-v2**: `lab11` + синтетичні дефекти (дублікати, суперечності, пропуски, дзеркала статей). **Від нього міряємо якість у Результаті 1** |
| `clean` | `data/clean/`, `indexes/clean/` | робоча копія baseline для виправлень: `make init-clean`, після змін `make index-clean` |

`lab11` і `baseline` заморожені: `quality/tests/test_baseline_frozen.py` перевіряє SHA-256 із `quality/lab11_manifest.json` та `quality/baseline_manifest.json`.
Код вибирає стан за змінною `KB_VARIANT` (за замовчуванням `baseline`): `KB_VARIANT=clean python -m src.rag_eval --run`.

### Чому є синтетичні дефекти
Сховище ЛР 11 майже ідеально чисте (немає дублікатів і суперечностей), тож нічого було б вимірювати й виправляти. `scripts/inject_defects.py` (seed 42) імітує другий потік даних і дзеркала статей та записує **ground truth** у `quality/ground_truth/injected_defects.json`: список дефектів, пари-дублікати, двійники-приманки (схожі, але різні особи), дзеркала.
- Ground truth потрібен лише для оцінювання (precision/recall) і перевірки виправлень. `dedup.py` та інші виявлячі його **не читають**.
- У звіті й `docs/ai_declaration.md` ці дефекти описуються як **синтетичні**, а не реальні помилки джерел. Це частина вимоги доброчесності.
- Опис складу дефектів: `data/baseline/INJECTION.md`.

## Структура
```
quality/      store.py (доступ: load_tables, load_corpus, to_rdf), tests/ (offline gate), ground_truth/ (лише для оцінювання)
              ← сюди підуть: profile_baseline.md, metrics.json, dedup.py, pairs_gold.csv, conflicts.md, shapes.ttl, after_report.md
src/          RAG з ЛР 11 (rag.py, rag_eval.py, index.py, chunking.py); шляхи залежать від KB_VARIANT
data/         lab11/ · baseline/ · clean/ (graph/*.csv, corpus.jsonl, kb/wiki/*.md, sensitivity_overrides.json)
indexes/      lab11/ (готовий) · baseline/ (make index-baseline) · clean/ (make index-clean)
eval/         rag_eval.jsonl (37 тестів), gold.jsonl, rag_eval_results_<стан>.jsonl
docs/         rag_eval_lab11.md, ai_declaration.md (шаблон)
llm_log/, claims/   обов'язкові артефакти протоколу «LLM-експерт»
scripts/      inject_defects.py, build_corpus.py, snapshot_baseline.py, init_clean.py, build_index.py, check_env.py
```
Відмінності від ЛР 11: немає Neo4j і API (для ЛР 14 не потрібні); граф береться з CSV, а `quality/store.py` будує з нього RDF для SHACL/SPARQL.

## Метрики RAG
До очищення міряємо на **baseline-v2** (`make rag-run rag-judge rag-report` з `VARIANT=baseline`), після очищення на `clean`. Для довідки є `lab11`: faithfulness 1.00 · context recall 0.94 · citation accuracy 0.93 · правильна відмова 10/10 · хибна відмова 1/18 (`docs/rag_eval_lab11.md`).

## Дорожня карта
1. **Р1:** 8+ метрик за вимірами з порогами, експертна вибірка ≥ 30, `quality/profile_baseline.md`, `quality/metrics.json`, 3 пріоритети.
2. **Р2:** `quality/dedup.py` (нормалізація → blocking → rapidfuzz + embeddings + атрибути), `quality/pairs_gold.csv` (≥ 100 пар, dev 50 / test 50), злиття з provenance та unmerge.
3. **Р3:** `quality/conflicts.md` (≥ 5 суперечностей ≥ 3 типів, альтернативи з provenance, журнал рішень).
4. **Р4:** `quality/shapes.ttl` + `quality/tests/*` (≥ 10 перевірок, ≥ 3 інструменти) + CI; 3 навмисні дефекти.
5. **Р5:** `quality/after_report.md`: `make init-clean` → виправлення → `make index-clean` → прогін RAG на `clean`, таблиці «до/після».
6. Протокол: кожен запит до LLM журналюється в `llm_log/`, твердження йдуть у `claims/claims.csv`. Без цього максимум 0.25 за результат.

## Відомі обмеження
- Gemini на безкоштовному тарифі дає 429: `rag_eval` має `--resume` і `--sleep`.
- Перший запуск моделей (e5, reranker) качає ~0.5 ГБ у кеш HuggingFace.
- `make inject` перегенерує baseline (результат детермінований, але його SHA-256 зафіксовано; тест упаде, доки не оновиш маніфест).


## Результат 4: автоматичні перевірки (gate)
20 перевірок із 4 інструментів: SHACL (`quality/shapes.ttl`, S01–S09), SPARQL (`quality/sparql/*.rq`, Q01–Q04), pytest/pandas «data contract» (P01–P06), JSON Schema (`quality/schemas/`, J01); реєстр у `quality/checks.py`.
- `make gate` — еталон lab11 (має пройти); `make gate-baseline` — очікувано заблоковано; `make gate-clean` — робоча копія; `make ci-demo` — 3 навмисні дефекти.
- Звіт: `quality/ci_report.md` (порушення за правилами + тренд), історія `quality/ci_history.jsonl`, журнали `quality/ci_logs/`.
- CI: скопіюй `ci/quality.yml` у `<репозиторій>/.github/workflows/quality.yml`; локальний gate: `make install-hooks`.

## Результат 5: після покращення
Порядок (усе в `data/clean`; baseline і lab11 заморожені): `make init-clean` → `make dedup-apply` (+ `JUDGE`/`make dedup-manual PAIR=airport:PRG,prg_b`) → `make conflicts-propose` / `conflicts-apply` → `make fix-clean` → `make gate-clean` → `make measure-clean` → `make index-clean` → `make rag-run rag-judge rag-report VARIANT=clean` → `make after-report` (→ `quality/after_report.md`).
Виправлення `fix-clean` логуються в `data/clean/fix_log.jsonl`; значення з надійністю `imputed` — медіани, а не відновлена істина.
