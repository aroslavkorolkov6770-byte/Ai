'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');

async function packageApp() {
  // Перед упаковкой проверяем официальный архив по checksum из npm-пакета Electron.
  const version = require('./node_modules/electron/package.json').version;
  const filename = `electron-v${version}-win32-x64.zip`;
  const checksums = JSON.parse(await fs.readFile(path.join(__dirname, 'node_modules', 'electron', 'checksums.json'), 'utf8'));
  const actual = createHash('sha256').update(await fs.readFile(path.join(__dirname, 'vendor', filename))).digest('hex');
  if (actual !== checksums[filename]) throw new Error('Контрольная сумма Electron не совпадает. Архив не будет запущен.');
  const { packager } = await import('@electron/packager');
  const outputs = await packager({
    dir: __dirname, name: 'VatesAI', platform: 'win32', arch: 'x64',
    out: path.join(__dirname, 'dist'), overwrite: false, asar: true,
    prune: true, executableName: 'VatesAI', electronZipDir: path.join(__dirname, 'vendor'),
    icon: path.join(__dirname, 'vates.ico'),
    extraResource: [path.join(__dirname, 'backend_runner.py')],
    ignore: [/^\/dist($|\/)/, /^\/vendor($|\/)/, /^\/tests($|\/)/, /^\/test-results($|\/)/, /^\/node_modules($|\/)/],
    win32metadata: { CompanyName: 'Vates', FileDescription: 'Ватэс AI — локальная нейросеть', ProductName: 'Ватэс AI' },
  });
  for (const output of outputs) {
    await fs.writeFile(path.join(output, 'project-location.json'), JSON.stringify({ projectRoot: path.resolve(__dirname, '..'), port: 8000 }, null, 2));
  }
  console.log(outputs.join('\n'));
}
packageApp().catch(error => { console.error(error); process.exitCode = 1; });
