/* Проверяет доказательства этапа и сохраняет отчёт вместе с полным кодом. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_COMPARISON_REPORT_2026_10_30.md';
const snapshotName = 'CLOSED_SEARCH_COMPARISON_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async name => JSON.parse((await fs.readFile(path.join(lab, name), 'utf8')).replace(/^\uFEFF/, ''));
const percentile = (rows, fraction) => [...rows].sort((a, b) => a - b)[Math.ceil(rows.length * fraction) - 1];

// История остаётся на месте: новая запись добавляется сразу после заголовка.
async function prepareEntry(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + file);
    const split = text.indexOf('\n');
    if (split < 0) throw new Error('Missing Markdown heading: ' + file);
    return { file, text: text.slice(0, split + 1) + '\n' + entry + '\n\n' + text.slice(split + 1) };
}

async function main() {
    const baseline = await read('comparison-baseline-1793339859056060400.json');
    const local = await read('comparison-final-1793340741145921800.json');
    const holdout = await read('comparison-new-first-1793340539394228100.json');
    const http = await read('comparison-http-1793340715425.json');
    const services = await read('comparison-services.json');
    const browser = await read('comparison-browser-verification.json');
    const manifest = await read('real-materials/manifest.json');
    const specHash = sha(await fs.readFile(path.join(__dirname, 'comparison_20261030.json')));
    const integrity = { originals: [], copies: [], code: [], specUnchanged: false };
    for (const input of manifest.inputs) {
        const actual = sha(await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name)));
        integrity.originals.push({ name: input.name, expected: input.sha256, actual, pass: actual === input.sha256 });
    }
    for (const doc of manifest.documents) {
        const actual = sha(await fs.readFile(path.join(lab, 'real-materials', doc.name)));
        integrity.copies.push({ name: doc.name, expected: doc.sha256, actual, pass: actual === doc.sha256 });
    }
    integrity.specUnchanged = [baseline.spec_sha256, local.spec_sha256, holdout.spec_sha256, http.specSHA256]
        .every(value => value === specHash);
    const unitLog = await fs.readFile(path.join(lab, 'comparison-unittest.log'), 'utf8');
    if (!integrity.specUnchanged || !integrity.originals.every(row => row.pass) || !integrity.copies.every(row => row.pass)
        || local.passed !== local.total || holdout.passed !== holdout.total || http.passed !== http.total
        || !services.pass || !services.restoredFromDisk || !browser.pass
        || !/Ran 216 tests/.test(unitLog) || !/\bOK\s*$/.test(unitLog)) {
        throw new Error('Stage evidence is incomplete');
    }
    const latency = http.cases.map(row => row.ms);
    const report = `# Сравнение двух товаров и размеры упаковки — 30.10.2026

Текущий этап основного плана ВатэсAi: закрытый поиск по материалам сайта.
Код: C:/Users/KROLL/Desktop/Ai. Изменён собственный алгоритм поиска и извлечения;
обучение весов нейросети не выполнялось. Рабочая LMS и публикация не затронуты.

## Что изменилось

Поиск выбирает два отдельных товара, извлекает одну запрошенную характеристику
для каждого и формирует короткий ответ. Совпавшие коды в разных копиях выгрузки
объединяются. Категория из вопроса помогает отличить чахай «Феникс» от кофе
с похожим именем. Названия ингредиентов с предлогом «с» не разрывают название
товара при сравнении составов.

Размеры изделия, размеры коробки, масса нетто, масса с упаковкой и масса самой
упаковки разбираются отдельно. Размер коробки не заполняет отсутствующий размер
изделия. Пищевая ценность на 100 г, масса белков/жиров и допустимая нагрузка
не становятся массой товара. Длина носика или крышки не выдаётся за размер
всего изделия. Нормализация миллиметров/сантиметров, граммов/килограммов,
миллилитров/литров позволяет сравнить значения и вычислить разницу.

Пара товаров и характеристика сохраняются в памяти конкретного чата.
Продолжения «А упаковки?» и «А с упаковкой?» используют соответствующие поля.
Если характеристика не названа, система уточняет её. Если название неоднозначно,
нужно выбрать вариант. Если доступен факт только об одном товаре, ответ сообщает
известное и явно указывает недостающие данные. Скрытый товар не подменяется
доступным. Противоречия внутри описания и с официальными документами не скрываются.

| Вопрос или продолжение | Проверенный результат |
|---|---|
| Сравни объём чахаев Дао и Феникс | 190 мл и 140 мл; разница 50 мл |
| Сравни материал подставок Хайвэй и Звёздочка | Металл и стекло |
| Сравни состав двух вариантов медового мусса | Отдельный состав каждого выбранного варианта |
| Сравни размеры термосов Север и Юг | 20×8×8 см и 220×80×80 мм — размеры изделий |
| А упаковки? | 24×10×10 см и 26×11×11 см — размеры коробок |
| Что легче, Север или Юг? | 300 г и 0,35 кг; нормализация единиц, разница 50 г |
| Какой размер стакана Луг? | Размер изделия неизвестен; данные коробки не подставляются |
| Сравни размеры Ча Бань Груша и Ча Бань Точка | Сообщение о противоречии в исходных данных |

Север, Юг, Луг, Пик, Лес и Камень — шесть явно учебных позиций, созданных только
для проверки границ. В настоящих выгрузках не нашлось достаточных пар полей
«размер изделия / размер упаковки». Учебный файл помечен как тестовый,
не является регламентом компании и доступен только профилю Б/администратору.
Настоящие сравнения проверены отдельно на предоставленных товарных выгрузках.

## Результаты проверки

- Основной фиксированный набор: ${baseline.passed}/${baseline.total} до изменения → ${local.passed}/${local.total} после.
- Дополнительные новые формулировки: ${holdout.passed}/${holdout.total} с первого запуска после разработки.
  Эти 12 вопросов не использовались для исправлений после получения результата.
- Весь локальный набор: 216/216 тестов за 6,149 с, включая 23 новых сценария.
- Предыдущие товарные вопросы: 14/14; исходная теория: 16/16 и 4/4.
- Через настоящий HTTP-маршрут сайта: ${http.passed}/${http.total}. Это 25 вопросов,
  две проверки отсутствия доступа у профиля А и один вопрос в новом чате Б.
- В HTTP-прогоне медиана ${percentile(latency, .5)} мс, p95 ${percentile(latency, .95)} мс,
  максимум ${Math.max(...latency)} мс. Это короткий замер, не длительная нагрузка.
- В браузере получены три ответа: объёмы чахаев, размеры изделий, размеры коробок.
  Перед третьим вопросом действительно перезапущен API 8001; пара и предмет
  уточнения сохранились. Повторное открытие страницы сохранило весь диалог.
- Обнаружено и исправлено совпадение React-ключей у ссылок на два товара из одного
  файла. После перезагрузки ошибок браузера нет. Остаётся предупреждение Next.js
  о приоритете загрузки логотипа, не связанное с поиском. Проверка синтаксиса TSX
  прошла; полная проверка типов всего стороннего стенда здесь не заявляется.
- Все ${integrity.originals.length} оригинала в Downloads и ${integrity.copies.length} подготовленные копии сохранили SHA-256.
- SHA-256 вопросов и ожиданий неизменен: ${specHash}.

В журнале unittest сообщения restore_failed и context_save_failed принадлежат
тестам искусственного сбоя. Итоговый результат набора — OK.

Это ограниченная приёмка новых сценариев, а не доказательство понимания всех
1 827 товаров и любых формулировок. Предыдущая нагрузка 430/430 относится к
прошлой версии и не считается нагрузочной проверкой этого изменения.

## Состояние стенда

Сайт: http://127.0.0.1:3040/tasks?tab=standards. Закрытый API: 8001.
${services.catalogDocuments} документов, ${services.indexedSources} источников, ${services.indexedPassages} фрагментов.
Индекс восстановлен с диска за ${services.restoreMs} мс; повторный OCR не выполнялся.
Общий чат 8000 не участвует в этой проверке. Команда проверки явно использовала
--closed-only; проверка без этого флага по-прежнему требует общий API.
Порт 3000 и рабочий проект LMS не изменялись. На GitHub ничего не отправлено.

## Ограничения и следующий шаг

Сравнение поддерживает две позиции и одну названную характеристику за вопрос.
Произвольные рекомендации «что лучше» без критерия требуют уточнения.
Из вектора без подписи осей нельзя надёжно вывести длину или высоту;
приблизительные значения и диапазоны не превращаются в точные числа.
Распознавание свободного текста ограничено поддержанными конструкциями.
Новые реальные карточки с другим оформлением требуют отдельной приёмки.

Архив от 18.06.2026 не подтверждает сегодняшние цены, остатки и официальность
рекламных утверждений. Ссылки имеют названия товаров, но пока открывают целую
часть выгрузки. Следующий шаг по плану — открывать нужную позицию или фрагмент
по ссылке из ответа и проверять это с правами доступа и сменой редакции.
Затем — приёмка на актуальных настоящих документах и сканах, когда они будут доступны.

## Воспроизводимость

Из C:/Users/KROLL/Desktop/Ai:

~~~powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p 'test_site_search*.py'
.\\.venv\\Scripts\\python.exe -X utf8 -m evaluation.closed_search.comparison_probe --label comparison-recheck
.\\.venv\\Scripts\\python.exe -X utf8 -m evaluation.closed_search.comparison_probe --holdout --label comparison-new-recheck
node evaluation/closed_search/comparison_http.cjs
node evaluation/closed_search/verify_lab.cjs --closed-only
~~~

Свидетельства находятся в .runtime/closed-search-lab-20261028:
comparison-baseline-1793339859056060400.json;
comparison-final-1793340741145921800.json;
comparison-new-first-1793340539394228100.json;
comparison-http-1793340715425.json;
comparison-catalog-regression-1793340738515432800.json;
comparison-theory-1793340742082207100.json;
comparison-theory-alternate-1793340742813196000.json;
comparison-unittest.log; comparison-services.json;
comparison-browser-verification.json; comparison-proof.png; comparison-integrity.json.

Полные листинги всех новых и изменённых файлов кода: ${snapshotName}.
`;
    const entry = `## 2026-10-30 — сравнение товаров и разделение размеров изделия/упаковки

Завершён следующий этап закрытого поиска: сравнение двух товаров по выбранной
характеристике; размеры изделия и коробки; масса нетто, с упаковкой и самой
упаковки; нормализация единиц. Пара товаров сохраняется после рестарта.
Недостающие значения не выдумываются, конфликт остаётся конфликтом, права
проверяются до выбора товара. Шесть карточек с упаковками явно помечены учебными.

Проверено: 216/216 локальных тестов; основной набор 2/13 → 13/13;
12/12 новых формулировок с первого прогона; HTTP 28/28 с двумя профилями.
В браузере проверено сравнение и продолжение после реального рестарта API.
Исправлен повтор ключей двух ссылок на один документ. Оригиналы сохранены.
Стенд 3040 / API 8001: 125 документов, 208 источников, 2 849 фрагментов.
Это улучшение алгоритма поиска, не обучение весов и не рабочая интеграция LMS.

Отчёт: ${reportName}. Полный код: ${snapshotName}.
Далее по плану: переход из ответа прямо к нужному товару/фрагменту; проверка
доступа и смены редакции. Затем — приёмка на актуальных документах и сканах.
Ниже сохранена история предыдущих этапов.`;
    const siteEntry = `## 2026-10-30 — приёмка сравнения двух товаров

Изолированный стенд ВатэсAi 3040 использует закрытый поиск 8001. Добавлен один
явно учебный файл с шестью карточками и различными размерами изделия/коробки,
доступный Б/администратору. Число документов — 125, источников — 208.
Через сайт прошли 28/28 проверок сравнения, памяти и доступа; в браузере
проверено продолжение «А упаковки?» после перезапуска API. В AIAssistant.tsx
исправлены ключи ссылок: два товара в одном файле больше не создают ошибку React.
Настоящие исходные файлы не изменены. Рабочая LMS и публикация не затронуты;
процедура запуска/развёртывания не менялась.

Полный отчёт: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — переход к нужной позиции по ссылке с проверкой доступа и редакции.`;
    const writes = [{ file: path.join(root, reportName), text: report }];
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        writes.push(await prepareEntry(path.join(root, name), entry));
    }
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        writes.push(await prepareEntry(path.join(site, name), siteEntry));
    }
    const files = ['ai_core/site_search_measurements.py', 'ai_core/site_search_comparison.py',
        'ai_core/site_search_catalog.py', 'ai_core/site_search.py', 'ai_core/site_search_runtime.py',
        'tests/test_site_search_comparison.py', 'evaluation/closed_search/comparison_20261030.json',
        'evaluation/closed_search/comparison_probe.py', 'evaluation/closed_search/comparison_http.cjs',
        'evaluation/closed_search/verify_lab.cjs', 'evaluation/closed_search/finalize_comparison.cjs',
        '.runtime/closed-search-lab-20261028/tea-app-main/app/tasks/components/AIAssistant.tsx'];
    let snapshot = '# Полные листинги этапа сравнения товаров — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file));
        const hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.json') ? 'json'
            : file.endsWith('.tsx') ? 'tsx' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    writes.push({ file: path.join(root, snapshotName), text: snapshot });
    writes.push({ file: path.join(lab, 'comparison-integrity.json'), text: JSON.stringify(integrity, null, 2) });
    // Запись начинается лишь после проверки всех входов и подготовки всех текстов.
    for (const item of writes) await fs.writeFile(item.file, item.text, 'utf8');
    console.log(JSON.stringify({ report: reportName, snapshot: snapshotName, sourceFiles: files.length,
        originalsUnchanged: true, copiesUnchanged: true, specUnchanged: true,
        httpMedianMs: percentile(latency, .5), httpP95Ms: percentile(latency, .95), documentsUpdated: writes.length }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
