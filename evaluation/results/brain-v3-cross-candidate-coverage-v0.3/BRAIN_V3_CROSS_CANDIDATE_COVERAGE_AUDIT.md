# Brain v3 — cross-candidate semantic coverage audit

## Результат

- Проверено candidate workflows: `6`.
- Статичных approved review-only сцен: `10`.
- Runtime-contract сцен отдельно от static path: `7`.
- Несогласованных legacy состояний: `0`.
- Ни одна review-only сцена не стала частью active corpus или training snapshot.

## Приоритеты следующей статичной партии

- `explanation_and_instruction`: baseline `0`, approved static `0`, remaining shortfall `2000` turns.
- `context_and_memory`: baseline `0`, approved static `0`, remaining shortfall `1200` turns.
- `safety_boundaries`: baseline `0`, approved static `0`, remaining shortfall `900` turns.
- `casual_dialogue`: baseline `0`, approved static `4`, remaining shortfall `1596` turns.
- `opinion_and_choice`: baseline `0`, approved static `4`, remaining shortfall `1396` turns.
- `emotional_support`: baseline `0`, approved static `4`, remaining shortfall `1196` turns.

## Граница

Отчёт содержит только hashes, labels и агрегированные counts. Runtime web/tool
contracts учтены отдельно. Старый manual candidate имеет смешанные verdict
и непросмотренные сцены. v1.2 также проверен лишь частично. Поэтому
`16` одобренных записей
из этих партий показаны отдельно и не входят в предварительный расчёт.
Manifest совпадает с проверенным reference snapshot: `true`.
Полный raw corpus audit и проверка межкандидатных повторов нужны
перед любым training gate.

## Следующий шаг

Подготовить только отдельный static review-only candidate для приоритетных
пробелов. Staging, training и изменение active corpus требуют нового решения
владельца после следующего audit gate.
