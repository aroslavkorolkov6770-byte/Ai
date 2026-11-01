# Brain v3 — OASST1 approved human-review candidate

## Результат

- Записей в review queue: `104`.
- Ручных решений: `104`.
- Одобрено человеком: `97`.
- Отклонено человеком: `7`.
- В immutable staging candidate записано: `97`.
- Split: `train: 75, validation: 7, test: 15`.
- SHA-256 candidate: `8a0861c8b7fcc667529fbc03c26189420d3e33d75e80260d18e950ea1458c786`.

## Граница

Candidate создан только для следующего изолированного audit. Он не является active source, не меняет manifest, не запускает обучение и не передаётся в GitHub.

## Следующий gate

Запустить isolated audit, active-corpus preflight и только при чистом preflight — prospective dialogue coverage и отдельную quality review.
