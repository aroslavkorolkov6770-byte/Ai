/* Test-only local setup. Never edits the Desktop LMS checkout or archive source data. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const site = path.join(lab, 'tea-app-main');
const data = path.join(site, 'fixture-data');
fs.mkdirSync(data, { recursive: false });
for (const name of ['tea_hub_dynamic_route_v2.json', 'tea_hub_dynamic_basics_v2.json', 'tea_hub_assortment_matrix_v1.json']) {
    fs.copyFileSync(path.join(site, 'data', name), path.join(data, name));
}
const accounts = [
    { id: 'closed_lab_admin', login: 'closed-admin', name: 'Тестовый администратор', role: 'admin' },
    { id: 'closed_lab_a', login: 'closed-a', name: 'Тестовый сотрудник А', role: 'staff' },
    { id: 'closed_lab_b', login: 'closed-b', name: 'Тестовый сотрудник Б', role: 'staff' },
].map(user => ({ ...user, password: crypto.randomBytes(18).toString('base64url') }));
const users = accounts.map(({ password, ...user }) => {
    const salt = crypto.randomBytes(16).toString('hex');
    const hash = crypto.scryptSync(password, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 }).toString('hex');
    return { ...user, passHash: `scrypt2$${salt}$${hash}`, authVersion: 0, isRegistered: true };
});
fs.writeFileSync(path.join(data, 'tea_hub_users_v1.json'), JSON.stringify(users, null, 2), { flag: 'wx' });
fs.writeFileSync(path.join(data, 'tea_hub_urgent_files_v1.json'), '[]', { flag: 'wx' });
fs.writeFileSync(path.join(lab, 'fixture-accounts.private.json'), JSON.stringify(accounts, null, 2), { flag: 'wx', mode: 0o600 });
const token = crypto.randomBytes(40).toString('hex');
const configuration = path.join(root, '.runtime/closed-search');
fs.mkdirSync(configuration, { recursive: true });
const configFile = path.join(configuration, 'sites.json');
const previous = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, 'utf8')) : { sites: [] };
previous.sites = previous.sites.filter(entry => entry.id !== 'archive19-local');
previous.sites.push({ id: 'archive19-local', origins: ['http://127.0.0.1:3040'], token_sha256: crypto.createHash('sha256').update(token).digest('hex') });
fs.writeFileSync(configFile, JSON.stringify(previous, null, 2), { mode: 0o600 });
const environment = [
    'DATA_BACKEND=json', `TEA_DATA_DIR=${data.replaceAll('\\', '/')}`, 'APP_ORIGIN=http://127.0.0.1:3040',
    'ALLOWED_ORIGINS=http://127.0.0.1:3040', `AUTH_SECRET=${crypto.randomBytes(48).toString('hex')}`,
    'AI_API_KEY=', 'AI_ENABLE_WEB_SEARCH=false', 'SMTP_HOST=', 'SMTP_USER=', 'SMTP_PASS=',
    'VATES_CLOSED_SEARCH_URL=http://127.0.0.1:8001/closed-search/archive19-local', `VATES_CLOSED_SEARCH_KEY=${token}`,
    'VATES_SEARCH_LAB=true',
].join('\n') + '\n';
fs.writeFileSync(path.join(site, '.env.local'), environment, { flag: 'wx', mode: 0o600 });
// OCR languages are private runtime components, copied read-only from the prior verified installation.
const languages = path.join(configuration, 'ocr-tessdata');
fs.mkdirSync(languages, { recursive: true });
for (const language of ['rus', 'eng']) {
    fs.copyFileSync(path.join('C:/Users/KROLL/Desktop/tea-app-main/.runtime/ocr-tessdata', `${language}.traineddata`), path.join(languages, `${language}.traineddata`));
}
console.log(JSON.stringify({ site, data, accountCount: accounts.length, secretValuesPrinted: false }));
