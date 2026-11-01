# Brain v3 — semantic triage project-owned dialogues v0.2

Дата фиксации: 22 сентября 2026 года.

## Статус

**REJECTED FOR PROMOTION.**

Candidate `brain-v3-owned-dialogues-v0.2` остаётся сохранённым immutable
техническим артефактом и не удаляется. Однако он не может быть принят в raw
corpus, active manifest или обучение.

- SHA-256 candidate: `882774770f64a47109a7f71811f8f813418ddd6915edeb6e97cdc3cd26f7b25b`.
- Технические isolated audit, active preflight и prospective coverage прошли.
- Это не отменяет смысловую проверку: автоматические quotas и отсутствие
  exact/near-дубликатов не доказывают естественность русской речи.

## Причины отклонения

- `repetitive_authoring_boilerplate`: повторялись служебно-звучащие формулы
  вместо продолжения конкретного разговора.
- `russian_case_and_verb_grammar`: в выборке были неправильные падежи и
  формы глагола.
- `topic_scenario_mismatch`: часть generic detail не соответствовала теме
  или следующему шагу сценария.

## Граница изменений

- Active corpus, raw sources, manifest, API, веса, обучение и GitHub этим
  решением не изменялись.
- V0.3 создаётся как новый отдельный candidate с другим text-authoring
  pipeline, расширенным language lint и обязательной новой проверкой.
- Положительный технический результат v0.3 не отменяет отдельную
  стратифицированную ручную проверку и решение о promotion.
