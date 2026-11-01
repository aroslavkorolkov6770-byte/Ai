/* Полные листинги и контрольные суммы выбора отметок и условий хранения.
 * Скрипт не меняет ядро, эталоны, первые журналы или рабочую интеграцию.
 */
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');

function main() {
  const here=__dirname;
  const root=path.resolve(here,'../../..');
  const before=path.join(here,'before/ai_core');
  const out=path.join(here,'results/candidate2');
  const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const relative=file=>path.relative(root,file).replaceAll('\\','/');
  const start=JSON.parse(fs.readFileSync(path.join(out,'START.json'),'utf8'));
  for(const [name,expected] of Object.entries(start.runtime)) {
    if(hash(path.join(root,name))!==expected)throw new Error('Runtime changed: '+name);
  }
  const names=fs.readdirSync(path.join(root,'ai_core')).filter(name=>name.endsWith('.py')).sort();
  const changed=names.filter(name=>!fs.existsSync(path.join(before,name))||hash(path.join(before,name))!==hash(path.join(root,'ai_core',name)));
  const files=changed.map(name=>path.join(root,'ai_core',name));
  files.push(path.join(root,'tests/test_site_search_rule_selection.py'));
  for(const name of ['run.py','diagnose.py','postcheck.py','grade.cjs','reviews.json','package_report.cjs'])files.push(path.join(here,name));
  const manifest={
    at:new Date().toISOString(),root,final_run:'results/candidate2',runtime_count:Object.keys(start.runtime).length,
    changed_core:changed.map(name=>({path:'ai_core/'+name,
      before:fs.existsSync(path.join(before,name))?hash(path.join(before,name)):null,
      after:hash(path.join(root,'ai_core',name))})),
    full_code_files:files.map(file=>({path:relative(file),sha256:hash(file)})),
    evidence:['tests-final.txt','candidate1/answers.jsonl','candidate1/summary.json','candidate2/answers.jsonl','candidate2/judgments.json','candidate2/summary.json','candidate2/postcheck.json']
      .map(name=>({path:'results/'+name,sha256:hash(path.join(here,'results',name))}))
  };
  const md=['# ВатэсAi: отметки и условия хранения, полный код','',
    'Проект: C:/Users/KROLL/Desktop/Ai. Ниже приведены полные текущие файлы без сокращений. Ядро совпадает с хешами итогового прохода candidate2. Прежние версии сохранены в evaluation/closed_search/conditions_v10_20261031/before/ai_core.','',
    'Результат и ограничения: SEARCH_CONDITIONS_V10_REPORT_2026_10_31.md. Повторные 100 вопросов относятся к известной регрессии. Целевой уровень качества ещё не достигнут; интеграция LMS в этом этапе не обновлялась.',''];
  for(const file of files) {
    md.push('## '+relative(file),'','Полный путь: '+file+'.','','SHA-256: `'+hash(file)+'`.','',
      '````'+(file.endsWith('.py')?'python':file.endsWith('.json')?'json':'javascript'),fs.readFileSync(file,'utf8'),'````','');
  }
  fs.writeFileSync(path.join(root,'SEARCH_CONDITIONS_V10_FULL_CODE_2026_10_31.md'),md.join('\n'));
  fs.writeFileSync(path.join(here,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  console.log(JSON.stringify({changed_core:changed,full_files:files.length,runtime_files:manifest.runtime_count}));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
