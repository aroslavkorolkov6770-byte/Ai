# Brain v3 — OASST1 material triage v0.3

## Источник

Закреплённая карточка OASST1 указывает Apache-2.0 и содержит
[README](https://huggingface.co/datasets/OpenAssistant/oasst1/blob/fdf72ae0827c1cda404aff25b6603abec9e3399b/README.md) и [LICENSE](https://huggingface.co/datasets/OpenAssistant/oasst1/blob/fdf72ae0827c1cda404aff25b6603abec9e3399b/LICENSE).
Это уровень публикации набора, а не проверка прав на каждый фрагмент,
который участник мог вставить в сообщение. Отдельный human review нужен
до использования конкретных примеров в новом training snapshot.

## Сигналы по обеим ролям

- Проверено `75` ранее одобренных train-ID; 
  более конкретный сигнал: `14`, только длина: `31`, без сигнала: `30`.
- Предложена выборка для чтения: `24` ID — все с конкретным сигналом плюс
  по `5` из групп «только длина» и «без сигнала».
- `assistant_attribution_word`: `7`.
- `assistant_long_quoted_span`: `4`.
- `assistant_long_turn`: `42`.
- `user_attribution_word`: `1`.
- `user_long_quoted_span`: `3`.
- `user_long_turn`: `5`.

URL, цитаты и повторы найдены лишь эвристически; отсутствие кода не
означает чистоту. Здесь нет исходных реплик или цитируемых URL.
Все human material verdict остаются `pending`.

## Gate

Показать выбранные диалоги целиком в локальном review, установить
происхождение подозрительных фрагментов и решить include/hold/exclude
без удаления source. До этого training snapshot и promotion не разрешены.
