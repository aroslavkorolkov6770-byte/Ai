/* Проверка сохранённых ссылок и закрытого PDF после настоящего рестарта API. */
const { fs, path, lab, origin, call, login } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');

async function main() {
    const reportFile = process.argv[2];
    if (!reportFile || path.basename(reportFile) !== reportFile) throw new Error('Pass a report filename inside the lab');
    const prior = JSON.parse(await fs.readFile(path.join(lab, reportFile), 'utf8'));
    const b = await login('closed-b'), a = await login('closed-a');
    const cases = [];
    const check = (id, pass, details = {}) => cases.push({ id, pass: Boolean(pass), ...details });
    for (const key of ['base', 'scan']) {
        const url = new URL(prior.urls[key], origin);
        const opened = await call('/api/closed-search-fragment' + url.search, undefined, b);
        check(key + '-location-after-restart', opened.response.status === 200 && opened.value.pdf?.initialPage === 2,
            { page: opened.value.pdf?.initialPage });
        const response = await fetch(origin + opened.value.pdf.url, { headers: { Cookie: b }, signal: AbortSignal.timeout(20000) });
        const bytes = Buffer.from(await response.arrayBuffer());
        check(key + '-original-after-restart', response.status === 200 && bytes.subarray(0, 5).toString() === '%PDF-'
            && createHash('sha256').update(bytes).digest('hex') === opened.value.pdf.sha256);
    }
    const staleUrl = new URL(prior.urls.moved, origin);
    const stale = await call('/api/closed-search-fragment' + staleUrl.search, undefined, b);
    check('stale-revision-blocked-after-restart', stale.response.status === 409 && !stale.value.text && !stale.value.pdf);
    // Проверяем именно HTML-страницу, чтобы закрытый текст не попал в SSR.
    for (const [actor, cookie, expected] of [['allowed', b, true], ['denied', a, false], ['anonymous', '', false]]) {
        const response = await fetch(origin + prior.urls.base, { headers: cookie ? { Cookie: cookie } : {}, signal: AbortSignal.timeout(20000) });
        const html = await response.text();
        check('html-' + actor, html.includes('Поставку Лиственит принимает координатор.') === expected,
            { http: response.status });
    }
    const file = path.join(lab, 'pdf-focus-postcheck-' + Date.now() + '.json');
    const report = { total: cases.length, passed: cases.filter(item => item.pass).length, cases };
    await fs.writeFile(file, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ file, total: report.total, passed: report.passed, failures: cases.filter(item => !item.pass) }));
    if (report.passed !== report.total) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
