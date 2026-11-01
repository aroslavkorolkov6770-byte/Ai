/* Проверка реального изменения текста, отзыва прав и удаления на локальном стенде. */
const { fs, path, lab, call, login, storage } = require('./lab_client.cjs');
const identifier = 'fixture-freshness-mayak';
async function main() {
    const admin = await login('closed-admin');
    const a = await login('closed-a');
    const b = await login('closed-b');
    const results = [];
    const question = 'Какой срок подтверждения заявки Маяк?';
    async function ask(cookie) {
        const result = await call('/api/ai-chat', { chatId: 'freshness-check', messages: [{ role: 'user', content: question }] }, cookie);
        if (!result.response.ok) throw new Error('Search request failed: ' + result.response.status);
        return result.value;
    }
    async function expectEventually(cookie, number) {
        for (let attempt = 0; attempt < 8; attempt++) {
            const answer = await ask(cookie);
            if (answer.search_status === 'answer' && answer.output_text.includes(number + ' рабочих')) return answer;
            await new Promise(resolve => setTimeout(resolve, 1200));
        }
        throw new Error('Fresh document was not indexed');
    }
    function record(id, pass, value) {
        const row = { id, pass, status: value.search_status, answer: value.output_text };
        results.push(row); console.log(JSON.stringify(row));
    }
    try {
        const catalog = await storage('tea_hub_urgent_files_v1', admin);
        if (catalog.some(item => item.id === identifier)) throw new Error('A previous fixture needs cleanup');
        const entry = { id: identifier, name: 'Временная_проверка_Маяк.txt', hasSeparateData: true,
            section: 'Только функциональная проверка', allowedUserIds: ['closed_lab_a'] };
        const bytes = days => 'data:text/plain;base64,' + Buffer.from(`Срок подтверждения заявки Маяк составляет ${days} рабочих дней.`, 'utf8').toString('base64');
        await storage('file_data_' + identifier, admin, bytes(4));
        await storage('tea_hub_urgent_files_v1', admin, [...catalog, entry]);
        const first = await expectEventually(a, '4');
        record('private-a-readable', first.search_status === 'answer', first);
        const deniedB = await ask(b);
        record('private-b-denied', deniedB.search_status === 'not_found' && !deniedB.sourceLinks?.length, deniedB);

        await storage('file_data_' + identifier, admin, bytes(6));
        const immediate = await ask(a);
        record('updated-text-never-old', !immediate.output_text.includes('4 рабочих') && ['processing', 'answer'].includes(immediate.search_status), immediate);
        const updated = await expectEventually(a, '6');
        record('new-version-readable', updated.output_text.includes('6 рабочих'), updated);

        const current = await storage('tea_hub_urgent_files_v1', admin);
        await storage('tea_hub_urgent_files_v1', admin, current.map(item => item.id === identifier ? { ...item, allowedUserIds: ['closed_lab_b'] } : item));
        const revoked = await ask(a);
        record('revoked-a-denied-immediately', ['processing', 'not_found'].includes(revoked.search_status) && !revoked.output_text.includes('6 рабочих'), revoked);
        const allowedB = await expectEventually(b, '6');
        record('new-recipient-b-readable', allowedB.output_text.includes('6 рабочих'), allowedB);

        const latest = await storage('tea_hub_urgent_files_v1', admin);
        await storage('tea_hub_urgent_files_v1', admin, latest.filter(item => item.id !== identifier));
        const removed = await ask(b);
        record('deleted-document-no-answer', ['processing', 'not_found'].includes(removed.search_status) && !removed.output_text.includes('6 рабочих'), removed);
        const orphan = await call('/api/storage?key=file_data_' + identifier, undefined, b);
        record('orphan-bytes-not-readable', orphan.response.status === 403, { search_status: 'http-' + orphan.response.status });
    } finally {
        // Удаляются только собственные явно временные тестовые записи.
        const catalog = await storage('tea_hub_urgent_files_v1', admin);
        await storage('tea_hub_urgent_files_v1', admin, catalog.filter(item => item.id !== identifier));
        await storage('file_data_' + identifier, admin, null);
    }
    const report = { createdAt: new Date().toISOString(), passed: results.filter(row => row.pass).length, total: results.length, cases: results };
    const output = path.join(lab, 'freshness-' + Date.now() + '.json');
    await fs.writeFile(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ report: output, passed: report.passed, total: report.total }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
