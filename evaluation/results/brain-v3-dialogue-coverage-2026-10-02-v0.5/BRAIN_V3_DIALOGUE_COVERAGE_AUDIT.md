# Brain v3 — аудит разнообразия dialogue-корпуса

Policy: `brain_v3_dialogue_coverage_v1`.
Проверка работает только с metadata чистого корпуса; реплики и свободные теги источников не выводятся.

## Базовая чистота корпуса

Записей: 188125; принято: 188125; отклонено: 0.
Основной corpus gate: `True`; причины: нет.

## Наблюдаемое покрытие

Dialogue-записей: 11945/6000.
Dialogue-ходов: 147970/20000.
Многоходовых записей (от 4 ходов): 11354/2500.
Доля многоходовых записей: 0.951/0.350.
Неотображённых в policy topic/goal/tone/property ходов: 5720/5720/5710/0.

### Split

- `test`: 15034/2000 ходов.
- `train`: 117778/16000 ходов.
- `validation`: 15158/2000 ходов.

### Темы

- `casual_dialogue`: 0/1600 ходов.
- `context_and_memory`: 0/1200 ходов.
- `correction`: 0/1000 ходов.
- `emotional_support`: 0/1200 ходов.
- `explanation_and_instruction`: 0/2000 ходов.
- `opinion_and_choice`: 0/1400 ходов.
- `privacy_and_memory`: 0/700 ходов.
- `safety_boundaries`: 0/900 ходов.
- `style_and_length`: 0/900 ходов.
- `tool_routing`: 142250/1200 ходов.
- `uncertainty_and_clarification`: 0/1000 ходов.
- `web_grounding`: 0/1000 ходов.

### Цели диалога

- `acknowledge_and_correct`: 0/1200 ходов.
- `adapt_style`: 0/1000 ходов.
- `casual_connection`: 0/1500 ходов.
- `clarify_missing_context`: 0/1200 ходов.
- `emotional_support`: 0/1300 ходов.
- `explain_or_plan`: 0/1800 ходов.
- `follow_up_with_context`: 142250/1400 ходов.
- `handle_uncertainty`: 0/1200 ходов.
- `reflect_and_decide`: 0/1500 ходов.
- `respect_privacy`: 0/800 ходов.
- `route_verified_fact`: 0/1500 ходов.
- `safe_boundary`: 0/900 ходов.

### Эмоциональные тона

- `calm`: 142250/1800 ходов.
- `concise`: 0/900 ходов.
- `firm`: 0/900 ходов.
- `neutral`: 10/2400 ходов.
- `supportive`: 0/1800 ходов.
- `thoughtful`: 0/1600 ходов.
- `warm`: 0/2000 ходов.

### Свойства ответа

- `acknowledges_correction`: 0/1300 ходов.
- `appropriate_empathy`: 0/1500 ходов.
- `ask_when_missing`: 142250/1400 ходов.
- `brief`: 22/2200 ходов.
- `clear_next_step`: 142250/1800 ходов.
- `context_retained`: 146802/1600 ходов.
- `fact_grounded`: 0/1800 ходов.
- `natural_tone`: 5720/4000 ходов.
- `no_fake_certainty`: 142250/1600 ходов.
- `no_repeat`: 0/1600 ходов.
- `no_unfounded_memory`: 0/1400 ходов.
- `no_url_without_request`: 0/2000 ходов.
- `privacy_respected`: 0/900 ходов.
- `relevant`: 147970/5000 ходов.
- `safe_boundary`: 0/1000 ходов.
- `source_on_request`: 0/700 ходов.
- `uses_requested_style`: 0/1300 ходов.

## Решение

Допуск dialogue-слоя по policy: **заблокировано**.
Причины: insufficient_topic_coverage:casual_dialogue, insufficient_topic_coverage:context_and_memory, insufficient_topic_coverage:correction, insufficient_topic_coverage:emotional_support, insufficient_topic_coverage:explanation_and_instruction, insufficient_topic_coverage:opinion_and_choice, insufficient_topic_coverage:privacy_and_memory, insufficient_topic_coverage:safety_boundaries, insufficient_topic_coverage:style_and_length, insufficient_topic_coverage:uncertainty_and_clarification, insufficient_topic_coverage:web_grounding, insufficient_dialogue_goal_coverage:acknowledge_and_correct, insufficient_dialogue_goal_coverage:adapt_style, insufficient_dialogue_goal_coverage:casual_connection, insufficient_dialogue_goal_coverage:clarify_missing_context, insufficient_dialogue_goal_coverage:emotional_support, insufficient_dialogue_goal_coverage:explain_or_plan, insufficient_dialogue_goal_coverage:handle_uncertainty, insufficient_dialogue_goal_coverage:reflect_and_decide, insufficient_dialogue_goal_coverage:respect_privacy, insufficient_dialogue_goal_coverage:route_verified_fact, insufficient_dialogue_goal_coverage:safe_boundary, insufficient_emotional_tone_coverage:concise, insufficient_emotional_tone_coverage:firm, insufficient_emotional_tone_coverage:neutral, insufficient_emotional_tone_coverage:supportive, insufficient_emotional_tone_coverage:thoughtful, insufficient_emotional_tone_coverage:warm, insufficient_response_property_coverage:acknowledges_correction, insufficient_response_property_coverage:appropriate_empathy, insufficient_response_property_coverage:brief, insufficient_response_property_coverage:fact_grounded, insufficient_response_property_coverage:no_repeat, insufficient_response_property_coverage:no_unfounded_memory, insufficient_response_property_coverage:no_url_without_request, insufficient_response_property_coverage:privacy_respected, insufficient_response_property_coverage:safe_boundary, insufficient_response_property_coverage:source_on_request, insufficient_response_property_coverage:uses_requested_style.
Готовность запустить генеративное обучение: **не готов**.
Общие причины блокировки: dialogue_coverage:insufficient_topic_coverage:casual_dialogue, dialogue_coverage:insufficient_topic_coverage:context_and_memory, dialogue_coverage:insufficient_topic_coverage:correction, dialogue_coverage:insufficient_topic_coverage:emotional_support, dialogue_coverage:insufficient_topic_coverage:explanation_and_instruction, dialogue_coverage:insufficient_topic_coverage:opinion_and_choice, dialogue_coverage:insufficient_topic_coverage:privacy_and_memory, dialogue_coverage:insufficient_topic_coverage:safety_boundaries, dialogue_coverage:insufficient_topic_coverage:style_and_length, dialogue_coverage:insufficient_topic_coverage:uncertainty_and_clarification, dialogue_coverage:insufficient_topic_coverage:web_grounding, dialogue_coverage:insufficient_dialogue_goal_coverage:acknowledge_and_correct, dialogue_coverage:insufficient_dialogue_goal_coverage:adapt_style, dialogue_coverage:insufficient_dialogue_goal_coverage:casual_connection, dialogue_coverage:insufficient_dialogue_goal_coverage:clarify_missing_context, dialogue_coverage:insufficient_dialogue_goal_coverage:emotional_support, dialogue_coverage:insufficient_dialogue_goal_coverage:explain_or_plan, dialogue_coverage:insufficient_dialogue_goal_coverage:handle_uncertainty, dialogue_coverage:insufficient_dialogue_goal_coverage:reflect_and_decide, dialogue_coverage:insufficient_dialogue_goal_coverage:respect_privacy, dialogue_coverage:insufficient_dialogue_goal_coverage:route_verified_fact, dialogue_coverage:insufficient_dialogue_goal_coverage:safe_boundary, dialogue_coverage:insufficient_emotional_tone_coverage:concise, dialogue_coverage:insufficient_emotional_tone_coverage:firm, dialogue_coverage:insufficient_emotional_tone_coverage:neutral, dialogue_coverage:insufficient_emotional_tone_coverage:supportive, dialogue_coverage:insufficient_emotional_tone_coverage:thoughtful, dialogue_coverage:insufficient_emotional_tone_coverage:warm, dialogue_coverage:insufficient_response_property_coverage:acknowledges_correction, dialogue_coverage:insufficient_response_property_coverage:appropriate_empathy, dialogue_coverage:insufficient_response_property_coverage:brief, dialogue_coverage:insufficient_response_property_coverage:fact_grounded, dialogue_coverage:insufficient_response_property_coverage:no_repeat, dialogue_coverage:insufficient_response_property_coverage:no_unfounded_memory, dialogue_coverage:insufficient_response_property_coverage:no_url_without_request, dialogue_coverage:insufficient_response_property_coverage:privacy_respected, dialogue_coverage:insufficient_response_property_coverage:safe_boundary, dialogue_coverage:insufficient_response_property_coverage:source_on_request, dialogue_coverage:insufficient_response_property_coverage:uses_requested_style.

Audit не создаёт данные, не обучает модель, не меняет active manifest и не показывает тексты диалогов.
