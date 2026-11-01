/* Повторное открытие готовых ссылок после рестарта и проверка других видов источников. */
const { fs, path, lab, origin, call, login } = require('./lab_client.cjs');
async function main() {
    const reportFile = process.argv[2];
    if (!reportFile || path.basename(reportFile) !== reportFile) throw new Error('Pass a report filename inside the lab');
    const prior = JSON.parse(await fs.readFile(path.join(lab, reportFile), 'utf8'));
    const b = await login('closed-b'), a = await login('closed-a');
    const cases = [];
    function check(id, pass, details = {}) { cases.push({ id, pass: Boolean(pass), ...details }); }
    async function open(link, cookie = b) {
        const url = new URL(link, origin);
        return call('/api/closed-search-fragment' + url.search, undefined, cookie);
    }
    for (const link of prior.pairLinks) {
        const result = await open(link.url);
        check('restored-' + link.title, result.response.status === 200 && result.value.heading === link.title);
    }
    const restored = await open(prior.oldLink);
    check('document-link-after-restart', restored.response.status === 200 && restored.value.text.includes('координатор'));
    const stale = await open(prior.newLink);
    check('stale-link-after-restart', stale.response.status === 409 && !stale.value.text);
    // Проверяем и HTML-страницу: отказ API не должен обходиться через SSR.
    for (const [actor, cookie, expected] of [['allowed', b, true], ['denied', a, false], ['anonymous', '', false]]) {
        const response = await fetch(origin + prior.pairLinks[0].url, { headers: cookie ? { Cookie: cookie } : {}, signal: AbortSignal.timeout(30000) });
        const html = await response.text();
        const hasTitle = html.includes('Чахай фарфор');
        check('html-' + actor, hasTitle === expected, { http: response.status, hasTitle, cache: response.headers.get('cache-control') });
    }
    for (const item of [
        { id: 'lesson', q: 'Где в базе отражаются оплаты по QR-коду через монитор?', source: 'route:route_24', has: 'По QR-коду' },
        // На стенде есть ещё скан с другим правилом Нефрита. Без выбора
        // документа конфликт верен; отсылку проверяем в выбранном источнике.
        { id: 'cross-document', q: 'Упаковка вскрыта. Сколько хранится товар Нефрит?', documentId: 'link_base', source: 'document:link_exception', has: '2 календарных' },
    ]) {
        const answer = await call('/api/ai-chat', { chatId: 'fragment-post-' + Date.now(),
            ...(item.documentId ? { documentId: item.documentId } : {}), messages: [{ role: 'user', content: item.q }] }, b);
        const links = answer.value.sourceLinks || [];
        const target = links.find(link => new URL(link.url, origin).searchParams.get('source') === item.source);
        if (!target) { check(item.id, false, { answer: answer.value }); continue; }
        const opened = await open(target.url);
        check(item.id, opened.response.status === 200 && opened.value.text.includes(item.has), { answer: answer.value, fragment: opened.value });
    }
    const conflict = await call('/api/ai-chat', { chatId: 'fragment-conflict-' + Date.now(),
        messages: [{ role: 'user', content: 'Упаковка вскрыта. Сколько хранится товар Нефрит?' }] }, b);
    check('conflicting-fixtures-do-not-create-a-link', conflict.value.search_status === 'conflict' && !conflict.value.sourceLinks?.length);
    const file = path.join(lab, 'fragments-postcheck-' + Date.now() + '.json');
    await fs.writeFile(file, JSON.stringify({ total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file, total: cases.length, passed: cases.filter(item => item.pass).length, failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
