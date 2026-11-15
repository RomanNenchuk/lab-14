PY ?= python
.PHONY: measure measure-clean accuracy-sample setup extras check test inject snapshot init-clean index-baseline index-clean rag-run rag-judge rag-report
VARIANT ?= baseline

setup:            ## venv + torch (CPU) + залежності
	python3 -m venv .venv && . .venv/bin/activate && pip install -U pip \
	&& pip install torch --index-url https://download.pytorch.org/whl/cpu \
	&& pip install -r requirements.txt

extras:           ## Great Expectations і RAGAS (важкі, необов'язково)
	pip install -r requirements-extras.txt

check:            ## що працює, а що ні (make check ARGS="--models --llm")
	$(PY) scripts/check_env.py $(ARGS)

test:             ## офлайн-тести (без ключа й мережі)
	$(PY) -m pytest -q

inject:           ## (вже виконано) data/lab11 -> data/baseline: синтетичні дефекти, seed 42; перегенерація змінить baseline!
	$(PY) scripts/inject_defects.py && $(PY) scripts/snapshot_baseline.py --variant baseline --force

snapshot:         ## зафіксувати версію: make snapshot VARIANT=baseline
	$(PY) scripts/snapshot_baseline.py --variant $(VARIANT)

init-clean:       ## робоча копія data/clean для виправлень (Р2–Р3)
	$(PY) scripts/init_clean.py

index-baseline:   ## ОБОВ'ЯЗКОВО один раз: індекс e5 для baseline-v2 (є дзеркальні документи; перший запуск качає модель)
	KB_VARIANT=baseline $(PY) scripts/build_index.py

index-clean:      ## перебудувати індекс e5 для data/clean (після виправлень, перед Р5)
	KB_VARIANT=clean $(PY) scripts/build_index.py

rag-run:          ## Р5: прогін 37 тестів RAG: make rag-run VARIANT=baseline|clean|lab11 (потрібен ключ Gemini)
	KB_VARIANT=$(VARIANT) $(PY) -m src.rag_eval --run --sleep 3 --resume

rag-judge:        ## faithfulness від судді
	KB_VARIANT=$(VARIANT) $(PY) -m src.rag_eval --judge --sleep 3

rag-report:       ## метрики RAG -> docs/rag_eval_<variant>.md
	KB_VARIANT=$(VARIANT) $(PY) -m src.rag_eval --report

measure:          ## Р1: виміряти метрики -> quality/metrics.json + таблицю в profile_baseline.md
	$(PY) -m quality.measure --validate --report

measure-clean:    ## Р5: ті самі метрики на data/clean -> quality/metrics_clean.json (M14 потребує make index-clean)
	$(PY) -m quality.measure --variant clean

accuracy-sample:  ## Р1: створити quality/accuracy_sample.csv (40 записів для експертної розмітки)
	$(PY) -m quality.make_accuracy_sample

# ---- Результат 2: дедуплікація ----
dedup-gold:        ## еталонні пари (перевір колонку label вручну)
	python quality/make_pairs_gold.py
dedup-tune:        ## поріг на dev (embeddings e5)
	python -m quality.dedup tune
dedup-eval:        ## precision/recall на test + наскрізна оцінка; JUDGE=1 додає LLM-суддю
	python -m quality.dedup eval $(if $(JUDGE),--judge,)
dedup-demo:        ## злиття + unmerge на тимчасовій копії
	python -m quality.dedup demo
dedup-docs:        ## дублікати документів корпусу
	python -m quality.dedup docs-eval
dedup-apply:       ## злити знайдене в data/clean
	python -m quality.dedup apply $(if $(JUDGE),--judge,)

# ---- Результат 3: суперечності (працює в data/clean: спершу make init-clean && make dedup-apply) ----
conflicts-detect:   ## знайти суперечності (нічого не міняє)
	python -m quality.conflicts detect
conflicts-propose:  ## заготовка quality/owner_decisions.csv (рішення власника; confirmed=yes — після перевірки)
	python -m quality.conflicts propose-owner
conflicts-apply:    ## розв'язати в data/clean (альтернативи + журнал рішень)
	python -m quality.conflicts apply
conflicts-report:   ## quality/conflicts.md + conflicts.json
	python -m quality.conflicts report
conflicts-eval:     ## recall проти ground truth (лише оцінювання)
	python -m quality.conflicts eval

# ---- Результат 4: автоматичні перевірки (gate) ----
gate:              ## усі 20 перевірок для VARIANT (lab11|baseline|clean); код виходу 1 при порушеннях
	python -m quality.gate --variant $(or $(GATE_VARIANT),lab11)
gate-baseline:     ## очікувано заблоковано: звіт із порушеннями за правилами
	-python -m quality.gate --variant baseline
gate-clean:        ## gate на робочій копії clean
	python -m quality.gate --variant clean
ci-demo:           ## 3 навмисні дефекти й доказ, що gate їх виявляє (quality/ci_demo.md)
	python -m quality.ci_demo
install-hooks:     ## локальний gate: pre-push hook
	sh scripts/install_hooks.sh

# ---- Результат 5: виправлення, повторні виміри, звіт «до/після» ----
fix-clean:         ## F1–F6 у data/clean + перебудова corpus.jsonl (журнал fix_log.jsonl)
	python -m quality.fix apply
dedup-manual:      ## рішення власника: make dedup-manual PAIR=airport:PRG,prg_b
	python -m quality.dedup apply --manual $(PAIR)
after-report:      ## quality/after_report.md (до/після для метрик якості, gate і RAG)
	python -m quality.after_report
