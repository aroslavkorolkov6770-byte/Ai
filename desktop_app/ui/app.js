'use strict';
    // Сценарии макета не запускают процессы и не обращаются к рабочим весам модели.
    const APP_KEY = 'vates-ai-desktop-v1';
    const $ = (selector, root = document) => root.querySelector(selector);
    const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
    const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const state = {
      page: 'chat', theme: 'light', chats: [], activeChat: null, files: [],
      api: 'stopped', apiLogs: ['[макет] Сервер не запущен. Нажмите кнопку демонстрации.'],
      training: { phase: 'empty', records: [], errors: [], source: '', progress: 0, epoch: 0, epochs: 5, candidate: null },
      versions: [{ id: 'demo-base', name: 'Базовая версия · пример', created: 'Исходное состояние макета', score: '—', active: true }],
      settings: { name: 'Ватэс', remember: true }, researchFilter: 'Все', researchSearch: '', favorites: []
    };
    let timer = null;
    let apiTimer = null;
    let toastTimer = null;
    let draftText = '';
    let attachment = null;
    const research = JSON.parse($('#research-data').textContent);
    const pageNames = {chat:'Чат с Ватэс', library:'Мои материалы', api:'Локальный API', training:'Обучение', versions:'Версии модели', research:'Обзор ИИ', settings:'Настройки'};

    // Браузер может запретить localStorage для file://. Тогда макет продолжает работать в памяти.
    function restore() {
      try {
        const saved = JSON.parse(localStorage.getItem(APP_KEY) || 'null');
        if (!saved || typeof saved !== 'object') return;
        state.theme = saved.theme === 'dark' ? 'dark' : 'light';
        if (saved.settings && typeof saved.settings.name === 'string') state.settings = {name:saved.settings.name.slice(0,30), remember:saved.settings.remember !== false};
        if (state.settings.remember && Array.isArray(saved.chats)) {
          state.chats = saved.chats.filter(c => typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.messages)).slice(0,30).map(c => ({id:c.id,title:c.title.slice(0,80),messages:c.messages.filter(m => typeof m.text === 'string' && ['user','assistant'].includes(m.role)).slice(-80).map(m => ({role:m.role,text:m.text.slice(0,10000),feedback:m.feedback === true}))}));
        }
        state.favorites = Array.isArray(saved.favorites) ? saved.favorites.filter(n => Number.isInteger(n)) : [];
      } catch (error) { toast('История недоступна. Можно продолжить без сохранения.'); }
    }
    function persist() {
      try { localStorage.setItem(APP_KEY, JSON.stringify({theme:state.theme, settings:state.settings, chats:state.settings.remember ? state.chats.slice(0,30) : [], favorites:state.favorites})); }
      catch (error) { toast('Браузер не сохранил историю. Экспортируйте важный диалог.'); }
    }
    function toast(text) {
      clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').classList.remove('hidden');
      toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 4300);
    }
    function dialog(title, html) { $('#dialog-title').textContent = title; $('#dialog-body').innerHTML = html; $('#info-dialog').showModal(); }
    function footer() { return '<footer class="footer"><span>Ватэс AI · своё пространство, свой ритм</span><span>Прототип интерфейса / 01.10.2026</span></footer>'; }
    function demoBanner(text) { return `<div class="demo-banner">${icon('spark')} ${text}</div>`; }
    function head(label, title, text) { return `<div class="page-head"><div class="eyebrow">${label}</div><h1>${title}</h1><p>${text}</p></div>`; }
    function navigate(page, preserveDraft = true) {
      if (!pageNames[page]) page = 'chat';
      const composer = $('#prompt'); if (composer && preserveDraft) draftText = composer.value;
      state.page = page; location.hash = page;
      document.body.classList.remove('menu-open'); $('#menu-toggle').setAttribute('aria-expanded','false'); render();
    }
    function renderHistory() {
      $('#history').innerHTML = state.chats.length ? state.chats.slice(0,5).map(c => `<button data-chat="${escapeHtml(c.id)}" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</button>`).join('') : '<span class="muted tiny" style="padding:7px 12px">Здесь появятся твои диалоги</span>';
    }
    function render() {
      document.documentElement.dataset.theme = state.theme;
      $('#breadcrumb').textContent = pageNames[state.page];
      document.querySelectorAll('[data-page]').forEach(button => { const active = button.dataset.page === state.page; button.classList.toggle('active',active); if (active) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
      const views = {chat:chatView, library:libraryView, api:apiView, training:trainingView, versions:versionsView, research:researchView, settings:settingsView};
      $('#page-content').innerHTML = views[state.page]() + footer();
      renderHistory(); bindPage();
    }
    function chatView() {
      const chat = state.chats.find(c => c.id === state.activeChat);
      const messages = chat && chat.messages.length ? `<div class="row between"><h2>${escapeHtml(chat.title)}</h2><button class="btn small" id="export-chat">${icon('upload')}Экспорт</button></div><div class="message-list">${chat.messages.map((m,i) => `<article class="message ${m.role}"><div class="message-meta">${m.role === 'user' ? 'Ты' : escapeHtml(state.settings.name) + ' · демонстрационный ответ'}</div><div class="message-text">${escapeHtml(m.text)}</div>${m.role === 'assistant' ? `<div class="message-actions"><button class="btn small ghost" data-copy-message="${i}">${icon('copy')}Копировать</button><button class="btn small ghost" data-feedback="${i}">${icon('check')}${m.feedback ? 'Отмечено' : 'Полезно'}</button></div>` : ''}</article>`).join('')}</div>` : `<div class="welcome"><svg class="greeting-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M24 4v40M4 24h40M10 10l28 28M10 38l28-28" stroke="currentColor" stroke-width="2.3"/><circle cx="24" cy="24" r="13" stroke="currentColor" stroke-width="1.4"/></svg><div class="eyebrow">Личный ИИ. На твоей стороне.</div><h1>Место, где идеи<br>становятся <em>делом.</em></h1><p>Обсуди задачу, собери знания и развивай своего помощника.<br>Начнём с того, что важно тебе сегодня.</p></div>`;
      return `<div class="home-layout"><section>${messages}<form class="composer" id="chat-form"><label for="prompt" class="hidden">Сообщение для Ватэс</label><textarea id="prompt" aria-label="Сообщение для Ватэс" maxlength="6000" placeholder="О чём подумаем вместе?">${escapeHtml(draftText)}</textarea>${attachment ? `<div class="file-chip">${icon('file')}${escapeHtml(attachment.name)}<button type="button" class="btn small ghost" id="remove-attachment" aria-label="Убрать вложение">×</button></div>` : ''}<div class="composer-bottom"><div class="row"><button class="btn square ghost" type="button" id="attach-button" aria-label="Прикрепить текстовый файл">${icon('plus')}</button><select id="chat-mode" aria-label="Режим ответа"><option value="general">Обычный разговор</option><option value="explain">Объясни просто</option><option value="plan">Составь план</option></select></div><div class="row"><span class="pill good">Демо</span><button class="send" type="submit" aria-label="Отправить сообщение">${icon('up')}</button></div></div><input type="file" id="chat-file" accept=".txt,.md" hidden></form><p class="hint">Сейчас ответы подготовлены заранее. Реальная нейросеть не подключена.</p>${chat ? '' : `<div class="section-title"><h2>С чего начнём?</h2><span class="muted tiny">Выбери направление</span></div><div class="suggestions"><button class="suggestion" data-prompt="Объясни простыми словами, как работает API">${icon('code')}<strong>Разобраться в сложном</strong><span>Объяснение без лишних терминов</span></button><button class="suggestion" data-prompt="Помоги составить план обучения моей нейросети">${icon('learn')}<strong>Наметить следующий шаг</strong><span>От идеи до понятного плана</span></button><button class="suggestion" data-prompt="Какие идеи из обзора 50 ИИ взять в Ватэс?">${icon('grid')}<strong>Найти хорошую идею</strong><span>Лучшее из исследования ИИ</span></button></div>`}</section><aside class="rail"><div class="card"><div class="row between"><span class="status-icon">${icon('server')}</span><span class="pill">Демо-статус</span></div><h2>На этом ноутбуке</h2><p class="muted">Твой будущий центр управления нейросетью.</p><div class="stat-row"><span class="muted">Локальный API</span><span>${state.api === 'running' ? 'Запущен · демо' : state.api === 'starting' ? 'Запускается · демо' : 'Остановлен'}</span></div><div class="stat-row"><span class="muted">Активная модель</span><span>Не подключена</span></div><div class="stat-row"><span class="muted">Подключение</span><span>Только макет</span></div><button class="btn" data-page="api">Открыть управление${icon('arrow')}</button></div><div class="card dark"><span class="pill">Лаборатория Ватэс</span><svg class="mini-art" viewBox="0 0 220 100" fill="none" aria-hidden="true"><path d="M15 78 60 48 110 67 155 28 205 15M15 40l45 8 50-25 45 5 50 53M60 48v37m50-62v44m45-39v57" stroke="#afc79c" stroke-width="1" opacity=".65"/><circle cx="15" cy="78" r="4" fill="#d6e8b5"/><circle cx="60" cy="48" r="7" fill="#d6e8b5"/><circle cx="110" cy="67" r="5" fill="#d6e8b5"/><circle cx="155" cy="28" r="8" fill="#d6e8b5"/><circle cx="205" cy="15" r="4" fill="#d6e8b5"/><circle cx="110" cy="23" r="4" fill="#7aa585"/></svg><h2>Лучше с каждым шагом</h2><p class="muted">Посмотри, как данные превращаются в новую версию помощника.</p><button class="btn" data-page="training">Попробовать обучение${icon('arrow')}</button></div></aside></div>`;
    }
    function apiView() {
      const labels = {stopped:'Остановлен',starting:'Проверка запуска',running:'Работает в демонстрации'};
      return head('Подключение','ИИ, доступный твоим приложениям','Один экран для запуска, проверки состояния и понятной диагностики.') + demoBanner('HTML не может запускать Python-процессы. Здесь можно пройти сценарий запуска; настоящий API не включается.') + `<div class="grid-2"><div class="card"><div class="row between"><span class="status-icon">${icon('server')}</span><span class="pill ${state.api === 'running' ? 'good' : ''}"><span class="dot"></span>${labels[state.api]}</span></div><h2>Локальный сервер</h2><p class="muted tiny">Сначала проверка файлов, затем загрузка модели и проверка готовности.</p><label class="label" for="api-address">Пример адреса подключения</label><div class="row"><input id="api-address" class="field mono" value="http://127.0.0.1:8000" readonly><button class="btn square" id="copy-address" aria-label="Скопировать пример адреса">${icon('copy')}</button></div><div class="stat-row"><span>Доступ</span><strong>Только этот ноутбук</strong></div><div class="stat-row"><span>Состояние настоящего API</span><span class="muted">Макетом не проверяется</span></div><div class="spacer"></div><div class="row wrap"><button class="btn primary" id="api-start" ${state.api !== 'stopped' ? 'disabled' : ''}>${icon('play')}Показать запуск API</button><button class="btn" id="api-stop" ${state.api === 'stopped' ? 'disabled' : ''}>Остановить демо</button></div></div><div class="card"><div class="section-title"><h2>Журнал запуска</h2><button class="btn small ghost" id="export-log">Экспорт</button></div><div class="log" role="log">${escapeHtml(state.apiLogs.join('\n'))}</div><div class="spacer"></div><button class="btn" id="api-error">Показать ошибку порта</button><details><summary>Что появится в полноценном приложении</summary>Фоновый запуск Python, проверка /health, управление портом и процессом, журнал с настоящими ошибками. Кнопка «Запущен» станет доступна только после проверки модели.</details></div></div>`;
    }
    function libraryView() {
      return head('Личная база','Материалы под рукой','Добавь небольшой текстовый файл. В макете он читается локально и не отправляется в обучение.') + `<div class="row between wrap"><button class="btn primary" id="library-add">${icon('plus')}Добавить материал</button><span class="muted tiny">TXT / Markdown · до 1 МБ · только текущая сессия</span><input type="file" id="library-file" accept=".txt,.md" hidden></div><div class="spacer"></div>${state.files.length ? `<div class="grid-3">${state.files.map((f,i) => `<article class="card"><span class="status-icon">${icon('file')}</span><h3>${escapeHtml(f.name)}</h3><p class="muted tiny">${(f.size/1024).toFixed(1)} КБ · ${f.text.length} символов</p><button class="btn small" data-file-preview="${i}">Посмотреть текст</button></article>`).join('')}</div>` : `<div class="card empty">${icon('folder')}<h2 style="margin-top:15px">Твои знания начинаются здесь</h2><p>Заметка, инструкция или описание проекта — добавь первый материал.</p></div>`}<div class="spacer"></div><div class="card soft"><h3>Знания и обучение — разные вещи</h3><p class="muted tiny" style="margin:0">Материал в библиотеке сам по себе не меняет веса нейросети. В будущей версии ответы по документам будут ссылаться на конкретный фрагмент источника.</p></div>`;
    }
    function trainingView() {
      const t = state.training;
      const phaseIndex = {empty:0,loaded:0,invalid:0,ready:1,running:2,paused:2,complete:3}[t.phase];
      const status = {empty:'Нужны примеры',loaded:'Нужна проверка',invalid:'Исправь ошибки',ready:'Готово к демонстрации',running:'Идёт симуляция',paused:'Симуляция на паузе',complete:'Демонстрация завершена'}[t.phase];
      return head('Лаборатория','Обучение, которое понятно','От подготовки примеров до новой версии. Рабочая модель всегда остаётся отдельно.') + demoBanner('Все проценты и результаты обучения ниже — симуляция. Файлы проекта и настоящие веса не изменяются.') + `<div class="steps">${['Подготовить данные','Проверить примеры','Обучить кандидата','Сравнить и применить'].map((s,i) => `<div class="step ${i <= phaseIndex ? 'active' : ''}">0${i+1}<b>${s}</b></div>`).join('')}</div><div class="grid-2"><div class="card"><h2>Обучающие примеры</h2><p class="muted tiny">Для просмотра сценария достаточно встроенного набора.</p><div class="dropzone">${icon('upload')}<h3>${t.source ? escapeHtml(t.source) : 'Выбери JSON или JSONL'}</h3><p class="muted tiny">Поля каждой записи: prompt и completion.<br>До 2 МБ, до 5 000 примеров.</p><input type="file" id="dataset-file" accept=".json,.jsonl" aria-label="Выбрать набор обучения" ${['running','paused'].includes(t.phase) ? 'disabled' : ''}></div><div class="row wrap" style="margin-top:16px"><button class="btn" id="sample-data" ${['running','paused'].includes(t.phase) ? 'disabled' : ''}>Взять демо-набор</button><button class="btn primary" id="validate-data" ${!['loaded','invalid','ready'].includes(t.phase) ? 'disabled' : ''}>Проверить данные</button></div>${t.records.length ? `<div class="stat-row"><span>Примеры</span><strong>${t.records.length}</strong></div>` : ''}${t.errors.length ? `<div class="notice-error" role="alert">${t.errors.map(escapeHtml).join('<br>')}</div>` : ''}${t.phase === 'ready' || phaseIndex >= 2 ? `<p class="tiny" style="color:var(--green);margin-top:16px">${icon('check')} Структура проверена; пустых и повторяющихся вопросов нет.</p>` : ''}<details><summary>Границы этой проверки</summary>Это проверка формата, пустых строк и точных дублей после нормализации. Она не оценивает качество ответов, права на данные и смысловые повторы. В реальном обучении нужны отдельные train / validation / test, проверка качества и действующие ограничения проекта.</details></div><div class="card"><div class="section-title"><h2>Новый эксперимент</h2><span class="pill good">${status}</span></div><label class="label" for="epochs">Количество учебных проходов</label><select class="field" id="epochs" ${['running','paused'].includes(t.phase) ? 'disabled' : ''}>${[3,5,10].map(n => `<option value="${n}" ${t.epochs === n ? 'selected' : ''}>${n} проходов · демонстрация</option>`).join('')}</select><div class="progress-wrap"><div class="row between tiny"><span id="train-status">${status}</span><strong id="train-percent">${t.progress}%</strong></div><progress id="train-progress" max="100" value="${t.progress}" aria-label="Прогресс симуляции"></progress><div class="row between muted tiny"><span id="train-epoch">Проход ${t.epoch} из ${t.epochs}</span><span>Демо-данные</span></div></div><svg class="chart" viewBox="0 0 420 150" role="img" aria-label="Иллюстрация снижения ошибки; не настоящие метрики"><path d="M30 20h375M30 65h375M30 110h375" stroke="var(--line)" stroke-dasharray="4 5"/><path d="M30 28C70 30 75 78 125 78S165 104 215 107s85 16 185 18" stroke="currentColor" stroke-width="2.3" fill="none"/><path d="M30 38C60 40 80 63 125 67s50 24 95 25 85 14 180 12" stroke="#ad8a63" stroke-width="1.7" fill="none" stroke-dasharray="5 5"/><text x="30" y="147" fill="var(--muted)" font-size="10">Иллюстрация: обучение / проверка</text></svg><div class="row wrap"><button class="btn primary" id="train-start" ${!['ready','paused'].includes(t.phase) ? 'disabled' : ''}>${icon('play')}${t.phase === 'paused' ? 'Продолжить симуляцию' : 'Запустить симуляцию'}</button><button class="btn" id="train-pause" ${t.phase !== 'running' ? 'disabled' : ''}>Пауза</button>${t.phase === 'complete' ? '<button class="btn accent" data-page="versions">Посмотреть кандидата</button>' : ''}</div>${t.phase === 'paused' ? '<button class="btn small ghost" id="train-reset" style="margin-top:12px">Завершить этот эксперимент</button>' : ''}<p class="muted tiny" style="margin-top:15px;margin-bottom:0">Прогресс симуляции хранится только до закрытия страницы. Веса не создаются.</p></div></div>`;
    }
    function versionsView() {
      return head('История развития','Каждый шаг можно проверить','Кандидат сначала сравнивается с рабочей версией, а затем применяется отдельно.') + demoBanner('Это версии макета. Применение и откат меняют только демонстрационный статус в текущей сессии.') + `<div class="card"><div class="table-wrap"><table><thead><tr><th>Версия</th><th>Состояние</th><th>Демо-оценка</th><th>Действие</th></tr></thead><tbody>${state.versions.map(v => `<tr><td><div class="version-name">${escapeHtml(v.name)}</div><span class="muted tiny">${escapeHtml(v.created)}</span></td><td><span class="pill ${v.active ? 'good' : ''}">${v.active ? 'Активна в макете' : 'Демо-кандидат'}</span></td><td>${escapeHtml(v.score)}</td><td>${v.active ? '<span class="muted">Используется</span>' : `<button class="btn small" data-apply="${escapeHtml(v.id)}">${v.id === 'demo-base' ? 'Вернуть в макете' : 'Сравнить и применить'}</button>`}</td></tr>`).join('')}</tbody></table></div></div><div class="spacer"></div><div class="grid-2"><div class="card"><h3>Рабочая версия остаётся целой</h3><p class="muted tiny">Новые веса должны сохраняться в отдельную папку. Неудачное обучение не должно лишать тебя работающего помощника.</p><button class="btn" data-page="training">Создать демо-кандидата${icon('arrow')}</button></div><div class="card"><h3>Качество — больше одного процента</h3><p class="muted tiny">Для настоящего выбора нужны ответы на одинаковые независимые вопросы, проверка потери старых навыков и оценка человеком. Пример «92%» в макете не относится к нашей нейросети.</p></div></div>`;
    }
    function researchView() {
      return head('Исследование / 50 продуктов','Смотрим шире. Выбираем осмысленно.','Что можно перенести в Ватэс: ясный чат, работа с материалами, проверяемые результаты и управление своим ИИ.') + `<div class="demo-banner">Выборка: 50 веб-продуктов a16z, публикация 09.03.2026, трафик за январь 2026. Это не рейтинг качества. Номера карточек — алфавитный порядок, не места в рейтинге. <a href="https://a16z.com/100-gen-ai-apps-6/" target="_blank" rel="noopener noreferrer">Методика и список</a></div><div class="row wrap between"><input class="field" id="research-search" aria-label="Поиск по обзору ИИ" placeholder="Найти продукт, функцию или идею…" value="${escapeHtml(state.researchSearch)}" style="max-width:420px"><span class="muted tiny" id="research-count"></span></div><div class="tabs" id="research-tabs">${['Все','Ассистенты','Работа','Медиа','Персонажи','Избранное'].map(c => `<button class="${state.researchFilter === c ? 'active' : ''}" data-filter="${c}">${c}</button>`).join('')}</div><div class="research-grid" id="research-results"></div><div class="spacer"></div><p class="muted tiny">Функции описаны по открытым официальным материалам. Закрытые и платные сценарии не тестировались. У каждого продукта указано, что удалось посмотреть. Предложения для Ватэс — наш вывод, а не обещания производителя.</p>`;
    }
    function renderResearchResults() {
      const query = state.researchSearch.trim().toLocaleLowerCase('ru');
      const filtered = research.filter(p => (state.researchFilter === 'Все' || (state.researchFilter === 'Избранное' ? state.favorites.includes(p.id) : p.category === state.researchFilter)) && `${p.name} ${p.feature} ${p.idea}`.toLocaleLowerCase('ru').includes(query));
      $('#research-count').textContent = `${filtered.length} из ${research.length} продуктов`;
      $('#research-results').innerHTML = filtered.length ? filtered.map(p => `<article class="card research-card"><div class="row between"><h3>${String(p.id).padStart(2,'0')} / ${escapeHtml(p.name)}</h3><button class="btn small ghost" data-favorite="${p.id}" aria-label="${state.favorites.includes(p.id) ? 'Убрать' : 'Добавить'} ${escapeHtml(p.name)} ${state.favorites.includes(p.id) ? 'из избранного' : 'в избранное'}" aria-pressed="${state.favorites.includes(p.id)}">${icon(state.favorites.includes(p.id) ? 'check' : 'plus')}</button></div><span class="muted tiny">${escapeHtml(p.category)} · ${escapeHtml(p.evidence)}</span><p><strong>Визуал.</strong> ${escapeHtml(p.visual)}</p><p><strong>Функция.</strong> ${escapeHtml(p.feature)}</p><p class="recommendation"><strong>Для Ватэс:</strong> ${escapeHtml(p.idea)}</p><a class="source" href="${escapeHtml(p.source)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.sourceLabel || 'Официальный источник')}${icon('arrow')}</a></article>`).join('') : '<div class="empty">Ничего не найдено. Попробуй другой запрос или категорию.</div>';
    }
    function settingsView() {
      return head('Твоё пространство','Настроить под себя','Только параметры макета. Они не меняют конфигурацию рабочей нейросети.') + `<div class="grid-2"><form class="card" id="settings-form"><h2>Личные предпочтения</h2><label class="label" for="assistant-name">Имя помощника</label><input class="field" id="assistant-name" maxlength="30" required value="${escapeHtml(state.settings.name)}"><label class="checkline"><input type="checkbox" id="remember-history" ${state.settings.remember ? 'checked' : ''}><span>Сохранять демо-диалоги в этом браузере<span class="muted tiny" style="display:block">Исходные файлы и обучающие данные здесь не хранятся.</span></span></label><p class="muted tiny">При отключении сохранения уже сохранённая история остаётся доступной в этой сессии, но будет удалена из хранилища макета.</p><button class="btn primary" type="submit">Сохранить настройки</button></form><div class="card"><h2>Внешний вид</h2><p class="muted tiny">Спокойный светлый интерфейс днём и тёмная тема вечером.</p><button class="btn" id="settings-theme">${icon('moon')}Переключить тему</button><div class="spacer"></div><h3>Границы прототипа</h3><p class="muted tiny">Чат использует подготовленные ответы. Запуск API, обучение и применение версий имитируются. Просмотр TXT/MD, проверка структуры JSON/JSONL, поиск и экспорт действительно работают в браузере.</p></div></div>`;
    }
    // Экспорт выполняется самим браузером; исходные файлы проекта не затрагиваются.
    function download(name, text, mime = 'text/plain;charset=utf-8') {
      let url;
      try {
        url = URL.createObjectURL(new Blob([text], {type:mime}));
        const link = document.createElement('a'); link.href = url; link.download = name;
        document.body.append(link); link.click(); link.remove();
      } catch (error) { toast('Не удалось скачать файл. Разреши загрузки в браузере.'); }
      finally { if (url) setTimeout(() => URL.revokeObjectURL(url),1000); }
    }
    async function copyText(text) {
      try {
        if (!navigator.clipboard) throw new Error('Clipboard is unavailable');
        await navigator.clipboard.writeText(text); toast('Скопировано');
      } catch (error) {
        dialog('Скопируй текст вручную', `<p>Браузер не разрешил доступ к буферу обмена.</p><textarea class="field" readonly aria-label="Текст для копирования">${escapeHtml(text)}</textarea>`);
      }
    }
    function newChat() { state.activeChat = null; draftText = ''; attachment = null; navigate('chat',false); $('#prompt').focus(); }
    function demoReply(prompt, mode, file) {
      const query = prompt.toLocaleLowerCase('ru');
      if (file) return `Вложен файл «${file.name}» (${file.text.length} символов). Он прочитан только в этом браузере.\n\nВот начало текста:\n${file.text.slice(0,500)}\n\nЭто демонстрация просмотра вложения. Анализ нейросетью здесь ещё не выполняется.`;
      if (/api|апи|сервер/.test(query)) return 'API — это вход для других программ в твоего ИИ. Например, сайт отправляет вопрос, а сервер возвращает ответ.\n\nВ будущем приложении ты нажмёшь «Запустить API», оно загрузит модель и проверит готовность. Только после проверки появится статус «Работает».\n\nОткрой «Локальный API», чтобы попробовать этот сценарий. Здесь он демонстрационный.';
      if (/обуч|нейросет|модел/.test(query)) return 'Предлагаю идти по шагам:\n\n1. Выбрать одну задачу для улучшения.\n2. Собрать и проверить хорошие примеры.\n3. Отделить обучающие данные от проверочных.\n4. Обучить новую версию отдельно от рабочей.\n5. Сравнить ответы и только затем применить результат.\n\nВ разделе «Обучение» можно пройти сокращённую демонстрацию. Она не запускает настоящее обучение.';
      if (/обзор|50|иде[яиюй]|продукт/.test(query)) return 'Для Ватэс я бы выбрал четыре направления:\n\n• Чистый чат и понятные режимы работы.\n• Материалы и диалоги внутри одного проекта.\n• Источники рядом с ответом, когда подключим поиск по документам.\n• Наглядные этапы задачи и обратимые изменения.\n\nОткрой «Обзор ИИ»: там можно искать продукты и отмечать полезные идеи.';
      if (mode === 'plan') return 'Пример структуры плана:\n\n1. Записать ожидаемый результат одним предложением.\n2. Разделить работу на три небольших шага.\n3. Выбрать самый простой первый шаг.\n4. Проверить результат на реальном примере.\n\nЭтот ответ подготовлен для макета и не является анализом твоей задачи.';
      if (mode === 'explain') return 'В режиме «Объясни просто» помощник должен давать короткое определение, знакомый пример и один следующий шаг.\n\nСейчас показан шаблон такого ответа. Чтобы объяснять именно твою тему, потребуется подключить локальную нейросеть.';
      return 'Сообщение получено. Этот макет помогает проверить, удобно ли общаться с Ватэс. Свободные ответы настоящего ИИ здесь ещё не подключены.\n\nПопробуй спросить про API, обучение нейросети или идеи из обзора 50 продуктов — для этих тем подготовлены демонстрационные ответы.';
    }
    function sendMessage(event) {
      event.preventDefault();
      const prompt = $('#prompt').value.trim();
      if (!prompt && !attachment) return toast('Напиши сообщение или прикрепи текстовый файл.');
      const mode = $('#chat-mode').value;
      let chat = state.chats.find(c => c.id === state.activeChat);
      if (!chat) {
        chat = {id:`chat-${Date.now()}-${Math.random().toString(16).slice(2,8)}`,title:(prompt || attachment.name).slice(0,55),messages:[]};
        state.chats.unshift(chat); state.chats = state.chats.slice(0,30); state.activeChat = chat.id;
      }
      chat.messages.push({role:'user',text:prompt + (attachment ? `\nФайл: ${attachment.name}` : '')});
      chat.messages.push({role:'assistant',text:demoReply(prompt,mode,attachment),feedback:false});
      chat.messages = chat.messages.slice(-80); draftText = ''; attachment = null; persist(); render(); $('#prompt').focus();
    }
    async function readMaterial(file, forChat) {
      if (!file) return;
      try {
        if (!/\.(txt|md)$/i.test(file.name)) throw new Error('Выбери текстовый файл .txt или .md.');
        if (file.size > 1024*1024) throw new Error('Файл больше 1 МБ. Для макета нужен небольшой текст.');
        const text = await file.text();
        if (!text.trim()) throw new Error('Файл пуст. Выбери файл с текстом.');
        const material = {name:file.name,size:file.size,text};
        if (forChat) { const prompt = $('#prompt'); if (prompt) draftText = prompt.value; attachment = material; }
        else { if (state.files.length >= 20) throw new Error('В макете можно открыть до 20 материалов за сессию.'); state.files.push(material); }
        render(); toast(forChat ? 'Файл добавлен к сообщению.' : 'Материал прочитан локально.');
      } catch (error) { toast(error.message || 'Не удалось прочитать файл.'); }
    }
    function sampleData() {
      const pairs = [
        ['Что такое API?','Интерфейс обмена данными между программами.'],
        ['Зачем нужен сервер?','Сервер принимает запросы и возвращает результат.'],
        ['Что такое обучение?','Настройка параметров модели на подготовленных примерах.'],
        ['Что такое датасет?','Набор примеров для обучения или проверки.'],
        ['Как сохранить результат?','Запиши новую модель в отдельную версию.'],
        ['Зачем проверять модель?','Чтобы увидеть качество на новых примерах.'],
        ['Что такое JSON?','Текстовый формат хранения структурированных данных.'],
        ['Что такое HTML?','Язык разметки веб-страниц.'],
        ['Что такое CSS?','Язык оформления веб-страниц.'],
        ['Что делает кнопка?','Запускает действие после нажатия.'],
        ['Как вернуть модель?','Выбери предыдущую проверенную версию.'],
        ['Зачем сохранять журнал?','Чтобы понять, что происходило во время работы.']
      ];
      setDataset(pairs.map(([prompt,completion]) => ({prompt,completion})),'Встроенный демо-набор · 12 примеров');
    }
    function setDataset(records, source) {
      state.training = {phase:'loaded',records,source,errors:[],progress:0,epoch:0,epochs:state.training.epochs,candidate:null};
      render(); toast('Набор добавлен. Теперь проверь его структуру.');
    }
    async function importDataset(file) {
      if (!file) return;
      try {
        if (!/\.(json|jsonl)$/i.test(file.name)) throw new Error('Поддерживаются только .json и .jsonl.');
        if (file.size > 2*1024*1024) throw new Error('Набор больше 2 МБ. Используй небольшой фрагмент.');
        const content = (await file.text()).replace(/^\uFEFF/,'').trim();
        if (!content) throw new Error('Файл пуст.');
        let records;
        if (/\.jsonl$/i.test(file.name)) {
          records = content.split(/\r?\n/).filter(line => line.trim()).map((line,i) => { try { return JSON.parse(line); } catch (error) { throw new Error(`Не удалось прочитать JSON в строке ${i+1}.`); } });
        } else {
          try { records = JSON.parse(content); } catch (error) { throw new Error('JSON повреждён. Проверь скобки, запятые и кавычки.'); }
        }
        if (!Array.isArray(records) || !records.length) throw new Error('JSON должен содержать непустой массив примеров.');
        if (records.length > 5000) throw new Error('В макете можно проверить не более 5 000 примеров.');
        setDataset(records,file.name);
      } catch (error) { toast(error.message || 'Не удалось прочитать набор.'); }
    }
    function validateData() {
      const t = state.training; const errors = []; const seen = new Map();
      t.records.forEach((record,index) => {
        if (!record || typeof record !== 'object' || typeof record.prompt !== 'string' || typeof record.completion !== 'string' || !record.prompt.trim() || !record.completion.trim()) {
          errors.push(`Запись ${index+1}: нужны непустые строки prompt и completion.`); return;
        }
        const key = record.prompt.normalize('NFKC').trim().replace(/\s+/g,' ').toLocaleLowerCase('ru');
        if (seen.has(key)) errors.push(`Запись ${index+1}: вопрос повторяет запись ${seen.get(key)}.`);
        else seen.set(key,index+1);
      });
      if (t.records.length < 3) errors.push('Для демонстрации нужны хотя бы 3 примера.');
      t.errors = errors.slice(0,8); if (errors.length > 8) t.errors.push(`Ещё ошибок: ${errors.length-8}.`);
      t.phase = errors.length ? 'invalid' : 'ready'; render();
      toast(errors.length ? 'Найдены ошибки. Исходный файл не изменён.' : 'Структура набора подходит для демонстрации.');
    }
    // Таймеры ниже исключительно рисуют сценарий и никогда не запускают обучение.
    function startTraining() {
      const t = state.training;
      if (!['ready','paused'].includes(t.phase) || timer) return;
      t.epochs = Number($('#epochs').value); t.phase = 'running'; render();
      timer = setInterval(() => {
        t.progress = Math.min(100,t.progress+4); t.epoch = Math.min(t.epochs,Math.ceil(t.progress*t.epochs/100));
        if (t.progress >= 100) {
          clearInterval(timer); timer = null; t.phase = 'complete';
          const candidate = {id:`demo-${Date.now()}`,name:`Демо-кандидат ${state.versions.length}`,created:'Создан симуляцией; весов нет',score:'92% · пример',active:false};
          state.versions.push(candidate); t.candidate = candidate.id;
          if (state.page === 'training') render(); toast('Симуляция завершена. Демо-кандидат доступен в версиях.');
        } else if (state.page === 'training') {
          $('#train-progress').value = t.progress; $('#train-percent').textContent = `${t.progress}%`; $('#train-epoch').textContent = `Проход ${t.epoch} из ${t.epochs}`;
        }
      },400);
    }
    function startApi() {
      if (state.api !== 'stopped') return;
      state.api = 'starting'; state.apiLogs.push('[демо] Проверка среды и доступности порта…'); render();
      apiTimer = setTimeout(() => {
        state.api = 'running'; state.apiLogs.push('[демо] Модель загружена: пример статуса.','[демо] Проверка готовности пройдена.','[важно] Настоящий сервер не запускался.'); apiTimer = null;
        if (['api','chat'].includes(state.page)) render(); toast('Демонстрация запуска завершена.');
      },1000);
    }
    function stopApi() {
      clearTimeout(apiTimer); apiTimer = null; state.api = 'stopped'; state.apiLogs.push('[демо] Демонстрационный сервер остановлен.'); render();
    }
    function applyVersion(id) {
      const version = state.versions.find(v => v.id === id); if (!version) return;
      dialog('Сравнение демонстрационных версий', `<p>Выбрана «${escapeHtml(version.name)}». Эти значения придуманы для проверки интерфейса и не описывают качество нейросети.</p><table><tr><th>Критерий</th><th>Базовая</th><th>Кандидат</th></tr><tr><td>Демо-оценка</td><td>88% · пример</td><td>92% · пример</td></tr><tr><td>Файл весов</td><td>Нет</td><td>Нет</td></tr></table><p style="margin-top:15px">В реальном приложении здесь будут ответы на одинаковые вопросы и результаты проверки.</p><button class="btn primary" id="confirm-apply">${id === 'demo-base' ? 'Вернуть базовую версию в макете' : 'Применить только в макете'}</button>`);
      $('#confirm-apply').addEventListener('click',() => { state.versions.forEach(v => v.active = v.id === id); $('#info-dialog').close(); render(); toast('Демонстрационная версия переключена. Рабочая модель не изменялась.'); });
    }
    function bindPage() {
      const on = (selector,event,handler) => { const node = $(selector); if (node) node.addEventListener(event,handler); };
      on('#chat-form','submit',sendMessage);
      on('#prompt','keydown',event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); } });
      on('#attach-button','click',() => $('#chat-file').click());
      on('#chat-file','change',event => readMaterial(event.target.files[0],true));
      on('#remove-attachment','click',() => { draftText = $('#prompt').value; attachment = null; render(); });
      on('#export-chat','click',() => { const chat = state.chats.find(c => c.id === state.activeChat); download('vates-demo-dialogue.md',`# ${chat.title}\n\nДемонстрационный диалог; ответы не сгенерированы моделью.\n\n` + chat.messages.map(m => `## ${m.role === 'user' ? 'Пользователь' : 'Ватэс · демо'}\n\n${m.text}`).join('\n\n')); });
      on('#api-start','click',startApi); on('#api-stop','click',stopApi);
      on('#copy-address','click',() => copyText('http://127.0.0.1:8000'));
      on('#export-log','click',() => download('vates-demo-api.log',state.apiLogs.join('\n')));
      on('#api-error','click',() => { clearTimeout(apiTimer); apiTimer = null; state.api = 'stopped'; state.apiLogs.push('[пример ошибки] Порт 8000 занят другой программой.','[решение] В полном приложении: выбери другой порт или проверь запущенный сервер.'); render(); });
      on('#library-add','click',() => $('#library-file').click()); on('#library-file','change',event => readMaterial(event.target.files[0],false));
      on('#sample-data','click',sampleData); on('#dataset-file','change',event => importDataset(event.target.files[0]));
      on('#validate-data','click',validateData); on('#epochs','change',event => state.training.epochs = Number(event.target.value));
      on('#train-start','click',startTraining);
      on('#train-pause','click',() => { clearInterval(timer); timer = null; state.training.phase = 'paused'; render(); });
      on('#train-reset','click',() => { clearInterval(timer); timer = null; state.training.phase = 'ready'; state.training.progress = 0; state.training.epoch = 0; render(); });
      on('#settings-theme','click',toggleTheme);
      on('#settings-form','submit',event => { event.preventDefault(); const name = $('#assistant-name').value.trim(); if (!name) return toast('Введи имя помощника.'); state.settings.name = name; state.settings.remember = $('#remember-history').checked; persist(); toast('Настройки сохранены.'); });
      if (state.page === 'research') { renderResearchResults(); on('#research-search','input',event => { state.researchSearch = event.target.value; renderResearchResults(); }); }
    }
    function toggleTheme() {
      const prompt = $('#prompt'); if (prompt) draftText = prompt.value;
      state.theme = state.theme === 'light' ? 'dark' : 'light'; document.documentElement.dataset.theme = state.theme; persist();
    }
    // Один обработчик обслуживает динамические кнопки, чтобы не плодить подписки.
    document.addEventListener('click',event => {
      const button = event.target.closest('button'); if (!button) return;
      if (button.dataset.page) navigate(button.dataset.page);
      if (button.dataset.chat) { state.activeChat = button.dataset.chat; draftText = ''; attachment = null; navigate('chat',false); }
      if (button.dataset.prompt) { $('#prompt').value = button.dataset.prompt; $('#prompt').focus(); }
      if (button.dataset.copyMessage !== undefined) { const chat = state.chats.find(c => c.id === state.activeChat); if (chat) copyText(chat.messages[Number(button.dataset.copyMessage)].text); }
      if (button.dataset.feedback !== undefined) {
        const chat = state.chats.find(c => c.id === state.activeChat); const index = Number(button.dataset.feedback);
        if (chat) { chat.messages[index].feedback = !chat.messages[index].feedback; draftText = $('#prompt').value; persist(); render(); toast('Оценка сохранена только в макете; обучение не запускается.'); }
      }
      if (button.dataset.filePreview !== undefined) { const file = state.files[Number(button.dataset.filePreview)]; if (file) dialog(file.name,`<pre class="code" style="max-height:360px;overflow:auto">${escapeHtml(file.text.slice(0,20000))}</pre>${file.text.length > 20000 ? '<p>Показаны первые 20 000 символов.</p>' : ''}`); }
      if (button.dataset.apply) applyVersion(button.dataset.apply);
      if (button.dataset.filter) { state.researchFilter = button.dataset.filter; $('#research-tabs').querySelectorAll('button').forEach(b => b.classList.toggle('active',b.dataset.filter === state.researchFilter)); renderResearchResults(); }
      if (button.dataset.favorite) { const id = Number(button.dataset.favorite); state.favorites = state.favorites.includes(id) ? state.favorites.filter(n => n !== id) : [...state.favorites,id]; persist(); renderResearchResults(); }
    });
    $('#new-chat').addEventListener('click',newChat);
    $('#theme-toggle').addEventListener('click',toggleTheme);
    $('#close-dialog').addEventListener('click',() => $('#info-dialog').close());
    $('#menu-toggle').addEventListener('click',() => { const open = document.body.classList.toggle('menu-open'); $('#menu-toggle').setAttribute('aria-expanded',String(open)); });
    document.addEventListener('keydown',event => { if (event.key === 'Escape') { document.body.classList.remove('menu-open'); $('#menu-toggle').setAttribute('aria-expanded','false'); } });
    window.addEventListener('hashchange',() => { const page = location.hash.slice(1); if (pageNames[page] && page !== state.page) navigate(page); });
    window.addEventListener('beforeunload',() => { clearInterval(timer); clearTimeout(apiTimer); });
    restore(); state.page = pageNames[location.hash.slice(1)] ? location.hash.slice(1) : 'chat'; render();
  