# Brain v3 — manual candidate human-review summary

## Статус

- В queue: `24`.
- Проверено: `2`.
- Одобрено: `2`.
- Отклонено: `0`.
- Без verdict: `22`.
- High-risk: `2` из `12`.
- General sample: `0` из требуемых `2`.
- Required human-review coverage complete: `false`.
- Training candidate allowed: `false`.
- Promotion allowed: `false`.

## Граница

Summary содержит только metadata решений. Даже полный положительный review не создаёт staging candidate, не меняет active manifest и не запускает обучение: требуется отдельное решение владельца и отдельные audit/preflight gates.

## Следующий gate

`complete required human-review coverage before any staging decision`.
