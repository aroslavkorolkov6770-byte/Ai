/* Дополняет библиотеку через штатное API. Закрытый файл получает права до индексации. */
const { fs, path, lab, login, storage } = require('./lab_client.cjs');
async function main() {
    const cookie = await login('closed-admin');
    const catalog = await storage('tea_hub_urgent_files_v1', cookie);
    const fixtures = [
        ['policy-admin', 'Служебная_проверка_доступа.txt', { accessPolicy: { version: 1, visibility: 'admin' } }],
        ['policy-archive', 'Архивный_регламент.txt', { isCurrent: false }],
        ['policy-orion-1', 'Регламент_Орион_первый.txt', {}],
        ['policy-orion-2', 'Регламент_Орион_второй.txt', {}],
    ];
    for (const [id, name, policy] of fixtures) {
        if (catalog.some(item => item.id === id)) continue;
        const data = await fs.readFile(path.join(lab, 'documents', name));
        await storage('file_data_' + id, cookie, 'data:text/plain;base64,' + data.toString('base64'));
        catalog.push({ id, name, hasSeparateData: true, section: 'Проверки закрытого поиска',
            size: `${data.length} B`, date: 'Локальный тест', ...policy });
    }
    await storage('tea_hub_urgent_files_v1', cookie, catalog);
    console.log(JSON.stringify({ registered: fixtures.length, catalogDocuments: catalog.length }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
