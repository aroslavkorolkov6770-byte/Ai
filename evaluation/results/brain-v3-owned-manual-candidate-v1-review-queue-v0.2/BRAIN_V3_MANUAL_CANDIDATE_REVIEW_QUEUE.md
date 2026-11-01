# Brain v3 — first manual review-only candidate: queue

## Статус

- Статус: `unreviewed`.
- В очереди: 24 оригинальные четырёхходовые сцены.
- Отчёт содержит только IDs, SHA-256 и semantic metadata; тексты реплик не копируются.
- Active manifest, staging corpus, API, веса и обучение не менялись.

## Покрытие

- Темы: {'casual_dialogue': 4, 'correction': 4, 'emotional_support': 4, 'privacy_and_memory': 4, 'safety_boundaries': 4, 'uncertainty_and_clarification': 4}.
- Dialogue goals: {'acknowledge_and_correct': 4, 'casual_connection': 4, 'clarify_missing_context': 4, 'emotional_support': 4, 'respect_privacy': 4, 'safe_boundary': 4}.
- Тоны: {'concise': 4, 'firm': 7, 'supportive': 5, 'thoughtful': 5, 'warm': 3}.
- Response properties: {'acknowledges_correction': 4, 'appropriate_empathy': 6, 'ask_when_missing': 5, 'brief': 1, 'clear_next_step': 15, 'context_retained': 7, 'natural_tone': 24, 'no_fake_certainty': 6, 'no_repeat': 6, 'no_unfounded_memory': 5, 'privacy_respected': 5, 'relevant': 24, 'safe_boundary': 6, 'uses_requested_style': 1}.
- Полная ручная проверка / пул для стратифицированной выборки: {'full_review_required': 12, 'stratified_review_pool': 12}.

## Что должен сделать reviewer

Проверить все 12 high-risk сцен и минимум 2 стратифицированно выбранные остальные сцены: происхождение и отсутствие личных данных, связность всех четырёх ходов, естественный нетемплейтный тон и совпадение metadata с реальным смыслом. Текст одной сцены можно вывести только локально по её ID через `--show-id`; положительный verdict не создаёт training candidate автоматически.
