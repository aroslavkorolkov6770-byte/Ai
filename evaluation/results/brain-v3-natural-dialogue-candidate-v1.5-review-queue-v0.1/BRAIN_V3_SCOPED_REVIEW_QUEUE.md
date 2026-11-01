# Brain v3 — scoped review queue: brain-v3-natural-dialogue-candidate-v1.5

## Статус

- Queue содержит только ID, SHA-256 и semantic metadata; реплики не копируются.
- Сцен: `6`.
- Active corpus, API, веса и обучение не менялись.

## Покрытие

- Темы: {'casual_dialogue': 1, 'correction': 1, 'emotional_support': 1, 'opinion_and_choice': 1, 'style_and_length': 1, 'uncertainty_and_clarification': 1}.
- Goals: {'acknowledge_and_correct': 1, 'adapt_style': 1, 'casual_connection': 1, 'clarify_missing_context': 1, 'emotional_support': 1, 'reflect_and_decide': 1}.
- Тоны: {'calm': 1, 'concise': 1, 'neutral': 1, 'supportive': 1, 'thoughtful': 1, 'warm': 1}.
- Human review: {'full_review_required': 6}.

## Следующий шаг

Провести human review согласно scope. Любой verdict остаётся metadata-only и не даёт право на training или promotion.
