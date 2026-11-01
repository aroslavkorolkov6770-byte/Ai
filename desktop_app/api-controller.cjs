'use strict';
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const { randomUUID } = require('node:crypto');
const { access, mkdir, appendFile, stat, rename } = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');

// Проверка порта ничего не завершает: занятый порт остаётся владельцу.
async function portAvailable(port) {
  return new Promise(resolve => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)));
  });
}

class ApiController extends EventEmitter {
  constructor({ projectRoot, runner, logDir, port = 8000, extraEnv = {}, startupTimeout = 300000 }) {
    super();
    this.projectRoot = projectRoot;
    this.runner = runner;
    this.logDir = logDir;
    this.port = port;
    this.extraEnv = extraEnv;
    this.startupTimeout = startupTimeout;
    this.child = null;
    this.operation = 0;
    this.phase = 'stopped';
    this.detail = 'API выключен. Нажми «Включить API».';
    this.logs = [];
    this.health = null;
    this.instance = null;
    this.pollTimer = null;
    this.stopTask = null;
    this.logQueue = Promise.resolve();
  }

  snapshot() {
    return { phase: this.phase, detail: this.detail, port: this.port,
      address: `http://127.0.0.1:${this.port}`, pid: this.child?.pid || null,
      health: this.health, logs: [...this.logs], projectRoot: this.projectRoot };
  }

  publish() { this.emit('state', this.snapshot()); }

  log(message) {
    const line = `${new Date().toLocaleTimeString('ru-RU')} ${String(message).trim()}`;
    this.logs.push(line);
    this.logs = this.logs.slice(-180);
    // Последовательная запись и ограничение размера не забивают диск.
    this.logQueue = this.logQueue.then(async () => {
      try {
        await mkdir(this.logDir, { recursive: true });
        const file = path.join(this.logDir, 'api.log');
        const size = await stat(file).then(s => s.size).catch(() => 0);
        if (size > 2 * 1024 * 1024) await rename(file, path.join(this.logDir, 'api.previous.log'));
        await appendFile(file, line + '\n', 'utf8');
      } catch (error) {
        this.emit('log-warning', `Журнал не записан: ${error.message}`);
      }
    });
  }

  async start(port = this.port) {
    if (this.child || ['starting', 'stopping'].includes(this.phase)) return this.snapshot();
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Порт должен быть целым числом от 1024 до 65535.');
    this.port = port;
    const operation = ++this.operation;
    this.phase = 'starting';
    this.detail = 'Проверяем Python, файлы и порт…';
    this.health = null;
    this.publish();
    try {
      const python = path.join(this.projectRoot, '.venv', 'Scripts', 'python.exe');
      await Promise.all([access(python), access(path.join(this.projectRoot, 'ai_core', 'api_server.py')), access(this.runner)]);
      if (!(await portAvailable(port))) throw new Error(`Порт ${port} занят. Выбери другой порт или останови сервер в программе, которая его запустила. Чужой процесс не изменён.`);
      if (operation !== this.operation) return this.snapshot();
      this.instance = randomUUID();
      const child = spawn(python, ['-u', this.runner, '--project', this.projectRoot,
        '--port', String(port), '--instance', this.instance], {
        cwd: this.projectRoot, windowsHide: true, shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...this.extraEnv, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      });
      this.child = child;
      this.startedAt = Date.now();
      this.detail = 'Загружаем нейросеть. Обычно это занимает несколько секунд…';
      this.log(`Запуск API на 127.0.0.1:${port}.`);
      child.stdin.on('error', error => this.log(`Канал управления: ${error.message}`));
      for (const stream of [child.stdout, child.stderr]) {
        stream.setEncoding('utf8');
        stream.on('data', data => { this.log(data); this.publish(); });
      }
      child.once('error', error => { this.detail = `Не удалось запустить Python: ${error.message}`; this.log(this.detail); });
      child.once('close', code => {
        if (this.child !== child) return;
        clearTimeout(this.pollTimer);
        const expected = this.phase === 'stopping';
        this.child = null;
        this.health = null;
        this.phase = expected ? 'stopped' : 'error';
        this.detail = expected ? 'API выключен. Можно включить снова.' : `Процесс API завершился (код ${code}). Подробности в журнале.`;
        this.log(this.detail);
        this.publish();
      });
      this.publish();
      void this.poll(child);
    } catch (error) {
      if (operation === this.operation) {
        this.phase = 'error';
        this.detail = error.code === 'ENOENT' ? 'Не найдены файлы нейросети или Python. Проверь папку проекта в настройках.' : error.message;
        this.log(this.detail);
        this.publish();
      }
    }
    return this.snapshot();
  }

  async poll(child) {
    if (this.child !== child || this.phase === 'stopping') return;
    try {
      const response = await fetch(`http://127.0.0.1:${this.port}/health`, { signal: AbortSignal.timeout(1800) });
      if (!response.ok || response.headers.get('x-vates-instance') !== this.instance) throw new Error('Ожидаем ответ своего процесса.');
      const health = await response.json();
      if (this.child !== child || this.phase === 'stopping') return;
      this.health = health;
      const ready = health.status === 'ok' && health.model_ready && health.reliable_model_ready && health.conversation_mind_ready;
      this.phase = ready ? 'running' : 'degraded';
      this.detail = ready ? 'API включён. Нейросеть готова отвечать.' : 'API работает, но не все компоненты модели загрузились. Проверь журнал и состояние модели.';
    } catch (error) {
      if (this.child !== child || this.phase === 'stopping') return;
      if (Date.now() - this.startedAt > this.startupTimeout && !this.health) {
        this.log('Истекло время запуска. Останавливаем только свой процесс.');
        await this.stop();
        this.phase = 'error'; this.detail = `API не стал готов за ${Math.round(this.startupTimeout / 1000)} секунд. Проверь журнал и попробуй ещё раз.`;
        this.publish();
        return;
      }
      if (this.health) { this.phase = 'unresponsive'; this.detail = 'Процесс работает, но сейчас не отвечает на проверку. Возможно, занят запросом. Можно подождать или выключить API.'; }
    }
    this.publish();
    if (this.child === child && this.phase !== 'stopping') this.pollTimer = setTimeout(() => void this.poll(child), 2000);
  }

  async stop() {
    if (this.stopTask) { await this.stopTask; return this.snapshot(); }
    ++this.operation; // Отменяет запуск, если тот ещё проверяет файлы.
    clearTimeout(this.pollTimer);
    const child = this.child;
    if (!child) {
      this.phase = 'stopped'; this.health = null; this.detail = 'API выключен.'; this.publish();
      return this.snapshot();
    }
    this.phase = 'stopping'; this.detail = 'Завершаем запросы и выключаем API…'; this.publish();
    this.stopTask = new Promise(resolve => {
      const deadline = setTimeout(() => {
        // Объект ChildProcess относится только к запущенному нами процессу.
        // Процессы по имени, порту или найденному стороннему PID не завершаются.
        if (this.child === child && child.exitCode === null) { this.log('Срок мягкой остановки истёк; завершаем свой процесс.'); child.kill(); }
      }, 17000);
      child.once('close', () => { clearTimeout(deadline); resolve(); });
      try { child.stdin.end('stop\n'); } catch (error) { this.log(`Остановка: ${error.message}`); }
    });
    await this.stopTask;
    this.stopTask = null;
    return this.snapshot();
  }

  async generate(input) {
    if (!this.child || !['running', 'degraded'].includes(this.phase)) throw new Error('Сначала включи API и дождись готовности.');
    if (typeof input?.prompt !== 'string' || !input.prompt.trim() || input.prompt.length > 2000) throw new Error('Сообщение должно содержать от 1 до 2000 символов.');
    const response = await fetch(`http://127.0.0.1:${this.port}/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.timeout(90000),
      body: JSON.stringify({ prompt: input.prompt, conversation_id: typeof input.conversation_id === 'string' ? input.conversation_id.slice(0,100) : null, response_mode: 'auto', use_conversation_context: true }),
    });
    if (!response.ok) throw new Error(`API вернул ошибку ${response.status}. Проверь журнал.`);
    if (response.headers.get('x-vates-instance') !== this.instance) throw new Error('Ответ получен не от ожидаемого экземпляра API.');
    return response.json();
  }
}

module.exports = { ApiController, portAvailable };
