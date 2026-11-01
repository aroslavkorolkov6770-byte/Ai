# Brain v3 — контракт качества

Сценариев: **200**.
Сценариев с историей: **54**.
Уровень контракта: `expanded`.

## Граница использования

Этот набор используется только для независимой оценки. Он не является датасетом обучения и не содержит эталонных текстов ответа.

## Покрытие доменов

- `casual_dialogue`: 16.
- `context_and_memory`: 20.
- `correction`: 18.
- `emotional_support`: 16.
- `explanation_and_instruction`: 16.
- `opinion_and_choice`: 16.
- `privacy_and_memory`: 16.
- `safety_boundaries`: 16.
- `style_and_length`: 16.
- `tool_routing`: 18.
- `uncertainty_and_clarification`: 16.
- `web_grounding`: 16.

## Ожидаемые маршруты

- `clarify`: 17.
- `direct_dialogue`: 58.
- `local_explanation`: 17.
- `memory_lookup`: 41.
- `refuse_or_safe_redirect`: 17.
- `structured_reasoning`: 21.
- `tool_time`: 6.
- `tool_weather`: 5.
- `tool_web`: 18.
