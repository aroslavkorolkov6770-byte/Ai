/* Ручные оценки после выполнения движка. Этот файл не импортируется поиском.
 * Эталон, вопрос и первый ответ остаются в прежнем каталоге без изменений.
 * Скрипт проверяет целостность, подсчитывает решения и пишет полный журнал.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = __dirname;
const root = path.resolve(base, '../../..');
const exam = path.join(base, '../holdout_20261031');
const out = path.join(base, 'results/verified');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const lines = file => fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const decisions = new Map();
function mark(numbers, grade, reason, issue = null, presentation = []) {
  for (const number of numbers) {
    const id = 'q' + String(number).padStart(3, '0');
    if (decisions.has(id)) throw new Error('Duplicate judgment ' + id);
    decisions.set(id, { id, grade, reason, issue, presentation });
  }
}

mark([1], 'correct', 'Для модификации 12 В выбран её номинальный ток 2,50 А.');
mark([2], 'correct', 'Для модификации 24 В выбран её номинальный ток 1,25 А.');
mark([3], 'correct', 'Общая ячейка правильно отнесена к обеим модификациям: 30 Вт.');
mark([4], 'abstained', 'Подстройка ±8% есть в оригинале, но ответ не найден.', 'retrieval_miss');
mark([5,6,7,8,9,10], 'correct', 'Искомая характеристика, числовая граница, единицы и режим соответствуют эталону.');
mark([11], 'correct', 'Сохранены условие параллельного подключения и одинаковые длина и сечение проводов.');
mark([12], 'correct', 'В тексте присутствует полный ответ: керамический конденсатор параллельно нагрузке, не менее 0,1 мкФ и ≥1,5 Uвых. Статус partial сам по себе не определяет смысловую оценку; лишнее уточнение и подписи отмечены отдельно.', null, ['diagram_noise','unnecessary_clarification']);
mark([13,14,15], 'correct', 'Масса, срок эксплуатации и гарантия найдены для того же блока; короткие уточнения сохраняют предмет.');
mark([16], 'correct', 'Смена характеристики на монтаж выполнена; питание должно быть отключено у блока и всех подключённых устройств.', null, ['service_heading']);
mark([17,18], 'correct', 'Не выдуманы отсутствующие складские остатки или личный серийный номер.');
mark([19,20], 'correct', 'Запрошен недостающий вид характеристики без случайного выбора значения. Модификацию может потребоваться уточнить следующим шагом.');

mark([21,22,24,25], 'abstained', 'Ответ есть в инструкции, но смысл индикатора либо порядок проверки соединения не найден.', 'retrieval_miss');
mark([23], 'wrong', 'На вопрос о мигающем индикаторе выдан порядок проверки кабеля; передача/приём данных не объяснены.', 'retrieval_precision');
mark([26], 'correct', 'Максимум 100 метров указан явно, но вместе с лишними действиями проверки.', null, ['excess']);
mark([27,28,29,30], 'correct', 'Рабочий режим и хранение различены; температуры и влажность с отсутствием конденсата верны.');
mark([31], 'correct', 'Назван правильный метод Store-and-Forward.');
mark([32], 'correct', 'Для Gigabit Ethernet явно указаны 2000 Мбит/с в полнодуплексном режиме.', null, ['excess']);
mark([33], 'correct', 'Названы все три автоматически согласуемые скорости RJ45.');
mark([34,35], 'correct', 'Сохранены коммутатор и характеристика кадров; нужные 1 488 000 и 148 800 явно привязаны к соответствующим портам.', null, ['excess']);
mark([36], 'partial', 'Запрет сырого подвала узнаваем, но строки условия перемешаны с авторскими правами соседней колонки. Такой повреждённый текст не засчитывается полным ответом.', 'column_order', ['mixed_columns']);
mark([37,38], 'correct', 'Цена магазина и MAC-адрес экземпляра не выдуманы; отсутствие данных сообщено.');
mark([39,40], 'correct', 'В новом чате запрошен неизвестный предмет вопроса.');

mark([41], 'wrong', 'Вместо регистрации с 14 лет выданы условия посещения несовершеннолетними мероприятий с взрослым.', 'retrieval_precision');
mark([42], 'wrong', 'Описана очная запись; возможность дистанционной записи не названа.', 'retrieval_precision');
mark([43], 'correct', 'Очная запись прямо названа бесплатной.', null, ['excess']);
mark([44], 'correct', 'Пластиковый билет с фотографией указан явно.', null, ['excess']);
mark([45], 'correct', 'Стоимость изготовления дубликата компенсирует пользователь.', null, ['service_heading']);
mark([46], 'correct', 'Основание коллективного пользования — договор.');
mark([47], 'correct', 'Сохранены отсутствие документа у ребёнка и сопровождение лицом от 18 лет.', null, ['excess']);
mark([48], 'correct', 'Сохранено требование выключенных звуковых сигналов.');
mark([49], 'correct', 'Свободные розетки в читательских помещениях указаны.');
mark([50], 'correct', 'Сохранены музыкальный отдел, чехол и специально отведённое место для игры.');
mark([51], 'wrong', 'Общий запрет теперь сохранён, но найден запрет копировать электронные ресурсы вместо правила о штампе на книге.', 'retrieval_precision');
mark([52], 'correct', 'Сохранён родительский запрет и граница до 1830 года, применимая к книге 1820 года.', null, ['excess']);
mark([53], 'correct', 'Билет выдаётся на 5 лет.');
mark([54], 'correct', 'Короткая реплика об утере относится к прежнему билету; компенсация дубликата указана.', null, ['service_heading']);
mark([55], 'correct', 'Назван допустимый способ дистанционного пользования — открытые ресурсы электронной библиотеки.');
mark([56], 'correct', 'Полный пункт сохраняет запрет съёмки при корешке более 4 см; он применим к 5 см.', null, ['excess','unrelated_footnote']);
mark([57], 'correct', 'Не выдуман номер личного читательского билета.');
mark([58], 'partial', 'Обязанность оплатить дубликат названа, но не сообщено, что конкретной суммы в документе нет.', 'wrong_action', ['service_heading']);
mark([59,60], 'correct', 'Запрошен недостающий предмет вопроса.');

mark([61], 'abstained', 'Напряжение и частота находятся в исходнике, но составной вопрос не получил ответа.', 'retrieval_miss');
mark([62], 'correct', 'Мощность 1850–2200 Вт явно указана; рядом остались лишние технические сведения.', null, ['excess']);
mark([63], 'correct', 'Сохранён прямой запрет использования на улице и в ванной.');
mark([64], 'correct', 'Прямо указаны бытовое назначение и запрет профессионального использования.', null, ['punctuation']);
mark([65], 'correct', 'Условие повреждения шнура, запрет использования и обращение в авторизованный сервис сохранены вместе.');
mark([66], 'wrong', 'Вместо запрета удлинителя выбрана общая вводная о безопасности. Это регрессия относительно первой оценки.', 'retrieval_precision');
mark([67], 'wrong', 'Запрет открывать крышку при кипении не отвечает на вопрос о заполнении на подставке. Это регрессия относительно первой оценки.', 'retrieval_precision');
mark([68], 'correct', 'Открытая крышка связана с отсутствием автоматического выключения.', null, ['excess','service_symbols']);
mark([69], 'correct', 'Для ручной остановки явно указано перевести выключатель вверх.', null, ['excess','service_symbols']);
mark([70], 'correct', 'Прямо указано дать чайнику остыть перед каждым нагревом.', null, ['excess','service_symbols']);
mark([71], 'correct', 'Смягчённая или фильтрованная вода рекомендована для предотвращения накипи.', null, ['excess','service_symbols']);
mark([72], 'correct', 'Сохранён порядок: вскипятить, отключить от сети, добавить 2 стакана уксуса либо 1 столовую ложку кислоты.', null, ['excess','service_symbols']);
mark([73], 'wrong', 'Вместо запрещённых чистящих средств выдано правило утилизации упаковки.', 'retrieval_precision');
mark([74], 'correct', 'Предмет предыдущего вопроса сохранён; запрет погружения прибора в воду назван.');
mark([75], 'wrong', 'Вместо условий хранения выдана процедура удаления накипи.', 'retrieval_precision', ['excess']);
mark([76], 'abstained', 'Перевозка относится к прежнему прибору, но смешанный длинный раздел вызывает уточнение вместо ответа.', 'section_boundary');
mark([77,78], 'correct', 'Серийный номер личного экземпляра и текущая цена не выдуманы.');
mark([79,80], 'correct', 'В новом чате без предмета использования/добавления запрошена вводная.');

mark([81], 'correct', 'Названы допустимые варианты упаковки бумажных материалов до 2 кг.');
mark([82], 'correct', 'Размер 51 × 28 × 20 см относится именно к гофрокоробке.', null, ['excess']);
mark([83], 'correct', 'Минимальное место для адресного ярлыка 10,5 × 14,8 см указано.', null, ['excess']);
mark([84], 'correct', 'Явно запрещено повторное использование коробки Почты России.', null, ['excess']);
mark([85], 'correct', 'Явно запрещено перекрывать штрихкод скотчем или плёнкой.', null, ['excess']);
mark([86], 'abstained', 'Правило заполнения пустот есть, но разговорная формулировка не распознана.', 'retrieval_miss');
mark([87], 'abstained', 'Вместо правил о жидкости запрошено постороннее условие о наклеивании бланка.', 'false_condition');
mark([88], 'wrong', 'Вместо герметичной ёмкости для порошка выдана упаковка пушнины.', 'retrieval_precision', ['excess']);
mark([89], 'wrong', 'Свалка правил для других вложений не содержит нужного ответа о саженцах и защите дна.', 'retrieval_precision', ['excess']);
mark([90], 'partial', 'Вентиляция указана только для твёрдых сортов; общий вопрос о фруктах покрыт не полностью, как и в первой оценке.', 'missing_condition', ['excess']);
mark([91], 'correct', 'Шины должны быть чистыми и в мешках.', null, ['excess']);
mark([92], 'correct', 'Исключение для цельного небьющегося предмета без острых выступов сохранено.', null, ['excess']);
mark([93], 'wrong', 'Требования к светлой однородной ткани и внутренним швам отсутствуют.', 'retrieval_precision');
mark([94], 'abstained', 'Короткое продолжение о тканевой упаковке потеряно.', 'followup');
mark([95], 'wrong', 'Вместо упаковки хрупкого вложения выбран класс электрической защиты блока питания из другого документа.', 'wrong_source');
mark([96], 'abstained', 'Порядок предъявления отправления с описью не найден в прежнем почтовом контексте.', 'followup');
mark([97], 'correct', 'Тариф отсутствует и не выдуман.');
mark([98], 'wrong', 'Вместо признания отсутствия сведений о личной посылке выдано меню сайта.', 'wrong_action', ['navigation']);
mark([99], 'correct', 'При неизвестном предмете выбора запрошена вводная.');
mark([100], 'partial', 'Зависимость от типа отправления названа, но тип и направление не запрошены, как и в первой оценке.', 'wrong_action');

function metric(rows) {
  const correct = rows.filter(r => r.grade === 'correct').length;
  return { correct, total: rows.length, percent: 100 * correct / rows.length };
}
function counts(rows, key) {
  const values = {};
  for (const row of rows) values[row[key] || 'none'] = (values[row[key] || 'none'] || 0) + 1;
  return values;
}
function main() {
  const freeze = read(path.join(exam, 'FREEZE.json'));
  const finish = read(path.join(out, 'FINISH.json'));
  const start = read(path.join(out, 'START.json'));
  const errors = [];
  for (const [name, hash] of Object.entries(freeze.files)) {
    if (digest(path.join(exam, name)) !== hash) errors.push(name);
  }
  for (const [name, hash] of Object.entries(start.runtime)) {
    if (digest(path.join(root, name)) !== hash) errors.push(name);
  }
  const firstFinish = read(path.join(exam, 'results/FINISH.json'));
  const firstStart = read(path.join(exam, 'results/START.json'));
  if (digest(path.join(exam, 'FREEZE.json')) !== firstStart.freeze_sha256) errors.push('first freeze');
  // Связываем ручные решения с тем неизменённым журналом, который прочитан.
  if (finish.answers_sha256 !== '033c4cedba11a484a8eb09399ede0d256599dd1cc8977052f9851c17f6f3e015') errors.push('unreviewed answers');
  if (digest(path.join(exam, 'results/baseline.jsonl')) !== firstFinish.baseline_sha256) errors.push('first baseline');
  if (digest(path.join(out, 'answers.jsonl')) !== finish.answers_sha256) errors.push('current answers');
  if (finish.changed_during_run.length) errors.push('runtime modified during evaluation');
  if (errors.length) throw new Error('Integrity failed: ' + errors.join(', '));
  const raw = lines(path.join(out, 'answers.jsonl'));
  const questions = read(path.join(exam, 'questions.json'));
  const gold = read(path.join(exam, 'gold.json'));
  const previous = read(path.join(exam, 'results/judgments.json'));
  if (raw.length !== 100 || decisions.size !== 100) throw new Error('Need 100 answers and judgments');
  for (let i = 0; i < 100; i++) {
    if (raw[i].id !== questions[i].id || raw[i].question !== questions[i].question || raw[i].chat !== questions[i].chat) {
      throw new Error('Changed question or order: ' + i);
    }
  }
  const rows = raw.map((r, i) => ({ ...r, ...gold[i], ...decisions.get(r.id), previous_grade: previous[i].grade }));
  const latency = raw.map(r => r.milliseconds).sort((a, b) => a - b);
  const sources = read(path.join(exam, 'sources.json'));
  const postcheck = read(path.join(out, 'postcheck.json'));
  const summary = {
    graded_at: new Date().toISOString(), type: 'known regression; not new holdout',
    integrity: 'frozen inputs, first baseline and final runtime hashes verified',
    answers_sha256: finish.answers_sha256, first_answers_sha256: firstFinish.baseline_sha256,
    overall: metric(rows), answerable: metric(rows.filter(r => r.action === 'answer')),
    insufficient: metric(rows.filter(r => r.action === 'insufficient')),
    clarify: metric(rows.filter(r => r.action === 'clarify')),
    dialogue_turns: metric(rows.filter(r => r.dialogue)),
    followup_only: metric(rows.filter(r => r.dialogue && ((Number(r.id.slice(1)) - 1) % 20) > 12)),
    by_source: Object.fromEntries(sources.map(s => [s.id, metric(rows.filter(r => r.source === s.id))])),
    grades: counts(rows, 'grade'), issues: counts(rows.filter(r => r.grade !== 'correct'), 'issue'),
    statuses: counts(raw.map(r => r.result), 'status'),
    newly_correct: rows.filter(r => r.grade === 'correct' && r.previous_grade !== 'correct').map(r => r.id),
    lost_correct: rows.filter(r => r.grade !== 'correct' && r.previous_grade === 'correct').map(r => r.id),
    correct_with_presentation_issues: rows.filter(r => r.grade === 'correct' && r.presentation.length).length,
    latency_ms: { median: (latency[49] + latency[50]) / 2, p95: latency[94], max: latency[99], total: latency.reduce((a, b) => a + b, 0) },
    index: read(path.join(out, 'index.json')), extraction: read(path.join(out, 'extraction.json')),
    postcheck: { passed: postcheck.passed, total: postcheck.total }
  };
  const md = ['# Повторная проверка PDF и уточнений: 100 прежних вопросов', '',
    'Это известный регрессионный набор. Вопросы, эталоны, оригиналы и первый результат не изменялись. Движок получил только прежние вопросы и чаты; правильный файл или страницы извне не выбирались. Оценка ручная по прежнему METHOD.md, оценщик — агент, не внешняя комиссия. Статус ответа не подменяет смысловую оценку.', '',
    'Известные вопросы использовались при отладке; диагностические прогоны сохранены отдельно. Итоговый журнал verified записан одним проходом без изменений ядра. Доля правильных здесь не является оценкой качества на новых документах.', '',
    'Итог: ' + summary.overall.correct + '/100; раньше 30/100. SHA-256: `' + finish.answers_sha256 + '`.', ''];
  for (const row of rows) {
    md.push('## ' + row.id, '', 'Вопрос: ' + row.question, '', 'Прежний эталон: ' + row.answer, '',
      'Проверка по оригиналу: ' + row.reference, '', 'Ожидаемое действие: ' + row.action + '. Статус движка: ' + row.result.status + '.', '',
      'Полный ответ Ватэс:', '', '```text', row.result.text, '```', '',
      'Оценка: ' + row.grade + ' (первый прогон: ' + row.previous_grade + '). ' + row.reason, '',
      'Подача: ' + (row.presentation.join(', ') || 'существенных замечаний нет') + '.', '',
      'Доказательства и ссылки:', '', '```json', JSON.stringify({ evidence: row.result.evidence, links: row.result.links }, null, 2), '```', '');
  }
  fs.writeFileSync(path.join(out, 'judgments.json'), JSON.stringify([...decisions.values()].sort((a, b) => a.id.localeCompare(b.id)), null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  fs.writeFileSync(path.join(base, 'ALL_100_RESULTS.md'), md.join('\n'));
  console.log(JSON.stringify(summary, null, 2));
}
try { main(); } catch (error) { console.error(error.stack); process.exitCode = 1; }
