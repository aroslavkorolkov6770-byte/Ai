/* Сохраняет проверяемый отчёт и полные листинги этого локального этапа. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const reportName = 'CLOSED_SEARCH_CATALOG_REPORT_2026_10_30.md';
const snapshotName = 'CLOSED_SEARCH_CATALOG_CODE_SNAPSHOT_2026_10_30.md';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = file => fs.readFile(file, 'utf8').then(JSON.parse);
const percentile = (rows, fraction) => [...rows].sort((a, b) => a - b)[Math.ceil(rows.length * fraction) - 1];

async function prepend(file, entry) {
    const text = await fs.readFile(file, 'utf8');
    if (text.includes(entry.split('\n')[0])) throw new Error('Stage already recorded: ' + path.basename(file));
    const firstLine = text.indexOf('\n');
    await fs.writeFile(file, text.slice(0, firstLine + 1) + '\n' + entry + '\n\n' + text.slice(firstLine + 1), 'utf8');
}

async function main() {
    const baseline = await read(path.join(lab, 'catalog-baseline-1793337866785251200.json'));
    const local = await read(path.join(lab, 'catalog-final-1793339063419103600.json'));
    const http = await read(path.join(lab, 'catalog-http-1793339105101.json'));
    const soakDir = path.join(lab, 'soak-http-1793339082490');
    const soak = await read(path.join(soakDir, 'report.json'));
    const post = await read(path.join(soakDir, 'postcheck.json'));
    const services = await read(path.join(lab, 'final-service-status.json'));
    const manifest = await read(path.join(lab, 'real-materials/manifest.json'));
    const integrity = { originals: [], copies: [], code: [], specUnchanged: false };
    for (const input of manifest.inputs) {
        const bytes = await fs.readFile(path.join('C:/Users/KROLL/Downloads', input.name));
        integrity.originals.push({ name: input.name, pass: sha(bytes) === input.sha256 });
    }
    for (const doc of manifest.documents) {
        const bytes = await fs.readFile(path.join(lab, 'real-materials', doc.name));
        integrity.copies.push({ name: doc.name, pass: sha(bytes) === doc.sha256 });
    }
    integrity.specUnchanged = baseline.spec_sha256 === local.spec_sha256 && local.spec_sha256 === http.specSHA256
        && http.specSHA256 === soak.catalogSpecSHA256;
    if (!integrity.specUnchanged || !integrity.originals.every(row => row.pass) || !integrity.copies.every(row => row.pass)
        || local.passed !== local.total || http.passed !== http.total || !post.pass || !services.pass) {
        throw new Error('Stage evidence is incomplete');
    }
    const metrics = http.cases.map(row => row.ms);
    const loadRows = soak.phases.map(phase => `| ${phase.name} | ${phase.requests} | ${phase.p50Ms} | ${phase.p95Ms} |`).join('\n');
    const report = `# Поиск по товарным позициям — 30.10.2026

Продолжение основного плана закрытого поиска. Код: C:/Users/KROLL/Desktop/Ai.
Это изменение алгоритма извлечения и памяти; обучение весов модели не выполнялось.
Рабочая LMS, публикация и GitHub не затронуты.

## Исправленное поведение

Раньше контекст указывал на целый файл с десятками товаров. Теперь доверенный
импорт передаёт код и заголовок каждой позиции; поиск сохраняет конкретный товар.
Повтор одного кода в двух выгрузках не создаёт два варианта ответа. Права, текущая
редакция и срок доступности проверяются до выбора товара и показа уточнений.

| Вопрос | Результат |
|---|---|
| Какой состав у медового мусса Dolche Vita? | Уточнение варианта, без случайного выбора |
| С миндалем | Короткий состав выбранного мусса |
| Из чего сделана подставка Хайвэй? → А свечки в комплекте? | Металл → свечки не входят; соседняя позиция не подставляется |
| Из какого материала подставка Звездочка? | Стекло, с переключением на новый товар |
| Какие размеры у Ча Бань Груша? | Сообщение о противоречии между миллиметрами в названии и сантиметрами в описании |
| Из какого дерева Ча Бань Груша? | Грушевое дерево, без спорных размеров |
| Сколько сейчас стоит подставка? | Архивная запись не выдаётся за текущую цену |

Для совпавшего товара проверяются также более авторитетные документы с явно
названным предметом. Расхождение характеристик приводит к обращению к
администратору; при совпадении фактов ссылка отдаётся на более авторитетный источник.
Размеры вида 540×340 мм и 54×34 см сравниваются в одной единице, без перестановки осей.

Память товара и ожидаемой характеристики сохраняется в SQLite. Она разделена
по пользователю, роли, группам и чату; изменение версии или отзыв прав отменяет
прежний контекст. Исправлено также сохранение признака вопроса об упаковке.

Название ссылки теперь содержит название товара. Ссылка открывает исходную
часть выгрузки с её полным текстом. Отдельная карточка с переходом прямо к
нужному фрагменту пока не реализована.

## Проверки

- Локальные тесты: 193/193, из них 21 новый тест товарного поиска; 7,354 с.
- Фиксированный набор реальных товарных диалогов: ${baseline.passed}/${baseline.total} до изменений, ${local.passed}/${local.total} после.
- Исходная теория: 20/20 при проверке этого этапа. Прежние 16 вопросов реальных
  файлов также включены в финальный нагрузочный прогон с новой разметкой каталога.
- Через сайт с настоящими тестовыми сессиями: ${http.passed}/${http.total}; два профиля,
  отдельный чат, понятные ссылки. Медиана ${percentile(metrics, .5)} мс, p95 ${percentile(metrics, .95)} мс.
- В браузере проверены уточнение варианта, ответ «с миндалём», открытие документа.
  После настоящего перезапуска поиска продолжение «А сколько он весит?» вернуло «50 г.».
- Оригиналы Downloads: ${integrity.originals.length}/${integrity.originals.length} хешей совпали; подготовленные копии: ${integrity.copies.length}/${integrity.copies.length}.
- Вопросы и ожидаемые ответы не менялись между исходным и конечным прогоном.
  SHA-256 набора: ${local.spec_sha256}.

Набор использовался при исправлениях, поэтому 14/14 — регрессия, а не слепая
проверка способности понимать любые формулировки или весь каталог.

## Нагрузка финальной версии

Отдельный процесс на 8016, отдельная база, 3 000 источников / 16 997 фрагментов.
Перед нагрузкой прошли ${soak.checks.length}/${soak.checks.length} сценариев. Нагрузка запрошена на 60 секунд;
${soak.summary.passed}/${soak.summary.requests} запросов прошли проверки, ошибок HTTP и ответов нет.

| Режим | Запросы | Медиана, мс | p95, мс |
|---|---:|---:|---:|
${loadRows}

Общий p95: ${soak.summary.p95Ms} мс; максимум: ${soak.summary.maxMs} мс.
SQLite quick_check: ${post.quickCheck}; сохранено ${post.contexts} контекстов;
контрольная сумма базы и ${Object.keys(post.unchangedCode).length} хешей кода совпали.
Память процессов: ${post.rssMiB.join('–')} MiB RSS. Это короткий замер одного
процесса API, без браузера и без OCR на каждом вопросе; не многочасовая приёмка.
Предыдущий промежуточный прогон 451/451 сохранён отдельно и не подменяет финальный.

## Запуск стенда

При первом запуске были ответы 404. Производный .next сохранён под другим
именем. Затем обнаружены пустые папки pdf-parse и sharp; зависимости тестовой
копии восстановлены командой npm ci --ignore-scripts --no-audit --no-fund
по package-lock.json. Исходники сайта не переносились и не заменялись.

Скрипт scripts/start_closed_search_lab.ps1 теперь проверяет готовность маршрутов
входа, документов и чата, а не только поискового индекса. Проверки требуют JSON
и отказ 401 без сессии; ошибка сборки больше не считается успешным запуском.

Финальное состояние: ${services.indexedSources} источников, ${services.indexedPassages} фрагмента,
${services.catalogDocuments} документа; индекс восстановлен с диска за ${services.restoreMs} мс.
Закрытый API 8001 и сайт 3040 работают. Общий чат 8000 в этом этапе не запущен;
он не используется этим стендом. Проверка выполнена с явным --closed-only;
обычный режим verify_lab.cjs по-прежнему требует также общий API.

## Ограничения и следующий шаг

Товарный режим включается только серверной разметкой catalog_items. Он поддерживает
ограниченный набор характеристик и русских формулировок. Не реализованы произвольные
сравнения товаров, все варианты диаметра/длины/массы упаковки, автоматическое
выделение любого состава из рекламного текста и полная проверка всех 1 827 позиций.
Совпадения фактов в разных свободных формулировках пока сравниваются осторожно:
возможен лишний отказ вместо самостоятельного разрешения противоречия.

Выгрузка от 18.06.2026 остаётся архивным снимком. Её наличие не подтверждает
актуальные цены, остатки, официальность или медицинские рекламные утверждения.
Исходные описания сохранены, исправленные размеры не выдумывались.

Далее: сравнение двух товаров по конкретной характеристике, различение размеров
товара и упаковки, затем независимые новые вопросы. После этого — адресная ссылка
на нужную позицию и приёмка на настоящих актуальных документах/сканах.

## Воспроизводимость

Команды запускаются из C:/Users/KROLL/Desktop/Ai; для проверок OPENBLAS_NUM_THREADS=1
и OMP_NUM_THREADS=1. Файлы проверки: evaluation/closed_search/catalog_probe.py,
catalog_http.cjs, catalog_cases_20261030.json; полный тестовый набор:
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p 'test_site_search*.py'.

Основные результаты в .runtime/closed-search-lab-20261028:
catalog-baseline-1793337866785251200.json; catalog-final-1793339063419103600.json;
catalog-http-1793339105101.json; soak-http-1793339082490/report.json и postcheck.json;
final-service-status.json; catalog-integrity.json.

Полные листинги всех изменённых файлов кода: ${snapshotName}.
Снимки интерфейса: catalog-dialogue-proof.png и catalog-restart-proof.png.
`;
    await fs.writeFile(path.join(root, reportName), report, 'utf8');
    const entry = `## 2026-10-30 — конкретный товар и уточнения в диалоге

Добавлена разметка 2 004 товарных строк с 1 827 ключами. Поиск различает варианты,
сохраняет выбранный товар после рестарта, не смешивает позиции одного файла,
проверяет дубли и противоречия размеров/характеристик, использует понятные подписи
ссылок. Конфликт с официальным документом не скрывается каталогом.

Проверено: 193/193 локальных теста; реальные диалоги 8/14 → 14/14; HTTP 17/17;
нагрузка ${soak.summary.passed}/${soak.summary.requests} на 3 000 источников. Исходные файлы не изменены.
Сайт 3040 и API закрытого поиска 8001 работают; общий чат 8000 не участвует.
Восстановлены зависимости тестового сайта и добавлена проверка готовности его маршрутов.

Отчёт: ${reportName}. Полные листинги: ${snapshotName}.
Следующий этап: сравнение двух товаров, размеры товара/упаковки и независимые
вопросы. Ссылка пока ведёт в файл выгрузки; адресная карточка остаётся отдельной задачей.
Закрытый поиск остаётся основным приоритетом. Обучение весов, рабочая LMS и
публикация не выполнялись. Ниже сохранена история прежних этапов.`;
    for (const name of ['CLOSED_SEARCH_PLAN_2026_10_28.md', 'README.md', 'PROGRESS_LOG.md', 'DECISIONS.md']) {
        await prepend(path.join(root, name), entry);
    }
    const siteEntry = `## 2026-10-30 — проверка товарных диалогов ВатэсAi

Это изолированный стенд, а не рабочая интеграция LMS. Для 41 копии товарных
выгрузок добавлена серверная разметка searchCatalog; содержимое и доступ не менялись.
Экспортёр передаёт ключи товаров собственному API 8001. Проверки HTTP 17/17,
в браузере — уточнение, состав, открытие файла и память после рестарта.
Зависимости восстановлены по package-lock.json; запуск из основного Ai через
scripts/start_closed_search_lab.ps1 проверяет также маршруты сайта.
Подробности: C:/Users/KROLL/Desktop/Ai/${reportName}.
Далее — сравнения товаров и переход к нужной позиции. Публикация не выполнялась.`;
    for (const name of ['PROJECT_CONTEXT.md', 'TASKS.md', 'HANDOFF.md', 'DEPLOYMENT.md']) {
        await prepend(path.join(site, name), siteEntry);
    }
    const files = ['ai_core/site_search_catalog.py', 'ai_core/site_search.py', 'ai_core/site_search_runtime.py',
        'ai_core/site_search_store.py', 'tests/test_site_search_catalog.py',
        'evaluation/closed_search/catalog_cases_20261030.json', 'evaluation/closed_search/catalog_probe.py',
        'evaluation/closed_search/catalog_http.cjs', 'evaluation/closed_search/export_site.cjs',
        'evaluation/closed_search/real_materials_http.cjs', 'evaluation/closed_search/soak_http.cjs',
        'evaluation/closed_search/wait_site_ready.cjs', 'evaluation/closed_search/verify_lab.cjs',
        'scripts/start_closed_search_lab.ps1', 'evaluation/closed_search/finalize_catalog.cjs'];
    let snapshot = '# Полные листинги этапа товарного поиска — 30.10.2026\n\n';
    for (const file of files) {
        const bytes = await fs.readFile(path.join(root, file));
        const hash = sha(bytes);
        integrity.code.push({ file, sha256: hash, bytes: bytes.length });
        const language = file.endsWith('.py') ? 'python' : file.endsWith('.json') ? 'json' : file.endsWith('.ps1') ? 'powershell' : 'javascript';
        snapshot += '\n## ' + file + '\n\nSHA-256: ' + hash + '\n\n````' + language + '\n' + bytes.toString('utf8') + '\n````\n';
    }
    await fs.writeFile(path.join(root, snapshotName), snapshot, 'utf8');
    await fs.writeFile(path.join(lab, 'catalog-integrity.json'), JSON.stringify(integrity, null, 2));
    console.log(JSON.stringify({ report: reportName, snapshot: snapshotName, sourceFiles: files.length,
                                originalsUnchanged: true, copiesUnchanged: true, specUnchanged: integrity.specUnchanged }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
