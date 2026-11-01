/* Адресные ссылки и смена редакций: только локальный стенд и учебная запись. */
const { fs, path, lab, origin, call, login, storage } = require('./lab_client.cjs');
const { waitSnapshot } = require('./scan_http.cjs');
const ID = 'fragment_edition_fixture';
const text = '# Приёмка поставки Линза\nПоставку Линза принимает координатор.\n\n# Хранение поставки Линза\nПоставка Линза хранится 7 календарных дней.';
const dataUrl = value => 'data:text/markdown;base64,' + Buffer.from(value).toString('base64');
const base = { id: ID, name: 'Учебная_проверка_редакций.md', section: 'Учебные проверки ВатэсAi',
    hasSeparateData: true, size: Buffer.byteLength(text) + ' B', date: 'Учебный файл 30.10.2026',
    searchAuthority: 'reference', accessPolicy: { version: 1, visibility: 'restricted', userIds: ['closed_lab_b'] } };

async function main() {
    const cookies = { admin: await login('closed-admin'), a: await login('closed-a'), b: await login('closed-b') };
    const cases = [], started = Date.now();
    const check = (id, pass, detail = {}) => { cases.push({ id, pass: Boolean(pass), ...detail }); };
    async function setRecord(record) {
        const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
        const others = catalog.filter(item => item.id !== ID);
        await storage('tea_hub_urgent_files_v1', cookies.admin, record ? [...others, record] : others);
    }
    const existing = (await storage('tea_hub_urgent_files_v1', cookies.admin)).find(item => item.id === ID);
    if (existing && (JSON.stringify(existing) !== JSON.stringify(base) || await storage('file_data_' + ID, cookies.admin) !== dataUrl(text))) {
        throw new Error('Fixture changed outside this acceptance; refusing overwrite');
    }
    await storage('file_data_' + ID, cookies.admin, dataUrl(text));
    await setRecord(base);
    await fs.mkdir(path.join(lab, 'fragment-assets'), { recursive: true });
    await fs.writeFile(path.join(lab, 'fragment-assets/manifest.json'), JSON.stringify([{ id: ID, name: base.name }]));
    let pairLinks = [], oldLink = '', newLink = '';
    async function ask(question, documentId) {
        const result = await call('/api/ai-chat', { chatId: 'fragment-' + started + '-' + cases.length,
            ...(documentId ? { documentId } : {}), messages: [{ role: 'user', content: question }] }, cookies.b);
        if (!result.response.ok) throw new Error('Question status ' + result.response.status + '; Retry-After ' + result.response.headers.get('retry-after'));
        return result.value;
    }
    async function open(link, actor = 'b') {
        const url = new URL(link, origin);
        if (url.pathname !== '/closed-search-fragment') throw new Error('Missing fragment link: ' + url.pathname);
        const result = await call('/api/closed-search-fragment' + url.search, undefined, cookies[actor]);
        return { http: result.response.status, cache: result.response.headers.get('cache-control'), ...result.value };
    }
    try {
        await waitSnapshot();
        const pair = await ask('Сравни объем чахаев Дао и Феникс');
        pairLinks = pair.sourceLinks || [];
        check('different-links-same-file', pairLinks.length === 2 && pairLinks[0].url !== pairLinks[1].url, { links: pairLinks });
        for (const [i, expected] of ['190 мл', '140 мл'].entries()) {
            const result = await open(pairLinks[i].url);
            const visible = (result.heading || '') + '\n' + (result.text || '');
            check('real-product-' + i, result.http === 200 && visible.includes(expected) && result.kind === 'product'
                && !visible.includes(i ? '190 мл' : '140 мл'), { result });
        }
        const denied = await open(pairLinks[0].url, 'a');
        check('profile-a-denied-real-product', denied.http === 404 && !denied.title && !denied.text, { result: denied });
        const anonymous = await open(pairLinks[0].url, 'anonymous');
        check('anonymous-denied', anonymous.http === 401 && !anonymous.text, { result: anonymous });
        const answer = await ask('Кто принимает поставку Линза?', ID);
        oldLink = answer.sourceLinks?.[0]?.url;
        if (!oldLink) throw new Error('Fixture answer has no link');
        const old = await open(oldLink);
        check('specific-document-fragment', old.http === 200 && old.text.includes('координатор') && !old.text.includes('7 календарных'), { result: old });
        check('no-cache', /no-store/.test(old.cache), { cache: old.cache });
        const invalid = new URL(oldLink, origin); invalid.searchParams.set('fragment', '0'.repeat(64));
        const unknown = await open(invalid.href);
        check('unknown-fragment-not-substituted', unknown.http === 404 && !unknown.text);
        await setRecord({ ...base, accessPolicy: { version: 1, visibility: 'admin' } });
        const revoked = await open(oldLink);
        check('revocation-without-waiting-for-index', revoked.http === 404 && !revoked.title && !revoked.text);
        await setRecord(base);
        await storage('file_data_' + ID, cookies.admin, dataUrl(text.replace('координатор', 'диспетчер')));
        const stale = await open(oldLink);
        check('changed-before-index-ready', stale.http === 409 && !stale.text && !stale.title, { result: stale });
        await waitSnapshot();
        const after = await open(oldLink);
        check('old-link-stays-changed', after.http === 409 && !after.text);
        const revised = await ask('Кто принимает поставку Линза?', ID);
        newLink = revised.sourceLinks?.[0]?.url;
        const current = await open(newLink);
        check('new-answer-new-revision', newLink !== oldLink && current.http === 200 && current.text.includes('диспетчер') && !current.text.includes('координатор'), { answer: revised, result: current });
        await setRecord({ ...base, archived: true });
        const archived = await open(newLink);
        check('archived-blocked', archived.http === 404 && !archived.text);
        await setRecord(null);
        const deleted = await open(newLink);
        check('deleted-blocked', deleted.http === 404 && !deleted.text);
        const unaffected = await open(pairLinks[1].url);
        check('unrelated-product-unchanged', [200, 503].includes(unaffected.http) && (unaffected.http !== 200 || unaffected.heading.includes('140 мл')));
    } finally {
        // Возвращаем только нашу учебную запись. Другие документы не откатываются.
        await storage('file_data_' + ID, cookies.admin, dataUrl(text));
        await setRecord(base);
        await waitSnapshot();
    }
    const restored = await open(oldLink);
    check('fixture-restored', restored.http === 200 && restored.text.includes('координатор'));
    const output = path.join(lab, 'fragments-http-' + started + '.json');
    await fs.writeFile(output, JSON.stringify({ createdAt: new Date().toISOString(), pairLinks, oldLink, newLink,
        total: cases.length, passed: cases.filter(item => item.pass).length, cases }, null, 2));
    console.log(JSON.stringify({ file: output, total: cases.length, passed: cases.filter(item => item.pass).length,
        failures: cases.filter(item => !item.pass) }));
    if (cases.some(item => !item.pass)) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
