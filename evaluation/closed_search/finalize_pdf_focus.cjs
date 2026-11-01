/* Сохраняет проверенные результаты этапа и полные листинги без секретов. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_PDF_FOCUS_REPORT_2026_10_30.md';
const codeName = 'CLOSED_SEARCH_PDF_FOCUS_CODE_SNAPSHOT_2026_10_30.md';
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
    const httpName = 'pdf-focus-http-1793344348012.json';
    const postName = 'pdf-focus-postcheck-1793344628156.json';
    const http = await read(httpName), post = await read(postName), status = await read('pdf-focus-services.json');
    const manifest = await read('real-materials/manifest.json'), syntax = await read('pdf-focus-syntax.json');
    const unit = await fs.readFile(path.join(lab, 'pdf-focus-unittest.log'), 'utf8');
    const fixtures = path.join(__dirname, 'fixtures/pdf-focus');
    const specBytes = await fs.readFile(path.join(fixtures, 'spec.json'));
    const spec = JSON.parse(specBytes.toString('utf8'));
    const integrity = { originals: [], copies: [], fixtures: [], code: [] };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: input.sha256 === actual });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    for (const doc of spec.documents) {
        const actual = sha(await fs.readFile(path.join(fixtures, doc.file)));
        integrity.fixtures.push({ name: doc.file, expected: doc.sha256, actual, pass: doc.sha256 === actual });
    }
    if (http.passed !== http.total || post.passed !== post.total || !status.pass || !status.restoredFromDisk
        || !/Ran 245 tests/.test(unit) || !/\bOK\s*$/.test(unit) || syntax.some(item => item.errors.length)
        || http.specSHA256 !== sha(specBytes)
        || ![...integrity.originals, ...integrity.copies, ...integrity.fixtures].every(item => item.pass)) {
        throw new Error('Acceptance evidence is incomplete');
    }
    const screenshots = ['native', 'zoom', 'other-page', 'scan', 'stale', 'moved', 'restart'];
    for (const name of screenshots) {
        if (!(await fs.stat(path.join(lab, 'pdf-focus-' + name + '-proof.png'))).size) throw new Error('Browser proof missing: ' + name);
    }
    const report = `# Открытие страницы PDF и подсветка цитаты — 30.10.2026

Основной проект: C:/Users/KROLL/Desktop/Ai. Изменены собственное поисковое ядро
и изолированный стенд 3040 / API 8001. Рабочая LMS не изменялась. Это улучшение
извлечения и поиска источников, не обучение весов нейросети. Публикации не было.

## Результат

Ссылка из нового ответа открывает исходный PDF на странице с подтверждённой
цитатой. Нужная строка выделена жёлтым и сразу видна в области просмотра.
Работают увеличение, переходы между страницами и кнопка возврата к цитате.
На соседних страницах чужая подсветка не появляется.

Координаты извлекаются в фоне: для текстовых PDF — из текстового слоя, для
сканов — OCR по исходному изображению страницы. Позиции сохраняются в кэше
и SQLite вместе с индексом, связаны с контрольной суммой PDF. Нажатие ссылки
не запускает OCR. Добавлена зависимость pdfplumber==0.11.9; существующие
Poppler и Tesseract с русским языком используются повторно.

Новые адреса PDF ведут к отдельному предложению с фактом. Старые адреса
абзацев неизменённой редакции тоже сохранены: они не ломаются из-за появления
более точных ссылок. История ранее отправленных сообщений не переписывается.

## Перенос, удаление и доступ

| Ситуация | Проверенный результат |
|---|---|
| Учебный текстовый PDF | Цитата на странице 2 из 3, рамка на нужной строке |
| Учебный скан | Цитата на странице 2 из 2, рамка на исходном растре |
| Перенос раздела в новую редакцию | Старый адрес сообщает об обновлении, новый ответ открывает страницу 3 |
| Удаление раздела в новой редакции | Старые адреса блокируются, новый вопрос получает отсутствие данных |
| Два одинаковых вхождения | Нет выбора страницы наугад; показаны цитата и предложение открыть исходник |
| Другой профиль без доступа | PDF не выдаётся; код 404, содержимого нет |
| Нет сессии | PDF не выдаётся; код 401 |
| Смена редакции до завершения индексации | Старая ссылка уже заблокирована, прежние байты не выдаются |
| Перезапуск API | Ссылки и координаты восстановлены с диска |

Права проверяются сервером сайта при открытии фрагмента и повторно при
получении байтов PDF. Также проверяются актуальная редакция и SHA-256 файла.
Адрес сам по себе не даёт доступ. Ответ с PDF имеет private, no-store.
HTML-страница также проверена: закрытый текст не попадает в ответ профилю А
или анонимному посетителю. Уже показанный авторизованному пользователю документ
не исчезает сам по себе при последующем изменении; новая загрузка проверяет
редакцию и права заново.

## Доказательства

- 245/245 локальных тестов за 7,942 с, включая 13 новых проверок PDF.
- HTTP: ${http.passed}/${http.total}. Замена байтов выполнялась у одной учебной записи;
  перенос, удаление и повторение цитаты проверены на отдельных PDF.
- После настоящего рестарта API: ${post.passed}/${post.total}; страницы, исходные байты,
  устаревшая редакция и HTML для двух профилей/без сессии.
- Четыре TS/TSX-файла прошли проверку синтаксиса. Next.js скомпилировал маршруты
  и страницу; они проверены реальными запросами и браузером. Полная проверка
  типов всего архивного проекта не заявляется.
- В браузере проверены обычный PDF, скан, масштаб 150%, соседняя страница,
  возврат к цитате, отказ старого адреса и новое положение на странице 3.
  На этой проверке браузер не зарегистрировал ошибок.
- ${integrity.originals.length}/${integrity.originals.length} оригиналов Downloads и ${integrity.copies.length}/${integrity.copies.length} подготовленных копий сохранили SHA-256.
- Все 5 учебных PDF и их заранее записанные ожидания сохранили контрольные суммы.

Сценарии и PDF зафиксированы до основной реализации. Они являются локальной
регрессионной проверкой, а не независимой оценкой на неизвестном корпусе.
Первый HTTP-прогон также дал 14/14; после добавления совместимости старых
ссылок выполнен повторный прогон ${httpName}, результат 14/14.
Отдельный тест подтверждает, что повторное чтение скана использует кэш и не
запускает отрисовку/OCR. В общий журнал тестов намеренно попадают сообщения
проверок восстановления повреждённого хранилища; общий результат OK.

Первая подготовка координат для ранее загруженных PDF заняла больше времени,
чем ожидал скрипт запуска, и он сообщил о неготовности индекса. Процесс не
остановился; обработка завершилась в фоне. Повторный запуск с готовыми данными
прошёл штатно. В конце: ${status.catalogDocuments} документов / ${status.indexedSources} источников /
${status.indexedPassages} фрагмент. Индекс восстановлен с диска за ${status.restoreMs} мс.
Общий чат 8000 не участвует в этом стенде. Новая длительная нагрузочная
проверка в данном этапе не выполнялась; прежние цифры сюда не переносятся.

## Границы и дальнейшая работа

Подсветка ставится только при одном полном совпадении последовательности слов.
Сомнительные OCR-цифры не принимаются автоматически. Сложные таблицы, сноски,
переносы и исправленный OCR-текст могут не совпасть буквально с потоком слов:
тогда остаются текст цитаты и переход к полному исходнику, без выдуманной рамки.
Обрабатываются PDF до 15 МБ и 40 страниц; неполное извлечение не выдаётся за
полный проверенный источник. Для произвольного плохого скана точность не гарантируется.

При переносе не выполняется молчаливое перенаправление старой ссылки в новую
редакцию: пользователь повторяет вопрос. Это не позволяет открыть похожий,
но уже другой раздел. По завершении проверок базовый учебный PDF восстановлен;
две новые учебные записи доступны профилю Б и администратору стенда.

Далее по плану: привязка составных ответов к отдельным ячейкам таблицы и сноскам,
включая переходы между страницами; затем приёмка на актуальных настоящих
регламентах и сканах. Архивная товарная выгрузка 18.06.2026 не объявлена текущей.

## Повторение и артефакты

План приёмки: evaluation/closed_search/PDF_FOCUS_ACCEPTANCE_2026_10_30.md.
Фикстуры: evaluation/closed_search/fixtures/pdf-focus/spec.json и 5 PDF.
Локально: .venv/Scripts/python.exe -m unittest discover -s tests -p "test_site_search*.py".
HTTP: node evaluation/closed_search/pdf_focus_http.cjs.
После рестарта: node evaluation/closed_search/pdf_focus_postcheck.cjs ${httpName}.
Сервисы: node evaluation/closed_search/verify_lab.cjs --closed-only.

Отчёты в .runtime/closed-search-lab-20261028:
${httpName}; ${postName}; pdf-focus-services.json; pdf-focus-syntax.json;
pdf-focus-unittest.log; pdf-focus-integrity.json.
Снимки: ${screenshots.map(name => 'pdf-focus-' + name + '-proof.png').join(', ')}.
Полные листинги: ${codeName}.
`;
    const entry = `## 2026-10-30 — страница PDF, подсветка и перенос разделов

Выполнено: фоновое извлечение координат текстовых PDF/сканов, кэш и SQLite;
открытие страницы с жёлтой подсветкой, масштаб и возврат к цитате. Старые
ссылки неизменённой редакции сохранены. Новая редакция блокирует старые адреса;
перенос даёт новую страницу, удаление — отсутствие ответа. Повторы и
неуверенная геометрия не превращаются в произвольную подсветку.

Проверено: 245/245 локальных тестов, HTTP 14/14, после рестарта 8/8; браузер —
PDF, скан, масштаб, соседняя страница, перенос 2 → 3 и устаревшая ссылка.
128 документов / 211 источников / 2 871 фрагмент. Оригиналы и 42 копии сохранены.
Добавлен pdfplumber==0.11.9. Первая фоновая подготовка превысила ожидание
скрипта запуска, но завершилась; тёплый рестарт штатный. Рабочая LMS не менялась.

Отчёт: ${reportName}. Полные листинги: ${codeName}.
Далее: точная привязка ячеек сложных таблиц и сносок к страницам PDF;
затем проверка на актуальных настоящих регламентах. Ниже история этапов.`;
    const siteEntry = `## 2026-10-30 — исходная страница PDF с подсветкой

Только изолированный стенд 3040 / API 8001. Страница фрагмента показывает
исходный PDF на нужной странице. Защищённый /api/closed-search-pdf повторно
проверяет сессию, права, редакцию и SHA-256. Добавлены подсветка, масштаб и
возврат к цитате; прежний просмотр по dataUrl сохранён. Две новые учебные
записи доступны Б/администратору. После испытаний восстановлена базовая версия.

Проверено: HTTP 14/14, после реального рестарта 8/8; в браузере текстовый PDF,
скан, увеличение и перенос строки со страницы 2 на 3. 128 документов.
Ядру требуется pdfplumber==0.11.9; зависимости сайта не менялись. Координаты
готовятся фоном, первичная обработка может выйти за ожидание скрипта запуска.
Публикация не выполнялась. Отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — ячейки таблиц и сноски, затем проверка актуальных настоящих документов.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepend(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepend(path.join(site, name), siteEntry));
    }
    const files = ['requirements.txt', 'ai_core/site_pdf_locations.py', 'ai_core/site_search_fragments.py',
        'ai_core/site_search.py', 'ai_core/site_search_api.py', 'ai_core/site_search_store.py', 'ai_core/site_search_runtime.py',
        'tests/test_site_search_pdf_focus.py', 'evaluation/closed_search/pdf_focus_assets.py',
        'evaluation/closed_search/pdf_focus_http.cjs', 'evaluation/closed_search/pdf_focus_postcheck.cjs',
        'evaluation/closed_search/verify_lab.cjs', 'evaluation/closed_search/finalize_pdf_focus.cjs',
        ...['app/lib/vatesFragment.ts', 'app/api/closed-search-pdf/route.ts', 'app/components/PdfDocumentViewer.tsx',
            'app/closed-search-fragment/page.tsx'].map(file => '.runtime/closed-search-lab-20261028/tea-app-main/' + file)];
    let snapshot = '# Полные листинги PDF-подсветки — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file)), digest = sha(bytes);
        integrity.code.push({ file, sha256: digest, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.tsx') ? 'tsx'
            : file.endsWith('.ts') ? 'typescript' : file.endsWith('.cjs') ? 'javascript' : 'text';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + digest + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, codeName), text: snapshot });
    writes.push({ file: path.join(lab, 'pdf-focus-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    for (const output of writes) await fs.writeFile(output.file, output.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, code: codeName, files: files.length,
        originals: integrity.originals.length, copies: integrity.copies.length, fixtures: integrity.fixtures.length, allUnchanged: true }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
