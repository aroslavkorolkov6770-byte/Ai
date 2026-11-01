# Brain v3 — сверка существующей OASST1 human review

Дата: 2026-10-01. Это read-only сверка уже выполненной работы, не новая review.

## Подтверждённое состояние

Для immutable queue `oasst1-semantic-review-queue-v0.4` существующий
metadata-only ledger содержит решения по всем 104 ID: 97 approved и 7 rejected,
непросмотренных нет. Штатные функции `load_review_queue`,
`load_review_decisions` и `build_review_summary` проверили соответствие ID,
content SHA-256, split и обязательных reason codes. Они снова вернули
`training_candidate_allowed=false` и `promotion_allowed=false`.

Среди 97 approved ID: 75 train, 7 validation и 15 test. Эвристические
`primary_candidate_topic` дают в approved-подмножестве 24 объяснения,
12 контекстных продолжений, 11 бытовых разговоров, 12 выборов, по шесть
эмоциональных поддержек и безопасных границ, остальные — в других темах.
**Эти числа не являются подтверждённым semantic coverage**: human review
проверял естественность, связность, полезность и риски, но не выдавал отдельный
verdict по каждой эвристической теме или goal/tone/property.

Уже существует approved-only staging candidate из 97 ID и isolated
replacement snapshot. Его coverage audit всё равно оставил
`generator_training_ready=false`. Добавлять 97 записей поверх активного
родительского OASST1 нельзя: это дубли, а не новый независимый корпус.

## Воспроизводимая read-only команда

```powershell
.\.venv\Scripts\python.exe -c "from evaluation.oasst1_human_review import load_review_queue, load_review_decisions, build_review_summary, DEFAULT_DECISIONS_PATH; q=load_review_queue(); d=load_review_decisions(DEFAULT_DECISIONS_PATH,q); s=build_review_summary(q,d); print({k:s[k] for k in ('queue_selected_records','reviewed_records','approved_records','rejected_records','unreviewed_records','training_candidate_allowed','promotion_allowed')})"
```

## Следующий шаг и граница

Не запускать новую выборку и не исправлять старые verdict. Нужно создать
отдельный протокол подтверждения semantic labels у approved ID, оставляя
train/validation/test раздельными. Ранний просмотр исходного parent source
обнаружил пример длинной цитаты из стороннего сайта; поэтому дополнительно
нужна выборочная проверка copyright/provenance на уровне реплик. Ни этот файл,
ни существующие 97 verdict не разрешают обучение или изменение active corpus.
