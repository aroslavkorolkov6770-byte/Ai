# OASST2: completed quality audit v0.1

Просмотрено: 36/36.
Не просмотрено: 278 из общего пилота 314.

## Решения

- acceptable: 35.
- exclude: 1.

## Состояние по split

- train: {"acceptable_observation": 24, "pending": 223}.
- validation: {"acceptable_observation": 6, "pending": 33}.
- test: {"acceptable_observation": 5, "excluded": 1, "pending": 22}.

## Материалы

- length_only: 120.
- no_signal: 157.
- priority_signal: 36.

Исключённые записи:

- `oasst2.ru.ed9d99ba3c31b02107ac86724e54`.

Эвристика не доказывает происхождение и не подтверждает права.
Человеческое исключение действует и при отсутствии автоматического сигнала.
Положительные наблюдения качества не заменяют проверку фактов/материалов.
Контрольные split не переходят в train; непросмотренный остаток остаётся pending.
Далее: материал и смысловая разметка quality-observed train; расширение проверки риска.
Полный active/holdout preflight всё ещё необходим. Обучение/promotion не запущены.
