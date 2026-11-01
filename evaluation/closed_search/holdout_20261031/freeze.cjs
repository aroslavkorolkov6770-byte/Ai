/* Фиксируем входы до обращения к ИИ. Контрольные суммы проверяем повторно в конце. */
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const root=path.resolve(__dirname,'../../..');
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function walk(directory){return fs.readdirSync(directory,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?walk(path.join(directory,entry.name)):[path.join(directory,entry.name)]);}
function main(){
 const questions=JSON.parse(fs.readFileSync(path.join(__dirname,'questions.json'),'utf8'));
 const gold=JSON.parse(fs.readFileSync(path.join(__dirname,'gold.json'),'utf8'));
 const sources=JSON.parse(fs.readFileSync(path.join(__dirname,'sources.json'),'utf8'));
 if(questions.length!==100||gold.length!==100||sources.length!==5)throw new Error('Wrong count');
 for(const q of questions){if(Object.keys(q).sort().join(',')!=='chat,id,question'||!gold.some(g=>g.id===q.id))throw new Error('Invalid question');}
 for(const source of sources){if(hash(path.join(__dirname,'sources',source.file))!==source.sha256)throw new Error('Source changed');}
 const inputs=['sources.json','questions.json','gold.json','QUESTIONS_AND_GOLD.md','METHOD.md','author.cjs','prepare.cjs','read_reference.py','freeze.cjs','run.py',...sources.map(s=>'sources/'+s.file)];
 const files=Object.fromEntries(inputs.map(name=>[name,hash(path.join(__dirname,name))]));
 const runtime=Object.fromEntries(walk(path.join(root,'ai_core')).filter(file=>file.endsWith('.py')).sort().map(file=>[path.relative(root,file).replaceAll('\\','/'),hash(file)]));
 const freeze={frozen_at:new Date().toISOString(),questions:100,documents:5,files,runtime};
 fs.writeFileSync(path.join(__dirname,'FREEZE.json'),JSON.stringify(freeze,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({frozen_at:freeze.frozen_at,freeze_sha256:hash(path.join(__dirname,'FREEZE.json')),runtime_files:Object.keys(runtime).length}));
}
try{main();}catch(error){console.error(error.stack);process.exitCode=1;}
