# Preflight DReaM-ru-open против active Brain v3 corpus

Статус: временная проверка с hard links; active manifest, raw и обучение не изменялись.
Проверено active sources: 1; candidate: `dream-ru-open-cc-by-v1`.
SHA-256 candidate JSONL: `bec0786af854e0ae5bf564261ffea1a1fb79abdf3fed455cf9ebf7270b65f0e6`.
Принято: 77773; отклонено: 0; причины: {}.
Межsplit точных дублей: 0; близких кандидатов: 0.
Чистое слияние: `True`.
Training allowed временного объединения: `False`; причины: ['insufficient_unique_dialogue_turns', 'insufficient_language_characters'].

Даже чистое слияние не переносит candidate в raw/ автоматически. Для этого нужен отдельный локальный шаг с новым immutable raw JSONL, manifest и повторным active audit.
