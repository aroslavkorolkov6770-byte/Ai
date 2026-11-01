# Brain v3 — project-owned dialogues v0.6: review queue

## Статус

- Статус проверки: `unreviewed`.
- В candidate: 3475 dialogue-записей.
- В очередь выбрано: 174 из 174 (0.050).
- Queue содержит только IDs, SHA-256 и semantic metadata; реплики candidate не записываются в отчёты.
- Active manifest, raw, API, веса и обучение не меняются.

## Покрытие очереди

- Split: {'test': 65, 'train': 69, 'validation': 40}.
- Длины диалогов: {'2': 55, '4': 62, '6': 57}.
- Темы: {'casual_dialogue': 18, 'context_and_memory': 17, 'correction': 13, 'emotional_support': 15, 'explanation_and_instruction': 15, 'opinion_and_choice': 15, 'privacy_and_memory': 15, 'safety_boundaries': 14, 'style_and_length': 14, 'tool_routing': 8, 'uncertainty_and_clarification': 15, 'web_grounding': 15}.
- Goals: {'acknowledge_and_correct': 18, 'adapt_style': 14, 'casual_connection': 17, 'clarify_missing_context': 15, 'emotional_support': 15, 'explain_or_plan': 14, 'handle_uncertainty': 15, 'reflect_and_decide': 15, 'respect_privacy': 15, 'route_verified_fact': 24, 'safe_boundary': 12}.
- Tones: {'calm': 8, 'concise': 24, 'firm': 22, 'neutral': 44, 'supportive': 30, 'thoughtful': 26, 'warm': 20}.
- Response properties: {'acknowledges_correction': 30, 'appropriate_empathy': 32, 'ask_when_missing': 33, 'brief': 56, 'clear_next_step': 79, 'context_retained': 30, 'fact_grounded': 38, 'natural_tone': 174, 'no_fake_certainty': 71, 'no_repeat': 174, 'no_unfounded_memory': 47, 'no_url_without_request': 174, 'privacy_respected': 32, 'relevant': 174, 'safe_boundary': 29, 'source_on_request': 24, 'uses_requested_style': 29}.

## Следующее решение

Каждая выбранная запись требует проверки естественности русского языка, связности follow-up, уместности ответа, отсутствия ложной памяти, лишних URL и небезопасных советов. Положительный технический audit не заменяет это решение.
