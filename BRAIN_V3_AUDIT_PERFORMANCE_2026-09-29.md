# Brain v3 — ускорение prospective audit без ослабления quality gate

Статус: **проверено read-only control run**  
Дата: 2026-09-29  
Связанный план: `BRAIN_V3_QUALITY_MODEL_ROADMAP.md`, этап B

## Наблюдение

Первый полный audit staging snapshot v0.1 занял около 22 минут. Профильная
выборка показала, что проблема не в near-duplicate фильтре:

- на 20 000 записей Tatoeba проверка index заняла `0.0160 ms/record`, а
  безопасный parse — `0.1884 ms/record`;
- среднее число просмотренных near-duplicate кандидатов составило `2.4035`
  на запись; у bucket `p95=3`, `p99=6`, максимум `30`;
- на многоходовых источниках parse существенно дороже index: например,
  на выборке Russian MultiWOZ — `2.9629 ms/record` против `0.0503 ms/record`.

Следовательно, упрощать или отключать duplicate audit нельзя и не нужно:
главная потеря времени была вызвана повторным полным schema/risk/duplicate
проходом одного и того же temporary prospective manifest.

## Изменение

Добавлен единый read-only путь
`preflight_and_prospective_dialogue_coverage_for_staged_candidate`:

1. создаёт тот же временный manifest из hard link immutable active sources и
   staging candidate;
2. проводит один полный `audit_corpus`;
3. передаёт именно этот проверенный base audit в metadata-only coverage audit,
   пока тот же manifest и те же hard links существуют;
4. coverage всё равно перепроверяет checksum manifest и checksum каждого raw
   source до и после metadata-прохода.

Внешний API прежних отдельных preflight и coverage функций сохранён. Reuse
разрешён только во внутреннем объединённом read-only transaction; переданный
base audit строго проверяется по форме и непротиворечивым счётчикам.

## Контрольный результат

На существующем `brain-v3-owned-reviewed-dialogues-v0.1` выполнен один
read-only контрольный запуск после изменения:

- время: `641.619` секунды, то есть примерно `10 минут 42 секунды`;
- isolated audit: `16/16` приняты;
- active preflight: `clean_merge=true`;
- coverage observed: `true`;
- `generator_training_ready=false`;
- candidate не активирован, обучение не начато.

Сравнение с историческим «около 22 минут» ориентировочное, потому что это
разные запуски на рабочей машине. Но контрольный run подтверждает заметное
сокращение времени без изменения verdict и без снятия duplicate, checksum,
schema или risk проверок.

## Проверки

Целевой набор из 23 тестов прошёл успешно. В него вошли unit-тест reuse
проверенного base audit, отказ на поддельный audit, combined prospective
fixture, scoped review, reviewed staging и новая semantic-gap партия.

## Граница

Это инфраструктурное ускорение audit, а не изменение качества данных. Оно не
закрывает G3/G4, не даёт право на promotion, не запускает обучение и не
разрешает использовать неreviewed сцены как training data.

## Следующий шаг

Провести human review новой review-only партии
`brain-v3-semantic-gap-candidate-v1.2`: все шесть обязательных context/tool/web
сцен и минимум две сцены из общего stratified pool.
