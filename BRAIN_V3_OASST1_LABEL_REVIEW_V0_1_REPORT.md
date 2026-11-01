# Brain v3 — итог проверки главной темы OASST1, пилот v0.1

Дата: 2026-10-01. Владелец рассмотрел все 24 ранее одобренных train-диалога
в отдельном локальном экране. Это проверка предложенной главной темы,
а не повторная оценка качества ответа или новое разрешение на обучение.

## Подтверждённый результат

- В журнале 24 уникальных решения из 24 отобранных ID.
- Предложенная тема подтверждена у 19 диалогов; у 5 исправлена на другую;
  без замены не отклонена ни одна.
- Все записи относятся к исходному split `train`. Источник, quality queue,
  старый quality ledger, пилотный inventory и новый topic ledger связаны
  точными SHA-256 в immutable audit.
- Среди исправлений: `correction → casual_dialogue`,
  `emotional_support → context_and_memory`,
  `emotional_support → opinion_and_choice`,
  `privacy_and_memory → explanation_and_instruction`,
  `web_grounding → casual_dialogue` — по одному случаю.

Это намеренно разнообразная, но не случайная выборка. Доля 19/24 не является
оценкой точности эвристики на 75 approved train-записях или полном OASST1.
Оставшиеся 51 approved train-диалог не имеют проверенной человеком главной
темы. Validation/test не переименованы и не перенесены в train.

Полный metadata-only результат с ID и hashes:
`evaluation/results/oasst1-approved-label-review-audit-v0.1/`.
Исходных реплик в отчёте нет.

## Следующий gate

Определить безопасный размер следующей train-only проверки меток, отдельно
проверить возможные сторонние цитаты и условия использования отдельных
примеров. Перед training snapshot остаются полный raw corpus audit,
межкандидатные повторы, закрытый holdout и медицинский hold v1.6-006.
Active corpus, staging, API, веса, обучение и GitHub не менялись.
