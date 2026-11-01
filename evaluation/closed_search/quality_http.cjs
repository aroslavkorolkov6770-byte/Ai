/* Читаемые и повреждённые PDF проходят настоящий маршрут сайта и собственное ядро. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const assets=path.join(__dirname,'fixtures/quality');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const negative=new Set(spec.documents.filter(d=>d.negative).map(d=>d.id));
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        if(createHash('sha256').update(bytes).digest('hex')!==item.sha256)throw new Error('Frozen fixture changed '+item.id);
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Качество сканов ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
            ...(item.negative?{adminOnly:true}:{})};
        const prior=catalog.find(d=>d.id===item.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Site fixture changed '+item.id);
        }else{
            await storage('file_data_'+item.id,cookies.admin,data);catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
    const ready=await waitSnapshot();
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-clear',source:'lab_quality_clean',q:'Кратко перескажи выбранный файл',status:'answer',has:['13 календарных','18 календарных']},
        {id:'summary-cropped',source:'lab_quality_negation_crop',q:'Кратко перескажи выбранный файл',status:'unavailable'},
        {id:'hidden-a',actor:'a',source:'lab_quality_negation_crop',q:'Можно ли хранить товар Иолит рядом с водой?',status:'not_found'},
        {id:'hidden-b',actor:'b',source:'lab_quality_number',q:'Сколько хранится товар Селенит?',status:'not_found'}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b','admin'][position%3]);
        const started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'quality-'+Date.now()+'-'+item.id,documentId:item.source,messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[],allowed=item.status==='exact_or_refuse'?['answer','unavailable']:[item.status];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!allowed.includes(value.search_status))errors.push('status:'+value.search_status);
        if(value.search_status==='answer'){
            for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
            if(item.numbers&&JSON.stringify([...new Set(answer.match(/\b\d+\b/g)||[])].sort())!==JSON.stringify([...item.numbers].sort()))errors.push('wrong-number');
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(item.source)))errors.push('source');
        }else if(value.sourceLinks?.length)errors.push('links-on-refusal');
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'quality-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};
