/* Многостраничные PDF проходят реальную загрузку, фоновые задачи и API сайта. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const assets=path.join(__dirname,'fixtures/multipage');

function contains(answer,text){
    const escaped=text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+escaped,'i').test(answer);
}

async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const negative=new Set(spec.documents.filter(d=>d.negative).map(d=>d.id));
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        if(createHash('sha256').update(bytes).digest('hex')!==item.sha256)throw new Error('Frozen fixture changed '+item.id);
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Многостраничные таблицы ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
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
        {id:'summary-chain',source:'lab_multi_chain',q:'Кратко перескажи выбранный файл',has:['сделайте фото','уведомите координатора','бумажный журнал']},
        {id:'summary-rows',source:'lab_multi_rows',q:'Кратко перескажи выбранный файл',has:['11 календарных','16 календарных','21 календарных','26 календарных']},
        {id:'summary-notes',source:'lab_multi_notes',q:'Кратко перескажи выбранный файл',has:['12 календарных','3 календарных','2 календарных','если упаковка','если этикетка']},
        {id:'summary-incomplete',source:'lab_multi_missing',q:'Кратко перескажи выбранный файл',status:'unavailable'},
        {id:'hidden-a',actor:'a',source:'lab_multi_revision',q:spec.cases[0].q,status:'not_found'},
        {id:'hidden-b',actor:'b',source:'lab_multi_wrong',q:spec.cases[0].q,status:'not_found'},
        {...spec.cases[0],id:'automatic-source',actor:'a',automatic:true}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b','admin'][position%3]);
        const started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'multipage-'+Date.now()+'-'+item.id,
            ...(item.automatic?{}:{documentId:item.source}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        if(value.search_status==='answer'){
            for(const text of item.has||[])if(!contains(answer,text))errors.push('missing:'+text);
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(item.source)))errors.push('source');
        }else if(value.sourceLinks?.length)errors.push('links-on-refusal');
        for(const text of item.absent||[])if(contains(answer,text))errors.push('unexpected:'+text);
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'multipage-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};
