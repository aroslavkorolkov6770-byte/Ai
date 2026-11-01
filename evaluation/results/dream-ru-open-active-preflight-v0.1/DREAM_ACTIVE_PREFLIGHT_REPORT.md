# Preflight DReaM-ru-open против active Brain v3 corpus

Статус: временная проверка с hard links; active manifest, raw и обучение не изменялись.
Проверено active sources: 1; candidate: `dream-ru-open-cc-by-v1`.
SHA-256 candidate JSONL: `d2f65956c064ec6c5672b83ae4ecfa1a87bbdce4f69beff59610557b68ba1d78`.
Принято: 77773; отклонено: 3; причины: {'exact_duplicate': 2, 'near_duplicate_candidate': 1}.
Межsplit точных дублей: 0; близких кандидатов: 0.
Чистое слияние: `False`.
Training allowed временного объединения: `False`; причины: ['insufficient_unique_dialogue_turns', 'insufficient_language_characters', 'rejected_record_ratio_exceeded'].

Даже чистое слияние не переносит candidate в raw/ автоматически. Для этого нужен отдельный локальный шаг с новым immutable raw JSONL, manifest и повторным active audit.
