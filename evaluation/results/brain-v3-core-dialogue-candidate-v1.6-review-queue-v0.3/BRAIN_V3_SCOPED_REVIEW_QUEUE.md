# Brain v3 — scoped review queue: brain-v3-core-dialogue-candidate-v1.6

## Статус

- Queue содержит только ID, SHA-256 и semantic metadata; реплики не копируются.
- Сцен: `6`.
- Active corpus, API, веса и обучение не менялись.

## Покрытие

- Темы: {'context_and_memory': 2, 'explanation_and_instruction': 2, 'safety_boundaries': 2}.
- Goals: {'explain_or_plan': 2, 'follow_up_with_context': 2, 'safe_boundary': 2}.
- Тоны: {'calm': 1, 'concise': 1, 'firm': 1, 'neutral': 1, 'thoughtful': 1, 'warm': 1}.
- Human review: {'full_review_required': 6}.

## Следующий шаг

Провести human review согласно scope. Любой verdict остаётся metadata-only и не даёт право на training или promotion.
