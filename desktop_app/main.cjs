'use strict';
const { app, BrowserWindow, ipcMain, protocol, net, session, dialog, shell, Menu } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { ApiController } = require('./api-controller.cjs');

protocol.registerSchemesAsPrivileged([{ scheme: 'vates', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName('Vates AI');
app.setAppUserModelId('local.vates.ai');
let window;
let controller;
let quitting = false;
let config;
let configFile;

// Один контроллер на ноутбук: повторный ярлык показывает уже открытое окно.
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(boot).catch(error => { dialog.showErrorBox('Ватэс AI — ошибка запуска', error.message); app.quit(); });
}

async function saveConfig() { await fs.writeFile(configFile, JSON.stringify(config, null, 2), 'utf8'); }

function trusted(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || !event.senderFrame.url.startsWith('vates://app/')) throw new Error('Недопустимый источник команды.');
}

function handle(name, operation) {
  ipcMain.handle(name, async (event, ...args) => {
    try { trusted(event); return { ok: true, value: await operation(...args) }; }
    catch (error) { return { ok: false, error: error.message }; }
  });
}

async function boot() {
  configFile = path.join(app.getPath('userData'), 'desktop-config.json');
  await fs.mkdir(app.getPath('userData'), { recursive: true });
  const portable = app.isPackaged ? path.join(path.dirname(process.execPath), 'project-location.json') : path.join(__dirname, 'project-location.json');
  try { config = JSON.parse(await fs.readFile(configFile, 'utf8')); }
  catch { try { config = JSON.parse(await fs.readFile(portable, 'utf8')); } catch { config = { projectRoot: path.resolve(__dirname, '..'), port: 8000 }; } }
  if (typeof config.projectRoot !== 'string') throw new Error('В настройках отсутствует путь к проекту.');
  if (!Number.isInteger(config.port) || config.port < 1024 || config.port > 65535) config.port = 8000;
  controller = new ApiController({ projectRoot: config.projectRoot,
    runner: app.isPackaged ? path.join(process.resourcesPath, 'backend_runner.py') : path.join(__dirname, 'backend_runner.py'),
    logDir: path.join(app.getPath('userData'), 'logs'), port: config.port });
  controller.on('state', state => { if (window && !window.isDestroyed()) window.webContents.send('vates:state', state); });

  // Разрешены только собственные ресурсы интерфейса. Внешние страницы не получают bridge.
  const assets = new Set(['index.html', 'style.css', 'app.js', 'desktop.js']);
  protocol.handle('vates', request => {
    const url = new URL(request.url);
    const name = url.pathname.slice(1) || 'index.html';
    if (url.host !== 'app' || !assets.has(name)) return new Response('Not found', { status: 404 });
    return net.fetch(pathToFileURL(path.join(__dirname, 'ui', name)).toString());
  });
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  handle('vates:status', () => controller.snapshot());
  handle('vates:start', async port => { const value = await controller.start(port); config.port = controller.port; await saveConfig(); return value; });
  handle('vates:stop', () => controller.stop());
  handle('vates:generate', input => controller.generate(input));
  handle('vates:choose-project', async () => {
    if (controller.child || controller.phase === 'starting') throw new Error('Сначала выключи API.');
    const result = await dialog.showOpenDialog(window, { title: 'Папка собственной нейросети', properties: ['openDirectory'] });
    if (!result.canceled) {
      const root = result.filePaths[0];
      await Promise.all([fs.access(path.join(root, 'ai_core', 'api_server.py')), fs.access(path.join(root, '.venv', 'Scripts', 'python.exe'))]);
      config.projectRoot = root; controller.projectRoot = root; await saveConfig(); controller.publish();
    }
    return controller.snapshot();
  });
  handle('vates:open-logs', async () => { const folder = path.join(app.getPath('userData'), 'logs'); await fs.mkdir(folder, { recursive: true }); const error = await shell.openPath(folder); if (error) throw new Error(error); return true; });
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({ width: 1280, height: 840, minWidth: 820, minHeight: 620,
    title: 'Ватэс AI', icon: path.join(__dirname, 'vates.ico'), backgroundColor: '#f6f5ef', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, spellcheck: false },
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) void shell.openExternal(url).catch(error => controller.log(error.message));
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => { if (!url.startsWith('vates://app/')) event.preventDefault(); });
  window.once('ready-to-show', () => window.show());
  window.on('close', event => {
    if (quitting) return;
    event.preventDefault();
    void closeSafely();
  });
  await window.loadURL('vates://app/index.html');
}

async function closeSafely() {
  if (quitting) return;
  quitting = true;
  try { if (controller) await controller.stop(); }
  catch (error) { dialog.showErrorBox('Ошибка остановки', error.message); }
  finally { app.quit(); }
}

app.on('before-quit', event => { if (!quitting && controller) { event.preventDefault(); void closeSafely(); } });
app.on('window-all-closed', () => app.quit());
