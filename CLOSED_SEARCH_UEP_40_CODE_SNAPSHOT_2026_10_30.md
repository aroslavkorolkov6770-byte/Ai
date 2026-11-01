# Полные листинги испытания UEP: 40 вопросов

Созданы только сценарии приёмки и отчёты. Функциональный код ИИ и сайта не менялся.

## uep_40_20261030.json

````json
{
  "source": "C:/Users/KROLL/Downloads/UEP-U_12004-00-00TR4.pdf",
  "source_sha256": "7d5eacbe9e562fab65cadb9ab38e88d98a4123aace96386372ba9f3c5f97010f",
  "source_pages": 92,
  "protocol": "Первый прогон неизменённого ИИ. Только исходный PDF, без конвертированных подсказок. Каждый вопрос в новом чате, выбран один документ. В API передаются только question и documentId; expected, pages и kind не передаются. Эталоны фиксируются до загрузки. Номера страниц — физические страницы PDF, не номера листов чертежа.",
  "rubric": "Верно: факты, единицы и условия полны, без неподтверждённых добавлений. Частично: есть верный факт, но пропущено существенное условие. Ошибка: неверный факт. Не ответил: отказ/не найдено на вопрос с ответом в документе. Технически недоступно: сбой/обработка/неполный источник; не засчитывается как правильное отсутствие данных. Для конфликта нужно назвать расхождение и не выбирать вариант самовольно. Для отсутствующих данных нужно явно признать недостаток информации. HTTP 200 не означает правильный ответ.",
  "cases": [
    {"id":"uep-01","kind":"fact","question":"Для каких устройств предназначена УЭП-У-М?","pages":[3],"expected":"Электропитание МПЦ-ЭЛ, РПЦ-ЭЛ, МПЦ EBILock 950, систем интервального регулирования на базе МПЦ-ЭЛ и других микропроцессорных, электронных и релейных устройств ЖАТ."},
    {"id":"uep-02","kind":"fact","question":"Где предполагается размещать УЭП-У-М?","pages":[3],"expected":"В постах электрической централизации, в том числе транспортабельных модулях."},
    {"id":"uep-03","kind":"definition","question":"Из каких частей состоит УБП по этому документу?","pages":[4],"expected":"Многократный преобразователь рода тока, электронный байпас и аккумуляторная батарея."},
    {"id":"uep-04","kind":"comparison","question":"Чем гарантированное питание отличается от бесперебойного при переключении источников?","pages":[4],"expected":"В системе гарантированного питания провалы напряжения при переключении не исключаются; бесперебойная система исключает провалы."},
    {"id":"uep-05","kind":"paraphrase","question":"Как выбирается источник в режиме равноценных фидеров?","pages":[4],"expected":"Подключается один из исправных фидеров в зависимости от того, какой первым готов к работе."},
    {"id":"uep-06","kind":"fact","question":"Какие внешние источники питания можно подключить к УЭП-У-М?","pages":[4],"expected":"Два внешних источника трёхфазного переменного тока — основной и резервный фидеры — и фидер ДГА."},
    {"id":"uep-07","kind":"numeric","question":"При каких значениях напряжения УЭП-У отключает фидер от нагрузки?","pages":[4],"expected":"При снижении ниже 187 В либо повышении свыше 252 В; не путать с порогами обратного подключения."},
    {"id":"uep-08","kind":"numeric","question":"При каких значениях напряжения допускается повторное подключение фидера к нагрузке?","pages":[4],"expected":"После понижения — при повышении более 198 В; после перенапряжения — при понижении менее 242 В."},
    {"id":"uep-09","kind":"numeric","question":"Какая частота питания нагрузок указана и какое отклонение допускается?","pages":[4],"expected":"50 Гц, отклонение не более ±0,4 Гц."},
    {"id":"uep-10","kind":"condition","question":"Разрешается ли подключать фидер, если одна из фаз оборвана?","pages":[4],"expected":"Нет, подключение при обрыве любой фазы запрещено."},
    {"id":"uep-11","kind":"paraphrase","question":"Как система может сообщить электромеханику об аварии, если его нет рядом?","pages":[5],"expected":"Через сотовую связь SMS на мобильный телефон электромеханика СЦБ или диспетчера дистанции СЦБ."},
    {"id":"uep-12","kind":"fact","question":"Какие системы заземления позволяет реализовать УЭП-У-М?","pages":[5],"expected":"TN-C, TT, TN-C-S, TN-S, IT."},
    {"id":"uep-13","kind":"exception","question":"Нужно ли отдельно заказывать щит РЩТ?","pages":[5,24],"expected":"Нет, РЩТ входит в состав ПВМ-Е."},
    {"id":"uep-14","kind":"paraphrase","question":"С какой стороны в УВЗФ заводится внешний кабель и с какой выходит кабель на нагрузку?","pages":[6],"expected":"Ввод снизу шкафа, выход на нагрузку сверху."},
    {"id":"uep-15","kind":"comparison","question":"Чем отличаются сроки службы элементов УВЗФ 12004-100-00-03 и 12004-100-00-05?","pages":[6],"expected":"Для исполнения -03 указан срок 25 лет, для -05 — 15 лет."},
    {"id":"uep-16","kind":"exception","question":"Есть ли счётчик электроэнергии в УВЗФ 12004-100-00-02?","pages":[6],"expected":"Нет, в этом исполнении счётчик PI1 не применяется."},
    {"id":"uep-17","kind":"condition","question":"В каком случае силовой выход ДГА разрешено подключить прямо к ПВМ-Е?","pages":[6,7],"expected":"Когда ДГА установлен в одном здании с УЭП-У-М; напрямую к третьему вводу ПВМ-Е."},
    {"id":"uep-18","kind":"comparison","question":"Сравни ПВМ-Е 12004-200-00-06 и 12004-200-00-07 по току и общим габаритам.","pages":[7,24],"expected":"-06: до 160 А включительно, 2000×1100×600 мм. -07: 200–400 А включительно, две секции, общие габариты 2000×1600×600 мм; 2000×800×600 мм в таблице — размер одной секции."},
    {"id":"uep-19","kind":"fact","question":"Какие параметры работы УБП можно получать через SNMP?","pages":[16],"expected":"Режимы работы, неисправности, напряжение и частоту на входе, уровень заряда АКБ и другие параметры мониторинга."},
    {"id":"uep-20","kind":"numeric","question":"Сколько аккумуляторов в ШАКБ и какие требования к их напряжению и ёмкости?","pages":[16],"expected":"Два необслуживаемых аккумулятора по 12 В; ёмкость не менее 17 А·ч."},
    {"id":"uep-21","kind":"condition","question":"Что произойдёт с питанием ЩЖО, если исчезнут оба фидера на входе ЩЗИП?","pages":[17],"expected":"В ЩЗИП типа 2 автоматически происходит аварийное отключение питания и одновременно отключается питание ЩЖО."},
    {"id":"uep-22","kind":"paraphrase","question":"Где должен находиться щит выключения питания ВП?","pages":[17],"expected":"Вблизи входной двери помещения питающей или модуля."},
    {"id":"uep-23","kind":"comparison","question":"Чем различаются ЩРП исполнения 1 и исполнения 2 по вводам и максимальному фазному току?","pages":[17],"expected":"Исполнение 1: три отдельных однофазных ввода, не более 25 А. Исполнение 2: один трёхфазный ввод, не более 63 А."},
    {"id":"uep-24","kind":"table","question":"Какие масса и размеры указаны для батарейного кабинета БК20-1?","pages":[24],"expected":"700 кг, 2000×1000×600 мм (В×Ш×Г), одна секция."},
    {"id":"uep-25","kind":"table-footnote","question":"Какая масса указана для ИТ 40 кВА и учтён ли в ней защитный кожух?","pages":[24],"expected":"200 кг с учётом защитного кожуха; масса самого кожуха 25 кг."},
    {"id":"uep-26","kind":"footnote","question":"Включает ли табличная масса РЩТ массу ИТ2 и ИТ3?","pages":[24],"expected":"Нет, 300 кг указаны без массы ИТ2 и ИТ3."},
    {"id":"uep-27","kind":"comparison-table","question":"Сравни тепловыделение УБП 20 кВА и УБП 60 кВА.","pages":[25],"expected":"Не более 1000 Вт для 20 кВА и не более 3000 Вт для 60 кВА; второе значение втрое больше."},
    {"id":"uep-28","kind":"table","question":"Какое максимальное тепловыделение указано для ИТ 30 кВА?","pages":[25],"expected":"Не более 1050 Вт."},
    {"id":"uep-29","kind":"table","question":"Какие автомат защиты, сечение кабеля и КПД указаны для ОСМ1-2,5 кВА?","pages":[29],"expected":"D20A, 4 мм², КПД 0,95."},
    {"id":"uep-30","kind":"table","question":"Какой КПД и Qхх на фазу указаны для ТСЗ-30 кВА?","pages":[29],"expected":"КПД 0,965; Qхх 230 Вар на фазу."},
    {"id":"uep-31","kind":"late-table-footnote","question":"Какое максимальное сечение кабеля допускается для ввода фидера 1 ПВМ-Е исполнения 12004-200-00-07?","pages":[89],"expected":"120 мм², значение со звёздочкой относится к исполнению -07. Не подменять обычным значением 50 мм²."},
    {"id":"uep-32","kind":"late-table","question":"Какие клеммы и максимальное сечение кабеля указаны для питания связи на шине гарантированного питания ПВМ-Е?","pages":[89],"expected":"Х12-27,28,29,30; 4 мм²."},
    {"id":"uep-33","kind":"late-table","question":"Где подключается ШАКБ к ПРМ-Е 12004-400-00-05 и какое сечение кабеля допускается?","pages":[90],"expected":"ШАКБ (ПБ/МБ), клеммы Х2-16,15; максимум 10 мм²."},
    {"id":"uep-34","kind":"late-table","question":"Какие клеммы АРМ ШН указаны для ПРМ-Е 12004-400-00-04?","pages":[91],"expected":"Х3-21,22,23 (максимальное сечение 2,5 мм²)."},
    {"id":"uep-35","kind":"late-table","question":"В ЩЗИП 12004-210-00-02 где подключается питание приёмников ТРЦ и какое максимальное сечение кабеля?","pages":[92],"expected":"Х5-3,4; максимум 2,5 мм²."},
    {"id":"uep-36","kind":"conflict","question":"Какие габариты ВхШхГ у ЩЗИП типа 1?","pages":[16,24],"expected":"Отметить противоречие: раздел 3.7.1.2 даёт 2000×800×600 мм, таблица 5.1 — 2000×600×800 мм. Не выбирать одну запись как единственно верную, направить к администратору/ответственному за документ."},
    {"id":"uep-37","kind":"missing","question":"Сколько сейчас стоит комплект УЭП-У-М в рублях?","pages":[],"expected":"В документе нет актуальной цены. Признать отсутствие данных; не придумывать стоимость."},
    {"id":"uep-38","kind":"missing","question":"Какой серийный номер УЭП-У-М установлен на нашей станции?","pages":[],"expected":"В типовых технических решениях нет сведений об устройстве на станции пользователя; нужны паспорт или сведения о конкретном объекте."},
    {"id":"uep-39","kind":"missing","question":"Какое напряжение сейчас на первом фидере нашей установки?","pages":[],"expected":"PDF не содержит живую телеметрию; текущее значение неизвестно. Не подменять фактическое значение нормативным."},
    {"id":"uep-40","kind":"missing","question":"Кто сегодня дежурит и отвечает за обслуживание нашей УЭП-У-М?","pages":[],"expected":"В документе нет актуального графика и ответственного на объекте пользователя; нельзя придумывать имя."}
  ]
}

````

## UEP_40_PROTOCOL_2026_10_30.md

````markdown
# Проверка исходного PDF: 40 вопросов без подсказок

Исходник: C:/Users/KROLL/Downloads/UEP-U_12004-00-00TR4.pdf.
Размер: 3 950 929 байт; 92 страницы.
SHA-256: 7d5eacbe9e562fab65cadb9ab38e88d98a4123aace96386372ba9f3c5f97010f.

Вопросы и эталоны: uep_40_20261030.json. Эталоны сформулированы по исходному
документу и визуально проверенным страницам до загрузки в стенд и первого
запроса к ИИ. 35 вопросов с однозначным ответом, 1 с противоречием между
разделом и таблицей, 4 вопроса об отсутствующих сведениях.

Условия первого прогона:

- Локальный стенд 3040 / API 8001, пользователь Б, доступ только Б/администратору.
- Загружается оригинальный PDF целиком, без ручной нарезки и замены его текстом.
- Каждый вопрос передаётся отдельно в новом чате с выбранным файлом.
- В запросе отсутствуют эталон, страница, цитаты и служебные объяснения.
- ИИ не получает замечания, исправления или оценки после ответа.
- Поисковое ядро, параметры обучения и обработчик PDF во время прогона не меняются.
- 429 обрабатывается с ожиданием Retry-After, без обхода лимита профилями.
- Ожидаем завершения штатной обработки. Неполный документ не объявляется готовым
  только потому, что общий индекс находится в состоянии ready.
- Сохраняются все запросы, исходные ответы, длительность и состояния обработки.

Оценка проводится по смыслу, с проверкой чисел, единиц, отрицаний, исключений,
источника и полноты. Техническая доступность и HTTP 200 считаются отдельно.
Сообщение о незавершённой обработке не считается правильным ответом на вопрос
об отсутствии данных. Факты из документа оцениваются в рамках данной редакции,
а не как современные инструкции для реальной эксплуатации оборудования.

Выявленные ошибки фиксируются после первого прогона. Исправленный последующий
прогон будет регрессией, а не независимой проверкой на этих же вопросах.

````

## uep_acceptance.cjs

````javascript
/* Честная приёмка: загрузка исходного файла и вопросы без эталонов в запросах. */
const { fs, path, root, lab, origin, call, login, storage } = require('./lab_client.cjs');
const { build } = require('./export_site.cjs');
const { createHash } = require('node:crypto');
const directory = path.join(lab, 'uep-acceptance');
const specPath = path.join(__dirname, 'uep_40_20261030.json');
const documentId = 'uep_original_7d5eacbe9e56';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function fingerprints() {
    const files = [];
    async function visit(folder) {
        for (const item of await fs.readdir(folder, { withFileTypes: true })) {
            const file = path.join(folder, item.name);
            if (item.isDirectory() && !['__pycache__', 'node_modules'].includes(item.name)) await visit(file);
            else if (item.isFile() && /\.(py|ts|tsx|js|json|css)$/.test(item.name)) {
                files.push({ file: path.relative(root, file), sha256: sha(await fs.readFile(file)) });
            }
        }
    }
    await visit(path.join(root, 'ai_core'));
    await visit(path.join(lab, 'tea-app-main/app'));
    return files.sort((a, b) => a.file.localeCompare(b.file));
}

async function core(route, body) {
    const response = await fetch(process.env.VATES_CLOSED_SEARCH_URL + route, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'X-Vates-Search-Key': process.env.VATES_CLOSED_SEARCH_KEY, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error('Core response ' + response.status);
    return response.json();
}

async function upload(spec, specBytes) {
    const file = await fs.readFile(spec.source);
    if (sha(file) !== spec.source_sha256) throw new Error('Original PDF differs');
    const frozen = { createdAt: new Date().toISOString(), specSHA256: sha(specBytes),
        sourceSHA256: sha(file), documentId, files: await fingerprints() };
    await fs.writeFile(path.join(directory, 'frozen.json'), JSON.stringify(frozen, null, 2), { flag: 'wx' });
    const admin = await login('closed-admin');
    const catalog = await storage('tea_hub_urgent_files_v1', admin);
    if (catalog.some(item => item.id === documentId)) throw new Error('Document already exists; refusing to overwrite');
    const data = 'data:application/pdf;base64,' + file.toString('base64');
    const chunkSize = 500000, totalChunks = Math.ceil(data.length / chunkSize);
    // Используем штатную загрузку крупных файлов, не обход ограничения тела запроса.
    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
        const result = await call('/api/storage', { key: 'file_data_' + documentId,
            data: data.slice(chunkIndex * chunkSize, (chunkIndex + 1) * chunkSize),
            chunked: true, chunkType: 'string', chunkIndex, totalChunks }, admin);
        if (!result.response.ok) throw new Error('Upload failed: ' + result.response.status);
    }
    const entry = { id: documentId, name: path.basename(spec.source), hasSeparateData: true,
        section: 'Проверка настоящего технического PDF', size: file.length + ' B',
        date: 'Файл пользователя; редакция 07.2019, актуальность не подтверждена', searchAuthority: 'reference',
        accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };
    catalog.push(entry);
    await storage('tea_hub_urgent_files_v1', admin, catalog);
    const stored = await storage('file_data_' + documentId, admin);
    if (stored !== data) throw new Error('Uploaded bytes differ');
    const result = await core('/snapshot', build());
    await fs.writeFile(path.join(directory, 'upload.json'), JSON.stringify({ uploadedAt: new Date().toISOString(),
        documentId, sha256: sha(file), bytes: file.length, pages: spec.source_pages, catalogDocuments: catalog.length,
        chunks: totalChunks, snapshot: result.snapshot, specSHA256: sha(specBytes) }, null, 2));
    console.log(JSON.stringify({ event: 'uploaded', pages: spec.source_pages, bytes: file.length, documentId, state: result.state }));
}

async function run(spec, specBytes) {
    const frozen = JSON.parse(await fs.readFile(path.join(directory, 'frozen.json'), 'utf8'));
    const uploaded = JSON.parse(await fs.readFile(path.join(directory, 'upload.json'), 'utf8'));
    if (frozen.specSHA256 !== sha(specBytes) || JSON.stringify(await fingerprints()) !== JSON.stringify(frozen.files)) {
        throw new Error('Questions or AI code changed before test');
    }
    const output = path.join(directory, 'answers.json');
    try { await fs.access(output); throw new Error('First-run answers already exist; refusing overwrite'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const statuses = [], deadline = Date.now() + 20 * 60 * 1000;
    let ready;
    while (Date.now() < deadline) {
        const current = await core('/status');
        statuses.push({ at: new Date().toISOString(), ...current });
        await fs.writeFile(path.join(directory, 'processing.json'), JSON.stringify(statuses, null, 2));
        console.log(JSON.stringify({ event: 'index', state: current.state, sources: current.sources }));
        if (current.snapshot === uploaded.snapshot && current.state === 'ready') { ready = current; break; }
        if (current.state === 'failed') throw new Error('Document processing failed');
        await pause(30000);
    }
    if (!ready) throw new Error('Index not ready within 20 minutes');
    const cookie = await login('closed-b');
    const report = { startedAt: new Date().toISOString(), sourceSHA256: uploaded.sha256,
        specSHA256: sha(specBytes), ready, selectedDocumentId: documentId, cases: [], waits: [] };
    const prefix = 'uep-first-' + Date.now();
    for (const item of spec.cases) {
        // Только пользовательский вопрос и выбранный файл. Эталон остаётся снаружи.
        const request = { chatId: prefix + '-' + item.id, documentId, messages: [{ role: 'user', content: item.question }] };
        let result, started, attempts = 0;
        do {
            started = performance.now();
            result = await call('/api/ai-chat', request, cookie);
            attempts++;
            if (result.response.status !== 429) break;
            const seconds = Number(result.response.headers.get('retry-after'));
            if (!Number.isFinite(seconds) || seconds < 0 || seconds > 360 || attempts > 3) throw new Error('Unexpected rate limit');
            report.waits.push({ id: item.id, seconds, at: new Date().toISOString() });
            for (let remaining = seconds * 1000 + 500; remaining > 0;) {
                console.log(JSON.stringify({ event: 'respecting-rate-limit', id: item.id, remainingSeconds: Math.ceil(remaining / 1000) }));
                const interval = Math.min(30000, remaining);
                await pause(interval); remaining -= interval;
            }
        } while (true);
        report.cases.push({ id: item.id, request, http: result.response.status,
            ms: Math.round(performance.now() - started), attempts, response: result.value });
        await fs.writeFile(output, JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ event: 'answer', id: item.id, http: result.response.status,
            status: result.value.search_status, ms: report.cases.at(-1).ms }));
    }
    report.completedAt = new Date().toISOString();
    report.codeUnchanged = JSON.stringify(await fingerprints()) === JSON.stringify(frozen.files);
    report.originalUnchanged = sha(await fs.readFile(spec.source)) === spec.source_sha256;
    report.specUnchanged = sha(await fs.readFile(specPath)) === frozen.specSHA256;
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ event: 'complete', count: report.cases.length,
        codeUnchanged: report.codeUnchanged, originalUnchanged: report.originalUnchanged, specUnchanged: report.specUnchanged }));
}

async function main() {
    const specBytes = await fs.readFile(specPath), spec = JSON.parse(specBytes);
    if (spec.cases.length !== 40 || new Set(spec.cases.map(item => item.id)).size !== 40) throw new Error('Expected 40 unique questions');
    await fs.mkdir(directory, { recursive: true });
    if (process.argv[2] === '--upload') await upload(spec, specBytes);
    else if (process.argv[2] === '--run') await run(spec, specBytes);
    else throw new Error('Use --upload or --run');
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## uep_resume.cjs

````javascript
/* Продолжение только неотвеченных вопросов; первый журнал остаётся неизменным. */
const { fs, path, root, lab, call, login } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');
const directory = path.join(lab, 'uep-acceptance');
const digest = data => createHash('sha256').update(data).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function wait(seconds) {
    for (let remaining = seconds; remaining > 0; remaining -= 30) {
        console.log(JSON.stringify({ event: 'rate-limit-wait', remainingSeconds: remaining }));
        await pause(Math.min(remaining, 30) * 1000);
    }
}

async function verify(frozen, specBytes, spec) {
    if (digest(specBytes) !== frozen.specSHA256) throw new Error('Frozen questions changed');
    if (digest(await fs.readFile(spec.source)) !== frozen.sourceSHA256) throw new Error('Original PDF changed');
    for (const entry of frozen.files) {
        if (digest(await fs.readFile(path.join(root, entry.file))) !== entry.sha256) {
            throw new Error('AI/site code changed: ' + entry.file);
        }
    }
}

async function main() {
    const frozen = JSON.parse(await fs.readFile(path.join(directory, 'frozen.json'), 'utf8'));
    const specBytes = await fs.readFile(path.join(__dirname, 'uep_40_20261030.json'));
    const spec = JSON.parse(specBytes);
    const firstBytes = await fs.readFile(path.join(directory, 'answers.json'));
    const first = JSON.parse(firstBytes);
    await verify(frozen, specBytes, spec);
    const output = path.join(directory, 'answers-continuation.json');
    const continuation = { startedAt: new Date().toISOString(), originalAnswersSHA256: digest(firstBytes),
        reason: 'First runner stopped after 30 answers on HTTP 429; missing Retry-After had been read as zero. No AI changes.',
        cases: [], waits: [] };
    await fs.writeFile(output, JSON.stringify(continuation, null, 2), { flag: 'wx' });
    // Ожидаем целое окно лимита. Тот же профиль, без сброса счётчиков и смены сервера.
    const remainingSeconds = Math.max(0, Math.ceil((Date.parse(first.startedAt) + 310000 - Date.now()) / 1000));
    if (remainingSeconds) await wait(remainingSeconds);
    const cookie = await login('closed-b');
    const completed = new Set(first.cases.map(item => item.id));
    for (const item of spec.cases.filter(item => !completed.has(item.id))) {
        const request = { chatId: 'uep-remaining-' + Date.now() + '-' + item.id,
            documentId: frozen.documentId, messages: [{ role: 'user', content: item.question }] };
        let result, started, attempts = 0;
        while (attempts < 4) {
            started = performance.now();
            result = await call('/api/ai-chat', request, cookie);
            attempts++;
            if (result.response.status !== 429) break;
            const header = result.response.headers.get('retry-after');
            const numeric = header === null ? NaN : Number(header);
            const seconds = Number.isFinite(numeric) && numeric >= 0 ? Math.ceil(numeric) + 1 : 310;
            if (seconds > 900) throw new Error('Rate limit requires longer pause: ' + seconds);
            continuation.waits.push({ id: item.id, at: new Date().toISOString(), header, seconds });
            await fs.writeFile(output, JSON.stringify(continuation, null, 2));
            await wait(seconds);
        }
        if (result.response.status === 429) throw new Error('Still limited; checkpoint preserved');
        continuation.cases.push({ id: item.id, request, http: result.response.status,
            ms: Math.round(performance.now() - started), attempts, response: result.value });
        await fs.writeFile(output, JSON.stringify(continuation, null, 2));
        console.log(JSON.stringify({ id: item.id, http: result.response.status, status: result.value.search_status }));
    }
    await verify(frozen, specBytes, spec);
    const combined = { ...first, cases: [...first.cases, ...continuation.cases],
        completedAt: new Date().toISOString(), continuation,
        codeUnchanged: true, originalUnchanged: true, specUnchanged: true,
        firstAnswersUnchanged: digest(await fs.readFile(path.join(directory, 'answers.json'))) === digest(firstBytes) };
    if (combined.cases.length !== 40 || new Set(combined.cases.map(item => item.id)).size !== 40) {
        throw new Error('Expected exactly 40 unique answers');
    }
    await fs.writeFile(path.join(directory, 'answers-final.json'), JSON.stringify(combined, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ event: 'complete', count: combined.cases.length, codeUnchanged: true, originalUnchanged: true }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````

## uep_report.cjs

````javascript
/* Отчёт по завершённому первому прогону. Не вызывает ИИ и не меняет источники. */
const { fs, path, root, lab } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');
const directory = path.join(lab, 'uep-acceptance');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const load = async name => JSON.parse(await fs.readFile(path.join(directory, name), 'utf8'));
const reportName = 'CLOSED_SEARCH_UEP_40_REPORT_2026_10_30.md';
const url = file => file.replaceAll('\\', '/');
const link = (label, file) => '[' + label + '](<' + url(file) + '>)';

async function main() {
    const specBytes = await fs.readFile(path.join(__dirname, 'uep_40_20261030.json'));
    const spec = JSON.parse(specBytes);
    const answers = await load('answers-final.json');
    const frozen = await load('frozen.json');
    const extraction = await load('extraction-diagnostic.json');
    const index = await load('index-diagnostic.json');
    const access = await load('access-and-health.json');
    const uploaded = await load('upload.json');
    const source = await load('source-info.json');
    if (answers.cases.length !== 40 || sha(specBytes) !== frozen.specSHA256) throw new Error('Incomplete or changed exam');
    const byId = new Map(answers.cases.map(item => [item.id, item]));
    const commonAnswer = 'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.';
    const judgments = spec.cases.map(item => {
        const actual = byId.get(item.id);
        if (!actual || actual.http !== 200 || actual.response.search_status !== 'unavailable' || actual.response.output_text !== commonAnswer) {
            throw new Error('This report requires renewed manual review for changed answer: ' + item.id);
        }
        // Строгая проверка запроса исключает передачу эталона, номера страницы и истории.
        if (Object.keys(actual.request).sort().join(',') !== 'chatId,documentId,messages'
            || actual.request.documentId !== frozen.documentId || actual.request.messages.length !== 1
            || JSON.stringify(actual.request.messages[0]) !== JSON.stringify({ role: 'user', content: item.question })) {
            throw new Error('Request differs from frozen protocol: ' + item.id);
        }
        const reason = item.kind === 'missing'
            ? 'Отказ вызван блокировкой источника, а не установленным отсутствием нужных сведений. Как правильный ответ не засчитан.'
            : item.kind === 'conflict'
                ? 'Не названы две противоречащие записи и их расположение. Общая отправка к администратору не подтверждает обнаружение конфликта.'
                : 'В исходном PDF есть ответ, но он не предоставлен из-за недоступности подготовленного источника.';
        return { ...item, answer: actual.response.output_text, status: actual.response.search_status,
            http: actual.http, milliseconds: actual.ms, verdict: 'Технически недоступно; не засчитано', reason };
    });
    if (new Set(answers.cases.map(item => item.request.chatId)).size !== 40) throw new Error('Chat contexts are mixed');
    const times = answers.cases.map(item => item.ms).sort((a, b) => a - b);
    const grade = { evaluatedAt: new Date().toISOString(), total: 40, credited: 0, unavailable: 40,
        answerable: 35, conflict: 1, missingData: 4, fabricatedFactualAnswersObserved: 0,
        latencyMs: { min: times[0], mean: times.reduce((a, b) => a + b, 0) / 40,
            median: (times[19] + times[20]) / 2, p95: times[37], max: times[39] }, cases: judgments };
    await fs.writeFile(path.join(directory, 'assessment.json'), JSON.stringify(grade, null, 2));
    const lines = [
        '# Настоящий технический PDF: независимая проверка на 40 вопросах', '',
        'Дата: 30.10.2026. Результат: проверка не пройдена. 0 из 40 ответов засчитаны; все 40 вернули техническую недоступность источника.', '',
        'Это не 40 выдуманных ответов и не доказательство неспособности модели рассуждать. Поисковый контур заблокировал документ до выдачи содержательного ответа. Возможность отвечать по этому PDF сейчас не работает.', '',
        '## Что было загружено', '',
        link('Исходный PDF', spec.source) + ': ' + source.pages + ' физических страниц, ' + source.bytes + ' байт.',
        'SHA-256: `' + source.sha256 + '`. Оригинал, загруженная копия и контрольная сумма после испытания совпадают.',
        'Материал пользователя с редакцией 07.2019; его актуальность для реальной эксплуатации не подтверждалась. Он не объявлялся действующим официальным регламентом.',
        'Испытание выполнено только в изолированном стенде 127.0.0.1:3040 с ядром закрытого поиска на 8001. Рабочая LMS и общий чат 8000 не участвовали.', '',
        '## Как исключались подсказки', '',
        '1. До загрузки сохранены вопросы, критерии, ожидаемые факты и страницы исходника. Момент фиксации: ' + frozen.createdAt + '.',
        '2. Загружен PDF целиком через штатное хранилище. Для ИИ не создавались конспект, текстовая замена, ответы или дополнительные материалы.',
        '3. Каждый вопрос задан один раз с получением содержательного HTTP-ответа в отдельном новом чате. Все запросы содержат только chatId, documentId и одну пользовательскую реплику.',
        '4. Выбран конкретный файл — как при кнопке «Спросить AI». Поиск нужного файла среди всей базы этим испытанием не оценивался.',
        '5. Эталоны, страницы, оценки и исправления ИИ не передавались. Файлы ядра и приложения сверены с зафиксированными хешами; изменений нет.',
        '6. Использован настоящий /api/ai-chat и обычная сессия пользователя Б. Прямого вызова внутренней функции поиска вместо пользовательского пути не было.', '',
        'Проверенные визуально страницы: 3, 4, 5, 6, 7, 16, 17, 24, 25, 29, 89, 90, 91, 92. Номера означают физические страницы PDF, не номера листов в штампах.', '',
        '## Итоги', '',
        '| Группа | Вопросов | Засчитано | Наблюдение |',
        '|---|---:|---:|---|',
        '| Факты, сравнения, условия, числа и таблицы | 35 | 0 | Источник заблокирован |',
        '| Реальное противоречие внутри документа | 1 | 0 | Расхождение не названо |',
        '| Сведения, которых нет в документе | 4 | 0 | Общий технический отказ, а не проверенное отсутствие сведений |',
        '| Всего | 40 | 0 | 40 ответов со статусом unavailable |', '',
        'Во всех 40 ответах дословно:', '', '> ' + commonAnswer, '',
        'Вопрос о конфликте: раздел 3.7.1.2 на странице 16 даёт габариты ЩЗИП типа 1 2000×800×600 мм, а таблица 5.1 на странице 24 — 2000×600×800 мм, при одинаковом порядке В×Ш×Г. Общая просьба обратиться к администратору не показывает, что ИИ увидел эти две записи.', '',
        'Четыре вопроса без ответа в PDF касались текущей цены, серийного номера установки пользователя, текущего напряжения и дежурного сотрудника. Технический отказ не засчитан как успешное распознавание отсутствующих данных.', '',
        '## Подтверждённые причины', '',
        '1. В ai_core/site_documents.py обработка ограничена первыми 40 страницами. Для файла из 92 страниц сохранено complete=false; 52 страницы за этим пределом не обрабатываются этим циклом.',
        '2. В извлечённом тексте найден маркер STRUCTURE_UNCERTAIN. В SQLite у источника structure_unavailable=true. Именно эта проверка в site_search.py возвращает общий отказ сразу после выбора файла.',
        '3. В кэше сохранено ' + extraction.textCharacters + ' символов. В индексе документ представлен одним фрагментом с issue=true. Полноценного набора пригодных для ответа разделов нет.',
        '4. Общий индекс сообщает ready, хотя этот документ неполон и заблокирован. Карточка файла говорит о доступности просмотра, но не объясняет пользователю, почему AI не может использовать его.',
        '5. Отдельный порог 200 000 символов также существует. В этом прогоне он не сработал, поскольку сохранено 117 429 символов. Независимое извлечение из всего PDF дало 233 112 символов: будущая полная обработка потребует проверки и этого ограничения, а не только увеличения числа страниц.', '',
        'Структурная неопределённость и неполное извлечение — две отдельные проблемы. Нельзя обещать, что одно увеличение лимита до 92 автоматически исправит ответы. Конкретное происхождение маркера неопределённости на отдельных страницах ещё требует диагностики.', '',
        '## Доступность, скорость и честность измерений', '',
        'Все 40 итоговых HTTP-ответов получили код 200, но это только доступность маршрута. Содержательная проверка не пройдена.',
        'Время одного отказа: минимум ' + grade.latencyMs.min + ' мс, среднее ' + grade.latencyMs.mean.toFixed(1) + ' мс, медиана ' + grade.latencyMs.median + ' мс, p95 ' + grade.latencyMs.p95 + ' мс, максимум ' + grade.latencyMs.max + ' мс. Это скорость отказов, не поиска правильного ответа.',
        'Прогон: ' + answers.startedAt + ' — ' + answers.completedAt + '. После первых 30 ответов сработал лимит 30 запросов за 5 минут. Первый скрипт сделал несколько коротких повторов 31-го запроса и остановился: отсутствующий Retry-After преобразовывался в ноль. Эти 429 не являются ответами ИИ.',
        'Продолжение выполнено после истечения полного окна, тем же профилем, без смены IP и перезапуска сервера. Первые 30 записей не переписаны; следующие 10 сохранены отдельно и объединены в answers-final.json. Изменён только инструмент продолжения теста, не ИИ.',
        'Доступ: профиль А не видит документ в каталоге, прямое чтение файла — 403. Профиль Б видит документ и получает файл с HTTP 200; контрольная сумма совпадает с оригиналом.',
        'В браузере открыта первая страница исходника, счётчик показывает «Страница 1 из 92». Это подтверждает загрузку и просмотр, но не корректность индексации.',
        'Стенд после проверки: ' + uploaded.catalogDocuments + ' документов, ' + index.totalSources + ' источников, ' + index.totalPassages + ' фрагмента; общий индекс ready. Файл оставлен в разделе «Проверка настоящего технического PDF», доступ Б/администратор.', '',
        '## Следующий этап', '',
        '1. Поддержать полную фоновую обработку крупных PDF по страницам: прогресс, продолжение после остановки и ограничение ресурсов без молчаливого обрезания.',
        '2. Разделять достоверные разделы, таблицы и проблемные страницы. Не снимать защиту неопределённых данных, но исследовать, почему неопределённость одной части закрывает весь файл.',
        '3. Показывать настоящий статус конкретного документа: обработан полностью, частично, требуется проверка. Общая готовность индекса не должна скрывать проблему файла.',
        '4. После исправлений повторить этот набор как регрессионный тест и отдельно подготовить новые вопросы для независимой проверки. Не заучивать ответы из этого отчёта.',
        '5. К точной подсветке сложных таблиц и сносок вернуться после подтверждения полноты извлечения и качества ответов.', '',
        'В этой проверке код ИИ, модель, обучение, лимиты и рабочая интеграция не изменялись. Commit/push/deploy не выполнялись.', '',
        '## Артефакты', '',
        ...['frozen.json', 'upload.json', 'processing.json', 'answers.json', 'answers-continuation.json', 'answers-final.json', 'assessment.json', 'extraction-diagnostic.json', 'index-diagnostic.json', 'access-and-health.json']
            .map(name => '- ' + link(name, path.join(directory, name))),
        '- ' + link('Зафиксированные 40 вопросов и эталоны', path.join(__dirname, 'uep_40_20261030.json')),
        '- ' + link('Протокол до испытания', path.join(__dirname, 'UEP_40_PROTOCOL_2026_10_30.md')),
        '- ' + link('Полные листинги инструментов испытания', path.join(root, 'CLOSED_SEARCH_UEP_40_CODE_SNAPSHOT_2026_10_30.md')), '',
        '![Исходный PDF открыт в локальном стенде](' + url(path.join(directory, 'upload-proof.png')) + ')', '',
        '## Все 40 вопросов: эталон, фактический ответ и оценка', '',
    ];
    for (const item of judgments) {
        lines.push('### ' + item.id + ' — ' + item.question, '',
            'Источник: ' + (item.pages.length ? 'страницы ' + item.pages.join(', ') + ' исходного PDF.' : 'сведения отсутствуют в типовом документе.'), '',
            'Ожидаемый результат: ' + item.expected, '',
            'Фактический ответ:', '', '> ' + item.answer, '',
            'Оценка: ' + item.verdict + '. ' + item.reason, '',
            'HTTP ' + item.http + '; статус ' + item.status + '; время ' + item.milliseconds + ' мс.', '');
    }
    await fs.writeFile(path.join(root, reportName), lines.join('\n'));
    const snapshot = ['# Полные листинги испытания UEP: 40 вопросов', '',
        'Созданы только сценарии приёмки и отчёты. Функциональный код ИИ и сайта не менялся.', ''];
    for (const name of ['uep_40_20261030.json', 'UEP_40_PROTOCOL_2026_10_30.md', 'uep_acceptance.cjs', 'uep_resume.cjs', 'uep_report.cjs']) {
        snapshot.push('## ' + name, '', '````' + (name.endsWith('.cjs') ? 'javascript' : name.endsWith('.json') ? 'json' : 'markdown'),
            await fs.readFile(path.join(__dirname, name), 'utf8'), '````', '');
    }
    await fs.writeFile(path.join(root, 'CLOSED_SEARCH_UEP_40_CODE_SNAPSHOT_2026_10_30.md'), snapshot.join('\n'));
    console.log(JSON.stringify({ report: reportName, total: grade.total, credited: grade.credited,
        unavailable: grade.unavailable, latencyMs: grade.latencyMs, requestsContainNoHints: true }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

````
