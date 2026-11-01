# Проверка LMS-кандидата

Тестовая часть построена из контекстных групп, отсутствующих в train и validation.

| Показатель | Значение |
|---|---:|
| model | lms-context-policy-v1.4 |
| checkpoint_sha256 | 805ad8e5ee8d062283766e7ae1389111c67c0eecf8681009ed6bf6f78cad6404 |
| records | 2812 |
| accepted_correct | 2384 |
| accepted_wrong | 428 |
| safe_clarifications | 0 |
| precision | 0.8477951635846372 |
| coverage | 1.0 |
| macro_recall | 0.7307587782587783 |
| context_overlap_with_train_or_validation | 0 |
| complete_text_fraction | 1.0 |
| grounded_fraction | 0.9223726114649682 |
| mean_expected_answer_token_recall | 0.46871118279880875 |
| safety_text_violations | 19 |
| test_task_distribution | {'admin_escalation': 54, 'clarify_ambiguity': 280, 'context_summary': 594, 'direct_context_answer': 1506, 'missing_context_no_web': 45, 'prompt_injection_resistance': 4, 'site_navigation': 4, 'source_attribution': 113, 'source_conflict': 15, 'web_fallback_general': 197} |
| lms_gate_passed | False |

## По типам задач

| Тип | Support | Правильно принято | Неверно принято | Уточнения | Recall |
|---|---:|---:|---:|---:|---:|
| admin_escalation | 54 | 54 | 0 | 0 | 1.0000 |
| clarify_ambiguity | 280 | 89 | 191 | 0 | 0.3179 |
| context_summary | 594 | 380 | 214 | 0 | 0.6397 |
| direct_context_answer | 1506 | 1506 | 0 | 0 | 1.0000 |
| missing_context_no_web | 45 | 27 | 18 | 0 | 0.6000 |
| prompt_injection_resistance | 4 | 3 | 1 | 0 | 0.7500 |
| site_navigation | 4 | 0 | 4 | 0 | 0.0000 |
| source_attribution | 113 | 113 | 0 | 0 | 1.0000 |
| source_conflict | 15 | 15 | 0 | 0 | 1.0000 |
| web_fallback_general | 197 | 197 | 0 | 0 | 1.0000 |

`mean_expected_answer_token_recall` — диагностическая, а не gate-метрика: модель извлекает факт из контекста, а не копирует свободно сгенерированный эталонный текст.
