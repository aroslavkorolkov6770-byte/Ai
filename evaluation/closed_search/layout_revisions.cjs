/* Смена байтов и прав у одного документа не должна сохранять прежние ответы. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {assets}=require('./layout_http.cjs');
const identifier='lab_layout_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    if((await storage('tea_hub_urgent_files_v1',cookies.admin)).some(item=>item.id===identifier))throw new Error('Unfinished layout revision fixture exists');
    const rows=[],chat='layout-revision-'+Date.now();
    const entry={id:identifier,name:'Временный_многостраничный_регламент.pdf',section:'Проверка смены редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']};
    async function change(update){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        await storage('tea_hub_urgent_files_v1',cookies.admin,update(catalog));
    }
    async function replace(name){
        const data='data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64');
        await storage('file_data_'+identifier,cookies.admin,data);
    }
    async function ask(id,{actor='a',q='Сколько хранится товар Амазонит?',statuses=['answer'],has=[],absent=[]}={}){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:identifier,messages:[{role:'user',content:q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of has)if(!answer.includes(text))errors.push('missing:'+text);
        for(const text of absent)if(answer.includes(text))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&(!(value.sourceLinks||[]).length||value.sourceLinks.some(link=>!link.url.includes(identifier))))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id,actor,question:q,answer,status:value.search_status,links:value.sourceLinks,pass:!errors.length,errors});
        console.log(JSON.stringify({id,pass:!errors.length,status:value.search_status,errors}));
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Таблица_без_рамок.pdf');await change(catalog=>[...catalog,entry]);await waitSnapshot();
        await ask('borderless-v1',{has:['6 календарных']});
        await ask('b-denied',{actor:'b',statuses:['not_found'],absent:['6 календарных']});
        await replace('Пустая_ячейка_без_рамок.pdf');
        await ask('missing-immediate',{statuses:['processing','unavailable'],absent:['6 календарных','14 календарных']});
        await waitSnapshot();
        await ask('missing-ready',{statuses:['unavailable'],absent:['6 календарных','14 календарных']});
        await replace('Таблица_без_рамок.pdf');await waitSnapshot();
        await ask('borderless-restored',{has:['6 календарных']});
        const q='Что сделать при повреждении упаковки поставки Флюорит?';
        await replace('Перенос_строки_Флюорит.pdf');await waitSnapshot();
        await ask('continued-v1',{q,has:['уведомите старшего смены']});
        await replace('Неверный_перенос_Флюорит.pdf');
        await ask('continued-broken-immediate',{q,statuses:['processing','unavailable'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('continued-broken-ready',{q,statuses:['unavailable'],absent:['уведомите старшего смены']});
        await replace('Перенос_строки_Флюорит.pdf');await waitSnapshot();
        await ask('continued-restored',{q,has:['уведомите старшего смены']});
        await change(catalog=>catalog.map(item=>item.id===identifier?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked-immediate',{q,statuses:['processing','not_found'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('a-revoked-ready',{q,statuses:['not_found'],absent:['уведомите старшего смены']});
        await ask('b-new-access',{actor:'b',q,has:['уведомите старшего смены']});
        await change(catalog=>catalog.filter(item=>item.id!==identifier));
        await ask('deleted-immediate',{actor:'b',q,statuses:['processing','not_found'],absent:['уведомите старшего смены']});
        await waitSnapshot();
        await ask('deleted-ready',{actor:'b',q,statuses:['not_found'],absent:['уведомите старшего смены']});
        const denied=await call('/api/storage?key=file_data_'+identifier,undefined,cookies.b);
        rows.push({id:'deleted-bytes-denied',http:denied.response.status,pass:denied.response.status===403});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            // Только временная запись этого сценария; каталог пользователя сохраняется.
            await change(catalog=>catalog.filter(item=>item.id!==identifier));
            await storage('file_data_'+identifier,cookies.admin,null);await waitSnapshot();cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
            const file=path.join(lab,'layout-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed}));
            if(!completed||!cleanup||report.passed!==report.total)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
