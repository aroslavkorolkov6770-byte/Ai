/* Exports only the approved test site's teaching content. No profiles, answers, histories or credentials. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const site = path.join(root, '.runtime/closed-search-lab-20261028/tea-app-main');
require(path.join(site, 'node_modules/@next/env')).loadEnvConfig(site, true);
const directory = path.resolve(process.env.TEA_DATA_DIR);
const expected = path.join(site, 'fixture-data');
if (directory !== expected || process.env.VATES_SEARCH_LAB !== 'true') throw new Error('Exporter accepts only this isolated fixture-data');
const origin = 'http://127.0.0.1:3040';
const endpoint = process.env.VATES_CLOSED_SEARCH_URL;
const headers = { 'Content-Type': 'application/json', 'X-Vates-Search-Key': process.env.VATES_CLOSED_SEARCH_KEY };
const read = key => { const file = path.join(directory, `${key}.json`); return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : []; };
const revision = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

function access(record) {
    const restrictions = [];
    let policy = { scope: 'workspace' };
    if (record.accessPolicy) {
        const p = record.accessPolicy;
        policy = p.version === 1 && !Object.keys(p).some(key => !['version', 'visibility', 'userIds', 'roles', 'ownerId'].includes(key)) ? { scope: p.visibility,
            ...(p.userIds ? { users: p.userIds } : {}), ...(p.roles ? { roles: p.roles } : {}), ...(p.ownerId ? { owner_id: p.ownerId } : {}) } : { scope: 'invalid' };
    }
    if (record.adminOnly || record.isAdminOnly || record.isPrivate || record.visibility === 'admin') policy = { scope: 'admin' };
    if (record.ownerId) restrictions.push({ type: 'users', values: [record.ownerId] });
    if (record.allowedUserIds) restrictions.push({ type: 'users', values: record.allowedUserIds });
    if (record.allowedRoles) restrictions.push({ type: 'roles', values: record.allowedRoles });
    if (record.permissions !== undefined || record.access !== undefined) policy = { scope: 'invalid' };
    return { access: policy, restrictions };
}
function current(record) {
    return ![record.deleted, record.isDeleted, record.archived, record.isArchived, record.isHidden, record.isObsolete].some(Boolean)
        && record.isActive !== false && record.isCurrent !== false && record.isPublished !== false
        && (record.status === undefined || ['active', 'published', 'current'].includes(record.status));
}
function source(record, id, title, url, kind, sections) {
    return { id, revision: revision(record), title, url: origin + url, kind, sections,
        fragment_url: origin + '/closed-search-fragment',
        authority: record.searchAuthority === undefined ? 'reference' : record.searchAuthority, ...access(record),
        status: current(record) ? 'published' : 'archived',
        ...(record.expiresAt ? { expires_at: Date.parse(record.expiresAt) / 1000 } : {}),
        ...(record.validFrom ? { valid_from: Date.parse(record.validFrom) / 1000 } : {}) };
}
function build() {
    const sources = [];
    for (const record of read('tea_hub_dynamic_route_v2')) {
        const sections = [1, 2, 3].filter(n => record[`t${n}`]).map(n => ({ heading: record[`h${n}`] || record.title, text: record[`t${n}`] }));
        sources.push(source(record, `route:${record.id}`, record.title || 'Урок', `/tasks?tab=edu&routeId=${encodeURIComponent(record.id)}`, 'lesson', sections));
    }
    for (const section of read('tea_hub_dynamic_basics_v2')) for (const module of section.modules || []) {
        // quiz/correct answer fields deliberately excluded, even for the admin search.
        const sections = [1, 2, 3].filter(n => module[`t${n}`]).map(n => ({ heading: module[`h${n}`] || module.title, text: module[`t${n}`] }));
        const item = source(module, `basic:${module.id}`, module.title || section.title,
            `/closed-search-material?basicId=${encodeURIComponent(module.id)}`, 'lesson', sections);
        item.parents = [{ ...access(section), status: current(section) ? 'published' : 'archived' }];
        item.revision = revision([module, section]);
        sources.push(item);
    }
    for (const record of read('tea_hub_urgent_files_v1')) {
        if (record.isDocPlaceholder || record.isTest || record.id?.startsWith('deadline_')) continue;
        const data = record.data || read(`file_data_${record.id}`);
        if (typeof data !== 'string' || !data.startsWith('data:')) continue;
        const item = source(record, `document:${record.id}`, record.name || 'Документ', `/tasks?tab=docs&documentId=${encodeURIComponent(record.id)}`, 'document', []);
        item.file_data = data; item.file_name = record.name; item.revision = revision([record, data]);
        // Тип каталога приходит из проверенного импорта, не из текста файла.
        if (record.searchCatalog !== undefined) item.catalog_items = record.searchCatalog;
        sources.push(item);
    }
    return { sources, aliases: { 'наличные': ['наличка', 'деньги'], 'перчатки': ['перчаток'], 'заваривание': ['заварить', 'заваривать'], 'лояльность': ['бонусная программа'], 'пересменка': ['передача смены'] } };
}
let previous = '';
async function tick() {
    const payload = build();
    const fingerprint = revision(payload);
    const status = await fetch(endpoint + '/status', { headers, signal: AbortSignal.timeout(5000) }).then(r => r.json());
    if (fingerprint !== previous || status.state === 'unavailable' || status.state === 'failed') {
        const response = await fetch(endpoint + '/snapshot', { method: 'POST', headers, body: JSON.stringify(payload), signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw new Error(`snapshot_status_${response.status}`);
        previous = fingerprint;
        console.log(JSON.stringify({ event: 'snapshot_submitted', sources: payload.sources.length }));
    }
}
async function main() {
    do {
        try { await tick(); } catch { console.error(JSON.stringify({ event: 'closed_export_retry' })); }
        if (process.argv.includes('--once')) break;
        await new Promise(resolve => setTimeout(resolve, 2500));
    } while (true);
}
if (require.main === module) main().catch(() => { console.error('closed_export_stopped'); process.exitCode = 1; });
module.exports = { build };
