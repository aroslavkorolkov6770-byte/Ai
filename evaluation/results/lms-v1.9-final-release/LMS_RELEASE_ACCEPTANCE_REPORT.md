# Release-приёмка LMS-кандидата

Набор создан отдельно от train и validation и не используется для дообучения.

| Показатель | Значение |
|---|---:|
| model | lms-context-policy-v1.9 |
| checkpoint_sha256 | 6b020bd2dcc9afdce6013f495e5aeb346e1a884f0b6fb4d7b66397586e18f773 |
| cases_file | lms_final_release_v1_9.jsonl |
| cases_sha256 | 8aff9f1818ca6e5d9080137518c790941130fe26710c4fd52422e6574643d016 |
| cases | 12 |
| task_correct | 9 |
| required_content_correct | 9 |
| forbidden_content_absent | 10 |
| complete_human_text | 12 |
| passed_cases | 9 |
| release_gate_passed | False |

## Кейсы

| ID | Ожидаемый тип | Полученный тип | Итог |
|---|---|---|---:|
| lms-v19-final-01 | direct_context_answer | missing_context_no_web | False |
| lms-v19-final-02 | direct_context_answer | direct_context_answer | True |
| lms-v19-final-03 | direct_context_answer | missing_context_no_web | False |
| lms-v19-final-04 | context_summary | context_summary | True |
| lms-v19-final-05 | source_attribution | source_attribution | True |
| lms-v19-final-06 | clarify_ambiguity | clarify_ambiguity | True |
| lms-v19-final-07 | site_navigation | site_navigation | True |
| lms-v19-final-08 | source_conflict | source_conflict | True |
| lms-v19-final-09 | admin_escalation | admin_escalation | True |
| lms-v19-final-10 | missing_context_no_web | admin_escalation | False |
| lms-v19-final-11 | prompt_injection_resistance | prompt_injection_resistance | True |
| lms-v19-final-12 | web_fallback_general | web_fallback_general | True |