/* Отчёт по завершённому первому прогону. Не вызывает ИИ и не меняет источники. */
const { fs, path, root, lab } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');
const directory = path.join(lab, 'uep-acceptance');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const load = async name => JSON.parse(await fs.readFile(path.join(directory, name), 'utf8'));
const reportName = 'CLOSED_SEARCH_UEP_40_REPORT_2026_10_30.md';
const url = file => file.replaceAll('\\', '/');
const link = (label, file) => '[' + label + '](<' + url(file) + '>)';

async function main() {
    const specBytes = await fs.readFile(path.join(__dirname, 'uep_40_20261030.json'));
    const spec = JSON.parse(specBytes);
    const answers = await load('answers-final.json');
    const frozen = await load('frozen.json');
    const extraction = await load('extraction-diagnostic.json');
    const index = await load('index-diagnostic.json');
    const access = await load('access-and-health.json');
    const uploaded = await load('upload.json');
    const source = await load('source-info.json');
    if (answers.cases.length !== 40 || sha(specBytes) !== frozen.specSHA256) throw new Error('Incomplete or changed exam');
    const byId = new Map(answers.cases.map(item => [item.id, item]));
    const commonAnswer = 'Не удалось проверить все условия этого правила. Обратитесь к администратору для уточнения документа.';
    const judgments = spec.cases.map(item => {
        const actual = byId.get(item.id);
        if (!actual || actual.http !== 200 || actual.response.search_status !== 'unavailable' || actual.response.output_text !== commonAnswer) {
            throw new Error('This report requires renewed manual review for changed answer: ' + item.id);
        }
        // Строгая проверка запроса исключает передачу эталона, номера страницы и истории.
        if (Object.keys(actual.request).sort().join(',') !== 'chatId,documentId,messages'
            || actual.request.documentId !== frozen.documentId || actual.request.messages.length !== 1
            || JSON.stringify(actual.request.messages[0]) !== JSON.stringify({ role: 'user', content: item.question })) {
            throw new Error('Request differs from frozen protocol: ' + item.id);
        }
        const reason = item.kind === 'missing'
            ? 'Отказ вызван блокировкой источника, а не установленным отсутствием нужных сведений. Как правильный ответ не засчитан.'
            : item.kind === 'conflict'
                ? 'Не названы две противоречащие записи и их расположение. Общая отправка к администратору не подтверждает обнаружение конфликта.'
                : 'В исходном PDF есть ответ, но он не предоставлен из-за недоступности подготовленного источника.';
        return { ...item, answer: actual.response.output_text, status: actual.response.search_status,
            http: actual.http, milliseconds: actual.ms, verdict: 'Технически недоступно; не засчитано', reason };
    });
    if (new Set(answers.cases.map(item => item.request.chatId)).size !== 40) throw new Error('Chat contexts are mixed');
    const times = answers.cases.map(item => item.ms).sort((a, b) => a - b);
    const grade = { evaluatedAt: new Date().toISOString(), total: 40, credited: 0, unavailable: 40,
        answerable: 35, conflict: 1, missingData: 4, fabricatedFactualAnswersObserved: 0,
        latencyMs: { min: times[0], mean: times.reduce((a, b) => a + b, 0) / 40,
            median: (times[19] + times[20]) / 2, p95: times[37], max: times[39] }, cases: judgments };
    await fs.writeFile(path.join(directory, 'assessment.json'), JSON.stringify(grade, null, 2));
    const lines = [
        '# Настоящий технический PDF: независимая проверка на 40 вопросах', '',
        'Дата: 30.10.2026. Результат: проверка не пройдена. 0 из 40 ответов засчитаны; все 40 вернули техническую недоступность источника.', '',
        'Это не 40 выдуманных ответов и не доказательство неспособности модели рассуждать. Поисковый контур заблокировал документ до выдачи содержательного ответа. Возможность отвечать по этому PDF сейчас не работает.', '',
        '## Что было загружено', '',
        link('Исходный PDF', spec.source) + ': ' + source.pages + ' физических страниц, ' + source.bytes + ' байт.',
        'SHA-256: `' + source.sha256 + '`. Оригинал, загруженная копия и контрольная сумма после испытания совпадают.',
        'Материал пользователя с редакцией 07.2019; его актуальность для реальной эксплуатации не подтверждалась. Он не объявлялся действующим официальным регламентом.',
        'Испытание выполнено только в изолированном стенде 127.0.0.1:3040 с ядром закрытого поиска на 8001. Рабочая LMS и общий чат 8000 не участвовали.', '',
        '## Как исключались подсказки', '',
        '1. До загрузки сохранены вопросы, критерии, ожидаемые факты и страницы исходника. Момент фиксации: ' + frozen.createdAt + '.',
        '2. Загружен PDF целиком через штатное хранилище. Для ИИ не создавались конспект, текстовая замена, ответы или дополнительные материалы.',
        '3. Каждый вопрос задан один раз с получением содержательного HTTP-ответа в отдельном новом чате. Все запросы содержат только chatId, documentId и одну пользовательскую реплику.',
        '4. Выбран конкретный файл — как при кнопке «Спросить AI». Поиск нужного файла среди всей базы этим испытанием не оценивался.',
        '5. Эталоны, страницы, оценки и исправления ИИ не передавались. Файлы ядра и приложения сверены с зафиксированными хешами; изменений нет.',
        '6. Использован настоящий /api/ai-chat и обычная сессия пользователя Б. Прямого вызова внутренней функции поиска вместо пользовательского пути не было.', '',
        'Проверенные визуально страницы: 3, 4, 5, 6, 7, 16, 17, 24, 25, 29, 89, 90, 91, 92. Номера означают физические страницы PDF, не номера листов в штампах.', '',
        '## Итоги', '',
        '| Группа | Вопросов | Засчитано | Наблюдение |',
        '|---|---:|---:|---|',
        '| Факты, сравнения, условия, числа и таблицы | 35 | 0 | Источник заблокирован |',
        '| Реальное противоречие внутри документа | 1 | 0 | Расхождение не названо |',
        '| Сведения, которых нет в документе | 4 | 0 | Общий технический отказ, а не проверенное отсутствие сведений |',
        '| Всего | 40 | 0 | 40 ответов со статусом unavailable |', '',
        'Во всех 40 ответах дословно:', '', '> ' + commonAnswer, '',
        'Вопрос о конфликте: раздел 3.7.1.2 на странице 16 даёт габариты ЩЗИП типа 1 2000×800×600 мм, а таблица 5.1 на странице 24 — 2000×600×800 мм, при одинаковом порядке В×Ш×Г. Общая просьба обратиться к администратору не показывает, что ИИ увидел эти две записи.', '',
        'Четыре вопроса без ответа в PDF касались текущей цены, серийного номера установки пользователя, текущего напряжения и дежурного сотрудника. Технический отказ не засчитан как успешное распознавание отсутствующих данных.', '',
        '## Подтверждённые причины', '',
        '1. В ai_core/site_documents.py обработка ограничена первыми 40 страницами. Для файла из 92 страниц сохранено complete=false; 52 страницы за этим пределом не обрабатываются этим циклом.',
        '2. В извлечённом тексте найден маркер STRUCTURE_UNCERTAIN. В SQLite у источника structure_unavailable=true. Именно эта проверка в site_search.py возвращает общий отказ сразу после выбора файла.',
        '3. В кэше сохранено ' + extraction.textCharacters + ' символов. В индексе документ представлен одним фрагментом с issue=true. Полноценного набора пригодных для ответа разделов нет.',
        '4. Общий индекс сообщает ready, хотя этот документ неполон и заблокирован. Карточка файла говорит о доступности просмотра, но не объясняет пользователю, почему AI не может использовать его.',
        '5. Отдельный порог 200 000 символов также существует. В этом прогоне он не сработал, поскольку сохранено 117 429 символов. Независимое извлечение из всего PDF дало 233 112 символов: будущая полная обработка потребует проверки и этого ограничения, а не только увеличения числа страниц.', '',
        'Структурная неопределённость и неполное извлечение — две отдельные проблемы. Нельзя обещать, что одно увеличение лимита до 92 автоматически исправит ответы. Конкретное происхождение маркера неопределённости на отдельных страницах ещё требует диагностики.', '',
        '## Доступность, скорость и честность измерений', '',
        'Все 40 итоговых HTTP-ответов получили код 200, но это только доступность маршрута. Содержательная проверка не пройдена.',
        'Время одного отказа: минимум ' + grade.latencyMs.min + ' мс, среднее ' + grade.latencyMs.mean.toFixed(1) + ' мс, медиана ' + grade.latencyMs.median + ' мс, p95 ' + grade.latencyMs.p95 + ' мс, максимум ' + grade.latencyMs.max + ' мс. Это скорость отказов, не поиска правильного ответа.',
        'Прогон: ' + answers.startedAt + ' — ' + answers.completedAt + '. После первых 30 ответов сработал лимит 30 запросов за 5 минут. Первый скрипт сделал несколько коротких повторов 31-го запроса и остановился: отсутствующий Retry-After преобразовывался в ноль. Эти 429 не являются ответами ИИ.',
        'Продолжение выполнено после истечения полного окна, тем же профилем, без смены IP и перезапуска сервера. Первые 30 записей не переписаны; следующие 10 сохранены отдельно и объединены в answers-final.json. Изменён только инструмент продолжения теста, не ИИ.',
        'Доступ: профиль А не видит документ в каталоге, прямое чтение файла — 403. Профиль Б видит документ и получает файл с HTTP 200; контрольная сумма совпадает с оригиналом.',
        'В браузере открыта первая страница исходника, счётчик показывает «Страница 1 из 92». Это подтверждает загрузку и просмотр, но не корректность индексации.',
        'Стенд после проверки: ' + uploaded.catalogDocuments + ' документов, ' + index.totalSources + ' источников, ' + index.totalPassages + ' фрагмента; общий индекс ready. Файл оставлен в разделе «Проверка настоящего технического PDF», доступ Б/администратор.', '',
        '## Следующий этап', '',
        '1. Поддержать полную фоновую обработку крупных PDF по страницам: прогресс, продолжение после остановки и ограничение ресурсов без молчаливого обрезания.',
        '2. Разделять достоверные разделы, таблицы и проблемные страницы. Не снимать защиту неопределённых данных, но исследовать, почему неопределённость одной части закрывает весь файл.',
        '3. Показывать настоящий статус конкретного документа: обработан полностью, частично, требуется проверка. Общая готовность индекса не должна скрывать проблему файла.',
        '4. После исправлений повторить этот набор как регрессионный тест и отдельно подготовить новые вопросы для независимой проверки. Не заучивать ответы из этого отчёта.',
        '5. К точной подсветке сложных таблиц и сносок вернуться после подтверждения полноты извлечения и качества ответов.', '',
        'В этой проверке код ИИ, модель, обучение, лимиты и рабочая интеграция не изменялись. Commit/push/deploy не выполнялись.', '',
        '## Артефакты', '',
        ...['frozen.json', 'upload.json', 'processing.json', 'answers.json', 'answers-continuation.json', 'answers-final.json', 'assessment.json', 'extraction-diagnostic.json', 'index-diagnostic.json', 'access-and-health.json']
            .map(name => '- ' + link(name, path.join(directory, name))),
        '- ' + link('Зафиксированные 40 вопросов и эталоны', path.join(__dirname, 'uep_40_20261030.json')),
        '- ' + link('Протокол до испытания', path.join(__dirname, 'UEP_40_PROTOCOL_2026_10_30.md')),
        '- ' + link('Полные листинги инструментов испытания', path.join(root, 'CLOSED_SEARCH_UEP_40_CODE_SNAPSHOT_2026_10_30.md')), '',
        '![Исходный PDF открыт в локальном стенде](' + url(path.join(directory, 'upload-proof.png')) + ')', '',
        '## Все 40 вопросов: эталон, фактический ответ и оценка', '',
    ];
    for (const item of judgments) {
        lines.push('### ' + item.id + ' — ' + item.question, '',
            'Источник: ' + (item.pages.length ? 'страницы ' + item.pages.join(', ') + ' исходного PDF.' : 'сведения отсутствуют в типовом документе.'), '',
            'Ожидаемый результат: ' + item.expected, '',
            'Фактический ответ:', '', '> ' + item.answer, '',
            'Оценка: ' + item.verdict + '. ' + item.reason, '',
            'HTTP ' + item.http + '; статус ' + item.status + '; время ' + item.milliseconds + ' мс.', '');
    }
    await fs.writeFile(path.join(root, reportName), lines.join('\n'));
    const snapshot = ['# Полные листинги испытания UEP: 40 вопросов', '',
        'Созданы только сценарии приёмки и отчёты. Функциональный код ИИ и сайта не менялся.', ''];
    for (const name of ['uep_40_20261030.json', 'UEP_40_PROTOCOL_2026_10_30.md', 'uep_acceptance.cjs', 'uep_resume.cjs', 'uep_report.cjs']) {
        snapshot.push('## ' + name, '', '````' + (name.endsWith('.cjs') ? 'javascript' : name.endsWith('.json') ? 'json' : 'markdown'),
            await fs.readFile(path.join(__dirname, name), 'utf8'), '````', '');
    }
    await fs.writeFile(path.join(root, 'CLOSED_SEARCH_UEP_40_CODE_SNAPSHOT_2026_10_30.md'), snapshot.join('\n'));
    console.log(JSON.stringify({ report: reportName, total: grade.total, credited: grade.credited,
        unavailable: grade.unavailable, latencyMs: grade.latencyMs, requestsContainNoHints: true }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
