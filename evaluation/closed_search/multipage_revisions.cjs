/* Изменяется только временный документ этого прогона; очистка выполняется в finally. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {assets}=require('./multipage_http.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const id='lab_multipage_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const initial=await storage('tea_hub_urgent_files_v1',cookies.admin);
    const unfinished=initial.find(d=>d.id===id);
    if(unfinished && process.argv.includes('--recover')){
        // После аварийного завершения разрешено убрать только собственный
        // временный объект с известным именем и байтами контрольного PDF.
        if(unfinished.name!=='Временная_многостраничная_таблица.pdf'||unfinished.section!=='Проверка редакций'
            ||unfinished.allowedUserIds?.length!==1||!['closed_lab_a','closed_lab_b'].includes(unfinished.allowedUserIds[0]))throw new Error('Unexpected recovery object');
        const data=await storage('file_data_'+id,cookies.admin);
        const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
        let recognized=false;
        for(const item of spec.documents){
            if(data==='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,item.name))).toString('base64'))recognized=true;
        }
        if(!recognized)throw new Error('Unknown bytes: recovery refused');
        await storage('tea_hub_urgent_files_v1',cookies.admin,initial.filter(d=>d.id!==id));
        await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();
        console.log(JSON.stringify({recovered:true,id}));return;
    }
    if(unfinished)throw new Error('Unfinished multipage fixture exists');
    const rows=[],chat='multipage-revision-'+Date.now();
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    async function replace(name){await storage('file_data_'+id,cookies.admin,'data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64'));}
    async function ask(name,statuses,tail='',actor='a'){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:id,
            messages:[{role:'user',content:'Что сделать при повреждении упаковки поставки Родонит?'}]},cookies[actor]);
        const text=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        if(value.search_status!=='answer'&&(value.sourceLinks?.length||text.includes('журнал')))errors.push('stale-answer');
        if(value.search_status==='answer'){
            if(!tail||!text.includes(tail)||text.includes(tail.includes('бумажный')?'электронный журнал':'бумажный журнал'))errors.push('wrong-tail');
            if(!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(id)))errors.push('wrong-source');
        }
        rows.push({id:name,actor,status:value.search_status,answer:text,pass:!errors.length,errors});
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Три_страницы_Родонит.pdf');
        await catalog(items=>[...items,{id,name:'Временная_многостраничная_таблица.pdf',section:'Проверка редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']}]);
        await waitSnapshot();await ask('v1',['answer'],'бумажный журнал');await ask('b-denied',['not_found'],'','b');
        await replace('Новая_редакция_Родонит.pdf');
        await ask('v2-immediate',['processing','answer'],'электронный журнал');
        await waitSnapshot();await ask('v2-ready',['answer'],'электронный журнал');
        await replace('Без_последней_страницы.pdf');
        await ask('incomplete-immediate',['processing','unavailable']);
        await waitSnapshot();await ask('incomplete-ready',['unavailable']);
        await replace('Три_страницы_Родонит.pdf');await waitSnapshot();await ask('restored',['answer'],'бумажный журнал');
        await catalog(items=>items.map(item=>item.id===id?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked',['processing','not_found']);await waitSnapshot();
        await ask('b-allowed',['answer'],'бумажный журнал','b');
        await catalog(items=>items.filter(item=>item.id!==id));
        await ask('removed',['processing','not_found'],'','b');await waitSnapshot();await ask('removed-ready',['not_found'],'','b');
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{await catalog(items=>items.filter(item=>item.id!==id));await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();cleanup=true;}
        finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
            const file=path.join(lab,'multipage-revisions-'+Date.now()+'.json');await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
            if(!completed||!cleanup||report.total!==report.passed)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
