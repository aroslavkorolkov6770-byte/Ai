# OASST2 — изолированный пилот новых семейств

Записей: 314; ходов: 1414; human-approved: 0.
Split: `{'test': 28, 'train': 247, 'validation': 39}`; длины: `{'4': 235, '6': 79}`.

## Отбор

- `credential_like`: 1.
- `deleted`: 200.
- `eligible_safe_paths`: 1264.
- `email_like`: 4.
- `existing_oasst1_family`: 10364.
- `incomplete_role_path`: 702.
- `new_families_seen`: 3490.
- `nodes_seen`: 8735.
- `non_russian`: 3004.
- `phone_like`: 14.
- `quality:assistant_code_or_ascii_block`: 119.
- `quality:assistant_model_identity`: 15.
- `quality:assistant_overlong_turn`: 286.
- `safe_leaf_paths_seen`: 1966.
- `toxicity_threshold`: 68.
- `trees_seen`: 13854.
- `two_turn_only_path`: 89.
- `unreviewed`: 195.
- `url_like`: 125.

## Граница

Старые OASST1 деревья исключены целиком. Одна ветвь на новое дерево.
Сравнен dialogue-слой active corpus и расширенный brain_v3_acceptance contract.
Языковые документы и остальные evaluation-наборы требуют полного preflight.
Найденные topic hints — эвристика для очереди просмотра, не истинные метки.
Тема/цель/тон записей unclassified, локальная human quality-приёмка pending.
Далее: изучить выход и сформировать стратифицированный quality review.
Active corpus, обучение и promotion не изменены.
