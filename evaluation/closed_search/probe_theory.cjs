const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const origin = 'http://127.0.0.1:3040';
async function call(route, body, cookie) {
    const response = await fetch(origin + route, { method: body ? 'POST' : 'GET',
        headers: { Origin: origin, ...(body ? { 'Content-Type': 'application/json' } : {}), ...(cookie ? { Cookie: cookie } : {}) },
        body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(45000) });
    const value = await response.json();
    return { response, value };
}
async function main() {
    const accounts = JSON.parse(await fs.readFile(path.join(lab, 'fixture-accounts.private.json'), 'utf8'));
    const account = accounts.find(item => item.login === 'closed-a');
    const login = await call('/api/auth/login', { login: account.login, password: account.password });
    if (!login.response.ok) throw new Error('Fixture login failed');
    const cookie = login.response.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
    const cases = [
        ['После каких действий обязательно мыть руки?', ['route_1']],
        ['Нужно ли мыть руки после контакта с наличными?', ['route_1']],
        ['В каких перчатках моют пол в помещении?', ['route_1']],
        ['Какие перчатки использовать для уборки улицы?', ['route_1']],
        ['Что сделать при открытии смены?', ['route_4']],
        ['Как хранить чай?', ['route_14']],
        ['Какой чай имеет вкус семечек?', ['m3_1']],
        ['Как устроена программа лояльности?', ['route_8']],
        ['Что говорить гостю при приветствии?', ['route_7', 'route_9']],
        ['Где искать расписание космических рейсов в 2088 году?', []],
    ];
    const results = [];
    for (let index = 0; index < cases.length; index++) {
        const [question, expected] = cases[index];
        const started = performance.now();
        const result = await call('/api/ai-chat', { chatId: `theory-baseline-${index}`, messages: [{ role: 'user', content: question }] }, cookie);
        const links = result.value.sourceLinks || [];
        const pass = result.response.status === 200 && (expected.length ? result.value.search_status === 'answer' && expected.some(id => links.some(link => link.url.includes(id))) : result.value.search_status === 'not_found');
        const row = { question, expected, httpStatus: result.response.status, status: result.value.search_status, answer: result.value.output_text, links, elapsedMs: Math.round(performance.now() - started), pass };
        results.push(row); console.log(JSON.stringify(row));
    }
    const report = { mode: 'actual-next-cookie-to-vates-closed-search', passed: results.filter(row => row.pass).length, total: results.length, cases: results };
    await fs.writeFile(path.join(lab, `theory-probe-${Date.now()}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ passed: report.passed, total: report.total }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
