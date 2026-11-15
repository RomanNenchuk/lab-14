Журнал взаємодії з LLM (обов'язковий артефакт протоколу «LLM-експерт»): дата, модель і версія, параметри (temperature, seed), версія підказки, сирий вихід, рішення людини.
Один запис = один файл `YYYY-MM-DD_<модель>_<тема>.md` за шаблоном `_template.md`.
Автоматичні виклики (RAG, суддя дублікатів) пишуть у `llm_log/*.jsonl` самі: `rag_calls.jsonl`, `judge_calls.jsonl`, `dedup_judge.jsonl`.
Рядки `claims/claims.csv`: id, твердження, тип (fact/rule/hypothesis), джерело, рівень верифікації (V1–V4), статус (verified/rejected/pending), коментар.
