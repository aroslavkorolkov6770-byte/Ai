# Brain v3 — Russian MultiWOZ: проверенный local intake и promotion

Дата: 20 сентября 2026 года.

## Решение

Russian MultiWOZ принят в локальный active Brain v3 corpus только после
закрепления версии, фильтрации, независимого staging audit, preflight против
всех активных источников и финального active audit. Никакие веса не загружены,
обучение не запущено, работающий API не изменён и ничего не отправлено на
GitHub.

## Происхождение и границы

- Source ID: `russian-multiwoz-mit-v1`.
- Официальный repository: <https://github.com/vonCount/Russian-MultiWOZ>.
- Закреплённый commit: `1bd77673533e568ba0746ee304f758296f8dc77f`.
- Лицензия: [MIT](https://github.com/vonCount/Russian-MultiWOZ/blob/1bd77673533e568ba0746ee304f758296f8dc77f/LICENSE).
- Получены только 21 фиксированный `interim/v1/russian/*_ru.txt` blob из
  pinned snapshot; исходный repository-код не запускался.
- Каждая нода проверялась как данные: роли чередуются, split известен,
  slot/reference/phone-like значения заменялись безопасными маркерами, а
  URL, e-mail, секреты, prompt-like строки и небезопасные записи отбрасывались.

Примечание: ссылка лицензии в этом отчёте относится к тому же pinned commit;
корректная canonical ссылка также записана в active manifest и provenance
каждой записи.

## История staging-версий

| Версия | Результат | Причина решения |
| --- | --- | --- |
| `v0.1` | не активирована | 5 175 записей и 70 076 ходов; слишком агрессивный phone-like фильтр отбросил большой полезный объём. |
| `v0.2` | не активирована | 10 387 записей и 142 170 ходов; isolated audit обнаружил 29 внутренних near-duplicate candidates. |
| `v0.3` | принята | 10 393 записи и 142 250 ходов; audit и preflight обнаружили 0 отклонений, 0 точных и 0 близких межsplit/cross-source пересечений. |

Старые candidate и отчёты сохранены как неизменяемая история проверки и не
используются как active source.

## Принятый snapshot

- Staging candidate: `data/brain_v3/staging/russian-multiwoz-mit-v0.3.jsonl`.
- SHA-256 candidate/raw: `a46b58068552acac31a09cf3bb34127ef9e3a9803505db573b1997a619f67222`.
- Размер: `30 717 354` bytes.
- Новый immutable raw: `data/brain_v3/raw/russian-multiwoz-mit-v1.jsonl`.
- Backup manifest до изменения:
  `data/brain_v3/manifest_backups/corpus_manifest.v1.before-russian-multiwoz-mit-v1.json`.

## Результаты проверок

| Проверка | До promotion | После promotion |
| --- | ---: | ---: |
| Active sources | 4 | 5 |
| Принятые записи | 176 191 | 186 584 |
| Отклонённые записи | 0 | 0 |
| Dialogue-записи | 11 | 10 404 |
| Уникальные dialogue-ходы | 22 | 142 272 |
| Языковые символы | 241 707 400 | 241 707 400 |
| Exact/near cross-split | 0 / 0 | 0 / 0 |
| Базовый corpus training gate | `false` | `true` |

Доказательства находятся в следующих immutable результатах:

- `evaluation/results/russian-multiwoz-audit-v0.3/`;
- `evaluation/results/russian-multiwoz-preflight-v0.3/`;
- `evaluation/results/russian-multiwoz-promotion-v0.1/`.

## Что это разрешает и чего не разрешает

Базовый gate объёма и чистоты теперь пройден. Это не означает, что модель уже
достаточно естественно разговаривает на любые темы: Russian MultiWOZ —
task-oriented слой с сильным покрытием follow-up и tool-routing. Последующий
`brain-v3-dialogue-coverage-v0.3` подтверждает избыток этих категорий и
нехватку бытового разговора, поддержки, исправлений, приватности, стиля,
безопасных границ и других обязательных направлений. Поэтому генеративное
обучение по строгому quality policy остаётся заблокированным до закрытия
разнообразия.
