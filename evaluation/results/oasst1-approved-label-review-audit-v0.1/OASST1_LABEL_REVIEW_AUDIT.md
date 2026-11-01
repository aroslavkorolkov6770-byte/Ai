# Brain v3 — аудит проверки тем OASST1, пилот v0.1

## Итог

- Проверено `24/24` approved train-диалогов.
- Предложенная тема подтверждена: `19`; исправлена: `5`; отклонена без замены: `0`.
- 5 исправлений показывают, что эвристические метки нельзя принимать
  автоматически. Это намеренная pilot-выборка, не оценка точности всех
  75 approved train-записей или полного OASST1.

## Исправления темы

- `correction` → `casual_dialogue`: `1`.
- `emotional_support` → `context_and_memory`: `1`.
- `emotional_support` → `opinion_and_choice`: `1`.
- `privacy_and_memory` → `explanation_and_instruction`: `1`.
- `web_grounding` → `casual_dialogue`: `1`.

## Граница

Отчёт содержит только ID, hashes и метки, без исходных реплик.
Сверены hashes source, quality queue/decisions, inventory и topic ledger.
Validation/test не переименованы и не перенесены в train.
Остальные 51 approved train-записей не имеют подтверждённой темы.
Никакой training snapshot или promotion этим аудитом не разрешены.

## Дальше

Проверить, насколько ошибки меток типичны для оставшейся обучающей
части, и отдельно оценить сторонние цитаты/лицензии. До обучения
сохраняются полный raw corpus audit и медицинский hold v1.6-006.
