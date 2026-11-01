# Brain v3 — OASST2: пилот новых русских многоходовых диалогов

Дата: 2026-10-02. Статус: отдельный технический candidate, quality review
пока не проведён. Рабочий чат и active corpus остаются на прежней версии.

## Фактический результат

Скачан закреплённый ready tree archive OASST2. Проверены точные bytes
`54 370 156` и SHA-256
`7a886a16ccfc1173c4f00a6897523e3c95b2785a86ee44a18a98f4f2807ee29b`.
Перед скачиванием прочитана pinned карточка с декларацией Apache-2.0.
HTTP redirect издателя ведёт на `us.aws.cdn.hf.co`; downloader разрешает
этот точный HTTPS host, а не произвольный домен. Временный signed URL
не сохраняется в metadata или журналах.

Из 13 854 ready деревьев исключены 10 364 старых семейства OASST1.
Осталось 3 490 новых семей всех языков; после русскоязычных, source-review,
toxicity, privacy и conversational-фильтров выбраны **314 диалогов / 1 414
ходов**. Каждая запись соответствует одной новой семье и одной полной ветви.

| Часть данных | Диалогов |
| --- | ---: |
| Train | 247 |
| Validation | 39 |
| Test | 28 |
| Всего | 314 |

235 диалогов имеют 4 хода, 79 — 6 ходов. Двухходовые пути в этот follow-up
pilot не включены: 89 таких путей отражены в счётчиках, а не потеряны молча.
1264 допустимых safe leaf paths не означают 1264 независимых диалога:
несколько ветвей одной семьи не размножались в candidate.

## Как исключались неподходящие ветви

Принимаются только явно русский язык, `synthetic=false`, `deleted=false`,
`review_result=true`, отсутствие model_name и правильное чередование ролей.
Проверяются parent links, размер распаковки, строка, глубина и число nodes.
Небезопасная нода обрывает всю свою ветвь: её безопасный хвост нельзя
выдать за отдельную беседу с сохранённым контекстом.

Из отмеченных причин: 200 deleted nodes, 195 unreviewed, 68 toxicity,
125 URL-like, 14 phone-like, 4 email-like, 1 credential-like. Это счётчики
посещённых нод, а не оценка числа рисковых записей во всём источнике.
На уровне путей отсеяны 286 слишком длинных assistant-ответов, 119
code/ASCII блоков и 15 упоминаний старой model identity. Исходник сохранён,
его реплики не переписывались и не перефразировались.

## Техническая проверка и её границы

Сопоставлены 11 945 активных dialogue-записей: полные exact/near подписи,
длинные user-реплики и пары последовательных ходов. Дополнительно сверены
218 сценариев текущего `brain_v3_acceptance.json`, включая user-реплики
истории, exact и near для достаточно длинных prompt. Счётчик 218 относится
к реально прочитанному текущему contract, а не к старому отчёту на 208.

Все active source hashes, manifest, descriptor и contract сверены.
Изолированный независимый schema/duplicate audit принял 314/314:
отклонений, межsplit exact и near-кандидатов — ноль.

176 180 языковых документов не сравнивались по содержимому в этом
dialogue-first проходе; остальные evaluation-наборы тоже ещё не охвачены.
Перед любым promotion нужны полный active preflight и весь актуальный
holdout. Поэтому этот пилот не выдаётся за полную проверку пересечений.

Локальная human-приёмка имеет **0 approved**. Publisher review flags не
подменяют решения владельца проекта. Темы, цели и тон записей остаются
`unclassified`, quality tier — `license_verified`, а единственное
`expected_response_properties=[relevant]` задаёт цель проверки, не утверждает
подтверждённое качество ответа. Права на отдельные вставленные цитаты и
приватность всех текстов тоже не доказываются одними статическими фильтрами.

## Подсказки для следующей выборки

В metadata найдены эвристические сигналы: explanation 87, choice 40,
casual 32, style 31, web 28, correction 21, emotional support 15,
context 14, privacy 13, safety 9, clarification 7. Категории пересекаются.
Это **не** подтверждённые темы и не число качественных сцен поддержки.
Они позволяют распределить следующую выборку для чтения, а не автоматически
закрыть coverage gate. После подтверждения нужно пересчитать долю реальной
пригодности и решить, стоит ли расширять источник.

## Артефакты и повторный запуск

- Fixed archive: `data/brain_v3/intake/openassistant-oasst2-apache-v1/2023-11-05_oasst2_ready.trees.jsonl.gz`.
- Candidate: `data/brain_v3/staging/openassistant-oasst2-new-families-v0.1.jsonl`.
- Metadata и независимый audit: `evaluation/results/oasst2-new-families-pilot-v0.1/`.
- Intake: `ai_core/openassistant_oasst2.py`.
- Orchestration: `evaluation/prepare_oasst2_pilot.py`.

Candidate SHA-256:
`64bcacece8c877d42dcf16b27247caac0f4ec18b4f9a5295951c19a0f202f891`.
Report JSON SHA-256:
`eafbc9a489698e5fea31089aaeaab8f78bd7de34fba497e579c21ea5453e01d1`.

Первый запуск выполнен командой:

```powershell
.\.venv\Scripts\python.exe -m evaluation.prepare_oasst2_pilot --prepare --download
```

Она не перезаписывает существующие candidate/отчёт. Для нового эксперимента
можно указать новые пути `--candidate` и `--report-directory` внутри
staging/results. Проверенный archive можно использовать повторно.

Профильные тесты intake/overlap/family guard прошли `15/15`; `compileall`
и `pip check` прошли. На реальном результате сверены hashes, 314 уникальные
семьи, pending human статус и неизменный active manifest.

Следующий шаг: подготовить стратифицированный quality review по split,
длине, semantic hints и контрольным записям без hints; прочитать полные
диалоги и проверить связность, полезность, стиль, factual/medical границы
и возможные сторонние вставки. Обучение, promotion и GitHub не выполнялись.
