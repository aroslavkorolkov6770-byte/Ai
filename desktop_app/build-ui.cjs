'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');

// Исходный пристрелочный макет сохраняется. Для приложения создаётся копия.
async function build() {
  const root = __dirname;
  let html = await fs.readFile(path.join(root, '..', 'desktop_preview', 'index.html'), 'utf8');
  const css = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  html = html.replace(/<title>[\s\S]*?<\/title>/, '<title>Ватэс AI</title>');
  let script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
  script = script.replace("const APP_KEY = 'vates-ai-preview-v1'", "const APP_KEY = 'vates-ai-desktop-v1'");
  // Отдельное хранилище не смешивает настоящую переписку с демо-ответами.
  script = script.replace(/const APP_KEY = [^;]+;/, "const APP_KEY = 'vates-ai-desktop-v1';");
  html = html.replace(/<style>[\s\S]*?<\/style>/, '<link rel="stylesheet" href="style.css">');
  html = html.replace(/<script>\s*[\s\S]*?<\/script>/, '<script src="app.js"></script><script src="desktop.js"></script>');
  html = html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">`);
  html = html.replace(/<button[^>]*data-page="library"[\s\S]*?<\/button>/, '');
  html = html.replaceAll('Макет работает на ноутбуке.', 'Приложение на этом ноутбуке.').replaceAll('Данные не отправляются на сервер.', 'API доступен только локально.');
  html = html.replaceAll('Локальный прототип · 0.1', 'Настольное приложение · 0.1').replaceAll('Интерактивный макет', 'Локальная нейросеть');
  await fs.mkdir(path.join(root, 'ui'), { recursive: true });
  await fs.writeFile(path.join(root, 'ui', 'index.html'), html);
  await fs.writeFile(path.join(root, 'ui', 'style.css'), css + '\n.live-detail{white-space:pre-wrap;overflow-wrap:anywhere}.log{max-height:360px;overflow:auto}');
  await fs.writeFile(path.join(root, 'ui', 'app.js'), script);
  await fs.copyFile(path.join(root, 'desktop.js'), path.join(root, 'ui', 'desktop.js'));
  console.log('Интерфейс приложения собран. Исходный HTML сохранён.');
}
build().catch(error => { console.error(error); process.exitCode = 1; });
