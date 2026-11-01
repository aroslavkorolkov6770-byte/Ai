# Brain v3 — v1.4 runtime-contract staging candidate

## Статус

- Создан отдельный immutable metadata-only staging/audit snapshot.
- Origin review records: `2`; оба имеют реальный approved verdict.
- В snapshot не перенесены реплики, URL, weather/web-факты и runtime markers.
- Active corpus, API, веса, обучение и GitHub не менялись.

## Почему это не training dataset

Финальный web-result появляется только во время живого route. Его нельзя
записать как статическую truth-реплику или использовать как вход сети.
Snapshot проверяет provenance и границу маршрута, но не содержит train JSONL.

## Следующий шаг

Запустить отдельный audit snapshot. Даже успешный audit не включает
материал в active corpus и не открывает обучение.
