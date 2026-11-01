/* Сквозная проверка сравнений только в изолированной копии сайта на 3040. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

async function prepare(spec, cookie) {
    const directory = path.join(lab, 'comparison-assets');
    await fs.mkdir(directory, { recursive: true });
    const catalog = await storage('tea_hub_urgent_files_v1', cookie);
    const manifest = [];
    for (const doc of spec.documents) {
        const text = 'Учебные данные для проверки ВатэсAi. Не являются характеристиками товаров компании.\n\n'
            + doc.rows.map(row => '# ' + row.heading + '\n' + row.text).join('\n\n');
        const bytes = Buffer.from(text, 'utf8');
        const data = 'data:text/markdown;base64,' + bytes.toString('base64');
        const record = { id: doc.id, name: doc.name, hasSeparateData: true,
            section: 'Учебная проверка сравнения товаров', size: bytes.length + ' B', date: 'Учебные данные 30.10.2026',
            searchAuthority: 'reference', accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] },
            searchCatalog: doc.rows.map(row => ({ key: row.key, heading: row.heading })) };
        const existing = catalog.find(item => item.id === doc.id);
        if (existing) {
            if (JSON.stringify(existing) !== JSON.stringify(record) || await storage('file_data_' + doc.id, cookie) !== data) {
                throw new Error('Existing comparison fixture differs');
            }
        } else {
            await storage('file_data_' + doc.id, cookie, data);
            catalog.push(record);
        }
        await fs.writeFile(path.join(directory, doc.name), bytes);
        manifest.push({ id: doc.id, name: doc.name, sha256: sha(bytes), rows: doc.rows.length });
    }
    await storage('tea_hub_urgent_files_v1', cookie, catalog);
    await fs.writeFile(path.join(directory, 'manifest.json'), JSON.stringify(manifest, null, 2));
}

async function main() {
    const bytes = await fs.readFile(path.join(__dirname, 'comparison_20261030.json'));
    const spec = JSON.parse(bytes);
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    await prepare(spec, cookies.admin);
    const ready = await waitSnapshot();
    const prefix = 'comparison-http-' + Date.now();
    const rows = [], waits = [];
    async function ask(item, actor = 'b') {
        const body = { chatId: prefix + '-' + item.chat, messages: [{ role: 'user', content: item.q }] };
        let start = performance.now();
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate limit interval');
            waits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            start = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '', links = value.sourceLinks || [], errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== item.status) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unexpected:' + phrase);
        const answered = ['answer', 'partial'].includes(value.search_status);
        if (answered && !links.length) errors.push('no-source-links');
        if (!answered && links.length) errors.push('unexpected-links');
        if (answer.length > 1200 || answer.includes('**')) errors.push('readability');
        rows.push({ ...item, actor, answer, links, status: value.search_status, errors,
                    pass: !errors.length, ms: Math.round(performance.now() - start) });
    }
    for (const item of [...spec.cases, ...spec.holdout]) await ask(item);
    await ask({ id: 'acl-fixture', chat: 'denied', q: 'Сравни объем термоса Север и термоса Юг', status: 'not_found' }, 'a');
    await ask({ id: 'acl-context', chat: 'both', q: 'А упаковки?', status: 'clarify' }, 'a');
    await ask({ id: 'separate-chat', chat: 'fresh', q: 'А упаковки?', status: 'clarify' });
    const report = { ready, specSHA256: sha(bytes), chatPrefix: prefix, waits,
                     total: rows.length, passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'comparison-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.total !== report.passed) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
