/* Настоящие PDF загружаются только в изолированный каталог стенда. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {build}=require('./export_site.cjs');
const assets=path.join(__dirname,'fixtures/scans');
async function waitSnapshot(){
    const payload=build();
    const headers={'Content-Type':'application/json','X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY};
    const endpoint=process.env.VATES_CLOSED_SEARCH_URL;
    const response=await fetch(endpoint+'/snapshot',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Snapshot submit failed '+response.status);
    const expected=(await response.json()).snapshot, deadline=Date.now()+90000;
    while(Date.now()<deadline){
        const status=await fetch(endpoint+'/status',{headers,signal:AbortSignal.timeout(5000)}).then(r=>r.json());
        if(status.state==='ready'&&status.snapshot===expected)return status;
        if(status.state==='failed')throw new Error('Snapshot extraction failed');
        await new Promise(resolve=>setTimeout(resolve,500));
    }
    throw new Error('Snapshot not ready before deadline');
}
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const admin=await login('closed-admin');
    const catalog=await storage('tea_hub_urgent_files_v1',admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        const entry={...item,hasSeparateData:true,section:'Проверка сканов ВатэсAi',size:bytes.length+' B',date:'Локальный тест'};
        const prior=catalog.find(doc=>doc.id===entry.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+entry.id,admin)!==data)throw new Error('Scan fixture differs '+entry.id);
        }else{
            await storage('file_data_'+entry.id,admin,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',admin,catalog);
    const ready=await waitSnapshot();
    const cookies={a:await login('closed-a'),b:await login('closed-b'),admin};
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-matrix',source:'lab_scan_matrix',documentId:'lab_scan_matrix',q:'Кратко перескажи выбранный файл',has:['Аметрин','8 календарных','2 календарных','Если упаковка']},
        {id:'summary-note',source:'lab_scan_note',documentId:'lab_scan_note',q:'Кратко перескажи выбранный файл',has:['11 календарных','3 календарных'],absent:['[p1-1]']},
        {id:'summary-damaged',documentId:'lab_scan_damaged',q:'Кратко перескажи выбранный файл',status:'unavailable'}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=['a','b','admin'][position%3],started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'scan-'+Date.now()+'-'+item.id,
            ...(item.documentId?{documentId:item.documentId}:{}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        if((item.status||'answer')==='answer'&&!(value.sourceLinks||[]).some(link=>link.url.includes(item.source)))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('unnecessary-links');
        rows.push({id:item.id,actor,question:item.q,answer,status:value.search_status,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'scan-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={waitSnapshot,assets};
