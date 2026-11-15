# Результат 3: суперечності та їх розв'язання

Станом на стан `data/clean`. Усі суперечності штучні (їх вставлено скриптом `scripts/inject_defects.py`, див. `data/baseline/INJECTION.md`); детектор ground truth не читає.

**Всього суперечностей: 34** (розв'язано 34, відкрито 0); за типами: {'temporal': 3, 'value': 17, 'logical': 9, 'terminology': 5}.

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
| C-usesAircraft-AL110-20261103 | часова | AL110-20261103 · usesAircraft | AL-009 [synthetic-generator-v1, 0.97, 2026-11-03] ⟷ AW-030 [legacy-roster, 0.7, 2026-10-20] | дата (новіше) | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: AL-009 |
| C-usesAircraft-BC116-20261102 | часова | BC116-20261102 · usesAircraft | BC-020 [feed-b-import, 0.8, 2026-11-02] ⟷ AW-031 [legacy-roster, 0.7, 2026-10-20] | дата (новіше) | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: BC-020 |
| C-usesAircraft-DJ107-20261102 | часова | DJ107-20261102 · usesAircraft | DJ-027 [synthetic-generator-v1, 0.97, 2026-11-02] ⟷ AW-032 [legacy-roster, 0.7, 2026-10-20] | дата (новіше) | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: DJ-027 |
| C-operatedBy-DJ108-20261103 | значень (різні джерела) | DJ108-20261103 · operatedBy | DJ [synthetic-generator-v1, 1.0, 2026-11-03] ⟷ SU [codeshare-feed, 0.6, 2026-11-03] | довіра до джерела | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: DJ |
| C-operatedBy-EN117-20261103 | значень (різні джерела) | EN117-20261103 · operatedBy | EN [synthetic-generator-v1, 1.0, 2026-11-03] ⟷ CA [codeshare-feed, 0.6, 2026-11-03] | довіра до джерела | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: EN |
| C-operatedBy-SU118-20261103 | значень (різні джерела) | SU118-20261103 · operatedBy | SU [synthetic-generator-v1, 1.0, 2026-11-03] ⟷ CA [codeshare-feed, 0.6, 2026-11-03] | довіра до джерела | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: SU |
| C-locatedIn-CPH | логічна (reasoner / інваріант) | CPH · locatedIn | copenhagen [iata-public-codes, 1.0, 2026-01-01] ⟷ dublin [feed-b-import, 0.8, 2026-10-20] | довіра до джерела | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: copenhagen |
| C-locatedIn-KIV | логічна (reasoner / інваріант) | KIV · locatedIn | chisinau [iata-public-codes, 1.0, 2026-01-01] ⟷ lviv [feed-b-import, 0.8, 2026-10-20] | довіра до джерела | залишено запис-переможець у `rel_*`; решту винесено в альтернативи | розв'язано: chisinau |
| C-departsArrivesSame-SU114-20261102 | логічна (reasoner / інваріант) | SU114-20261102 · arrivesAt | IEV [synthetic-generator-v1, 0.98, 2026-11-02] ⟷ LWO [ланцюг рейсів: наступний рейс SU115-20261102 вилітає звідси] | рішення власника | `arrivesAt` замінено на підтверджений аеропорт; помилкове значення збережено в альтернативах | розв'язано: LWO |
| C-departsArrivesSame-MS106-20261103 | логічна (reasoner / інваріант) | MS106-20261103 · arrivesAt | BUD [synthetic-generator-v1, 0.98, 2026-11-03] ⟷ MAD [ланцюг рейсів: наступний рейс MS107-20261103 вилітає звідси] | рішення власника | `arrivesAt` замінено на підтверджений аеропорт; помилкове значення збережено в альтернативах | розв'язано: MAD |
| C-flightDate-BC105-20261102 | логічна (reasoner / інваріант) | BC105-20261102 · date | 2026-11-02 [scheduledDeparture (і flightId) — 2 із 3 джерел] ⟷ 2026-11-03 [поле date] | консенсус | `flights.date` узгоджено зі `scheduledDeparture`; старе значення збережено в альтернативах | розв'язано: 2026-11-02 |
| C-flightDate-AW105-20261102 | логічна (reasoner / інваріант) | AW105-20261102 · date | 2026-11-02 [scheduledDeparture (і flightId) — 2 із 3 джерел] ⟷ 2026-11-03 [поле date] | консенсус | `flights.date` узгоджено зі `scheduledDeparture`; старе значення збережено в альтернативах | розв'язано: 2026-11-02 |
| C-flightDate-AL106-20261102 | логічна (reasoner / інваріант) | AL106-20261102 · date | 2026-11-02 [scheduledDeparture (і flightId) — 2 із 3 джерел] ⟷ 2026-11-03 [поле date] | консенсус | `flights.date` узгоджено зі `scheduledDeparture`; старе значення збережено в альтернативах | розв'язано: 2026-11-02 |
| C-flightDate-MS105-20261103 | логічна (reasoner / інваріант) | MS105-20261103 · date | 2026-11-03 [scheduledDeparture (і flightId) — 2 із 3 джерел] ⟷ 2026-11-04 [поле date] | консенсус | `flights.date` узгоджено зі `scheduledDeparture`; старе значення збережено в альтернативах | розв'язано: 2026-11-03 |
| C-model-SU-001 | термінологічна | SU-001 · model | Airbus A320 [консенсус (5 з 6 записів)] ⟷ A320 [aircraft.csv] | консенсус | `aircraft.model` уніфіковано; стару назву збережено в альтернативах | розв'язано: Airbus A320 |
| C-model-SU-002 | термінологічна | SU-002 · model | Airbus A220-300 [консенсус (6 з 7 записів)] ⟷ Airbus A220 [aircraft.csv] | консенсус | `aircraft.model` уніфіковано; стару назву збережено в альтернативах | розв'язано: Airbus A220-300 |
| C-model-SU-003 | термінологічна | SU-003 · model | Boeing 737-800 [консенсус (6 з 7 записів)] ⟷ B737-800 [aircraft.csv] | консенсус | `aircraft.model` уніфіковано; стару назву збережено в альтернативах | розв'язано: Boeing 737-800 |
| C-model-SU-004 | термінологічна | SU-004 · model | Embraer E190 [консенсус (7 з 8 записів)] ⟷ Embraer 190 [aircraft.csv] | консенсус | `aircraft.model` уніфіковано; стару назву збережено в альтернативах | розв'язано: Embraer E190 |
| C-model-SU-005 | термінологічна | SU-005 · model | ATR 72-600 [консенсус (5 з 6 записів)] ⟷ ATR72-600 [aircraft.csv] | консенсус | `aircraft.model` уніфіковано; стару назву збережено в альтернативах | розв'язано: ATR 72-600 |
| C-val-P006-flightHours | значень (різні джерела) | P006 · flightHours | 5082 [запис P006] ⟷ 4671 [запис P054 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 5082 |
| C-val-P007-flightHours | значень (різні джерела) | P007 · flightHours | 11363 [запис P007] ⟷ 11877 [запис P052 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 11877 |
| C-val-P008-flightHours | значень (різні джерела) | P008 · flightHours | 4757 [запис P008] ⟷ 5076 [запис P046 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 5076 |
| C-val-P009-flightHours | значень (різні джерела) | P009 · flightHours | 8373 [запис P009] ⟷ 8888 [запис P051 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 8888 |
| C-val-P015-flightHours | значень (різні джерела) | P015 · flightHours | 7381 [запис P015] ⟷ 8090 [запис P050 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 8090 |
| C-val-P016-flightHours | значень (різні джерела) | P016 · flightHours | 5833 [запис P016] ⟷ 5195 [запис P049 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 5833 |
| C-val-P018-flightHours | значень (різні джерела) | P018 · flightHours | 2791 [запис P018] ⟷ 3087 [запис P048 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 3087 |
| C-val-P035-flightHours | значень (різні джерела) | P035 · flightHours | 10792 [запис P035] ⟷ 10147 [запис P053 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 10792 |
| C-val-P041-flightHours | значень (різні джерела) | P041 · flightHours | 7430 [запис P041] ⟷ 7002 [запис P045 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 7430 |
| C-val-BC-020-seatCapacity | значень (різні джерела) | BC-020 · seatCapacity | 145 [запис BC-020] ⟷ 153 [запис bc-020 (дублікат), 0.8] | рішення власника | значення атрибута вибрано; друге збережено в альтернативах | розв'язано: 153 |
| C-doc-en_aircraft_ground_handling_mirror | значень (різні джерела) | en_aircraft_ground_handling_mirror · числове значення в тексті | 50 — «According to the International Air Transport Associati [en_aircraft_ground_handling, 2026-10-31] ⟷ 25 — «According to the International Air Transport Associati [en_aircraft_ground_handling_mirror, 2025-03-12] | дата (новіше) | дзеркало переміщено в `kb/_quarantine/`; обидва значення збережено в альтернативах | розв'язано: 50 — «According to the International Air |
| C-doc-en_airline_mirror | значень (різні джерела) | en_airline_mirror · числове значення в тексті | 1978 — «Legacy carriers, US carriers that predate the Airlin [en_airline, 2026-10-31] ⟷ 1980 — «Legacy carriers, US carriers that predate the Airlin [en_airline_mirror, 2025-03-12] | дата (новіше) | дзеркало переміщено в `kb/_quarantine/`; обидва значення збережено в альтернативах | розв'язано: 1978 — «Legacy carriers, US carriers tha |
| C-doc-en_airline_hub_mirror | значень (різні джерела) | en_airline_hub_mirror · числове значення в тексті | 1985 — «Sheikh Mohammed proceeded to establish a new airline [en_airline_hub, 2026-10-31] ⟷ 1982 — «Sheikh Mohammed proceeded to establish a new airline [en_airline_hub_mirror, 2025-03-12] | дата (новіше) | дзеркало переміщено в `kb/_quarantine/`; обидва значення збережено в альтернативах | розв'язано: 1985 — «Sheikh Mohammed proceeded to est |
| C-doc-en_eurocontrol_mirror | значень (різні джерела) | en_eurocontrol_mirror · числове значення в тексті | 21, 80 — «The average flight duration is approximately 21 mi [en_eurocontrol, 2026-10-31] ⟷ 10, 80 — «The average flight duration is approximately 10 mi [en_eurocontrol_mirror, 2025-03-12] | дата (новіше) | дзеркало переміщено в `kb/_quarantine/`; обидва значення збережено в альтернативах | розв'язано: 21, 80 — «The average flight duration is |
| C-departsArrivesSame-BC116-20261103 | логічна (reasoner / інваріант) | BC116-20261103 · arrivesAt | HEL [synthetic-generator-v1, 0.98, 2026-11-03] ⟷ RIX [рішення власника (Roman Nenchuk)] | рішення власника | `arrivesAt` замінено на підтверджений аеропорт; помилкове значення збережено в альтернативах | розв'язано: RIX |

## 4. Збереження альтернатив з provenance
Відхилене значення не видаляється: воно переходить у `data/clean/graph/conflict_alternatives.csv` (conflictId, subject, predicate, value, source, confidence, validFrom, status, strategy, decidedBy, approvedBy, decidedAt, restore).
`python -m quality.conflicts restore <ID>` повертає відхилене значення на місце (перевірено тестом). Приклади (по одному на тип суперечності):

**C-usesAircraft-AL110-20261103** (functional):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| AL-009 | synthetic-generator-v1 | 0.97 | 2026-11-03 | accepted | date |
| AW-030 | legacy-roster | 0.7 | 2026-10-20 | rejected | date |

**C-departsArrivesSame-SU114-20261102** (disjoint):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| IEV | synthetic-generator-v1 | 0.98 | 2026-11-02 | rejected | owner |
| LWO | ланцюг рейсів: наступний рейс SU115-20261102 вилітає звідси |  |  | accepted | owner |

**C-flightDate-BC105-20261102** (flightdate):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| 2026-11-02 | scheduledDeparture (і flightId) — 2 із 3 джерел |  |  | accepted | consensus |
| 2026-11-03 | поле date |  |  | rejected | consensus |

**C-model-SU-001** (terminology):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| Airbus A320 | консенсус (5 з 6 записів) |  |  | accepted | consensus |
| A320 | aircraft.csv |  |  | rejected | consensus |

**C-val-P006-flightHours** (attribute):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| 5082 | запис P006 |  |  | accepted | owner |
| 4671 | запис P054 (дублікат) | 0.8 |  | rejected | owner |

**C-doc-en_aircraft_ground_handling_mirror** (document):

| value | source | confidence | validFrom | status | strategy |
|---|---|---|---|---|---|
| 50 — «According to the International Air Transport Association (IATA), | en_aircraft_ground_handling |  | 2026-10-31 | accepted | date |
| 25 — «According to the International Air Transport Association (IATA), | en_aircraft_ground_handling_mirror |  | 2025-03-12 | rejected | date |

## 5. Журнал рішень (хто, коли, чому)
| Суперечність | Хто вирішив | Хто затвердив | Коли | Чому |
|---|---|---|---|---|
| C-usesAircraft-AL110-20261103 | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | запис із validFrom 2026-11-03 новіший і довіреніший; старий (2026-10-20, legacy-roster) — застарілий |
| C-usesAircraft-BC116-20261102 | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | запис із validFrom 2026-11-02 новіший і довіреніший; старий (2026-10-20, legacy-roster) — застарілий |
| C-usesAircraft-DJ107-20261102 | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | запис із validFrom 2026-11-02 новіший і довіреніший; старий (2026-10-20, legacy-roster) — застарілий |
| C-operatedBy-DJ108-20261103 | rule:trust | Roman Nenchuk | 2026-11-15 00:18:51 | джерело «synthetic-generator-v1» має вищу довіру (1.0) за альтернативи |
| C-operatedBy-EN117-20261103 | rule:trust | Roman Nenchuk | 2026-11-15 00:18:51 | джерело «synthetic-generator-v1» має вищу довіру (1.0) за альтернативи |
| C-operatedBy-SU118-20261103 | rule:trust | Roman Nenchuk | 2026-11-15 00:18:51 | джерело «synthetic-generator-v1» має вищу довіру (1.0) за альтернативи |
| C-locatedIn-CPH | rule:trust | Roman Nenchuk | 2026-11-15 00:18:51 | джерело «iata-public-codes» має вищу довіру (1.0) за альтернативи |
| C-locatedIn-KIV | rule:trust | Roman Nenchuk | 2026-11-15 00:18:51 | джерело «iata-public-codes» має вищу довіру (1.0) за альтернативи |
| C-departsArrivesSame-SU114-20261102 | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення LWO |
| C-departsArrivesSame-MS106-20261103 | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення MAD |
| C-flightDate-BC105-20261102 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | дві з трьох ознак (scheduledDeparture, flightId) дають одну дату; поле date — самотнє |
| C-flightDate-AW105-20261102 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | дві з трьох ознак (scheduledDeparture, flightId) дають одну дату; поле date — самотнє |
| C-flightDate-AL106-20261102 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | дві з трьох ознак (scheduledDeparture, flightId) дають одну дату; поле date — самотнє |
| C-flightDate-MS105-20261103 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | дві з трьох ознак (scheduledDeparture, flightId) дають одну дату; поле date — самотнє |
| C-model-SU-001 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах |
| C-model-SU-002 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах |
| C-model-SU-003 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах |
| C-model-SU-004 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах |
| C-model-SU-005 | rule:consensus | Roman Nenchuk | 2026-11-15 00:18:51 | найчастіша форма назви моделі в сховищі; відхилена форма лишається в альтернативах |
| C-val-P006-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 5082 |
| C-val-P007-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 11877 |
| C-val-P008-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 5076 |
| C-val-P009-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 8888 |
| C-val-P015-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 8090 |
| C-val-P016-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 5833 |
| C-val-P018-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 3087 |
| C-val-P035-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 10792 |
| C-val-P041-flightHours | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 7430 |
| C-val-BC-020-seatCapacity | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:18:51 | рішення власника (Roman Nenchuk): значення 153 |
| C-doc-en_aircraft_ground_handling_mirror | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | новіший документ (2026-10-31) переважає; застарілий дубль-дзеркало виводимо з обігу |
| C-doc-en_airline_mirror | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | новіший документ (2026-10-31) переважає; застарілий дубль-дзеркало виводимо з обігу |
| C-doc-en_airline_hub_mirror | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | новіший документ (2026-10-31) переважає; застарілий дубль-дзеркало виводимо з обігу |
| C-doc-en_eurocontrol_mirror | rule:date | Roman Nenchuk | 2026-11-15 00:18:51 | новіший документ (2026-10-31) переважає; застарілий дубль-дзеркало виводимо з обігу |
| C-departsArrivesSame-BC116-20261103 | owner:Roman Nenchuk | Roman Nenchuk | 2026-11-15 00:27:13 | рішення власника (Roman Nenchuk): значення RIX |

## 6. Оцінка виявлення проти ground truth (лише для оцінювання)
| Тип вставлених дефектів | Вставлено | Виявлено |
|---|---|---|
| logical_conflict | 10 | 9 |
| temporal_conflict | 3 | 3 |
| terminology_conflict | 5 | 5 |
| value_conflict | 19 | 17 |
