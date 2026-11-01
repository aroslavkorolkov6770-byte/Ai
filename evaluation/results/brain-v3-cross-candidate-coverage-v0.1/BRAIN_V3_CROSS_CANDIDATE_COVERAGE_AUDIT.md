# Brain v3 — cross-candidate semantic coverage audit

## Результат

- Проверено candidate workflows: `6`.
- Статичных approved review-only сцен: `14`.
- Runtime-contract сцен отдельно от static path: `7`.
- Несогласованных legacy состояний: `0`.
- Ни одна review-only сцена не стала частью active corpus или training snapshot.

## Приоритеты следующей статичной партии

- `explanation_and_instruction:` baseline `0`, approved static `0`, remaining shortfall `2000` turns.
- `web_grounding:` baseline `0`, approved static `0`, remaining shortfall `1000` turns.
- `safety_boundaries:` baseline `0`, approved static `0`, remaining shortfall `900` turns.
- `tool_routing:` baseline `142250`, approved static `0`, remaining shortfall `0` turns.
- `casual_dialogue:` baseline `0`, approved static `4`, remaining shortfall `1596` turns.
- `opinion_and_choice:` baseline `0`, approved static `4`, remaining shortfall `1396` turns.

## Граница

Отчёт содержит только hashes, labels и агрегированные counts. Runtime web/tool
contracts учтены отдельно и не считаются статичным training material. Старый
manual candidate с mixed outcomes и unreviewed records не включён в
provisional coverage. Manifest совпадает с проверенным reference snapshot: `true`.
Полный raw corpus audit намеренно не запускался в этом planning шаге и
обязателен заново перед любым training gate.

## Следующий шаг

Подготовить только отдельный static review-only candidate для приоритетных
пробелов. Staging, training и изменение active corpus требуют нового решения
владельца после следующего audit gate.
