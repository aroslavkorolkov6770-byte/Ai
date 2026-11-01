/* Товарные диалоги на отдельном стенде. Рабочую LMS и оригиналы не изменяет. */
const { fs, path, lab, login, storage, call } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const { createHash } = require('node:crypto');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function prepare(cookie) {
    const directory = path.join(lab, 'real-materials');
    const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
    const documents = await storage('tea_hub_urgent_files_v1', cookie);
    let changed = 0;
    for (const doc of manifest.documents.filter(item => item.kind === 'catalog-copy')) {
        const record = documents.find(item => item.id === doc.id);
        if (!record || record.name !== doc.name) throw new Error('Catalog copy missing');
        const bytes = await fs.readFile(path.join(directory, doc.name));
        const data = await storage('file_data_' + doc.id, cookie);
        if (digest(bytes) !== doc.sha256 || data !== 'data:application/octet-stream;base64,' + bytes.toString('base64')) {
            throw new Error('Catalog copy checksum differs');
        }
        const metadata = doc.rows.map(row => ({ key: 'tea-export:' + row.code, heading: row.title }));
        if (record.searchCatalog !== undefined && JSON.stringify(record.searchCatalog) !== JSON.stringify(metadata)) {
            throw new Error('Existing catalog metadata differs');
        }
        if (!record.searchCatalog) {
            record.searchCatalog = metadata;
            changed += 1;
        }
    }
    if (changed) await storage('tea_hub_urgent_files_v1', cookie, documents);
    return changed;
}

async function main() {
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    const changed = await prepare(cookies.admin);
    const ready = await waitSnapshot();
    const specBytes = await fs.readFile(path.join(__dirname, 'catalog_cases_20261030.json'));
    const spec = JSON.parse(specBytes);
    const prefix = 'catalog-http-' + Date.now();
    const rows = [];
    const waits = [];
    async function ask(item, actor = 'b') {
        const body = { chatId: prefix + '-' + item.chat, messages: [{ role: 'user', content: item.q }] };
        let started = performance.now();
        let result = await call('/api/ai-chat', body, cookies[actor]);
        if (result.response.status === 429) {
            const seconds = Number(result.response.headers.get('retry-after')) || 300;
            if (seconds < 0 || seconds > 360) throw new Error('Unexpected rate limit interval');
            waits.push({ id: item.id, actor, seconds });
            console.log(JSON.stringify({ event: 'respecting-rate-limit', seconds }));
            for (let remaining = seconds * 1000 + 100; remaining > 0; remaining -= Math.min(remaining, 30000)) {
                await new Promise(resolve => setTimeout(resolve, Math.min(remaining, 30000)));
            }
            started = performance.now();
            result = await call('/api/ai-chat', body, cookies[actor]);
        }
        const { response, value } = result;
        const answer = value.output_text || '';
        const links = value.sourceLinks || [];
        const errors = [];
        if (response.status !== 200) errors.push('http:' + response.status);
        if (value.search_status !== item.status) errors.push('status:' + value.search_status);
        for (const phrase of item.has || []) if (!answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('missing:' + phrase);
        for (const phrase of item.absent || []) if (answer.toLowerCase().includes(phrase.toLowerCase())) errors.push('unexpected:' + phrase);
        if (item.status === 'answer' && (!links.length || links.some(link => link.title.startsWith('Товары_')))) errors.push('readable-link');
        if (item.status !== 'answer' && links.length) errors.push('unexpected-links');
        rows.push({ ...item, actor, answer, links, status: value.search_status, errors, pass: !errors.length,
                    ms: Math.round(performance.now() - started) });
    }
    for (const item of spec.cases) await ask(item);
    await ask({ id: 'acl-product', chat: 'denied', q: 'Из чего сделана подставка Хайвэй?', status: 'not_found' }, 'a');
    await ask({ id: 'acl-context', chat: 'stand', q: 'А свечки в комплекте?', status: 'clarify' }, 'a');
    await ask({ id: 'other-chat', chat: 'other', q: 'А свечки в комплекте?', status: 'clarify' });
    const report = { ready, changed, waits, chatPrefix: prefix, specSHA256: digest(specBytes), total: rows.length,
                     passed: rows.filter(row => row.pass).length, cases: rows };
    const output = path.join(lab, 'catalog-http-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file: output, total: report.total, passed: report.passed, failures: rows.filter(row => !row.pass) }));
    if (report.passed !== report.total) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
