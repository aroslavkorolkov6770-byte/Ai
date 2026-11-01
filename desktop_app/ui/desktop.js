'use strict';

// Настольный слой использует оформление макета, но реальные операции идут
// через узкий мост Electron. Демонстрационный HTML остаётся отдельным файлом.
const desktop = window.vatesDesktop;
let live = { phase: 'stopped', detail: 'Проверяем состояние приложения…', port: 8000, address: 'http://127.0.0.1:8000', logs: [], health: null, pid: null, projectRoot: '' };
let portDraft = '8000';
let sending = false;
const phaseText = { stopped: 'Выключен', starting: 'Запускается', running: 'Включён', stopping: 'Выключается', error: 'Ошибка', degraded: 'Нужна проверка', unresponsive: 'Нет ответа' };
delete pageNames.library;
if (state.page === 'library') state.page = 'chat';

async function callDesktop(operation) {
  const result = await operation;
  if (!result?.ok) throw new Error(result?.error || 'Не удалось связаться с приложением.');
  return result.value;
}

function renderPreservingInput() {
  if ($('#prompt')) draftText = $('#prompt').value;
  if ($('#api-port')) portDraft = $('#api-port').value;
  render();
}

function acceptState(next) {
  const changed = live.phase !== next.phase || live.pid !== next.pid || live.projectRoot !== next.projectRoot;
  live = next; state.api = next.phase; state.apiLogs = next.logs;
  if (changed && ['chat', 'api', 'versions', 'settings'].includes(state.page)) renderPreservingInput();
  if ($('#live-log')) $('#live-log').textContent = live.logs.join('\n') || 'Журнал появится после запуска API.';
  if ($('#live-detail')) $('#live-detail').textContent = live.detail;
}

footer = () => '<footer class="footer"><span>Ватэс AI · локальное приложение</span><span>Закрытие приложения выключает запущенный им API</span></footer>';

function liveCard() {
  return `<div class="card"><div class="row between"><span class="status-icon">${icon('server')}</span><span class="pill ${live.phase === 'running' ? 'good' : ''}">${phaseText[live.phase]}</span></div><h2>На этом ноутбуке</h2><p class="muted live-detail">${escapeHtml(live.detail)}</p><div class="stat-row"><span>Адрес API</span><span class="tiny">${escapeHtml(live.address)}</span></div><div class="stat-row"><span>Устройство</span><span>${escapeHtml(live.health?.device || '—')}</span></div><button class="btn primary" data-page="api">Управлять API${icon('arrow')}</button></div>`;
}

chatView = function () {
  const chat = state.chats.find(c => c.id === state.activeChat);
  const ready = ['running', 'degraded'].includes(live.phase);
  const messages = chat?.messages.length ? `<div class="row between"><h2>${escapeHtml(chat.title)}</h2><button class="btn small" id="live-export-chat">${icon('upload')}Экспорт</button></div><div class="message-list">${chat.messages.map((m, i) => `<article class="message ${m.role}"><div class="message-meta">${m.role === 'user' ? 'Ты' : escapeHtml(state.settings.name)}</div><div class="message-text">${escapeHtml(m.text)}</div>${m.role === 'assistant' ? `<button class="btn small ghost" data-copy-message="${i}">${icon('copy')}Копировать</button>` : ''}</article>`).join('')}</div>` : `<div class="welcome"><svg class="greeting-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M24 4v40M4 24h40M10 10l28 28M10 38l28-28" stroke="currentColor" stroke-width="2.3"/><circle cx="24" cy="24" r="13" stroke="currentColor" stroke-width="1.4"/></svg><div class="eyebrow">Личный ИИ. На твоей стороне.</div><h1>Место, где идеи<br>становятся <em>делом.</em></h1><p>Твоя нейросеть, твой ноутбук.<br>Включи API — и можно начинать разговор.</p></div>`;
  return `<div class="home-layout"><section>${messages}<form class="composer" id="chat-form"><textarea id="prompt" aria-label="Сообщение для Ватэс" maxlength="2000" placeholder="О чём подумаем вместе?">${escapeHtml(draftText)}</textarea><div class="composer-bottom"><span class="muted tiny">${sending ? 'Нейросеть готовит ответ…' : 'До 2 000 символов · Enter — отправить'}</span><button class="send" type="submit" aria-label="Отправить сообщение" ${!ready || sending ? 'disabled' : ''}>${icon('up')}</button></div></form><p class="hint">${ready ? 'Ответы поступают от твоей нейросети. В режиме auto она может обращаться к интернету для актуальных вопросов.' : 'API выключен или запускается. Открой управление справа.'}</p>${!chat ? `<div class="suggestions"><button class="suggestion" data-prompt="Привет!">${icon('chat')}<strong>Начать разговор</strong><span>Познакомиться с помощником</span></button><button class="suggestion" data-prompt="Что такое HTML?">${icon('code')}<strong>Разобраться в сложном</strong><span>Задать вопрос по разработке</span></button><button class="suggestion" data-page="research">${icon('grid')}<strong>Посмотреть идеи</strong><span>Обзор 50 ИИ-продуктов</span></button></div>` : ''}</section><aside class="rail">${liveCard()}<div class="card dark"><span class="pill">Развитие Ватэс</span><h2>Проверять каждый шаг</h2><p class="muted">Обучение остаётся отдельным процессом с проверкой данных и качества.</p><button class="btn" data-page="training">План обучения${icon('arrow')}</button></div></aside></div>`;
};

apiView = function () {
  const busy = ['starting', 'stopping'].includes(live.phase);
  return head('Центр управления', 'Твоя нейросеть. Под твоим контролем.', 'Включай API перед работой и выключай, когда закончишь. Терминал открывать не нужно.') + `<div class="grid-2"><div class="card"><div class="row between"><h2>Локальный API</h2><span class="pill ${live.phase === 'running' ? 'good' : ''}">${phaseText[live.phase]}</span></div><p class="live-detail" id="live-detail" role="status">${escapeHtml(live.detail)}</p><label class="label" for="api-port">Порт</label><input class="field" id="api-port" type="number" min="1024" max="65535" value="${escapeHtml(portDraft)}" ${live.pid || busy ? 'disabled' : ''}><div class="stat-row"><span>Адрес подключения</span><strong class="tiny">${escapeHtml(live.address)}</strong></div><div class="stat-row"><span>Процесс приложения</span><strong>${live.pid || 'Не запущен'}</strong></div><div class="stat-row"><span>Доступ</span><span>Только этот ноутбук</span></div><div class="row wrap" style="margin-top:20px"><button class="btn primary" id="api-start" ${live.pid || busy ? 'disabled' : ''}>${icon('play')}Включить API</button><button class="btn" id="api-stop" ${(!live.pid && live.phase !== 'starting') || live.phase === 'stopping' ? 'disabled' : ''}>Выключить API</button><button class="btn small" id="live-copy-address">Копировать адрес</button></div><p class="muted tiny" style="margin-top:16px">Повторное открытие ярлыка вернёт это окно. При закрытии приложения его API выключится. Свёрнутое окно продолжает работать.</p></div><div class="card"><div class="row between"><h2>Журнал работы</h2><button class="btn small" id="live-open-logs">Открыть папку</button></div><pre class="log live-detail" id="live-log" role="log">${escapeHtml(live.logs.join('\n') || 'Журнал появится после запуска API.')}</pre><p class="muted tiny">Занятый порт не освобождается принудительно. Чужие процессы остаются нетронутыми.</p></div></div>`;
};

startApi = async function () {
  try { portDraft = $('#api-port')?.value || portDraft; acceptState(await callDesktop(desktop.start(Number(portDraft)))); }
  catch (error) { toast(error.message); }
};
stopApi = async function () {
  try { acceptState(await callDesktop(desktop.stop())); } catch (error) { toast(error.message); }
};

sendMessage = async function (event) {
  event.preventDefault();
  if (sending) return;
  const prompt = $('#prompt').value.trim();
  if (!prompt) return;
  if (!['running', 'degraded'].includes(live.phase)) return toast('Сначала включи API.');
  let chat = state.chats.find(c => c.id === state.activeChat);
  if (!chat) { chat = { id: crypto.randomUUID(), title: prompt.slice(0,55), messages: [] }; state.chats.unshift(chat); state.chats = state.chats.slice(0,30); state.activeChat = chat.id; }
  sending = true; chat.messages.push({ role: 'user', text: prompt }); draftText = ''; persist(); render();
  try {
    const answer = await callDesktop(desktop.generate({ prompt, conversation_id: chat.id }));
    chat.messages.push({ role: 'assistant', text: answer.text });
    chat.messages = chat.messages.slice(-80); persist();
  } catch (error) {
    // Не выдаём сетевую ошибку за ответ модели и не повторяем запрос скрыто.
    toast(`Ответ не получен: ${error.message}`);
    if (state.activeChat === chat.id && !$('#prompt')?.value) draftText = prompt;
  } finally {
    sending = false;
    if (state.page === 'chat') { if ($('#prompt')?.value) draftText = $('#prompt').value; render(); }
  }
};

trainingView = () => head('Развитие нейросети', 'Обучение — после проверки данных', 'В этой версии приложения обучение не запускается. Работающий API и веса защищены от случайного изменения.') + `<div class="card"><h2>План настоящего обучения</h2><ol><li>Выбрать навык, который нужно улучшить.</li><li>Проверить происхождение примеров, качество и повторы.</li><li>Отделить обучение от независимых проверочных вопросов.</li><li>Создать кандидата в отдельной папке, сохранив текущую модель.</li><li>Сравнить ответы и проверить, не потеряны ли прежние навыки.</li><li>Применить только принятую версию с возможностью отката.</li></ol><p class="muted">Существующие ограничения проекта на качество и ручную проверку остаются в силе. История чата автоматически не превращается в обучающие данные.</p></div>`;
versionsView = () => head('Состояние модели', 'Что действительно загружено', 'Сведения получены от /health твоего API. Версии здесь не переключаются.') + `<div class="card"><h2>${phaseText[live.phase]}</h2>${live.health ? `<div class="stat-row"><span>Основная модель</span><span>${escapeHtml(live.health.model_version)}</span></div><div class="stat-row"><span>Надёжные ответы</span><span>${escapeHtml(live.health.reliable_model_version || 'Не загружены')}</span></div><div class="stat-row"><span>Разговорный слой</span><span>${escapeHtml(live.health.conversation_mind_version || 'Не загружен')}</span></div><div class="stat-row"><span>Устройство</span><span>${escapeHtml(live.health.device)}</span></div><details><summary>Техническое состояние</summary><pre class="code live-detail">${escapeHtml(JSON.stringify(live.health,null,2))}</pre></details>` : '<p>Включи API, чтобы увидеть загруженные компоненты.</p>'}</div>`;

const originalSettingsView = settingsView;
settingsView = () => head('Настройки приложения', 'Подключение к собственной нейросети', 'Программа использует актуальные файлы проекта, не создавая вторую копию весов.') + `<div class="card"><h2>Папка проекта</h2><p class="live-detail">${escapeHtml(live.projectRoot)}</p><button class="btn" id="live-choose-project" ${live.pid || live.phase === 'starting' ? 'disabled' : ''}>Выбрать другую папку</button><p class="muted tiny">В папке должны находиться ai_core и .venv. Менять путь можно при выключенном API.</p></div>` + originalSettingsView().replace('Только параметры макета. Они не меняют конфигурацию рабочей нейросети.', 'Имя в интерфейсе, тема и сохранение истории.').replace('Границы прототипа', 'Хранение переписки').replace('Чат использует подготовленные ответы. Запуск API, обучение и применение версий имитируются. Просмотр TXT/MD, проверка структуры JSON/JSONL, поиск и экспорт действительно работают в браузере.', 'Чат подключён к реальному API. Эта настройка управляет историей в приложении. Сервер отдельно сохраняет диалоги в своей SQLite-базе; отключение истории интерфейса не удаляет записи сервера.').replaceAll('демо-диалоги', 'диалоги').replaceAll('хранилища макета', 'хранилища приложения');

const originalBindPage = bindPage;
bindPage = function () {
  originalBindPage();
  const bind = (id, handler) => { const node = $(id); if (node) node.addEventListener('click', handler); };
  bind('#live-copy-address', () => copyText(live.address));
  bind('#live-open-logs', async () => { try { await callDesktop(desktop.openLogs()); } catch (error) { toast(error.message); } });
  bind('#live-choose-project', async () => { try { acceptState(await callDesktop(desktop.chooseProject())); } catch (error) { toast(error.message); } });
  bind('#live-export-chat', () => { const chat = state.chats.find(c => c.id === state.activeChat); download('vates-dialogue.md', `# ${chat.title}\n\n` + chat.messages.map(m => `## ${m.role === 'user' ? 'Пользователь' : 'Ватэс'}\n\n${m.text}`).join('\n\n')); });
};

async function initializeDesktop() {
  try {
    if (!desktop) throw new Error('Управление API доступно только в настольном приложении Ватэс AI.');
    live = await callDesktop(desktop.status()); portDraft = String(live.port); desktop.onState(acceptState); render();
  } catch (error) { live.detail = error.message; render(); toast(error.message); }
}
void initializeDesktop();
