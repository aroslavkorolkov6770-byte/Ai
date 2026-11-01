# Brain v3 — pickup-context repair candidate v1.4

Статус: **human review coverage complete; 2/2 approved; не является training dataset**  
Дата: 2026-09-30  
Основной план: `BRAIN_V3_QUALITY_MODEL_ROADMAP.md`, этап B

## Причина создания

Пользователь отклонил immutable-сцену `v1.3-006`: в исходном вопросе не были
названы ни конкретный пункт выдачи, ни место. Поэтому ассистент не мог честно
сообщать о недоступности источника и должен был сначала задать одно точное
уточнение. Исходная v1.3-сцена и её verdict не изменяются.

## Что проверяет партия

- Две project-owned оригинальные четырёхходовые сцены `web_grounding`.
- Когда есть только общий вопрос о часах ПВЗ, ответ — короткое уточнение без
  поиска, ссылок и выдуманной причины ошибки.
- Поиск допускается после полного адреса либо после названия ПВЗ вместе с
  городом, только в продолжении этого же чата.
- Финальный marker `[[web.*]]` означает свежий структурированный результат
  runtime route. Это не факт, не URL и не обучающий текст; marker никогда не
  показывается пользователю и не может попасть в corpus, staging или веса.
- «Лирск», «Север», «Маяк» и адреса в сценах — вымышленные project-owned
  стимулы, а не пользовательские, личные или веб-данные.

## Границы

Оба сценария требуют полный human review. Положительные verdict сами по себе
не дают права на обучение, изменение active corpus или публикацию. После
отдельного решения владельца создан только metadata-only staging/audit snapshot:
он не содержит реплик или runtime markers и не является corpus JSONL. Отдельно
от review реализована runtime-защита: город без конкретного ПВЗ или адреса
остаётся недостаточным вводом; геолокация пользователя не выбирает сервисный
пункт за него.

## Артефакты

- Scope: `data/brain_v3/reviews/brain-v3-pickup-context-repair-v1.4.scope.json`.
- Сцены: `data/brain_v3/reviews/brain-v3-pickup-context-repair-v1.4.review.jsonl`.
- Проверка: `tests/test_brain_v3_pickup_context_repair_candidate.py`.

## Следующий шаг

Оба настоящих verdict записаны в append-only metadata-only ledger:
`brain-v3-pickup-context-repair-v1.4-001` и
`brain-v3-pickup-context-repair-v1.4-002` одобрены. Immutable summary v0.3
фиксирует полное coverage `2/2`. По отдельному разрешению создан и проверен
metadata-only runtime-contract staging/audit snapshot v0.1. Он не даёт права
на training или promotion. Следующий шаг — расширять только статичный
human-reviewed dialogue corpus по coverage matrix.
