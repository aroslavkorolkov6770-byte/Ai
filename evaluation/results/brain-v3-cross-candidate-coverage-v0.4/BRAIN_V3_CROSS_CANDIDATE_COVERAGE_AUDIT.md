# Brain v3 — cross-candidate semantic coverage audit

## Результат

- Проверено candidate workflows: `7`.
- В предварительном статичном покрытии: `15` сцен / `60` ходов.
- Одобренных медицинских сцен на отдельном hold: `1`.
- Runtime-contract сцен отдельно от static path: `7`.
- Несогласованных legacy состояний: `0`.
- Ни одна review-only сцена не стала частью active corpus или training snapshot.

## Приоритеты следующей статичной партии

- `explanation_and_instruction`: baseline `0`, approved static `8`, remaining shortfall `1992` turns.
- `casual_dialogue`: baseline `0`, approved static `4`, remaining shortfall `1596` turns.
- `opinion_and_choice`: baseline `0`, approved static `4`, remaining shortfall `1396` turns.
- `emotional_support`: baseline `0`, approved static `4`, remaining shortfall `1196` turns.
- `context_and_memory`: baseline `0`, approved static `8`, remaining shortfall `1192` turns.
- `correction`: baseline `0`, approved static `4`, remaining shortfall `996` turns.

## Граница

Отчёт содержит только hashes, labels и агрегированные counts. Runtime web/tool
contracts учтены отдельно. Старый manual candidate имеет смешанные verdict
и непросмотренные сцены; v1.2 проверен лишь частично. Из этих партий
`16` одобренных записей
не вошли в предварительный расчёт. Отдельно не засчитана одна
медицинская сцена v1.6-006: human review завершён, но профильная
проверка или исключение перед обучением ещё не выполнены. Всего
исключено `17` approved static сцен.
Manifest совпадает с проверенным reference snapshot: `true`.
Полный raw corpus audit и проверка межкандидатных повторов нужны
перед любым training gate.

## Следующий шаг

Подтвердить semantic labels уже просмотренного OASST1 и решить отдельный
medical-domain gate для v1.6-006. Staging, training и изменение active
corpus требуют полного аудита и нового решения владельца.
