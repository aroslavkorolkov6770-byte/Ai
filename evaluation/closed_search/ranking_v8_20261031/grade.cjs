/* Смысловая оценка повторных 100 вопросов. Не вызывается поисковым ядром.
 * Неизменённый ответ с теми же доказательствами сохраняет прежнюю оценку.
 * Каждое изменённое решение имеет отдельную ручную оценку ниже.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = __dirname;
const root = path.resolve(base, '../../..');
const exam = path.join(base, '../holdout_20261031');
const previousDir = path.join(base, '../pdf_sections_v7_20261031/results/verified');
const out = path.join(base, 'results/candidate2');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const lines = file => fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const signature = row => JSON.stringify({status: row.result.status, text: row.result.text,
  evidence: row.result.evidence, links: row.result.links});
const reviewed = {
  q041: {grade:'correct', reason:'Правило записи с 14 лет и предъявлением удостоверения личности относится к гражданам РФ и других стран; посещение мероприятий больше не подменяет регистрацию.', issue:null, presentation:[]},
  q051: {grade:'abstained', reason:'Посторонний запрет копировать электронные ресурсы исключён, но нужное правило о фотографировании книги со штампом ещё не найдено.', issue:'retrieval_miss', presentation:[]},
  q066: {grade:'correct', reason:'Найден и сохранён прямой запрет использовать прибор с удлинителем.', issue:null, presentation:[]},
  q067: {grade:'correct', reason:'Найден прямой запрет заполнять чайник водой при нахождении на подставке.', issue:null, presentation:[]},
  q073: {grade:'wrong', reason:'Выбрана очистка вместо утилизации, но ответ содержит отключение и сушку, а не перечень запрещённых чистящих средств.', issue:'retrieval_precision', presentation:[]},
  q075: {grade:'abstained', reason:'Чужая процедура удаления накипи исключена, но условия хранения из документа всё ещё не найдены.', issue:'retrieval_miss', presentation:[]},
  q095: {grade:'wrong', reason:'Почтовый документ сохранён и чужой блок питания исключён; однако выбрана защита от намокания, а не правило для хрупкого вложения.', issue:'retrieval_precision', presentation:[]},
  q096: {grade:'abstained', reason:'После почтового продолжения сообщено отсутствие данных, хотя порядок работы с описью есть в источнике.', issue:'followup', presentation:[]}
};

function metric(rows) {
  const correct=rows.filter(row=>row.grade==='correct').length;
  return {correct,total:rows.length,percent:100*correct/rows.length};
}
function counts(rows,field) {
  const result={};
  for(const row of rows)result[row[field]||'none']=(result[row[field]||'none']||0)+1;
  return result;
}
function main() {
  const freeze=read(path.join(exam,'FREEZE.json'));
  const firstStart=read(path.join(exam,'results/START.json'));
  const firstFinish=read(path.join(exam,'results/FINISH.json'));
  const previousStart=read(path.join(previousDir,'START.json'));
  const previousFinish=read(path.join(previousDir,'FINISH.json'));
  const start=read(path.join(out,'START.json'));
  const finish=read(path.join(out,'FINISH.json'));
  const errors=[];
  for(const [name,value] of Object.entries(freeze.files))if(hash(path.join(exam,name))!==value)errors.push(name);
  for(const [name,value] of Object.entries(start.runtime))if(hash(path.join(root,name))!==value)errors.push(name);
  for(const [name,value] of Object.entries(previousStart.runtime)) {
    if(hash(path.join(base,'before/ai_core',path.basename(name)))!==value)errors.push('previous snapshot: '+name);
  }
  if(hash(path.join(exam,'FREEZE.json'))!==firstStart.freeze_sha256)errors.push('first freeze');
  if(hash(path.join(exam,'results/baseline.jsonl'))!==firstFinish.baseline_sha256)errors.push('first answers');
  if(hash(path.join(previousDir,'answers.jsonl'))!==previousFinish.answers_sha256)errors.push('v7 answers');
  if(hash(path.join(out,'answers.jsonl'))!==finish.answers_sha256)errors.push('new answers');
  if(finish.answers_sha256!=='44c333ecb52750a81bc7f084d77f55e62db0299170fe52a8284767fd01cb96f2')errors.push('unreviewed journal');
  if(finish.changed_during_run.length)errors.push('runtime changed during run');
  if(errors.length)throw new Error(errors.join(', '));
  const questions=read(path.join(exam,'questions.json'));
  const gold=read(path.join(exam,'gold.json'));
  const raw=lines(path.join(out,'answers.jsonl'));
  const previousRaw=lines(path.join(previousDir,'answers.jsonl'));
  const previousGrades=read(path.join(previousDir,'judgments.json'));
  const firstGrades=read(path.join(exam,'results/judgments.json'));
  if(raw.length!==100||new Set(raw.map(row=>row.id)).size!==100)throw new Error('Need 100 distinct answers');
  const changed=[];
  const rows=raw.map((row,index)=>{
    const q=questions[index];
    if(row.id!==q.id||row.chat!==q.chat||row.question!==q.question)throw new Error('Question changed: '+row.id);
    const same=signature(row)===signature(previousRaw[index]);
    if(!same&&!reviewed[row.id])throw new Error('Missing manual grade: '+row.id);
    if(!same)changed.push(row.id);
    const grade=same?previousGrades[index]:reviewed[row.id];
    return {...row,...gold[index],...grade,id:row.id,
      previous_grade:previousGrades[index].grade,first_grade:firstGrades[index].grade,
      assessment:same?'unchanged text/status/evidence/links; previous assessment retained':'changed; manually assessed'};
  });
  if(changed.length!==Object.keys(reviewed).length)throw new Error('Manual overrides and changed rows disagree');
  const latency=raw.map(row=>row.milliseconds).sort((a,b)=>a-b);
  const sources=read(path.join(exam,'sources.json'));
  const postcheck=read(path.join(out,'postcheck.json'));
  const summary={
    at:new Date().toISOString(),type:'known regression; no new independent holdout',
    integrity:'frozen inputs, first/v7 journals, before snapshot and final runtime verified',
    answers_sha256:finish.answers_sha256,previous_answers_sha256:previousFinish.answers_sha256,
    overall:metric(rows),answerable:metric(rows.filter(row=>row.action==='answer')),
    insufficient:metric(rows.filter(row=>row.action==='insufficient')),clarify:metric(rows.filter(row=>row.action==='clarify')),
    dialogue_turns:metric(rows.filter(row=>row.dialogue)),
    followup_only:metric(rows.filter(row=>row.dialogue&&((Number(row.id.slice(1))-1)%20)>12)),
    by_source:Object.fromEntries(sources.map(source=>[source.id,metric(rows.filter(row=>row.source===source.id))])),
    grades:counts(rows,'grade'),issues:counts(rows.filter(row=>row.grade!=='correct'),'issue'),
    changed_answers:changed,unchanged_answers:100-changed.length,
    newly_correct:rows.filter(row=>row.grade==='correct'&&row.previous_grade!=='correct').map(row=>row.id),
    lost_correct:rows.filter(row=>row.grade!=='correct'&&row.previous_grade==='correct').map(row=>row.id),
    lost_from_first:rows.filter(row=>row.grade!=='correct'&&row.first_grade==='correct').map(row=>row.id),
    correct_with_presentation_issues:rows.filter(row=>row.grade==='correct'&&row.presentation.length).length,
    statuses:counts(raw.map(row=>row.result),'status'),
    latency_ms:{median:(latency[49]+latency[50])/2,p95:latency[94],max:latency[99],total:latency.reduce((a,b)=>a+b,0)},
    extraction:read(path.join(out,'extraction.json')),index:read(path.join(out,'index.json')),
    postcheck:{passed:postcheck.passed,total:postcheck.total}
  };
  const md=['# Выбор доказательства: повторные 100 вопросов','',
    'Известная регрессия, исходные вопросы и эталоны не изменены. Все 100 вопросов выполнены заново. Для 92 одинаковых ответов сверены текст, статус, полные доказательства и ссылки; прежние оценки сохранены. Восемь изменённых ответов оценены агентом по прежнему METHOD.md. Это не внешняя комиссия и не новый независимый экзамен.','',
    'Итог: '+summary.overall.correct+'/100 (v7: 72/100, первый прогон: 30/100). SHA-256 итогового журнала: `'+finish.answers_sha256+'`.',''];
  for(const row of rows) {
    md.push('## '+row.id,'','Вопрос: '+row.question,'','Неизменённый эталон: '+row.answer,'',
      'Место в оригинале: '+row.reference,'','Ожидаемое действие: '+row.action+'. Статус движка: '+row.result.status+'.','',
      'Полный ответ Ватэс:','','```text',row.result.text,'```','',
      'Оценка: '+row.grade+'; v7: '+row.previous_grade+'; первый прогон: '+row.first_grade+'. '+row.reason,'',
      'Способ оценки: '+row.assessment+'.','',
      'Подача: '+(row.presentation.join(', ')||'существенных замечаний нет')+'.','',
      '```json',JSON.stringify({evidence:row.result.evidence,links:row.result.links},null,2),'```','');
  }
  fs.writeFileSync(path.join(base,'ALL_100_RESULTS.md'),md.join('\n'));
  fs.writeFileSync(path.join(out,'judgments.json'),JSON.stringify(rows.map(row=>({id:row.id,grade:row.grade,
    reason:row.reason,issue:row.issue,presentation:row.presentation,previous_grade:row.previous_grade,assessment:row.assessment})),null,2)+'\n');
  fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify(summary,null,2));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
