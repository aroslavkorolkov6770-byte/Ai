/* Смена реальных PDF/прав: каждый ответ идёт через авторизацию сайта. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot,assets}=require('./scan_http.cjs');
const identifier='lab_scan_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const initial=await storage('tea_hub_urgent_files_v1',cookies.admin);
    if(initial.some(item=>item.id===identifier))throw new Error('Unfinished temporary revision fixture exists');
    const rows=[],chat='scan-revision-'+Date.now();
    const v1='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,'Редакция_Родонит_v1.pdf'))).toString('base64');
    const v2='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,'Редакция_Родонит_v2.pdf'))).toString('base64');
    const entry={id:identifier,name:'Временный_регламент_Родонит.pdf',section:'Проверка смены редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']};
    async function change(update){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        await storage('tea_hub_urgent_files_v1',cookies.admin,update(catalog));
    }
    async function bytes(data){await storage('file_data_'+identifier,cookies.admin,data);}
    async function ask(id,{actor='a',q='Упаковка вскрыта. Сколько хранится товар Родонит?',statuses=['answer'],has=[],absent=[],selected=true,chatId=chat}={}){
        const {response,value}=await call('/api/ai-chat',{chatId,...(selected?{documentId:identifier}:{}),messages:[{role:'user',content:q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of has)if(!answer.includes(text))errors.push('missing:'+text);
        for(const text of absent)if(answer.includes(text))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&!(value.sourceLinks||[]).some(link=>link.url.includes(identifier)))errors.push('wrong-source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id,actor,question:q,answer,status:value.search_status,links:value.sourceLinks,pass:!errors.length,errors});
        console.log(JSON.stringify({id,pass:!errors.length,status:value.search_status,errors}));
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await bytes(v1); await change(catalog=>[...catalog,entry]); await waitSnapshot();
        await ask('v1-exception',{has:['1 календарный'],absent:['3 календарных','7 календарных']});
        await ask('v1-base',{q:'Упаковка не вскрыта. Сколько хранится товар Родонит?',has:['7 календарных'],absent:['1 календарный','11 календарных']});
        await ask('v1-b-denied',{actor:'b',statuses:['not_found'],absent:['7 календарных','1 календарный']});
        await bytes(v2);
        await ask('immediate-replacement',{statuses:['processing','answer'],absent:['7 календарных','1 календарный']});
        await waitSnapshot();
        await ask('v2-old-chat',{has:['3 календарных'],absent:['7 календарных','1 календарный','11 календарных']});
        await ask('v2-base',{q:'Упаковка не вскрыта. Сколько хранится товар Родонит?',has:['11 календарных'],absent:['7 календарных']});
        await ask('v2-summary',{q:'Кратко перескажи выбранный файл',has:['11 календарных','3 календарных'],absent:['7 календарных','1 календарный']});
        // Повреждённая новая загрузка не даёт права вернуться к прежнему PDF.
        await bytes('data:application/pdf;base64,'+Buffer.from('%PDF-broken-test-file').toString('base64'));
        await ask('broken-immediate',{statuses:['processing','unavailable'],absent:['11 календарных','3 календарных']});
        await waitSnapshot();
        await ask('broken-ready',{statuses:['unavailable'],absent:['11 календарных','3 календарных']});
        await bytes(v2); await waitSnapshot();
        await ask('repaired-same-bytes',{has:['3 календарных'],absent:['1 календарный']});
        // Быстрые последовательные снимки: окончательная редакция всегда v2.
        await bytes(v1);
        const {build}=require('./export_site.cjs');
        const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/snapshot',{method:'POST',headers:{'Content-Type':'application/json','X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY},body:JSON.stringify(build()),signal:AbortSignal.timeout(15000)});
        if(!response.ok)throw new Error('Race snapshot submit failed');
        await bytes(v2); await waitSnapshot();
        await ask('rapid-revisions-final',{has:['3 календарных'],absent:['1 календарный','7 календарных']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('revoked-a-immediately',{statuses:['processing','not_found'],absent:['11 календарных','3 календарных']});
        await waitSnapshot();
        await ask('revoked-a-after-index',{statuses:['not_found'],absent:['11 календарных','3 календарных']});
        await ask('b-new-access',{actor:'b',has:['3 календарных'],absent:['1 календарный']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,isArchived:true}:item));
        await ask('archived-immediate',{actor:'b',statuses:['processing','not_found'],absent:['3 календарных']});
        await waitSnapshot();
        await ask('archived-after-index',{actor:'b',statuses:['not_found'],absent:['3 календарных']});
        await change(catalog=>catalog.filter(item=>item.id!==identifier));
        await ask('removed-old-chat',{actor:'b',statuses:['processing','not_found'],absent:['3 календарных','11 календарных']});
        const denied=await call('/api/storage?key=file_data_'+identifier,undefined,cookies.b);
        rows.push({id:'orphan-bytes-denied',pass:denied.response.status===403,http:denied.response.status});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            // Удаляем только созданную этим сценарием временную запись.
            await change(catalog=>catalog.filter(item=>item.id!==identifier));
            await bytes(null); await waitSnapshot(); cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
            const file=path.join(lab,'scan-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed}));
            if(!completed||!cleanup||report.passed!==report.total)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
