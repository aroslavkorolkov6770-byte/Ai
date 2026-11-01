/* Проверяет результаты адресных ссылок и обновляет локальные Markdown-журналы. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_FRAGMENTS_REPORT_2026_10_30.md';
const codeName = 'CLOSED_SEARCH_FRAGMENTS_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async file => JSON.parse((await fs.readFile(path.join(lab, file), 'utf8')).replace(/^\uFEFF/, ''));

async function prepend(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + file);
    const offset = text.indexOf('\n');
    if (offset < 0) throw new Error('Missing Markdown heading');
    return { file, text: text.slice(0, offset + 1) + '\n' + entry + '\n\n' + text.slice(offset + 1) };
}

async function main() {
    const http = await read('fragments-http-1793342254955.json');
    const post = await read('fragments-postcheck-1793342490680.json');
    const status = await read('fragments-services.json');
    const manifest = await read('real-materials/manifest.json');
    const unit = await fs.readFile(path.join(lab, 'fragments-unittest.log'), 'utf8');
    const integrity = { originals: [], copies: [], code: [] };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: input.sha256 === actual });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    if (http.passed !== http.total || post.passed !== post.total || !status.pass || !status.restoredFromDisk
        || !/Ran 232 tests/.test(unit) || !/\bOK\s*$/.test(unit)
        || !integrity.originals.every(item => item.pass) || !integrity.copies.every(item => item.pass)) {
        throw new Error('Acceptance evidence is incomplete');
    }
    for (const file of ['fragment-product-proof.png', 'fragment-document-proof.png', 'fragment-edition-proof.png']) {
        if (!(await fs.stat(path.join(lab, file))).size) throw new Error('Browser proof missing');
    }
    const report = `# Адресные ссылки из ответов ВатэсAi — 30.10.2026

Основной проект: C:/Users/KROLL/Desktop/Ai. Работа выполнена в собственном
поисковом ядре и отдельном стенде 3040 / API 8001. Рабочая LMS не изменялась.
Обучение весов, публикация и отправка в GitHub не выполнялись.

## Результат для пользователя

Новые ответы с подтверждённой цитатой ведут к отдельной карточке товара или
нужному фрагменту документа/урока. Две позиции из одного файла получают разные
адреса. На странице показаны название, источник и нужный текст; есть возврат
к диалогу и возможность открыть исходный материал целиком. Служебные параметры
не выводятся в тексте страницы. HTML из источника не исполняется.

Пример: из сравнения подставок «Хайвэй» и «Звездочка» первая ссылка открывает
металлическую подставку, вторая — стеклянную. В обеих карточках сохраняется
оговорка, что свечки в комплект не входят. Ссылка «Приёмка поставки Линза»
открывает только правило о координаторе, без соседнего раздела хранения.
Линза — явно учебный документ для проверки смены редакций.

## Доступ и актуальность

Каждый адрес содержит источник, его редакцию и контрольную сумму фрагмента.
Это адрес, не разрешение: сервер сайта заново проверяет текущую сессию,
доступ и редакцию. После получения текста от поискового API выполняется
повторная проверка, чтобы учесть изменение прав во время запроса.

| Ситуация | Проверенное поведение |
|---|---|
| Доступный товар | Только нужная карточка |
| Другой профиль без доступа | Нет текста и названия закрытого материала |
| Нет сессии | API требует вход; страница не раскрывает содержимое |
| Доступ отозван | Старый адрес блокируется сразу, без ожидания индексации |
| Документ изменён | Сообщение «Документ обновлён»; старый фрагмент не показывается |
| Новый вопрос после обработки | Новый ответ и ссылка на новую редакцию |
| Удаление или архивирование | Материал недоступен |
| Неизвестный фрагмент | Нет подстановки первого или похожего раздела |
| Перезапуск API | Существующие ссылки продолжают открываться |
| Отсылка к другому документу | Цитата ведёт к документу с самим исключением |

Указатели строятся один раз при публикации индекса и восстанавливаются из
сохранённого текста. При клике нет OCR и повторного извлечения документа.
Сервер сверяет только выбранный источник; ответ API имеет private, no-store.
Тексты открываются из проверенного индекса, а не из параметров браузерной ссылки.

## Проверки и обнаруженные особенности

- 232/232 локальных теста за 7,015 с, включая 16 новых проверок адресов.
- Основная HTTP-проверка: ${http.passed}/${http.total}; два профиля, отсутствие сессии,
  смена содержимого, отзыв прав, удаление и архивирование учебной записи.
- После настоящего рестарта API: ${post.passed}/${post.total}, включая HTML-страницы
  для Б, А и анонимного посетителя; урок и отсылку между документами.
- Четыре изменённых/новых TS/TSX-файла прошли проверку синтаксиса. Страница и
  маршрут API скомпилированы Next.js и проверены реальными запросами.
  Полная проверка типов всего архивного проекта не заявляется.
- В браузере нажаты обе ссылки сравнения, открыт фрагмент обычного документа,
  проверено сообщение для другой редакции. Ошибок браузера на проверке нет.
- ${integrity.originals.length}/${integrity.originals.length} оригиналов Downloads и ${integrity.copies.length}/${integrity.copies.length} подготовленных копий сохранили SHA-256.

Первая HTTP-попытка обнаружила факт, записанный только в названии товара:
объём чахая. Сопоставление расширено на заголовок именно товарной карточки;
добавлен отдельный локальный тест. Факты из названия не переносятся в выдуманное
описание: карточка показывает исходный заголовок и отсутствие описания.

Первый дополнительный прогон дал 8/9 из-за неверного ожидания теста о Нефрите.
На стенде одновременно есть учебный скан со сроком 1 день и другой учебный
регламент со сроком 2 дня. Общий вопрос правильно дал конфликт. Проверка
отсылки теперь явно выбирает исходный документ, а общий конфликт проверяется
отдельно. Итог 10/10 — исправленная регрессия, не независимая слепая приёмка.

В тестах состояния менялись только у новой записи fragment_edition_fixture.
В конце восстановлены её исходные текст и права. Чужие записи не откатывались.
Система хранит ${status.catalogDocuments} документов / ${status.indexedSources} источников /
${status.indexedPassages} фрагмент. Индекс восстановлен с диска за ${status.restoreMs} мс.
Предыдущие нагрузочные цифры не относятся к этой версии; новый нагрузочный
прогон в этом этапе не выполнялся.

## Границы реализации и следующий этап

Если буквальную цитату нельзя надёжно привязать к фрагменту, сохраняется ссылка
на полный источник. Существующие сообщения истории не переписываются: адресные
ссылки появляются в новых ответах. Старые ссылки на целый файл продолжают
открывать файл. Точная страница и подсветка координат внутри PDF ещё не реализованы:
сейчас открывается извлечённый текст фрагмента с переходом к полному источнику.
При обновлении документа ссылка не угадывает «такой же» абзац в новой редакции.

Следующий этап: связать фрагмент с конкретной страницей PDF и подсветить место
в исходнике; проверить перенос/удаление разделов при новой редакции. Затем —
приёмка на актуальных настоящих регламентах и сканах, когда они доступны.
Выгрузка 18.06.2026 остаётся архивной, её цены и остатки не объявлены текущими.

## Файлы и повторение проверки

План приёмки: evaluation/closed_search/FRAGMENT_ACCEPTANCE_2026_10_30.md.
Локальные тесты: tests/test_site_search_fragments.py и общий набор test_site_search*.py.
HTTP: node evaluation/closed_search/fragments_http.cjs.
После перезапуска: node evaluation/closed_search/fragments_postcheck.cjs <имя отчёта fragments-http из каталога стенда>.
Проверка сервисов: node evaluation/closed_search/verify_lab.cjs --closed-only.
Общий чат 8000 не участвует в этом стенде. Зависимости не менялись.

Отчёты в .runtime/closed-search-lab-20261028:
fragments-http-1793342254955.json;
fragments-postcheck-1793342490680.json;
fragments-postcheck-1793342438552.json (первая проверка 8/9);
fragments-unittest.log; fragments-services.json; fragments-integrity.json.
Снимки: fragment-product-proof.png, fragment-document-proof.png, fragment-edition-proof.png.
Полные листинги: ${codeName}.
`;
    const entry = `## 2026-10-30 — адресные ссылки, права и редакции источников

Новые ответы открывают конкретную карточку или фрагмент, включая факты из
названия товара. Адрес привязан к редакции. Сервер проверяет текущие права
до и после получения текста: отзыв доступа, удаление и архивирование блокируют
старую ссылку; новая редакция требует повторить вопрос. OCR при клике не запускается.

Проверено: 232/232 локальных теста, HTTP 16/16, после рестарта 10/10;
в браузере — две разные карточки, раздел документа и сообщение об обновлении.
Оригиналы и 42 копии сохранены. Стенд: 126 документов / 209 источников / 2 851 фрагмент.
Рабочая LMS, обучение весов и публикация не затронуты.

Отчёт: ${reportName}. Полный код: ${codeName}.
Далее: конкретная страница PDF и подсветка фрагмента, смена положения разделов;
затем актуальные настоящие документы/сканы. История прежних сообщений сохраняет
старые ссылки на целые файлы. Ниже сохранена история этапов.`;
    const siteEntry = `## 2026-10-30 — адресное открытие источника ответа

Только изолированный стенд 3040 / API 8001. Добавлены страница
/closed-search-fragment и закрытый серверный маршрут с проверкой сессии,
доступа и текущей редакции. Переход из ответа открывает конкретный товар/раздел;
изменившийся или закрытый источник не раскрывает старый текст. OCR при клике нет.
В AIAssistant разрешён только новый внутренний путь. Добавлена одна учебная
запись для проверки редакций; исходное состояние восстановлено после испытаний.

Проверено: HTTP 16/16 + 10/10 после рестарта, HTML без утечек для профиля А и
анонимного посетителя; открытие товаров и раздела в браузере. 126 документов.
Отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Запуск не изменён, публикация не выполнялась. Далее — страница и подсветка в PDF.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepend(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepend(path.join(site, name), siteEntry));
    }
    const files = ['ai_core/site_search_fragments.py', 'ai_core/site_search.py', 'ai_core/site_search_api.py',
        'ai_core/site_search_runtime.py', 'ai_core/site_search_store.py', 'tests/test_site_search_fragments.py',
        'evaluation/closed_search/export_site.cjs', 'evaluation/closed_search/fragments_http.cjs',
        'evaluation/closed_search/fragments_postcheck.cjs', 'evaluation/closed_search/verify_lab.cjs',
        'evaluation/closed_search/finalize_fragments.cjs',
        ...['app/lib/vatesFragment.ts', 'app/api/closed-search-fragment/route.ts', 'app/closed-search-fragment/page.tsx',
            'app/tasks/components/AIAssistant.tsx'].map(file => '.runtime/closed-search-lab-20261028/tea-app-main/' + file)];
    let snapshot = '# Полные листинги адресных ссылок — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file)), hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.tsx') ? 'tsx' : file.endsWith('.ts') ? 'typescript' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, codeName), text: snapshot });
    writes.push({ file: path.join(lab, 'fragments-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    for (const output of writes) await fs.writeFile(output.file, output.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, code: codeName, sourceFiles: files.length,
        originals: integrity.originals.length, copies: integrity.copies.length, allUnchanged: true }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
