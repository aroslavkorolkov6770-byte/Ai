/* Длительная HTTP-нагрузка в отдельном процессе и отдельной SQLite-базе.
   Стенд 3040 и рабочий поиск 8001 не изменяются. Пароли в отчёт не попадают. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { randomBytes, createHash } = require('node:crypto');
const net = require('node:net');
const execute = promisify(execFile);
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const origin = 'http://127.0.0.1:3040';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const duration = Number(process.argv[2] || 900);
const port = Number(process.argv[3] || 8016);
const includeStructure = process.argv.includes('--structure');
const includeScans = process.argv.includes('--multipage');
const includeCrossDocuments = process.argv.includes('--cross-documents');
const includeDocumentLinks = process.argv.includes('--document-links');
const includeRealMaterials = process.argv.includes('--real-materials');
const percentile = (values, fraction) => values.length ? [...values].sort((a, b) => a - b)[Math.max(0, Math.ceil(values.length * fraction) - 1)] : null;
const rounded = value => value === null ? null : Math.round(value * 100) / 100;

function synthetic(number) {
    const code = 'Модуль' + (10000 + number), days = 2 + number % 11;
    return { id: 'bench-' + number, revision: 'v1', kind: 'lesson', title: 'Учебный порядок ' + code,
        url: origin + '/materials/bench-' + number, access: { scope: 'workspace' }, sections: [
            { heading: 'Хранение', text: `Контейнер ${code} хранится ${days} календарных дней. Контейнер ставят на выделенную полку и сохраняют заводскую этикетку.` },
            { heading: 'Получение', text: `Контейнер ${code} выдаёт координатор склада. Сотрудник записывает номер контейнера в журнал выдачи и проверяет упаковку.` },
            { heading: 'Повреждение', text: `Если контейнер ${code} повреждён, сотрудник прекращает использование и сообщает координатору склада. Самостоятельный ремонт контейнера запрещён.` },
            { heading: 'Учёт', text: `Номер контейнера ${code} указывают в карточке движения. Карточка хранится вместе с накладной в выделенном разделе архива.` },
            { heading: 'Учебный характер', text: `Порядок ${code} вымышлен и используется только для измерения поиска. Он не является регламентом компании.` },
        ] };
}

function summarize(rows) {
    const times = rows.map(row => row.ms);
    return { requests: rows.length, passed: rows.filter(row => row.pass).length,
        httpErrors: rows.filter(row => row.http !== 200).length, semanticFailures: rows.filter(row => row.http === 200 && !row.pass).length,
        p50Ms: rounded(percentile(times, .5)), p95Ms: rounded(percentile(times, .95)), p99Ms: rounded(percentile(times, .99)), maxMs: rounded(times.length ? Math.max(...times) : null) };
}

async function main() {
    if (!Number.isFinite(duration) || duration < 5 || duration > 7200 || !Number.isInteger(port) || port < 8016 || port > 8099) throw new Error('Invalid duration or isolated port');
    const shell = process.env.VATES_TEST_POWERSHELL;
    if (!shell) throw new Error('Set VATES_TEST_POWERSHELL to the current PowerShell executable');
    // Не занимаем чужой порт и не присоединяемся к уже существующему процессу.
    await new Promise((resolve, reject) => {
        const probe = net.createServer();
        probe.once('error', reject);
        probe.listen(port, '127.0.0.1', () => probe.close(resolve));
    });
    const directory = path.join(lab, 'soak-http-' + Date.now());
    await fs.mkdir(directory);
    const token = randomBytes(32).toString('hex');
    const config = path.join(directory, 'sites.json');
    await fs.writeFile(config, JSON.stringify({ sites: [{ id: 'soak-local', origins: [origin], token_sha256: createHash('sha256').update(token).digest('hex') }] }));
    const headers = { 'Content-Type': 'application/json', 'X-Vates-Search-Key': token };
    const endpoint = `http://127.0.0.1:${port}/closed-search/soak-local`;
    const output = await fs.open(path.join(directory, 'server.stdout.log'), 'w');
    const errors = await fs.open(path.join(directory, 'server.stderr.log'), 'w');
    const child = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'uvicorn', 'ai_core.site_search_api:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
        cwd: root, windowsHide: true, stdio: ['ignore', output.fd, errors.fd],
        env: { ...process.env, VATES_SEARCH_CONFIG: config, VATES_SEARCH_STORAGE: path.join(directory, 'indexes') },
    });
    let spawnError;
    child.once('error', error => { spawnError = error; });
    const report = { startedAt: new Date().toISOString(), directory, mode: 'authenticated-core-http-single-process-sqlite', requestedSeconds: duration,
        includes: ['HTTP', 'ACL', 'current revisions', 'search', 'SQLite chat context'], excludes: ['site UI', 'site authentication adapter latency', 'OCR per question', 'external Internet'],
        sourceCount: 3000, measurements: [], samples: [], phases: [], failures: [], checks: [] };
    async function request(route, body) {
        const response = await fetch(endpoint + route, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
        return { http: response.status, value: await response.json() };
    }
    async function sample() {
        const command = `$ownedIds=@(${child.pid}) + @(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${child.pid}" | Select-Object -ExpandProperty ProcessId); $ownedProcesses=@(Get-Process -Id $ownedIds -ErrorAction SilentlyContinue); [pscustomobject]@{ processes=$ownedProcesses.Count; rssBytes=($ownedProcesses | Measure-Object WorkingSet64 -Sum).Sum; privateBytes=($ownedProcesses | Measure-Object PrivateMemorySize64 -Sum).Sum; cpuSeconds=($ownedProcesses | Measure-Object CPU -Sum).Sum } | ConvertTo-Json -Compress`;
        try {
            const { stdout } = await execute(shell, ['-NoProfile', '-Command', command], { windowsHide: true, timeout: 15000 });
            const files = await fs.readdir(path.join(directory, 'indexes'));
            let storageBytes = 0;
            for (const file of files) {
                try {
                    storageBytes += (await fs.stat(path.join(directory, 'indexes', file))).size;
                } catch (error) {
                    // Вспомогательный WAL-файл может исчезнуть между readdir и stat.
                    // Это нормальное обслуживание SQLite, а не сбой поискового API.
                    if (error.code !== 'ENOENT') throw error;
                }
            }
            const status = await request('/status');
            report.samples.push({ elapsedSeconds: rounded((performance.now() - started) / 1000), ...JSON.parse(stdout), storageBytes,
                state: status.value.state, contextCount: status.value.context_count, sources: status.value.sources });
        } catch (error) {
            report.samples.push({ elapsedSeconds: rounded((performance.now() - started) / 1000), error: error.name, errorCode: error.code, message: error.message });
        }
    }
    let started = performance.now();
    let monitor;
    let stopMonitor = false;
    try {
        let available = false;
        for (let i = 0; i < 60; i++) {
            if (spawnError) throw spawnError;
            try { available = (await request('/status')).http === 200; } catch {}
            if (available) break;
            await wait(500);
        }
        if (!available) throw new Error('Isolated server did not start');
        const base = JSON.parse(await fs.readFile(path.join(lab, 'probe-snapshot.json'), 'utf8'));
        const additionalCases = [];
        if (includeCrossDocuments || includeDocumentLinks) {
            const specPath=path.join(__dirname,'cross_documents_20261029.json');
            const bytes=await fs.readFile(specPath);
            const spec=JSON.parse(bytes.toString('utf8'));
            report.crossSpecSHA256=createHash('sha256').update(bytes).digest('hex');
            base.sources.push(...spec.documents.map(item=>({id:item.id,revision:'cross-v1',kind:'document',authority:'official',title:item.name,
                url:origin+'/materials/'+item.id,status:item.status||'published',
                access:item.allowedUserIds?{scope:'restricted',users:item.allowedUserIds}:{scope:'workspace'},
                sections:[{heading:item.name,text:item.text}]})));
            additionalCases.push(...[...spec.cases,...spec.holdout].map(item=>({...item,id:'cross-'+item.id,
                chat:item.chat?'cross-'+item.chat:undefined,status:item.status||'answer',has:item.has||[],
                principalId:'closed_lab_'+(item.user||'a')})));
        }
        if (includeDocumentLinks) {
            const bytes=await fs.readFile(path.join(__dirname,'document_links_20261029.json'));
            const spec=JSON.parse(bytes.toString('utf8'));
            report.linkSpecSHA256=createHash('sha256').update(bytes).digest('hex');
            base.sources.push(...spec.documents.map(item=>({id:item.id,revision:'cross-v1',kind:'document',authority:'official',title:item.name,
                url:origin+'/materials/'+item.id,access:item.allowedUserIds?{scope:'restricted',users:item.allowedUserIds}:{scope:'workspace'},
                sections:[{heading:item.name,text:item.text}]})));
            for(const group of ['cases','dialogue','holdout'])additionalCases.push(...spec[group].map(item=>({...item,id:'links-'+item.id,
                chat:group==='dialogue'?'links-long':item.chat?'links-'+item.chat:undefined,status:item.status||'answer',has:item.has||[],
                source:undefined,selected:item.source,sources:item.evidence_ids,principalId:'closed_lab_'+(item.user||'a')})));
        }
        if (includeScans) {
            report.fixtureHashes = {};
            for (const family of ['layout', 'quality', 'multipage']) {
                const folder = path.join(__dirname, 'fixtures', family);
                const spec = JSON.parse(await fs.readFile(path.join(folder, 'spec.json'), 'utf8'));
                for (const item of spec.documents) {
                    const data = await fs.readFile(path.join(folder, item.name));
                    const digest = createHash('sha256').update(data).digest('hex');
                    if (item.sha256 && digest !== item.sha256) throw new Error('Frozen fixture changed: ' + item.id);
                    report.fixtureHashes[item.id] = digest;
                    base.sources.push({id:item.id, revision:'scan-v1', kind:'document', title:item.name,
                        url:origin+'/materials/'+item.id, access:{scope:'workspace'}, sections:[],
                        file_name:item.name, file_data:'data:application/pdf;base64,'+data.toString('base64')});
                }
                additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({...item,
                    id:family+'-'+item.id, status:item.status||'answer', has:item.has||[], selected:item.source,
                    noLinks:Boolean(item.status && !['answer','exact_or_refuse'].includes(item.status))})));
            }
        }
        if (includeStructure) {
            for (const filename of ['conditions_20261029.json', 'structure_20261029.json']) {
                const spec = JSON.parse(await fs.readFile(path.join(__dirname, filename), 'utf8'));
                base.sources.push(...spec.documents.map(item => ({ id: item.id, revision: 'v1', kind: 'document',
                    title: item.name, authority: 'official', url: origin + '/materials/' + item.id,
                    access: { scope: 'workspace' }, sections: [{ heading: item.name, text: item.text }] })));
                additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({ ...item,
                    id: filename.split('_')[0] + '-' + item.id, status: item.status || 'answer', has: item.has || [],
                    otherUser: item.user === 'b', noLinks: Boolean(item.status && item.status !== 'answer') })));
            }
            const assets = JSON.parse(await fs.readFile(path.join(lab, 'structure-assets/manifest.json'), 'utf8'));
            base.sources.push(...assets.map(item => ({ id: item.id, revision: 'v1', kind: 'document', title: item.name,
                authority: 'official', url: origin + '/materials/' + item.id, access: { scope: 'workspace' },
                file_name: item.name, file_data: item.data, sections: [] })));
            additionalCases.push({ id: 'word-footnote', q: 'Упаковка вскрыта. Сколько хранится товар Берилл?', status: 'answer', has: ['2 календарных'], absent: ['6 календарных'], source: 'lab_struct_docx', selected: includeScans ? 'lab_struct_docx' : undefined },
                { id: 'html-footnote', q: 'Упаковка вскрыта. Сколько хранится товар Селенит?', status: 'answer', has: ['2 календарных'], absent: ['6 календарных'], source: 'lab_struct_html', selected: includeScans ? 'lab_struct_html' : undefined });
        }
        if (includeRealMaterials) {
            const directory = path.join(lab, 'real-materials');
            const manifestBytes = await fs.readFile(path.join(directory, 'manifest.json'));
            const manifest = JSON.parse(manifestBytes);
            report.realManifestSHA256 = createHash('sha256').update(manifestBytes).digest('hex');
            report.realProductRows = manifest.rows;
            for (const doc of manifest.documents) {
                const bytes = await fs.readFile(path.join(directory, doc.name));
                if (createHash('sha256').update(bytes).digest('hex') !== doc.sha256) throw new Error('Real source changed');
                base.sources.push({ id: 'document:' + doc.id, revision: doc.sha256, kind: 'document', title: doc.name,
                    url: origin + '/tasks?tab=docs&documentId=' + doc.id,
                    access: { scope: 'restricted', users: ['closed_lab_b'] }, sections: [],
                    ...(doc.kind === 'catalog-copy' ? { catalog_items: doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title })) } : {}),
                    file_name: doc.name, file_data: 'data:application/octet-stream;base64,' + bytes.toString('base64') });
            }
            const bytes = await fs.readFile(path.join(__dirname, 'real_materials_20261029.json'));
            report.realSpecSHA256 = createHash('sha256').update(bytes).digest('hex');
            const spec = JSON.parse(bytes);
            additionalCases.push(...[...spec.cases, ...spec.holdout].map(item => ({ ...item,
                status: item.status || 'answer', has: item.has || [], source: item.source_prefix,
                selected: item.source_prefix === 'real_faq' ? 'document:real_faq' : undefined,
                principalId: 'closed_lab_b' })));
            additionalCases.push({ id: 'real-private-faq', q: 'Сколько минимально можно купить чая вразвес?',
                selected: 'document:real_faq', principalId: 'closed_lab_a', status: 'not_found', has: [], noLinks: true });
            const catalogBytes = await fs.readFile(path.join(__dirname, 'catalog_cases_20261030.json'));
            report.catalogSpecSHA256 = createHash('sha256').update(catalogBytes).digest('hex');
            additionalCases.push(...JSON.parse(catalogBytes).cases.map(item => ({ ...item, has: item.has || [],
                chat: 'catalog-' + item.chat, principalId: 'closed_lab_b' })));
        }
        const extra = 3000 - base.sources.length;
        if (extra < 1) throw new Error('Fixture must contain fewer than 3000 sources');
        base.sources.push(...Array.from({ length: extra }, (_, number) => synthetic(number)));
        const revisions = Object.fromEntries(base.sources.map(source => [source.id, source.revision]));
        const published = performance.now();
        if ((await request('/snapshot', base)).http !== 202) throw new Error('Snapshot rejected');
        let ready;
        for (let i = 0; i < 240; i++) {
            ready = (await request('/status')).value;
            if (ready.state === 'ready' || ready.state === 'failed') break;
            await wait(500);
        }
        if (ready.state !== 'ready' || ready.sources !== 3000) throw new Error('Snapshot not ready');
        report.initialStatus = ready;
        report.publishUntilReadyMs = rounded(performance.now() - published);
        const cases = [
            { id: 'storage', dynamic: true, question: code => `Сколько хранится контейнер ${code}?`, status: 'answer', required: days => [days + ' календарных'] },
            { id: 'issuer', dynamic: true, question: code => `Кто выдаёт контейнер ${code}?`, status: 'answer', required: () => ['координатор склада'] },
            { id: 'compound', dynamic: true, question: code => `Кто выдаёт контейнер ${code} и сколько дней он хранится?`, status: 'answer', required: days => ['координатор склада', days + ' календарных'] },
            { id: 'north', q: 'Кому направить заявку Север?', status: 'answer', has: ['старшему'] },
            { id: 'gloves', q: 'Какие перчатки нужны для мытья пола внутри помещения?', status: 'answer', has: ['только резиновые'] },
            { id: 'conflict', q: 'Сколько хранится заказ Кварц?', status: 'conflict', has: ['противоречие'], noLinks: true },
            { id: 'comet', q: 'Как отменить заявку Комета и сколько ждать подтверждения?', status: 'answer', has: ['диспетчеру', '2 рабочих'] },
            { id: 'unknown', q: 'Сколько хранится контейнер НеизвестныйАстероид?', status: 'not_found', has: ['не нашёл'], noLinks: true },
            { id: 'private-staff', q: 'Какая контрольная отметка процедуры Борей?', status: 'not_found', has: ['не нашёл'], noLinks: true },
            { id: 'private-admin', q: 'Какая контрольная отметка процедуры Борей?', role: 'admin', status: 'answer', has: ['8246'] },
            { id: 'follow-seed', q: 'Кому направить заявку Север?', status: 'answer', has: ['старшему'], chat: 'follow' },
            { id: 'follow-answer', q: 'А сколько дней?', status: 'answer', has: ['трёх'], chat: 'follow', context: true },
            { id: 'other-user', q: 'А сколько дней?', status: 'clarify', has: [], chat: 'follow', otherUser: true, noLinks: true },
            { id: 'revision-changed', dynamic: true, question: code => `Сколько хранится контейнер ${code}?`, status: 'processing', required: () => [], changed: true, noLinks: true },
            { id: 'scan', q: 'Какая контрольная отметка у поставки Кристалл?', status: 'answer', has: ['7193'] },
            { id: 'new-chat', q: 'А сколько дней?', status: 'clarify', has: [], noLinks: true },
        ];
        cases.push(...additionalCases);
        report.scenarioCount = cases.length;
        report.structureScenarios = additionalCases.length;
        // Фиксируем состав и код, чтобы долгий прогон нельзя было приписать
        // последующим изменениям алгоритма или другой версии корпуса.
        report.engineHashes = {};
        for (const file of ['site_search.py','site_search_language.py','site_search_conditions.py','site_search_structure.py','site_search_reasoning.py','site_search_links.py','site_search_catalog.py','site_search_runtime.py','site_search_store.py','site_documents.py','site_scan_tables.py','site_scan_layout.py','site_scan_quality.py','site_search_api.py']) {
            report.engineHashes[file] = createHash('sha256').update(await fs.readFile(path.join(root,'ai_core',file))).digest('hex');
        }
        async function ask(client, sequence, phase, save = true) {
            const item = cases[sequence % cases.length];
            const number = (Math.floor(sequence / cases.length) * 37 + client * 191) % extra;
            const code = 'Модуль' + (10000 + number), days = 2 + number % 11;
            const q = item.dynamic ? item.question(code) : item.q;
            // Связанные вопросы делят чат внутри одного цикла. Следующий цикл
            // начинает новую ситуацию, а preflight не оставляет ему свои факты.
            const chat = `${client}-${save ? 'run' : 'preflight'}-${Math.floor(sequence / cases.length)}-${item.chat || 'request-' + sequence}`;
            const before = performance.now();
            let row;
            try {
                const { http, value } = await request('/query', { question: q, chat_id: chat,
                    principal: { user_id: item.principalId || `reader-${client}-${item.otherUser ? 'b' : 'a'}`, role: item.role || 'staff', groups: [] },
                    selected_source: item.selected,
                    current_revisions: item.changed ? { ...revisions, ['bench-' + number]: 'changed-v2' } : revisions });
                const required = item.dynamic ? item.required(days) : item.has;
                const contains = text => new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(value.text||'');
                const exactOrRefuse = item.status === 'exact_or_refuse';
                const expected = item.allowed_statuses || (exactOrRefuse ? ['answer','unavailable'] : [item.status]);
                const pass = http === 200 && expected.includes(value.status) && (exactOrRefuse && value.status !== 'answer' || required.every(contains))
                    && (item.absent || []).every(text => !contains(text))
                    && (!item.source || value.status !== 'answer' || value.links?.some(link => link.url.includes(item.source)))
                    && (['answer','partial'].includes(value.status) || !value.links?.length)
                    && (!item.sources || value.evidence?.length && item.sources.every(id=>value.evidence.some(e=>e.id===id))
                        && value.evidence.every(e=>item.sources.includes(e.id)))
                    && (!item.noLinks || !value.links?.length) && (item.context === undefined || value.context_used === item.context)
                    && (!item.dynamic || item.changed || JSON.stringify(value.links).includes('bench-' + number));
                row = { case: item.id, client, phase, sequence, elapsedSeconds: rounded((performance.now() - started) / 1000), ms: rounded(performance.now() - before), http, status: value.status, pass };
                if (!pass && report.failures.length < 50) report.failures.push({ ...row, question: q, expectedStatus: item.status, answer: value.text, links: value.links });
            } catch (error) {
                row = { case: item.id, client, phase, sequence, elapsedSeconds: rounded((performance.now() - started) / 1000), ms: rounded(performance.now() - before), http: 0, pass: false, error: error.name };
                if (report.failures.length < 50) report.failures.push(row);
            }
            if (save) report.measurements.push(row);
            return row;
        }
        // Быстрая проверка ожиданий до длительного прогона. Ошибки нельзя скрывать процентилями.
        for (let sequence = 0; sequence < cases.length; sequence++) report.checks.push(await ask(0, sequence, 'preflight', false));
        if (report.checks.some(row => !row.pass)) throw new Error('Preflight quality check failed');
        started = performance.now();
        await sample();
        monitor = (async () => {
            let nextSample = performance.now() + 30000;
            while (!stopMonitor) {
                await wait(500);
                if (!stopMonitor && performance.now() >= nextSample) {
                    await sample();
                    console.log(JSON.stringify({ progressSeconds: Math.round((performance.now() - started) / 1000), ...summarize(report.measurements), latestSample: report.samples.at(-1) }));
                    await fs.writeFile(path.join(directory, 'progress.json'), JSON.stringify({ ...report, measurements: undefined }, null, 2));
                    nextSample = performance.now() + 30000;
                }
            }
        })();
        console.log(JSON.stringify({ event: 'soak-started', directory, durationSeconds: duration, sources: ready.sources, passages: ready.passages, checks: report.checks.length }));
        const phases = [{ name: 'one-client', clients: 1, fraction: .2 }, { name: 'four-clients', clients: 4, fraction: 1 / 3 },
            { name: 'eight-clients', clients: 8, fraction: 1 / 3 }, { name: 'recovery-four', clients: 4, fraction: 2 / 15 }];
        const sequences = Array(8).fill(0);
        let deadline = started;
        for (const phase of phases) {
            deadline += duration * phase.fraction * 1000;
            const phaseStart = performance.now(), first = report.measurements.length;
            await Promise.all(Array.from({ length: phase.clients }, (_, client) => (async () => {
                while (performance.now() < deadline) {
                    await ask(client, sequences[client]++, phase.name);
                    await wait(100);
                }
            })()));
            const elapsed = (performance.now() - phaseStart) / 1000;
            const stats = summarize(report.measurements.slice(first));
            report.phases.push({ name: phase.name, clients: phase.clients, elapsedSeconds: rounded(elapsed), requestsPerSecond: rounded(stats.requests / elapsed), ...stats });
            console.log(JSON.stringify({ event: 'phase-complete', ...report.phases.at(-1) }));
        }
        report.elapsedSeconds = rounded((performance.now() - started) / 1000);
        stopMonitor = true;
        await monitor;
        await sample();
        report.summary = summarize(report.measurements);
        report.finalStatus = (await request('/status')).value;
        report.completed = true;
        if (report.summary.passed !== report.summary.requests) process.exitCode = 1;
    } catch (error) {
        report.completed = false;
        report.error = error.message;
        process.exitCode = 1;
    } finally {
        stopMonitor = true;
        if (monitor) await monitor;
        // Завершаем только процесс, созданный этим прогоном, вместе с его дочерним Python.
        if (child.pid && child.exitCode === null) {
            try { await execute('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 10000 }); }
            catch (error) { report.cleanupError = error.message; process.exitCode = 1; }
        }
        await output.close();
        await errors.close();
        report.finishedAt = new Date().toISOString();
        const reportFile = path.join(directory, 'report.json');
        await fs.writeFile(reportFile, JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ report: reportFile, completed: report.completed, summary: report.summary, error: report.error, failures: report.failures.slice(0, 5) }));
    }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
