/* Original synthetic fixtures, unrelated to real company rules or personal information. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '.runtime/closed-search-lab-20261028');
const requireSite = createRequire(path.join(lab, 'tea-app-main/package.json'));
const JSZip = requireSite('jszip');
const sharp = requireSite('sharp');
const directory = path.join(lab, 'documents');
const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

async function word(name, paragraphs) {
    const zip = new JSZip();
    zip.file('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
    zip.file('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
    zip.file('word/document.xml', `<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${paragraphs.map(text => `<w:p><w:r><w:t xml:space="preserve">${escape(text)}</w:t></w:r></w:p>`).join('')}<w:sectPr/></w:body></w:document>`);
    await fs.writeFile(path.join(directory, name), await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }), { flag: 'wx' });
}
function wrap(text, size = 64) {
    const lines = []; let current = '';
    for (const word of text.split(' ')) { if ((current + ' ' + word).length > size) { lines.push(current); current = word; } else current += (current ? ' ' : '') + word; }
    if (current) lines.push(current);
    return lines;
}
async function rasterPdf() {
    const lines = ['УЧЕБНЫЙ РЕГЛАМЕНТ ПРИЁМКИ', ...wrap('Поставка «Кристалл» проверяется после доставки.'),
        ...wrap('Для приёмки поставки «Кристалл» сверьте накладную и число мест.'),
        ...wrap('При повреждении упаковки поставки «Кристалл» сделайте фото и уведомите старшего смены.'), 'Контрольная отметка: КРИСТАЛЛ 7193.'];
    const height = Math.max(1100, 160 + lines.length * 95);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="${height}"><rect width="100%" height="100%" fill="white"/>${lines.map((line, n) => `<text x="110" y="${140 + n * 95}" font-family="Arial" font-size="42" fill="#111">${escape(line)}</text>`).join('')}</svg>`;
    const jpeg = await sharp(Buffer.from(svg)).rotate(0.25, { background: 'white' }).jpeg({ quality: 90 }).toBuffer();
    await fs.writeFile(path.join(directory, 'Приёмка_поставок_скан.jpg'), jpeg, { flag: 'wx' });
    const size = await sharp(jpeg).metadata();
    const content = Buffer.from('q\n595 0 0 430 0 0 cm\n/Scan Do\nQ\n');
    const objects = [Buffer.from('<< /Type /Catalog /Pages 2 0 R >>'), Buffer.from('<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
        Buffer.from('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 430] /Resources << /XObject << /Scan 4 0 R >> >> /Contents 5 0 R >>'),
        Buffer.concat([Buffer.from(`<< /Type /XObject /Subtype /Image /Width ${size.width} /Height ${size.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`), jpeg, Buffer.from('\nendstream')]),
        Buffer.concat([Buffer.from(`<< /Length ${content.length} >>\nstream\n`), content, Buffer.from('endstream')])];
    const buffers = [Buffer.from('%PDF-1.4\n')], offsets = [0]; let length = buffers[0].length;
    for (let i = 0; i < objects.length; i++) { offsets.push(length); const buffer = Buffer.concat([Buffer.from(`${i + 1} 0 obj\n`), objects[i], Buffer.from('\nendobj\n')]); buffers.push(buffer); length += buffer.length; }
    buffers.push(Buffer.from(`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${length}\n%%EOF\n`));
    await fs.writeFile(path.join(directory, 'Приёмка_поставок_скан.pdf'), Buffer.concat(buffers), { flag: 'wx' });
}
async function main() {
    await fs.mkdir(directory, { recursive: false });
    await word('Порядок_обработки_заявок.docx', ['Учебный тестовый документ ВатэсAi. Все названия и правила ниже вымышлены.',
        'Заявка «Север» передаётся старшему сотруднику через внутреннюю форму.',
        'Заявка «Север» обрабатывается в течение трёх рабочих дней.',
        'Для передачи заявки «Север» нужны номер заказа, наименование позиции и контактный канал заказчика.']);
    const files = {
        'Хранение_учебных_образцов.txt': 'Учебный тестовый регламент.\nОбразцы серии «Лира» хранятся в закрытой таре при температуре 16–18 °C.\nОткрытый образец серии «Лира» списывается через 9 календарных дней.\nОбразцы серии «Лира» нельзя размещать рядом с бытовой химией.\n',
        'График_инвентаризации.md': '# Учебная инвентаризация\nИнвентаризация учебного склада «Кедр» проводится 2 и 17 числа каждого месяца.\nОтветственный за инвентаризацию «Кедр» — старший смены.\nРезультат инвентаризации «Кедр» вносится в форму «Кедр-учёт» до 18:30 дня проверки.\n',
        'Нормы_упаковки.csv': 'Изделие;Число этикеток;Дополнение\nНабор «Янтарь»;2;Одна этикетка содержит состав другая дату упаковки\n',
        'Служебная_проверка_доступа.txt': 'Учебный служебный материал для проверки прав.\nСлужебная процедура «Борей» выполняется только старшим смены.\nКонтрольная метка служебной процедуры «Борей» — БОРЕЙ-8246.\n',
        'Архивный_регламент.txt': 'Срок учебного процесса «Спектр» составляет 88 календарных дней.\nЭтот документ должен быть помечен неактуальным в тесте.\n',
        'Регламент_Орион_первый.txt': 'Срок учебного процесса «Орион» составляет 5 рабочих дней.\n',
        'Регламент_Орион_второй.txt': 'Срок учебного процесса «Орион» составляет 7 рабочих дней.\n',
    };
    for (const [name, text] of Object.entries(files)) await fs.writeFile(path.join(directory, name), text, { flag: 'wx' });
    await rasterPdf();
    console.log(JSON.stringify({ directory, documents: 9, rasterPreviewImages: 1 }));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
