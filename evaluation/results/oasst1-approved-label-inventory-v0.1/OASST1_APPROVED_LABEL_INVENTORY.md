# Brain v3 — OASST1 approved label inventory

## Что подтверждено

- Existing human review: `104/104`; approved `97`, rejected `7`.
- Approved по исходному split: {'test': 15, 'train': 75, 'validation': 7}.
- Проверены исходные ID, content SHA-256, split и source SHA-256.
- Human review касался качества реплик, не истинности proposed topic labels.

## Пилот подтверждения меток

- Отобрано `24` только approved train ID; validation/test не используются для доразметки train.
- Полный metadata-only список ID и proposed topics находится в JSON-отчёте.
- Сначала по возможности два ID на каждый proposed topic, затем дополнительные
  примеры объяснения, контекста и обычной беседы. Выбор стабилен по hash ID.
- Это навигационная выборка, а не статистическая оценка качества всей OASST1.
- Ни одна proposed label пока не стала подтверждённой; решения о метках
  не записывались автоматически.

## Граница и следующий шаг

Нужен отдельный human label-review workflow: читать полный диалог по одному
ID, подтвердить или снять proposed topic, сохранить metadata verdict.
До него нельзя менять исходные метки, active corpus или запускать обучение.
