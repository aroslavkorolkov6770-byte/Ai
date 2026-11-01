/* Ручные смысловые решения после чтения всех ответов. Не обучает и не вызывает ИИ. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const base=__dirname, out=path.join(base,'results');
const read=name=>JSON.parse(fs.readFileSync(path.join(base,name),'utf8'));
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const decisions=new Map();
function mark(numbers,grade,reason,issue=null,presentation=[]){
 for(const number of numbers){const id='q'+String(number).padStart(3,'0');if(decisions.has(id))throw new Error('Duplicate '+id);decisions.set(id,{id,grade,reason,issue,presentation});}
}
const range=(first,last)=>Array.from({length:last-first+1},(_,i)=>first+i);
mark(range(1,13),'abstained','Нужные сведения есть в оригинале, но вместо ответа — сообщение о непроверенных условиях.','conditional_guard');
mark([14,15],'abstained','Предмет назван в первой реплике этой цепочки; запрошено повторное указание материала.','followup');
mark([16],'wrong','На вопрос о монтаже блока питания выдана инструкция по чайнику.','wrong_source',['excess']);
mark([17,18],'wrong','Не сообщено об отсутствии личного/складского факта: выдана техническая неопределённость условий.','wrong_action');
mark([19,20],'wrong','Не запрошен вид параметра или модификация; вместо уточнения — непроверенные условия.','wrong_action');
mark([21],'abstained','Уточнение об эксплуатации дома не нужно для расшифровки горящего индикатора.','false_condition');
mark([22,24],'abstained','Модель и индикатор уже названы, но движок просит указать материал.','unnecessary_clarify');
mark([23],'abstained','Значение мигающего индикатора есть на первой странице, но не найдено.','retrieval_miss');
mark([25],'abstained','Три действия из FAQ не выданы; сообщение о непроверенных условиях.','conditional_guard');
mark([...range(26,34),36],'abstained','Параметр или правило однозначны в оригинале; движок объявил ложное противоречие.','false_conflict');
mark([35],'abstained','Скорость 100 Мбит/с — продолжение вопроса о фильтрации кадров, но контекст потерян.','followup');
mark([37],'correct','Правильно сообщено об отсутствии цены в доступных материалах.');
mark([38],'wrong','Личного MAC-адреса нет, но вместо отсутствия данных объявлено противоречие.','false_conflict');
mark([39],'wrong','Нет вводных для выбора режима; нужен вопрос пользователю, а не просто отсутствие данных.','wrong_action');
mark([40],'correct','Запрошен недостающий предмет вопроса.');
mark([41],'correct','Сохранены возраст 14 лет, удостоверение личности и круг пользователей.',null,['excess','service_numbering']);
mark([42],'wrong','Описана очная запись, дистанционная запись из п.2.2.2 не упомянута.','retrieval_precision',['excess']);
mark([43],'wrong','Общие платные/бесплатные услуги не отвечают, бесплатно ли именно оформление билета.','retrieval_precision',['excess']);
mark([44],'wrong','Форма билета названа верно, но добавлено «Нарушать целостность документа…» без управляющего «Пользователям запрещается» из п.3.3. Это потеря смысла запрета, а не только многословие.','lost_prohibition',['excess','service_numbering']);
mark([45],'correct','Указано возмещение стоимости дубликата пользователем.',null,['excess','service_numbering']);
mark([46],'correct','Договор указан как основание регистрации коллективного пользователя.',null,['excess','service_numbering']);
mark([47],'wrong','Общее право посещать мероприятия выдано без обязательного сопровождения ребёнка взрослым.','missing_condition',['excess']);
mark([48],'correct','Сохранено требование выключенных звуковых сигналов.',null,['excess','service_numbering']);
mark([49],'correct','Названы свободные розетки в читательских помещениях.',null,['excess','service_numbering']);
mark([50],'correct','Сохранены музыкальный отдел, чехол и специально отведённое место для игры.',null,['excess','service_numbering']);
mark([51],'wrong','Запрет фотографирования отмеченной книги есть, но добавлено «Осуществлять несанкционированное копирование…» без общего запрета п.3.3. Ответ в целом нельзя считать корректным.','lost_prohibition',['excess','service_numbering']);
mark([52],'abstained','Не применено сравнение 1820 < 1830, хотя правило есть в документе.','comparison');
mark([53],'wrong','Вместо срока 5 лет выдано утверждение о рукописном фонде.','retrieval_precision',['service_numbering']);
mark([54,55],'abstained','Библиотека и билет установлены предыдущими репликами, но контекст не использован.','followup');
mark([56],'correct','Приведён запрет съёмки при корешке более 4 см, применимый к 5 см.',null,['excess','service_numbering']);
mark([57],'wrong','Не признано отсутствие личного номера билета; вместо этого — оформление билета и розетки.','wrong_action',['excess']);
mark([58],'partial','Сумма не выдумана и обязанность возмещения указана, но не сказано, что точной суммы в документе нет.','wrong_action',['excess','service_numbering']);
mark([59,60],'correct','Запрошена недостающая вводная для вопроса.');
mark([61],'wrong','Напряжение и частота не выданы; вместо них обложка, схемы, безопасность и утилизация.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([62],'correct','Правильная мощность 1850–2200 Вт явно присутствует. Подача засорена, но сам ответ на параметр однозначен.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([63,64,66,67],'correct','Нужный однозначный запрет прямо присутствует в ответе и подтверждён оригиналом; отмечены серьёзные дефекты подачи.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([65],'wrong','Нет ни запрета пользоваться при повреждённом шнуре, ни направления в авторизованный сервис.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([68],'correct','Названы закрытая крышка и отсутствие автоотключения при открытой.',null,['excess','service_numbering']);
mark([69,70,71],'correct','Искомое действие или рекомендация явно присутствуют и совпадают с источником.',null,['excess','other_language','diagram_noise','unrequested_url']);
mark([72],'partial','Количество и кипячёная вода указаны, но пропущено предварительное отключение от сети — существенная часть вопроса «когда».','missing_condition',['excess','other_language','diagram_noise','unrequested_url']);
mark([73],'wrong','Список запрещённых средств отсутствует; вместо него обложка и многоязычные подписи.','retrieval_precision',['excess','other_language','diagram_noise','unrequested_url']);
mark([74],'correct','Сохранён контекст чайника и явный запрет погружения прибора в воду.',null,['excess','service_numbering']);
mark([75],'abstained','Условия хранения есть в п.3.3, но объявлено противоречие.','false_conflict');
mark([76],'wrong','Вместо упаковки чайника выдано меню сайта Почты России.','wrong_source',['navigation']);
mark([77],'partial','Есть полезное указание, где искать номер на изделии, но отсутствует признание, что сам личный номер неизвестен.','wrong_action',['excess','other_language','diagram_noise','unrequested_url']);
mark([78],'wrong','Вместо отсутствия текущей цены — адрес, телефон и другой язык.','wrong_action',['other_language','unrequested_url']);
mark([79],'wrong','В новом чате без предмета и цели самостоятельно выбран рецепт удаления накипи.','wrong_action',['service_numbering']);
mark([80],'correct','Запрошен предмет использования.');
mark([81],'correct','Названы допустимые варианты упаковки для бумажных материалов до 2 кг.');
mark([82],'correct','Размер 51 × 28 × 20 см явно относится к гофрокоробке.',null,['excess']);
mark([83],'correct','Правильно указан минимальный размер места для ярлыка.',null,['excess']);
mark([84],'correct','Прямо указан запрет повторного использования коробки Почты России.',null,['excess']);
mark([85],'correct','Прямо запрещено перекрывать штрихкод плёнкой или скотчем.',null,['excess']);
mark([86],'abstained','Инструкция заполнить свободное пространство есть, но разговорная формулировка не распознана.','retrieval_miss');
mark([87],'abstained','Вместо правил для жидкости задан посторонний вопрос о наклеивании бланка.','false_condition');
mark([88],'wrong','Правило для пушнины подменило правило для красящего порошка; герметичность не названа.','retrieval_precision',['excess']);
mark([89],'wrong','Выдан огромный набор правил для разных вложений, но правило для саженцев и подкладка дна отсутствуют.','retrieval_precision',['excess','service_numbering']);
mark([90],'partial','Вентиляция названа только для твёрдых сортов, общий вопрос о фруктах покрыт не полностью.','missing_condition',['excess']);
mark([91],'correct','Сохранены чистота шин и мешки.',null,['excess']);
mark([92],'correct','Сохранено исключение: цельный небьющийся предмет без острых выступов допускается без дополнительной упаковки.',null,['excess']);
mark([93],'wrong','Текстильный товар перепутан с тканевой упаковкой; требования к цвету, однородности и шву не выданы.','retrieval_precision',['excess']);
mark([94],'abstained','Продолжение о тканевой упаковке не распознано, хотя прямой запрет пришивать адрес есть.','followup');
mark([95],'abstained','Правило о твёрдой упаковке и прокладках есть; вместо него — непроверенные условия.','conditional_guard');
mark([96],'abstained','Опись и упаковка относятся к установленной теме; вместо порядка действий — уточнение материала.','followup');
mark([97],'correct','Тариф не выдуман, сообщено об отсутствии информации.');
mark([98],'wrong','Местоположение личной посылки неизвестно, но выданы правила коробки и штрихкода.','wrong_action',['excess']);
mark([99],'wrong','В новом чате предмет выбора неизвестен, но самостоятельно выбрана тема почтовых отправлений.','wrong_action');
mark([100],'partial','Сообщена зависимость от типа отправления, но не запрошены конкретный тип и направление.','wrong_action');

function wilson(k,n){
 const z=1.959963984540054,p=k/n,d=1+z*z/n;
 const middle=(p+z*z/(2*n))/d,half=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;
 return [middle-half,middle+half].map(v=>Number((100*v).toFixed(2)));
}
function metric(rows){const correct=rows.filter(r=>r.grade==='correct').length;return {correct,total:rows.length,percent:100*correct/rows.length,wilson95: wilson(correct,rows.length)};}
function counts(rows,field){const result={};for(const r of rows){const v=r[field]||'none';result[v]=(result[v]||0)+1;}return result;}
function main(){
 const freeze=read('FREEZE.json'),finish=read('results/FINISH.json'),start=read('results/START.json');
 const root=path.resolve(base,'../../..');
 const errors=[];
 for(const [name,expected] of Object.entries(freeze.files))if(hash(path.join(base,name))!==expected)errors.push(name);
 for(const [name,expected] of Object.entries(freeze.runtime))if(hash(path.join(root,name))!==expected)errors.push(name);
 if(hash(path.join(base,'FREEZE.json'))!==start.freeze_sha256)errors.push('FREEZE.json');
 if(hash(path.join(out,'baseline.jsonl'))!==finish.baseline_sha256)errors.push('baseline.jsonl');
 if(errors.length)throw new Error('Changed after freeze: '+errors.join(','));
 const questions=read('questions.json'),gold=read('gold.json');
 const raw=fs.readFileSync(path.join(out,'baseline.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
 if(raw.length!==100||decisions.size!==100||new Set(raw.map(r=>r.id)).size!==100)throw new Error('Must grade 100 distinct answers');
 for(let i=0;i<100;i++)if(raw[i].id!==questions[i].id||raw[i].question!==questions[i].question||raw[i].chat!==questions[i].chat)throw new Error('Question sequence changed');
 if(Date.parse(start.started_at)<=Date.parse(freeze.frozen_at)||raw.some(r=>Date.parse(r.at)<Date.parse(start.started_at)))throw new Error('Wrong timing');
 const rows=raw.map(r=>({...r,...gold.find(g=>g.id===r.id),...decisions.get(r.id)}));
 const latency=raw.map(r=>r.milliseconds).sort((a,b)=>a-b);
 const sources=read('sources.json');
 const summary={graded_at:new Date().toISOString(),integrity:'all frozen files and 77 runtime files unchanged',
  first_run_sha256:finish.baseline_sha256,overall:metric(rows),answerable:metric(rows.filter(r=>r.action==='answer')),
  insufficient:metric(rows.filter(r=>r.action==='insufficient')),clarify:metric(rows.filter(r=>r.action==='clarify')),
  unknown_or_clarify:metric(rows.filter(r=>r.action!=='answer')),dialogue_turns:metric(rows.filter(r=>r.dialogue)),
  followup_only:metric(rows.filter(r=>r.dialogue&&((Number(r.id.slice(1))-1)%20)>12)),
  by_source:Object.fromEntries(sources.map(s=>[s.id,metric(rows.filter(r=>r.source===s.id))])),
  grades:counts(rows,'grade'),issues:counts(rows.filter(r=>r.grade!=='correct'),'issue'),
  statuses:counts(raw.map(r=>r.result),'status'),
  presentation_flagged:rows.filter(r=>r.presentation.length).length,
  correct_with_presentation_issues:rows.filter(r=>r.grade==='correct'&&r.presentation.length).length,
  latency_ms:{min:latency[0],median:(latency[49]+latency[50])/2,p95:latency[94],max:latency[99],total:latency.reduce((a,b)=>a+b,0)},
  answer_latency_ms:raw.filter(r=>r.result.status==='answer').map(r=>r.milliseconds),
  extraction:read('results/extraction.json'),index:read('results/index.json'),postcheck:read('results/postcheck.json')};
 const md=['# Результаты первого экзамена: все 100 решений','',
  'Все вопросы и эталоны зафиксированы до первого вызова. Ниже — неизменённые ответы Ватэс и ручная смысловая оценка. correct оценивает фактическое содержание, а не удобство подачи: дефекты длины, языка и служебного текста отмечены отдельно. Оценщик — агент, не независимая внешняя комиссия.',
  '', 'Фиксация: '+freeze.frozen_at+'. Начало: '+start.started_at+'. Конец: '+finish.finished_at+'.',
  '', 'SHA-256 журнала первого прогона: `'+finish.baseline_sha256+'`.', '',
  'Итого: '+summary.overall.correct+'/100. Отвечаемые: '+summary.answerable.correct+'/80. Отсутствие данных: '+summary.insufficient.correct+'/10. Уточнения: '+summary.clarify.correct+'/10.',''];
 for(const r of rows){
  md.push('## '+r.id+' — '+r.grade,'','Вопрос: '+r.question,'','Эталон: '+r.answer,'','Основание: '+r.reference+'.',
   '', 'Ответ Ватэс (полностью):','',...r.result.text.split('\n').map(line=>'> '+line),'',
   'Оценка: '+r.reason,'','Замечания к подаче: '+(r.presentation.join(', ')||'нет')+'.',
   '', 'Статус движка: '+r.result.status+'. Время: '+r.milliseconds+' мс. Источники: '+(r.result.evidence.map(e=>e.id).join(', ')||'нет')+'.','');
 }
 for(const [name,value] of [['judgments.json',rows.map(r=>({id:r.id,grade:r.grade,reason:r.reason,issue:r.issue,presentation:r.presentation}))],['summary.json',summary]])fs.writeFileSync(path.join(out,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
 fs.writeFileSync(path.join(base,'ALL_100_RESULTS.md'),md.join('\n'),{flag:'wx'});
 console.log(JSON.stringify({...summary,extraction:undefined,postcheck:{passed:summary.postcheck.passed,total:summary.postcheck.total},answer_latency_ms:undefined},null,2));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
