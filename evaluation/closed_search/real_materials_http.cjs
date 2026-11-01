/* Проверка реальных файлов на изолированном сайте. Оригиналы не изменяются. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');

async function main() {
    const directory = path.join(lab, 'real-materials');
    const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
    const specBytes = await fs.readFile(path.join(__dirname, 'real_materials_20261029.json'));
    const spec = JSON.parse(specBytes);
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    if (!process.argv.includes('--queries-only')) {
        const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
        for (const doc of manifest.documents) {
            const raw = await fs.readFile(path.join(directory, doc.name));
            if (createHash('sha256').update(raw).digest('hex') !== doc.sha256) throw new Error('Prepared document changed');
            const data = 'data:application/octet-stream;base64,' + raw.toString('base64');
            const entry = { id: doc.id, name: doc.name, hasSeparateData: true,
                section: 'Реальные материалы — проверка архивного снимка', size: raw.length + ' B',
                date: 'Снимок 18.06.2026; актуальность не подтверждена', searchAuthority: 'reference',
                accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] },
                ...(doc.kind === 'catalog-copy' ? { searchCatalog: doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title })) } : {}) };
            const prior = catalog.find(item => item.id === doc.id);
            if (prior) {
                if (JSON.stringify(prior) !== JSON.stringify(entry) || await storage('file_data_' + doc.id, cookies.admin) !== data) {
                    throw new Error('Existing real-material copy differs');
                }
            } else {
                await storage('file_data_' + doc.id, cookies.admin, data);
                catalog.push(entry);
            }
        }
        await storage('tea_hub_urgent_files_v1', cookies.admin, catalog);
        console.log(JSON.stringify({ event: 'real-materials-uploaded', documents: manifest.documents.length, productRows: manifest.rows }));
    }
    const ready = await waitSnapshot();
    const rows = [];
    const rateLimitWaits = [];
    const prefix = 'real-http-' + Date.now();
    async function ask(item, actor = 'b', source = undefined) {
        let start = performance.now();
        const body = { chatId: prefix + '-' + item.id + '-' + actor,
            messages: [{ role: 'user', content: item.q }], ...(source ? { documentId: source.replace(/^document:/, '') } : {}) };
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', id: item.id, seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            start = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '';
        const links = value.sourceLinks || [];
        const errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== (item.status || 'answer')) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unwanted:' + phrase);
        if (item.source_prefix && !links.some(link => link.url.includes('documentId=' + item.source_prefix))) errors.push('source');
        if (!['answer', 'partial'].includes(value.search_status) && links.length) errors.push('unexpected-links');
        if (answer.length > 900) errors.push('answer-too-long');
        rows.push({ id: item.id, actor, question: item.q, selectedSource: source, answer, links,
            status: value.search_status, ms: Math.round(performance.now() - start), errors, pass: !errors.length });
    }
    // У ЧаВо есть параллельные тексты в исходных уроках. Проверка оригинального
    // файла явно выбирает его; товары проверяются общим поиском всего каталога.
    for (const item of [...spec.cases, ...spec.holdout]) {
        await ask(item, 'b', item.source_prefix === 'real_faq' ? 'document:real_faq' : undefined);
    }
    await ask({ id: 'acl-faq', q: 'Сколько минимально можно купить чая вразвес?', status: 'not_found' }, 'a', 'document:real_faq');
    await ask({ id: 'acl-product', q: 'Свечки входят в комплект подставки Хайвэй?', status: 'not_found' }, 'a');
    const report = { ready, rateLimitWaits, specSHA256: createHash('sha256').update(specBytes).digest('hex'),
        total: rows.length, passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'real-materials-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.total !== report.passed) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
