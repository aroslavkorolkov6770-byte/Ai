# Brain v3 — MOT Russian / Voice of America language source

Дата приёма: 17 сентября 2026 года.

## Что принято

В local active corpus добавлен один новый языковой источник
`mot-rus-golosameriki-cc-by-v1`. Это не dialogue-корпус и не готовая модель:
его назначение — увеличить объём лицензированного связного русского текста для
будущего обучения с нуля.

- Источник: [Multilingual Open Text, Russian VOA release v1.11](https://github.com/bltlab/mot/releases/download/v1.11/rus_golosameriki.tgz).
- Происхождение: проект MOT указывает public-domain новости Voice of America;
  собранный corpus распространяется по `CC-BY-4.0`.
- Локальный archive snapshot:
  `data/brain_v3/source_archives/mot-rus-golosameriki-v1.11.tgz`.
- Размер archive: `444 157 097` bytes.
- SHA-256 archive:
  `2587c8ab3f10290c5d81a491e7fc78f0d58d825fecb577e466c354ee8f7a2259`.

## Безопасный intake

Для источника добавлены отдельные адаптер, importer и verifier. Они принимают
только fixed GitHub release URL, ровно один разрешённый HTTPS redirect, точный
размер archive и не распаковывают tar-пути в файловую систему. JSON members
читаются потоково; URL, email, телефоны, похожие на секреты строки,
instruction-like текст, точные и близкие дубли исключаются до staging JSONL.

Первый staging import `v0.1` прочитал `100 747` JSON members и создал `98 412`
language documents. Он исключил `2 339` записей: `1 380` точных дублей, `493`
близких дубля, `281` строку с URL, `167` похожих на телефон, `11` email,
`1` похожую на credential, `1` пустую token-строку и `5` не-regular tar
members. Выходной предел был достигнут осознанно: JSONL размером
`503 314 808` bytes остаётся ниже технического лимита active audit.

## Cross-source очистка и независимая проверка

Изолированный audit `v0.1` был чист, но временный preflight с тремя active
источниками нашёл `5` близких cross-source повторов. Эти записи не были
проигнорированы и не меняли старые источники. Новый локальный очиститель создал
immutable staging `v0.2`, исключив ровно `5` записей.

- `v0.2`: `98 407` принятых documents, `236 530 848` языковых символов.
- SHA-256 `v0.2`:
  `fcaa3e1af5a64929afd58cb553ed7a4ff67abbd05ff76bc55c3652a14753cee2`.
- Независимый staging audit `v0.2`: `98 407/98 407` приняты, `0` отклонений,
  `0` exact и `0` near межsplit-пересечений.
- Active preflight `v0.2`: `176 191/176 191` приняты, `0` отклонений,
  `clean_merge=true`.

## Local promotion

Только после чистого `v0.2` preflight candidate был скопирован без overwrite в
`data/brain_v3/raw/mot-rus-golosameriki-cc-by-v1.jsonl`. Его SHA-256 совпадает
со staging v0.2. В manifest добавлена provenance-запись с `CC-BY-4.0`.

Перед изменением создан immutable backup:
`data/brain_v3/manifest_backups/corpus_manifest.v1.before-mot-rus-golosameriki-cc-by-v1.json`.
Его SHA-256 совпадает с предыдущей версией manifest:
`fa682ad5219366e2d4e5067ac5cf4e4e6251d512d978651a985dd4d93071c162`.

Финальный active audit подтвердил 4 источника и `176 191/176 191` корректную
запись, без отклонений и без межsplit-пересечений. Обучение не запускалось.

Повторный metadata-only dialogue coverage audit v0.2 после promotion подтвердил
эти же границы на обновлённом manifest: 11 dialogue-записей, 22 хода и 0
многоходовых диалогов. Его base audit принял 176 191 запись и
241 707 400 языковых символов, а generator training оставил закрытым только
из-за недостатка уникальных dialogue turns.

## Что изменилось в готовности к обучению

Порог языкового объёма теперь выполнен: `241 707 400` из требуемых
`100 000 000` символов. Генеративное обучение остаётся заблокированным из-за
dialogue-слоя: есть только `22` уникальных хода из требуемых `20 000`, а также
не выполнена отдельная policy разнообразия многоходовых диалогов. Это честная
граница данных, а не ошибка чата.

## Границы и операционная заметка

Работающий чат, веса модели, обучение и GitHub не менялись. В staging остаются
старые immutable версии `v0.1` и `v0.2` для воспроизводимости. Один скрытый
временный `.tmp`-файл от остановленного раннего import не является candidate,
не указан в manifest и не читается никаким pipeline; его удаление допускается
только отдельной ручной операцией после проверки точного пути и отсутствия
активного import-процесса.

Подробные JSON/Markdown доказательства лежат в
`evaluation/results/mot-rus-golosameriki-*/`.
