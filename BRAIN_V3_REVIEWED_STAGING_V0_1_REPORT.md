# Brain v3 — отчёт об approved staging/audit snapshot v0.1

Статус: **технически чистый staging snapshot; не training dataset**  
Дата: 2026-09-29  
Основной план: `BRAIN_V3_QUALITY_MODEL_ROADMAP.md`, этап B

## Что было разрешено и создано

После отдельного решения владельца создан
`brain-v3-owned-reviewed-dialogues-v0.1`. Это новый неизменяемый staging JSONL,
а не изменение v1 или repair v1.1.

- В snapshot вошли только 16 сцен с реальными положительными verdict:
  12 из manual v1 и 4 из memory repair v1.1.
- Две отклонённые v1-сцены `018` и `019` не перенесены. Их требования
  представлены отдельной, полностью одобренной repair-партией.
- Распределение: 6 train, 4 validation, 6 test; всего 64 диалоговых хода.
- SHA-256 snapshot:
  `76818a41cd44d7f82f0c84282151d54e80c717ac75d777f49ee171d8aa660a62`.
- Состав selection и SHA исходных review-records хранятся без реплик в
  `evaluation/results/brain-v3-owned-reviewed-dialogues-staging-v0.1/`.

## Результат независимой проверки

- Isolated audit: 16 приняты, 0 отклонены, нет exact/near split duplicates.
- Active-corpus preflight: `clean_merge=true`; проверены все 6 активных
  источников, конфликтов с ними нет.
- Active manifest остался с 6 источниками; staging source не добавлялся в
  `raw/` и не активировался.
- Полный read-only audit занял около 22 минут. Это допустимо для контрольной
  точки, но требует отдельного ускорения duplicate-audit до частых запусков на
  большом объёме данных.

## Почему обучение не началось

Snapshot специально мал: 64 хода против минимальных 20 000, а coverage matrix
остаётся незакрытой по большинству разговорных тем, целей, тонов и свойств.
Поэтому isolated audit честно возвращает
`training_allowed=false`, а prospective coverage —
`generator_training_ready=false`.

Это не ошибка данных: это защита от ложного запуска слишком раннего обучения.
Не менялись corpus manifest, API, веса, текущий чат и GitHub.

## Следующий шаг

Подготовить следующий небольшой original review-only tranche только для
незаполненных ячеек semantic matrix: context/memory без privacy-leak,
uncertainty, explanation, opinion/choice, style/length, tool/web routing.
До его human review и следующего audit никакого нового staging snapshot или
обучения не создаётся.
