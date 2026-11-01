# Brain v3 — reviewed staging candidate v0.1 audit

## Isolated audit

- Принято: `16`; отклонено: `0`.
- Training allowed: `false`.
- Причины блокировки: `['insufficient_unique_dialogue_turns', 'insufficient_language_characters']`.

## Проверка вместе с active corpus

- Clean merge: `true`.
- Active sources checked: `6`.
- Merge rejections: `0`.

## Prospective dialogue coverage

- Coverage observed: `true`.
- Coverage allowed: `false`.
- Generator training ready: `false`.
- Coverage block reasons: `['insufficient_topic_coverage:casual_dialogue', 'insufficient_topic_coverage:context_and_memory', 'insufficient_topic_coverage:correction', 'insufficient_topic_coverage:emotional_support', 'insufficient_topic_coverage:explanation_and_instruction', 'insufficient_topic_coverage:opinion_and_choice', 'insufficient_topic_coverage:privacy_and_memory', 'insufficient_topic_coverage:safety_boundaries', 'insufficient_topic_coverage:style_and_length', 'insufficient_topic_coverage:uncertainty_and_clarification', 'insufficient_topic_coverage:web_grounding', 'insufficient_dialogue_goal_coverage:acknowledge_and_correct', 'insufficient_dialogue_goal_coverage:adapt_style', 'insufficient_dialogue_goal_coverage:casual_connection', 'insufficient_dialogue_goal_coverage:clarify_missing_context', 'insufficient_dialogue_goal_coverage:emotional_support', 'insufficient_dialogue_goal_coverage:explain_or_plan', 'insufficient_dialogue_goal_coverage:handle_uncertainty', 'insufficient_dialogue_goal_coverage:reflect_and_decide', 'insufficient_dialogue_goal_coverage:respect_privacy', 'insufficient_dialogue_goal_coverage:route_verified_fact', 'insufficient_dialogue_goal_coverage:safe_boundary', 'insufficient_emotional_tone_coverage:concise', 'insufficient_emotional_tone_coverage:firm', 'insufficient_emotional_tone_coverage:neutral', 'insufficient_emotional_tone_coverage:supportive', 'insufficient_emotional_tone_coverage:thoughtful', 'insufficient_emotional_tone_coverage:warm', 'insufficient_response_property_coverage:acknowledges_correction', 'insufficient_response_property_coverage:appropriate_empathy', 'insufficient_response_property_coverage:brief', 'insufficient_response_property_coverage:fact_grounded', 'insufficient_response_property_coverage:no_repeat', 'insufficient_response_property_coverage:no_unfounded_memory', 'insufficient_response_property_coverage:no_url_without_request', 'insufficient_response_property_coverage:privacy_respected', 'insufficient_response_property_coverage:safe_boundary', 'insufficient_response_property_coverage:source_on_request', 'insufficient_response_property_coverage:uses_requested_style']`.

## Граница

Отчёт не активирует candidate и не запускает обучение. Малый approved snapshot нужен только для проверки происхождения, split, schema и пересечений перед следующим расширением корпуса.
