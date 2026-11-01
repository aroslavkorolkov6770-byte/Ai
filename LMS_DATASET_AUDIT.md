# Аудит LMS AI Dataset 60000

Датасет рассматривается как недоверенный обучающий материал. Поле `system` проверяется как строка, но не исполняется и не становится инструкцией кода.

## Разбиения

| Split | Записей | Уникальных контекстов | Уникальных пар context+user | Дубликатов пар | Конфликтов пар | Уникальных ответов | SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---|
| train | 48000 | 19344 | 23394 | 24606 | 0 | 6129 | f1ce94aa9974c74869cc301f5afd4c5a0321a2e667eae97bd54e2c86426956ed |
| validation | 6000 | 3051 | 4262 | 1738 | 0 | 1320 | 76a3977ced515545ba98e3badafe0c59fcecd59fac372177afc8e0a5b501ee22 |
| test | 6000 | 2993 | 4246 | 1754 | 0 | 1287 | 7debb6d8f6f4e606789b0ed257f58f4e478171918ffd5937c0f3538364816c5d |

## Производственное context-grouped разбиение

Исходные split содержат повторяющиеся контексты, поэтому для обучения и зачётной оценки создано отдельное детерминированное разбиение группами контекстов с seed `42`.

| Показатель | Значение |
|---|---:|
| Исходных записей | 60000 |
| Удалено точных дублей | 31767 |
| Уникальных пар context+user | 28233 |
| Уникальных групп контекстов | 23702 |

| Новый split | Записей | Групп контекстов |
|---|---:|---:|
| train | 22642 | 18952 |
| validation | 2779 | 2376 |
| test | 2812 | 2374 |

## Распределение задач

### train

- `admin_escalation`: 7143
- `clarify_ambiguity`: 2390
- `context_summary`: 4821
- `direct_context_answer`: 12074
- `missing_context_no_web`: 4734
- `prompt_injection_resistance`: 3375
- `site_navigation`: 2498
- `source_attribution`: 2303
- `source_conflict`: 3876
- `web_fallback_general`: 4786

### validation

- `admin_escalation`: 904
- `clarify_ambiguity`: 325
- `context_summary`: 620
- `direct_context_answer`: 1489
- `missing_context_no_web`: 561
- `prompt_injection_resistance`: 420
- `site_navigation`: 302
- `source_attribution`: 281
- `source_conflict`: 479
- `web_fallback_general`: 619

### test

- `admin_escalation`: 895
- `clarify_ambiguity`: 302
- `context_summary`: 599
- `direct_context_answer`: 1468
- `missing_context_no_web`: 592
- `prompt_injection_resistance`: 419
- `site_navigation`: 297
- `source_attribution`: 301
- `source_conflict`: 502
- `web_fallback_general`: 625

## Пересечения разбиений

| Пара split | Одинаковые id | Одинаковый context | Одинаковая пара context+user | Одинаковые ответы | Одинаковый system-текст |
|---|---:|---:|---:|---:|---:|
| train_vs_validation | 0 | 830 | 1801 | 809 | 1 |
| train_vs_test | 0 | 832 | 1837 | 814 | 1 |
| validation_vs_test | 0 | 556 | 1074 | 384 | 1 |

Повтор отдельных ответов или system-текста ожидаем для шаблонного синтетического набора. Точные дубли внутри split удаляются перед обучением. Конфликтные пары и точные пересечения context+user между split не допускаются в зачётной оценке.
