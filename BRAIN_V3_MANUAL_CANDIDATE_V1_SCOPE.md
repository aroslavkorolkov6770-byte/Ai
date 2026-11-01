# Brain v3 — scope первого ручного original candidate

Статус: required human-review coverage завершён; две privacy-сцены отклонены,
поэтому v1 остаётся review-only материалом, не training dataset.  
Дата: 2026-09-29

## Почему нужен маленький tranche

Coverage audit показывает, что количественно dialogue-слой большой, но почти
целиком отражает tool-routing/follow-up из технических источников. Ранние
project-owned шаблонные drafts были технически чистыми, но неестественными.
Поэтому нельзя сразу писать тысячи строк: сначала нужно доказать качество
самого ручного authoring и review процесса на маленькой, полностью проверяемой
партии.

## Первый tranche

- 24 оригинальные записи по 4 хода; всего 96 ходов.
- По 4 записи: `casual_dialogue`, `emotional_support`, `correction`,
  `uncertainty_and_clarification`, `privacy_and_memory`, `safety_boundaries`.
- Обязательные цели: connection, support, correction, clarification, privacy
  и safe boundary.
- Обязательные тона: warm, supportive, thoughtful, concise, firm.
- Проверяемые свойства: natural tone, отсутствие повторов и выдуманной памяти,
  корректное исправление, уместная эмпатия, privacy и safe boundary.

Эти 24 записи не дают права на обучение, не закрывают G3/G4 и не заменяют
исторический ориентир 3 475 записей. Их единственная задача — показать, что
новый процесс не порождает шаблонный ритм до масштабирования.

## Границы источника

Источник будет иметь `source_kind=owned`, `license=OWNED_BY_PROJECT` и
`authoring_method=manual_original_only`. Запрещены внешний текст, личные чаты,
готовые ответы сторонних моделей, массовые шаблоны и автоматические
перефразировки. Черновик остаётся `review_only`, пока его не проверит человек.

## Контроль split и review

До staging для каждой dialogue family создаётся metadata ledger. Прямые
перефразы и альтернативные ветки не могут разойтись по train/validation/test.
На первом tranche split не назначается: он существует только для проверки
естественности и полноты review. После положительной review отдельным шагом
будет создан новый immutable training candidate с нормальным split.

## Отпечатки исходного состояния

- Coverage report v0.4 SHA-256:
  `d131d6d9ab1b47c40ca7a8a7e1698e723afcbba974c13d0d883cd8502e9a369a`.
- Expanded acceptance contract v0.2 SHA-256:
  `c7601245c604cdd157b2fa4d4e228acdb7040ac72c49dd618d58d411dc205254`.

Машинно-читаемая версия scope находится в
`data/brain_v3/reviews/brain-v3-owned-manual-candidate-v1.scope.json`.

## Текущий артефакт и следующий шаг

Сцены хранятся только в
`data/brain_v3/reviews/brain-v3-owned-manual-candidate-v1.review.jsonl`.
Их SHA-256: `07be0fef71c2e97263adba8261129f236d370e5e7f8fd38951c61bc8888f8731`.
Metadata-only очередь находится в
`evaluation/results/brain-v3-owned-manual-candidate-v1-review-queue-v0.2/`.

Отдельный repair-only candidate v1.1 для двух отклонённых privacy-сцен уже
создан в `BRAIN_V3_MEMORY_REPAIR_CANDIDATE_V1_1_SCOPE.md` и ожидает полную
human review. До её завершения не создаётся staging JSONL, не запускаются
audit/promotion/training и не меняется active corpus. Порядок исходной
проверки описан в `BRAIN_V3_MANUAL_CANDIDATE_V1_REVIEW_GUIDE.md`.
