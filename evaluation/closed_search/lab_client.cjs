/* Только локальный стенд: настоящие HTTP-сессии, без реальных учётных записей. */
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const origin = 'http://127.0.0.1:3040';
async function call(route, body, cookie) {
    const response = await fetch(origin + route, { method: body === undefined ? 'GET' : 'POST',
        headers: { Origin: origin, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(cookie ? { Cookie: cookie } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(45000) });
    const value = await response.json();
    return { response, value };
}
async function login(name) {
    const accounts = JSON.parse(await fs.readFile(path.join(lab, 'fixture-accounts.private.json'), 'utf8'));
    const account = accounts.find(item => item.login === name);
    if (!account) throw new Error('Unknown fixture profile');
    const result = await call('/api/auth/login', { login: account.login, password: account.password });
    if (!result.response.ok) throw new Error('Fixture login failed');
    return result.response.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
}
async function storage(key, cookie, data) {
    const result = await call('/api/storage' + (data === undefined ? '?key=' + encodeURIComponent(key) : ''),
        data === undefined ? undefined : { key, data }, cookie);
    if (!result.response.ok) throw new Error('Fixture storage request failed: ' + result.response.status);
    return result.value;
}
module.exports = { fs, path, root, lab, origin, call, login, storage };
