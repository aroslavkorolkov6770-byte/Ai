# Brain v3 — OASST1 semantic candidate review queue v0.4

## Статус

- Статус всех записей: `unreviewed`.
- Candidate labels получены эвристикой и не являются подтверждённой разметкой.
- Проверено source-записей: 1541.
- Многоходовых source-записей: 968.
- Многоходовых semantic candidates: 368.
- В ручную очередь выбрано: 104.
- Исключено до review по проверяемым quality-сигналам: {'assistant_code_or_ascii_block': 42, 'assistant_model_identity': 19, 'assistant_overlong_turn': 228, 'assistant_stale_web_capability': 3, 'user_rejects_previous_assistant_answer': 5, 'user_restates_unmet_request': 3}.
- В отчёте есть только IDs, SHA-256 и reason codes; ни user, ни assistant реплики не сохраняются.
- Active manifest, raw source, API, веса и обучение не меняются.

## Candidate coverage

- Eligible по candidate topic: {'casual_dialogue': 87, 'emotional_support': 20, 'opinion_and_choice': 60, 'explanation_and_instruction': 191, 'context_and_memory': 24, 'correction': 27, 'uncertainty_and_clarification': 27, 'web_grounding': 34, 'privacy_and_memory': 13, 'style_and_length': 37, 'safety_boundaries': 12}.
- Выбрано по candidate topic: {'casual_dialogue': 12, 'context_and_memory': 12, 'correction': 12, 'emotional_support': 12, 'explanation_and_instruction': 12, 'opinion_and_choice': 12, 'privacy_and_memory': 12, 'safety_boundaries': 12, 'style_and_length': 12, 'uncertainty_and_clarification': 12, 'web_grounding': 12}.
- Split: {'test': 15, 'train': 80, 'validation': 9}.
- Длины диалогов: {'4': 26, '6': 78}.
- Primary candidate topics: {'casual_dialogue': 14, 'context_and_memory': 12, 'correction': 5, 'emotional_support': 6, 'explanation_and_instruction': 25, 'opinion_and_choice': 13, 'privacy_and_memory': 5, 'safety_boundaries': 6, 'style_and_length': 4, 'uncertainty_and_clarification': 6, 'web_grounding': 8}.
- Все candidate topics: {'casual_dialogue': 22, 'context_and_memory': 14, 'correction': 15, 'emotional_support': 12, 'explanation_and_instruction': 35, 'opinion_and_choice': 20, 'privacy_and_memory': 12, 'safety_boundaries': 12, 'style_and_length': 18, 'uncertainty_and_clarification': 17, 'web_grounding': 16}.

## Обязательное решение reviewer

Для каждой выбранной ID нужно локально проверить естественность русского языка, связность follow-up, уместность ответа, отсутствие личных данных, лишних ссылок, ложной памяти и небезопасных советов. Только отдельная человеческая запись `approved` или `rejected` может стать основанием для будущей semantic-разметки. Эта очередь сама ничего не одобряет и не запускает обучение.
