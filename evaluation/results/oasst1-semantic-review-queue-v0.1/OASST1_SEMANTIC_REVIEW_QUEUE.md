# Brain v3 — OASST1 semantic candidate review queue v0.1

## Статус

- Статус всех записей: `unreviewed`.
- Candidate labels получены эвристикой и не являются подтверждённой разметкой.
- Проверено source-записей: 1541.
- Многоходовых semantic candidates: 534.
- В ручную очередь выбрано: 111.
- В отчёте есть только IDs, SHA-256 и reason codes; ни user, ни assistant реплики не сохраняются.
- Active manifest, raw source, API, веса и обучение не меняются.

## Candidate coverage

- Eligible по candidate topic: {'casual_dialogue': 136, 'emotional_support': 27, 'opinion_and_choice': 77, 'explanation_and_instruction': 263, 'context_and_memory': 30, 'correction': 38, 'uncertainty_and_clarification': 34, 'web_grounding': 59, 'privacy_and_memory': 17, 'style_and_length': 80, 'safety_boundaries': 23}.
- Выбрано по candidate topic: {'casual_dialogue': 12, 'context_and_memory': 12, 'correction': 12, 'emotional_support': 12, 'explanation_and_instruction': 12, 'opinion_and_choice': 12, 'privacy_and_memory': 12, 'safety_boundaries': 12, 'style_and_length': 12, 'uncertainty_and_clarification': 12, 'web_grounding': 12}.
- Split: {'test': 9, 'train': 93, 'validation': 9}.
- Длины диалогов: {'4': 16, '6': 95}.
- Primary candidate topics: {'casual_dialogue': 15, 'context_and_memory': 12, 'correction': 4, 'emotional_support': 9, 'explanation_and_instruction': 26, 'opinion_and_choice': 9, 'privacy_and_memory': 5, 'safety_boundaries': 4, 'style_and_length': 8, 'uncertainty_and_clarification': 8, 'web_grounding': 11}.
- Все candidate topics: {'casual_dialogue': 26, 'context_and_memory': 14, 'correction': 16, 'emotional_support': 14, 'explanation_and_instruction': 40, 'opinion_and_choice': 17, 'privacy_and_memory': 12, 'safety_boundaries': 12, 'style_and_length': 23, 'uncertainty_and_clarification': 16, 'web_grounding': 21}.

## Обязательное решение reviewer

Для каждой выбранной ID нужно локально проверить естественность русского языка, связность follow-up, уместность ответа, отсутствие личных данных, лишних ссылок, ложной памяти и небезопасных советов. Только отдельная человеческая запись `approved` или `rejected` может стать основанием для будущей semantic-разметки. Эта очередь сама ничего не одобряет и не запускает обучение.
