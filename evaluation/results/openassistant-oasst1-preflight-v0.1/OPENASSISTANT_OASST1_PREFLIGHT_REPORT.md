# OpenAssistant OASST1 — preflight против active Brain v3 corpus

Статус: active raw и candidate сопоставлены только через временные hard links.
Проверено active sources: 5; candidate: `openassistant-oasst1-apache-v1`.
SHA-256 candidate: `4d5da23ac1cae9b1902fa92ebbf448cd01bc47f4aef8486a457d039e66d732a1`.
Принято: 188125; отклонено: 2; причины: {'near_duplicate_candidate': 2}.
Межsplit exact: 0; near: 0.
Чистое слияние: `False`.
Training gate временного объединения: `False`; причины: ['rejected_record_ratio_exceeded'].

Preflight не переносит candidate в raw/, не меняет manifest и не запускает обучение.
