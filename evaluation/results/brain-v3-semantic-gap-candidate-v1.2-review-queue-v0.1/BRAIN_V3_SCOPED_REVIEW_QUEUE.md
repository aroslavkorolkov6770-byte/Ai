# Brain v3 — scoped review queue: brain-v3-semantic-gap-candidate-v1.2

## Статус

- Queue содержит только ID, SHA-256 и semantic metadata; реплики не копируются.
- Сцен: `14`.
- Active corpus, API, веса и обучение не менялись.

## Покрытие

- Темы: {'context_and_memory': 2, 'explanation_and_instruction': 2, 'opinion_and_choice': 2, 'style_and_length': 2, 'tool_routing': 2, 'uncertainty_and_clarification': 2, 'web_grounding': 2}.
- Goals: {'adapt_style': 2, 'explain_or_plan': 2, 'follow_up_with_context': 2, 'handle_uncertainty': 2, 'reflect_and_decide': 2, 'route_verified_fact': 4}.
- Тоны: {'calm': 3, 'concise': 3, 'neutral': 3, 'thoughtful': 3, 'warm': 2}.
- Human review: {'full_review_required': 6, 'stratified_review_pool': 8}.

## Следующий шаг

Провести human review согласно scope. Любой verdict остаётся metadata-only и не даёт право на training или promotion.
