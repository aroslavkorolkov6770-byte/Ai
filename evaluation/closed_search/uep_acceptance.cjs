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
