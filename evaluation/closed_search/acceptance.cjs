/* Сквозные функциональные проверки собственного поиска через API тестового сайта. */
const { fs, path, lab, call, login, storage } = require('./lab_client.cjs');
const noData = 'В доступных материалах я не нашёл информации, которая однозначно отвечает на этот вопрос.';
async function main() {
    const cookies = { a: await login('closed-a'), b: await login('closed-b'), admin: await login('closed-admin') };
    const catalog = await storage('tea_hub_urgent_files_v1', cookies.admin);
    const doc = suffix => catalog.find(item => item.name.endsWith(suffix)).id;
    const cases = [
        { id: 'docx-deadline', q: 'Сколько обрабатывается заявка Север?', has: ['трёх', 'рабочих'], link: doc('заявок.docx') },
        { id: 'docx-contact', q: 'Кому направить заявку Север?', has: ['старшему'], link: doc('заявок.docx') },
        { id: 'txt-temperature', q: 'При какой температуре хранить образцы Лира?', has: ['16', '18'], link: doc('образцов.txt') },
        { id: 'txt-duration', q: 'Сколько хранить открытый образец Лира?', has: ['9', 'календарных'], link: doc('образцов.txt') },
        { id: 'txt-negation', q: 'Можно ли хранить образцы Лира рядом с бытовой химией?', has: ['нельзя'], link: doc('образцов.txt') },
        { id: 'md-calendar', q: 'Когда проходит инвентаризация склада Кедр?', has: ['2', '17'], link: doc('инвентаризации.md') },
        { id: 'md-time', q: 'До какого времени заполнить форму Кедр-учёт?', has: ['18:30'], link: doc('инвентаризации.md') },
        { id: 'csv-value', q: 'Сколько этикеток нужно для набора Янтарь?', has: ['2', 'этикеток'], link: doc('упаковки.csv') },
        { id: 'ocr-action', q: 'Что делать при повреждении упаковки поставки Кристалл?', has: ['фото', 'старшего'], link: doc('скан.pdf') },
        { id: 'ocr-checklist', q: 'Что сверить при приёмке поставки Кристалл?', has: ['накладную', 'число мест'], link: doc('скан.pdf') },
        { id: 'ocr-marker', q: 'Какая контрольная отметка у поставки Кристалл?', has: ['7193'], link: doc('скан.pdf') },
        { id: 'expired-doc', q: 'Какой срок процедуры Спектр?', status: 'not_found', exact: noData },
        { id: 'conflicting-documents', q: 'Какой срок процесса Орион?', status: 'conflict', has: ['противоречие', 'администратора'] },
        { id: 'admin-private', user: 'admin', q: 'Какая контрольная отметка процедуры Борей?', has: ['8246'], link: 'policy-admin' },
        { id: 'staff-private', q: 'Какая контрольная отметка процедуры Борей?', status: 'not_found', exact: noData },
        { id: 'staff-private-selected', q: 'Какая контрольная отметка процедуры Борей?', documentId: 'policy-admin', status: 'not_found', exact: noData },
        { id: 'unknown-rule', q: 'Сколько часов длится доставка заказа Вега?', status: 'not_found', exact: noData },
        { id: 'outside-knowledge', q: 'Какая сегодня погода в Минске?', status: 'not_found', exact: noData },
        { id: 'topic-policy', q: 'Как настроить VPN?', exact: 'Я не могу ответить на данный вопрос.' },
        { id: 'followup-start', user: 'a', chat: 'memory-a', q: 'Как направить заявку Север?', has: ['старшему'], link: doc('заявок.docx') },
        { id: 'followup-duration', user: 'a', chat: 'memory-a', q: 'А сколько дней?', has: ['трёх'], context: true },
        { id: 'new-chat', user: 'a', chat: 'memory-fresh', q: 'А сколько дней?', status: 'clarify' },
        { id: 'other-user', user: 'b', chat: 'memory-a', q: 'А сколько дней?', status: 'clarify' },
        { id: 'topic-shift', user: 'a', chat: 'memory-a', q: 'А как хранить образцы Лира?', has: ['16', '18'], link: doc('образцов.txt'), context: false },
        { id: 'typo', q: 'Сколько обрабатываются заяки Север?', has: ['трёх'], link: doc('заявок.docx') },
        { id: 'paraphrase', q: 'Какие перчатки нужны для мытья пола внутри помещения?', has: ['только резиновые'], absent: ['улицы'], link: 'route_1' },
        { id: 'actions-facet', q: 'Что сделать при открытии смены?', has: ['осмотр', 'охраны'], absent: ['20.01'], link: 'route_4' },
        { id: 'loyalty-principle', q: 'Как устроена программа лояльности?', has: ['1000', '12%'], link: 'route_8' },
        { id: 'speak-facet', q: 'Что говорить гостю при приветствии?', has: ['сударь', 'сударыня'], absent: ['5 секунд'], link: 'route_9' },
        { id: 'identify-tea', q: 'Какой чай имеет вкус семечек?', has: ['Лунцзин', 'семечки'], link: 'm3_1' },
    ];
    const results = [];
    for (const [caseIndex, item] of cases.entries()) {
        // Независимые вопросы распределяем между двумя сотрудниками.
        // Явные профили сценариев доступа и продолжения диалога сохраняются.
        const user = item.user || (caseIndex % 2 ? 'b' : 'a');
        const started = performance.now();
        const result = await call('/api/ai-chat', { chatId: item.chat || 'acceptance-' + item.id,
            ...(item.documentId ? { documentId: item.documentId } : {}), messages: [{ role: 'user', content: item.q }] }, cookies[user]);
        const value = result.value;
        const answer = value.output_text || '';
        const errors = [];
        if (result.response.status !== 200) errors.push('http:' + result.response.status);
        if (item.status && value.search_status !== item.status) errors.push('status:' + value.search_status);
        if (!item.status && !item.exact && value.search_status !== 'answer') errors.push('not-answer');
        if (item.exact && answer !== item.exact) errors.push('exact-text');
        for (const word of item.has || []) if (!answer.toLowerCase().includes(word.toLowerCase())) errors.push('missing:' + word);
        for (const word of item.absent || []) if (answer.toLowerCase().includes(word.toLowerCase())) errors.push('unwanted:' + word);
        if (item.link && !(value.sourceLinks || []).some(link => link.url.includes(item.link))) errors.push('source');
        if (['not_found', 'clarify', 'conflict'].includes(value.search_status) && value.sourceLinks?.length) errors.push('unnecessary-links');
        if (item.context !== undefined && value.context_used !== item.context) errors.push('context');
        if ((value.sourceLinks || []).some(link => !link.url.startsWith('/tasks?') && !link.url.startsWith('/closed-search-material?'))) errors.push('external-link');
        const row = { id: item.id, question: item.q, user, status: value.search_status,
            answer, links: value.sourceLinks || [], elapsedMs: Math.round(performance.now() - started), pass: !errors.length, errors };
        results.push(row); console.log(JSON.stringify(row));
    }
    // ACL проверяется также на выдаче каталога и самих байтов, а не только в ответах ИИ.
    for (const user of ['a', 'b']) {
        const visible = await storage('tea_hub_urgent_files_v1', cookies[user]);
        const hidden = await call('/api/storage?key=file_data_policy-admin', undefined, cookies[user]);
        const preview = await call('/api/document-preview', { fileId: 'policy-admin', fileName: 'Служебная_проверка_доступа.txt' }, cookies[user]);
        const pass = !visible.some(item => item.id === 'policy-admin' || item.id === 'policy-archive')
            && hidden.response.status === 403 && preview.response.status === 404;
        const row = { id: 'document-access-' + user, pass, catalogVisible: visible.length,
            fileHttpStatus: hidden.response.status, previewHttpStatus: preview.response.status };
        results.push(row); console.log(JSON.stringify(row));
    }
    const times = results.filter(row => row.elapsedMs !== undefined).map(row => row.elapsedMs).sort((a, b) => a - b);
    const report = { mode: 'authenticated-test-site-to-own-search', createdAt: new Date().toISOString(),
        passed: results.filter(row => row.pass).length, total: results.length,
        medianMs: times[Math.floor(times.length / 2)], p95Ms: times[Math.ceil(times.length * .95) - 1], cases: results };
    const output = path.join(lab, `acceptance-${Date.now()}.json`);
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ report: output, passed: report.passed, total: report.total, medianMs: report.medianMs, p95Ms: report.p95Ms }));
    if (report.passed !== report.total) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
