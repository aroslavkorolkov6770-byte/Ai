# Brain v3 — manual candidate human-review summary

## Статус

- В queue: `24`.
- Проверено: `14`.
- Одобрено: `12`.
- Отклонено: `2`.
- Без verdict: `10`.
- High-risk: `12` из `12`.
- General sample: `2` из требуемых `2`.
- Required human-review coverage complete: `true`.
- Training candidate allowed: `false`.
- Promotion allowed: `false`.

## Граница

Summary содержит только metadata решений. Даже полный положительный review не создаёт staging candidate, не меняет active manifest и не запускает обучение: требуется отдельное решение владельца и отдельные audit/preflight gates.

## Следующий gate

`investigate rejected drafts; staging and training remain prohibited`.
