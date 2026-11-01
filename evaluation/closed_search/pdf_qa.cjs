/* Временные PDF для проверки просмотрщика. setup/cleanup затрагивают только свои три записи. */
const { fs, path, lab, login, storage } = require('./lab_client.cjs');
const names = ['two-pages', 'password', 'invalid'];
async function main() {
    const cookie = await login('closed-admin');
    const catalog = await storage('tea_hub_urgent_files_v1', cookie);
    const ids = new Set(names.map(name => 'pdf-viewer-qa-' + name));
    if (process.argv[2] === 'cleanup') {
        await storage('tea_hub_urgent_files_v1', cookie, catalog.filter(file => !ids.has(file.id)));
        for (const id of ids) await storage('file_data_' + id, cookie, null);
        console.log(JSON.stringify({ cleaned: [...ids] }));
        return;
    }
    if (process.argv[2] !== 'setup') throw new Error('Use setup or cleanup');
    if (catalog.some(file => ids.has(file.id))) throw new Error('QA documents already exist; inspect before reusing');
    const template = catalog.find(file => file.name.endsWith('скан.pdf'));
    if (!template) throw new Error('Original scan missing');
    const added = [];
    for (const name of names) {
        const bytes = await fs.readFile(path.join(lab, 'pdf-qa', 'viewer-' + name + '.pdf'));
        const entry = { ...template, id: 'pdf-viewer-qa-' + name, name: 'Проверка_PDF_' + name + '.pdf', section: 'Временная проверка PDF',
            size: bytes.length + ' B', hasSeparateData: true, data: undefined };
        added.push(entry);
    }
    // Сначала карточки: сервер проверяет права даже при записи самих файлов.
    await storage('tea_hub_urgent_files_v1', cookie, [...catalog, ...added]);
    for (const [index, name] of names.entries()) {
        const bytes = await fs.readFile(path.join(lab, 'pdf-qa', 'viewer-' + name + '.pdf'));
        await storage('file_data_' + added[index].id, cookie, 'data:application/pdf;base64,' + bytes.toString('base64'));
    }
    console.log(JSON.stringify({ added: added.map(file => ({ id: file.id, name: file.name })) }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
