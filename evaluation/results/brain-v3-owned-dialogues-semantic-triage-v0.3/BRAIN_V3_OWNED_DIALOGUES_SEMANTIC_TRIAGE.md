# Brain v3 — semantic triage project-owned dialogues v0.3

Дата фиксации: 22 сентября 2026 года.

## Статус

**REJECTED FOR PROMOTION.**

Candidate `brain-v3-owned-dialogues-v0.3` остаётся неизменяемым staging
артефактом. Он технически прошёл isolated audit, active preflight и prospective
coverage, но не может быть добавлен в raw corpus, active manifest или обучение
из-за найденных речевых дефектов.

- SHA-256 candidate: `d4bc689ebcd8b8add931e69aeb0753990416f47e0c40b1ef20f11c102595ddbf`.
- Staging audit: 3 475/3 475 accepted; exact/near cross-split: 0/0.
- Active preflight: 191 600/191 600 accepted; exact/near cross-split: 0/0.
- Prospective coverage: технически `coverage_allowed=true` и
  `generator_training_ready=true` только для временного объединения.

## Причины отклонения

- `infinitive_after_preposition`: отдельные follow-up шаблоны подставляли
  инфинитив после предлога, из-за чего появлялась неестественная конструкция.
- `preference_case_mismatch`: одна authoring-конструкция требовала
  винительного падежа, но получала форму preference из безопасного словаря.
- `awkward_topic_wrapper`: один wrapper темы создавал тяжёлую и неестественную
  связку вокруг предлога «о».

## Решение

- Не ослаблять technical gates и не перезаписывать v0.3.
- Исправить authoring pipeline под новым source ID v0.4.
- Повторить in-memory audit, isolated audit, active preflight, prospective
  coverage и metadata-only review queue для нового immutable candidate.
- До отдельного решения сохраняются `candidate_is_active=false`,
  `active_manifest_updated=false` и `training_started=false`.
