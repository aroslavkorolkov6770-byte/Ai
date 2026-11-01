# Brain v3 — scoped review queue: brain-v3-pickup-context-repair-v1.4

## Статус

- Queue содержит только ID, SHA-256 и semantic metadata; реплики не копируются.
- Сцен: `2`.
- Active corpus, API, веса и обучение не менялись.

## Покрытие

- Темы: {'web_grounding': 2}.
- Goals: {'clarify_then_route_verified_fact': 2}.
- Тоны: {'concise': 1, 'neutral': 1}.
- Human review: {'full_review_required': 2}.

## Следующий шаг

Провести human review согласно scope. Любой verdict остаётся metadata-only и не даёт право на training или promotion.
