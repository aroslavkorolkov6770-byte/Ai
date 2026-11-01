/* Проверка PDF только на учебных копиях изолированного стенда. */
const { fs, path, lab, origin, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const assets = path.join(__dirname, 'fixtures/pdf-focus');
const nativeId = 'focus_native', scanId = 'focus_scan';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function main() {
    const spec = JSON.parse(await fs.readFile(path.join(assets, 'spec.json'), 'utf8'));
    const cookies = { admin: await login('closed-admin'), b: await login('closed-b'), a: await login('closed-a') };
    const data = {};
    for (const item of spec.documents) {
        const bytes = await fs.readFile(path.join(assets, item.file));
        if (hash(bytes) !== item.sha256) throw new Error('Frozen PDF differs');
        data[item.key] = 'data:application/pdf;base64,' + bytes.toString('base64');
    }
    const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
    for (const [id, key, name] of [[nativeId, 'base', 'Учебный_регламент_Лиственит.pdf'], [scanId, 'scan', 'Учебный_скан_Бериллит.pdf']]) {
        const record = { id, name, section: 'Учебная проверка страниц PDF', hasSeparateData: true,
            size: 'Учебный PDF', date: '30.10.2026', searchAuthority: 'reference',
            accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };
        const existing = catalog.find(item => item.id === id);
        if (existing && JSON.stringify(existing) !== JSON.stringify(record)) throw new Error('Fixture record changed; refusing overwrite');
        if (!existing) { await storage('file_data_' + id, cookies.admin, data[key]); catalog.push(record); }
    }
    await storage('tea_hub_urgent_files_v1', cookies.admin, catalog);
    await fs.mkdir(path.join(lab, 'pdf-focus-assets'), { recursive: true });
    await fs.writeFile(path.join(lab, 'pdf-focus-assets/manifest.json'), JSON.stringify([{ id: nativeId }, { id: scanId }]));
    const mode = process.argv[2];
    if (mode === '--move' || mode === '--restore') {
        await storage('file_data_' + nativeId, cookies.admin, data[mode === '--move' ? 'moved' : 'base']);
        const ready = await waitSnapshot();
        console.log(JSON.stringify({ mode, state: ready.state })); return;
    }
    const cases = [], started = Date.now();
    const check = (id, pass, detail = {}) => cases.push({ id, pass: Boolean(pass), ...detail });
    const urls = {};
    async function ask(id, question) {
        const result = await call('/api/ai-chat', { documentId: id, chatId: 'pdf-focus-' + started + '-' + cases.length,
            messages: [{ role: 'user', content: question }] }, cookies.b);
        if (result.response.status === 429) throw new Error('Rate limited; Retry-After ' + result.response.headers.get('retry-after'));
        if (!result.response.ok) throw new Error('Question failed: ' + result.response.status);
        return result.value;
    }
    async function open(link, actor = 'b') {
        const url = new URL(link, origin);
        const result = await call('/api/closed-search-fragment' + url.search, undefined, cookies[actor]);
        return { http: result.response.status, ...result.value };
    }
    async function bytes(url, actor = 'b') {
        const response = await fetch(origin + url, { headers: cookies[actor] ? { Cookie: cookies[actor] } : {}, signal: AbortSignal.timeout(20000) });
        const value = Buffer.from(await response.arrayBuffer());
        return { status: response.status, sha256: hash(value), isPdf: value.subarray(0, 5).toString() === '%PDF-', cache: response.headers.get('cache-control') };
    }
    await storage('file_data_' + nativeId, cookies.admin, data.base);
    try {
        await waitSnapshot();
        const answer = await ask(nativeId, spec.question);
        urls.base = answer.sourceLinks?.[0]?.url;
        const base = await open(urls.base);
        check('native-page-2', base.pdf?.initialPage === 2 && base.text === spec.rule, { result: base });
        const file = await bytes(base.pdf.url);
        check('original-bytes-and-no-cache', file.status === 200 && file.isPdf && file.sha256 === spec.documents.find(item => item.key === 'base').sha256 && /no-store/.test(file.cache), { file });
        urls.basePdf = base.pdf.url;
        for (const actor of ['a', 'anonymous']) {
            const denied = await bytes(base.pdf.url, actor);
            check('pdf-access-' + actor, denied.status === (actor === 'a' ? 404 : 401) && !denied.isPdf, { result: denied });
        }
        const scanAnswer = await ask(scanId, spec.scan_question);
        urls.scan = scanAnswer.sourceLinks?.[0]?.url;
        const scan = await open(urls.scan);
        check('scan-page-2', scan.pdf?.initialPage === 2 && scan.text === spec.scan_rule, { result: scan });
        const scanFile = await bytes(scan.pdf.url);
        check('scan-bytes', scanFile.isPdf && scanFile.sha256 === spec.documents.find(item => item.key === 'scan').sha256);
        await storage('file_data_' + nativeId, cookies.admin, data.moved);
        const early = await open(urls.base);
        check('move-blocks-old-before-index', early.http === 409 && !early.pdf && !early.text);
        const oldBytes = await bytes(urls.basePdf);
        check('move-blocks-old-pdf-bytes', oldBytes.status === 409 && !oldBytes.isPdf);
        await waitSnapshot();
        const movedAnswer = await ask(nativeId, spec.question);
        urls.moved = movedAnswer.sourceLinks?.[0]?.url;
        const moved = await open(urls.moved);
        check('moved-rule-page-3', moved.pdf?.initialPage === 3 && moved.text === spec.rule && urls.moved !== urls.base, { result: moved });
        const movedFile = await bytes(moved.pdf.url);
        check('moved-bytes', movedFile.sha256 === spec.documents.find(item => item.key === 'moved').sha256);
        await storage('file_data_' + nativeId, cookies.admin, data.removed);
        check('removal-blocks-previous', (await open(urls.moved)).http === 409);
        await waitSnapshot();
        const removedAnswer = await ask(nativeId, spec.question);
        check('removed-rule-not-answered', removedAnswer.search_status === 'not_found' && !removedAnswer.sourceLinks?.length
            && !removedAnswer.output_text.includes('координатор'), { answer: removedAnswer });
        await storage('file_data_' + nativeId, cookies.admin, data.duplicate);
        await waitSnapshot();
        const duplicateAnswer = await ask(nativeId, spec.question);
        const duplicate = await open(duplicateAnswer.sourceLinks?.[0]?.url);
        check('duplicate-not-guessed', duplicate.http === 200 && !duplicate.pdf && duplicate.pdfMessage?.includes('несколько'), { result: duplicate });
    } finally {
        await storage('file_data_' + nativeId, cookies.admin, data.base);
        await waitSnapshot();
    }
    check('base-restored', (await open(urls.base)).pdf?.initialPage === 2);
    const file = path.join(lab, 'pdf-focus-http-' + started + '.json');
    await fs.writeFile(file, JSON.stringify({ specSHA256: hash(await fs.readFile(path.join(assets, 'spec.json'))),
        urls, total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file, total: cases.length, passed: cases.filter(item => item.pass).length,
        failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
