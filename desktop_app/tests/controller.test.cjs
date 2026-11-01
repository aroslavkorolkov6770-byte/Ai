'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs/promises');
const { fork } = require('node:child_process');
const { ApiController, portAvailable } = require('../api-controller.cjs');

const root = path.resolve(__dirname, '..', '..');
const runner = path.resolve(__dirname, '..', 'backend_runner.py');
async function fixture() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'vates-desktop-test-'));
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const controller = new ApiController({ projectRoot: root, runner, logDir: directory, port,
    extraEnv: { AI_MEMORY_DB_PATH: path.join(directory, 'test-conversations.sqlite3'), VATES_STARTUP_TRACE: '1' } });
  return { controller, port, directory };
}

async function waitFor(controller, predicate, timeout = 310000) {
  if (predicate(controller.snapshot())) return controller.snapshot();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { controller.removeListener('state', handler); reject(new Error(`Timeout: ${controller.detail}\n${controller.logs.join('\n')}`)); }, timeout);
    const handler = state => { if (predicate(state)) { clearTimeout(timer); controller.removeListener('state', handler); resolve(state); } };
    controller.on('state', handler);
  });
}

test('Занятый порт не затрагивает чужой сервер; повторный запуск возможен', async () => {
  const { controller, port } = await fixture();
  const foreign = net.createServer(socket => socket.end('foreign'));
  await new Promise(resolve => foreign.listen(port, '127.0.0.1', resolve));
  try {
    const state = await controller.start();
    assert.equal(state.phase, 'error');
    assert.match(state.detail, /занят/);
    assert.equal(state.pid, null);
    await controller.stop();
    assert.equal(await portAvailable(port), false);
    assert.equal(foreign.listening, true);
  } finally { await new Promise(resolve => foreign.close(resolve)); }
});

test('Отмена запуска во время проверки не создаёт процесс', async () => {
  const { controller } = await fixture();
  const starting = controller.start();
  await controller.stop();
  await starting;
  assert.equal(controller.phase, 'stopped');
  assert.equal(controller.child, null);
});

test('Отсутствующий проект показывает понятную ошибку', async () => {
  const { controller, directory } = await fixture();
  controller.projectRoot = path.join(directory, 'missing');
  const state = await controller.start();
  assert.equal(state.phase, 'error');
  assert.match(state.detail, /Не найдены/);
  await assert.rejects(controller.start(80), /1024/);
});

test('Настоящий API: готовность, один процесс, ответ, выключение и повторный запуск', { timeout: 660000 }, async () => {
  const { controller, port } = await fixture();
  try {
    await Promise.all([controller.start(), controller.start()]);
    const first = await waitFor(controller, state => ['running', 'degraded', 'error'].includes(state.phase));
    assert.equal(first.phase, 'running', first.detail + controller.logs.join('\n'));
    assert.ok(first.health.model_ready);
    const pid = first.pid;
    assert.equal((await controller.start()).pid, pid);
    const answer = await controller.generate({ prompt: 'Привет!', conversation_id: 'desktop-integration-test' });
    assert.ok(answer.text?.length > 0);
    assert.equal(answer.conversation_id, 'desktop-integration-test');
    const results = await Promise.all([controller.stop(), controller.stop()]);
    assert.equal(results[0].phase, 'stopped');
    assert.equal(controller.child, null);
    assert.equal(await portAvailable(port), true);
    await controller.start();
    const second = await waitFor(controller, state => ['running', 'degraded', 'error'].includes(state.phase));
    assert.equal(second.phase, 'running');
    assert.notEqual(second.pid, pid);
    await controller.stop();
    assert.equal(await portAvailable(port), true);
  } finally { await controller.stop(); }
});

test('Остановка в момент загрузки модели освобождает порт', { timeout: 35000 }, async () => {
  const { controller, port } = await fixture();
  await controller.start();
  assert.ok(controller.child);
  await controller.stop();
  assert.equal(controller.phase, 'stopped');
  assert.equal(await portAvailable(port), true);
});

test('Аварийное завершение приложения не оставляет свой API работающим', { timeout: 360000 }, async () => {
  const { port, directory } = await fixture();
  const owner = fork(path.join(__dirname, 'owner-fixture.cjs'), [String(port), directory], { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Не дождались готовности процесса для проверки аварии')), 310000);
      owner.on('message', message => {
        if (message.phase === 'running') { clearTimeout(timer); resolve(); }
        if (message.phase === 'error') { clearTimeout(timer); reject(new Error(message.error || 'API не запустился')); }
      });
    });
    assert.equal(await portAvailable(port), false);
    owner.send('simulate-owner-crash');
    const deadline = Date.now() + 25000;
    while (Date.now() < deadline && !(await portAvailable(port))) await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(await portAvailable(port), true, 'После аварии родителя порт должен освободиться');
  } finally { if (owner.exitCode === null) owner.kill(); }
});
