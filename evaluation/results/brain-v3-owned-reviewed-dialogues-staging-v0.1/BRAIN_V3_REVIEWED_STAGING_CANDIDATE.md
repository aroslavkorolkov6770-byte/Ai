# Brain v3 — reviewed staging candidate v0.1

## Статус

- Создан отдельный immutable staging/audit snapshot из реально одобренных review-сцен.
- Сцен: `16`; диалоговых ходов: `64`.
- Split records: `{'test': 6, 'train': 6, 'validation': 4}`.
- Topics: `{'casual_dialogue': 1, 'correction': 1, 'emotional_support': 4, 'privacy_and_memory': 6, 'safety_boundaries': 4}`.
- Active corpus, API, веса, обучение и GitHub не менялись.

## Граница качества

Это не training dataset и не доказательство универсального разговорного качества: snapshot намеренно мал и покрывает только уже проверенные темы.
Записи 018 и 019 из исходного v1 не включены; их поведение заменено отдельным одобренным repair v1.1.

## Следующий шаг

Запустить isolated audit, active-corpus preflight и prospective dialogue coverage. Даже чистый результат не разрешает promotion или обучение без отдельного решения.
