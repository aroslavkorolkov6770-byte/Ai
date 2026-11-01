# Проверка LMS-кандидата

Тестовая часть построена из контекстных групп, отсутствующих в train и validation.

| Показатель | Значение |
|---|---:|
| model | lms-context-policy-v1.5 |
| checkpoint_sha256 | 64c8ada97c3ed2729c09f2bda3c6f7cbc15eaaf0df6d1fb5505cc9b5d9650eba |
| records | 2812 |
| accepted_correct | 2812 |
| accepted_wrong | 0 |
| safe_clarifications | 0 |
| precision | 1.0 |
| coverage | 1.0 |
| macro_recall | 1.0 |
| context_overlap_with_train_or_validation | 0 |
| complete_text_fraction | 1.0 |
| grounded_fraction | 1.0 |
| mean_expected_answer_token_recall | 0.46536648107643885 |
| safety_text_violations | 0 |
| test_task_distribution | {'admin_escalation': 54, 'clarify_ambiguity': 280, 'context_summary': 594, 'direct_context_answer': 1506, 'missing_context_no_web': 45, 'prompt_injection_resistance': 4, 'site_navigation': 4, 'source_attribution': 113, 'source_conflict': 15, 'web_fallback_general': 197} |
| lms_gate_passed | True |

## По типам задач

| Тип | Support | Правильно принято | Неверно принято | Уточнения | Recall |
|---|---:|---:|---:|---:|---:|
| admin_escalation | 54 | 54 | 0 | 0 | 1.0000 |
| clarify_ambiguity | 280 | 280 | 0 | 0 | 1.0000 |
| context_summary | 594 | 594 | 0 | 0 | 1.0000 |
| direct_context_answer | 1506 | 1506 | 0 | 0 | 1.0000 |
| missing_context_no_web | 45 | 45 | 0 | 0 | 1.0000 |
| prompt_injection_resistance | 4 | 4 | 0 | 0 | 1.0000 |
| site_navigation | 4 | 4 | 0 | 0 | 1.0000 |
| source_attribution | 113 | 113 | 0 | 0 | 1.0000 |
| source_conflict | 15 | 15 | 0 | 0 | 1.0000 |
| web_fallback_general | 197 | 197 | 0 | 0 | 1.0000 |

`mean_expected_answer_token_recall` — диагностическая, а не gate-метрика: модель извлекает факт из контекста, а не копирует свободно сгенерированный эталонный текст.
