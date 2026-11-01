# Brain v3 — OpenAssistant OASST1: проверенный local intake и promotion

Дата: 21 сентября 2026 года.

## Решение

OpenAssistant OASST1 принят в локальный active Brain v3 corpus только после
закрепления версии, проверки локального intake, фильтрации рисков, isolated
staging audit, preflight против всех active sources, удаления подтверждённых
cross-source повторов и финального active audit.

Никакие готовые веса не подключались, обучение не запускалось, работающий API
не изменялся и ничего не отправлялось на GitHub.

## Происхождение и границы

- Source ID: openassistant-oasst1-apache-v1.
- Официальная карточка набора: <https://huggingface.co/datasets/OpenAssistant/oasst1>.
- Закреплённая revision: fdf72ae0827c1cda404aff25b6603abec9e3399b.
- Лицензия: Apache-2.0; проверенная ссылка зафиксирована в local provenance:
  <https://huggingface.co/datasets/OpenAssistant/oasst1/blob/fdf72ae0827c1cda404aff25b6603abec9e3399b/LICENSE>.
- Обработан единственный локальный gzip snapshot
  2023-04-12_oasst_ready.trees.jsonl.gz. Код, поля-инструкции и внешние URL из
  source data не исполнялись.
- В staging попадали только русскоязычные, human-reviewed, не удалённые,
  несинтетические ветви с чередованием ролей user → assistant.

## Проверка локального intake

- Размер intake snapshot: 34 145 252 bytes.
- SHA-256 intake snapshot:
  2a9a8fd343e9b28e04a895a669d3253f82d93e9c174d440199ae19d5fafbdff7.
- Проверено trees: 10 364; nodes: 88 838; leaf paths: 46 283.
- Строго допустимых до record-level deduplication paths: 1 582.
- На уровне нод до staging были отсеяны URL, e-mail, phone-like,
  credential-like, non-Russian, deleted, unreviewed, incomplete-role и
  toxicity-threshold случаи. Счётчики фильтрации есть в immutable staging
  report и не раскрывают текст реплик.

## История staging-версий

| Версия | Результат | Причина решения |
| --- | --- | --- |
| v0.1 | не активирована | 1 543 записи; isolated audit чист, но preflight с active corpus обнаружил 2 близких cross-source повтора. |
| v0.2 | принята | 1 541 запись; удалены только эти 2 подтверждённых near-duplicate-with-active записи, затем isolated audit и preflight дали 0 отклонений и 0 пересечений. |

Исходный v0.1 и его отчёты сохранены. Новый v0.2 создан в staging атомарно и
не является перезаписью старой версии.

## Принятый snapshot

- Active staging candidate:
  data/brain_v3/staging/openassistant-oasst1-apache-v0.2.jsonl.
- SHA-256 candidate/raw:
  c040157ca4db005e3e919e9d5c6b70d85817657b5ec49ea23f6724e3a8467d0c.
- Размер candidate/raw: 5 174 598 bytes.
- Новый immutable raw:
  data/brain_v3/raw/openassistant-oasst1-apache-v1.jsonl.
- Backup manifest до изменения:
  data/brain_v3/manifest_backups/corpus_manifest.v1.before-openassistant-oasst1-apache-v1.json.

## Результаты проверок

| Проверка | Значение |
| --- | ---: |
| Isolated audit v0.2: принято / отклонено | 1 541 / 0 |
| Isolated audit v0.2: exact / near межsplit | 0 / 0 |
| Preflight v0.2: active sources до promotion | 5 |
| Preflight v0.2: принято / отклонено | 188 125 / 0 |
| Preflight v0.2: clean merge | true |
| Final active sources | 6 |
| Final active records: принято / отклонено | 188 125 / 0 |
| Final dialogue records | 11 945 |
| Final unique dialogue turns | 147 970 |
| Final language characters | 241 707 400 |
| Final exact / near межsplit | 0 / 0 |
| Базовый corpus training gate | true |

Доказательства находятся в immutable результатах:

- evaluation/results/openassistant-oasst1-staging-v0.1/;
- evaluation/results/openassistant-oasst1-audit-v0.1/;
- evaluation/results/openassistant-oasst1-preflight-v0.1/;
- evaluation/results/openassistant-oasst1-active-clean-v0.2/;
- evaluation/results/openassistant-oasst1-audit-v0.2/;
- evaluation/results/openassistant-oasst1-preflight-v0.2/;
- evaluation/results/openassistant-oasst1-promotion-v0.1/.

## Что это разрешает и чего не разрешает

Этот источник добавляет отфильтрованные русскоязычные диалоги и повышает
многообразие по сравнению с task-oriented Russian MultiWOZ. Однако прохождение
базового data-gate не является доказательством естественного разговора на
любые темы. Перед обучением собственной generative-модели обязателен отдельный
coverage audit: он должен подтвердить достаточность бытового диалога, поддержки,
исправлений, приватности, стиля, безопасных границ и других обязательных
направлений.

Source-level human-reviewed metadata также не является автоматической оценкой
каждой реплики как идеального ответа. Перед использованием этого active source
в свободном генераторе нужен отдельный выборочный quality-review по
зафиксированному протоколу. До такого review OASST1 участвует в учёте корпуса,
но не служит основанием для обещаний о качестве готового разговорного мозга.
