/* Повреждение новой редакции не разрешает ни старое значение, ни потерянное «не». */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {assets}=require('./quality_http.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const id='lab_quality_revision_probe';
async function main(){
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    if((await storage('tea_hub_urgent_files_v1',cookies.admin)).some(d=>d.id===id))throw new Error('Unfinished quality revision fixture exists');
    const rows=[],chat='quality-revision-'+Date.now();
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    async function replace(name){await storage('file_data_'+id,cookies.admin,'data:application/pdf;base64,'+(await fs.readFile(path.join(assets,name))).toString('base64'));}
    async function ask(name,statuses,has=[],actor='a'){
        const {response,value}=await call('/api/ai-chat',{chatId:chat,documentId:id,messages:[{role:'user',content:'Можно ли хранить товар Иолит рядом с водой?'}]},cookies[actor]);
        const text=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!statuses.includes(value.search_status))errors.push('status:'+value.search_status);
        for(const part of has)if(!text.includes(part))errors.push('missing:'+part);
        if(value.search_status!=='answer'&&(value.sourceLinks?.length||text.toLowerCase().includes('храните товар')))errors.push('unsafe-refusal');
        if(value.search_status==='answer'&&(!text.includes('Не храните')||!value.sourceLinks?.length||value.sourceLinks.some(link=>!link.url.includes(id))))errors.push('wrong-answer');
        rows.push({id:name,actor,status:value.search_status,answer:text,pass:!errors.length,errors});
    }
    let completed=false,cleanup=false,failure=null;
    try{
        await replace('Чёткий_запрет.pdf');
        await catalog(items=>[...items,{id,name:'Временный_запрет_качество.pdf',section:'Проверка редакций',hasSeparateData:true,allowedUserIds:['closed_lab_a']}]);
        await waitSnapshot();await ask('clear',['answer'],['Не храните']);
        await ask('b-denied',['not_found'],[],'b');
        await replace('Обрезанное_отрицание.pdf');
        await ask('cropped-immediate',['processing','unavailable']);
        await waitSnapshot();await ask('cropped-ready',['unavailable']);
        await replace('Тень_на_запрете.pdf');await waitSnapshot();await ask('shadow-restored',['answer'],['Не храните']);
        await catalog(items=>items.map(item=>item.id===id?{...item,allowedUserIds:['closed_lab_b']}:item));
        await ask('a-revoked-immediate',['processing','not_found']);
        await waitSnapshot();await ask('b-allowed',['answer'],['Не храните'],'b');
        await catalog(items=>items.filter(item=>item.id!==id));
        await ask('removed',['processing','not_found'],[],'b');
        await waitSnapshot();await ask('removed-ready',['not_found'],[],'b');
        const denied=await call('/api/storage?key=file_data_'+id,undefined,cookies.b);
        rows.push({id:'orphan-bytes-denied',pass:denied.response.status===403,http:denied.response.status});
        completed=true;
    }catch(error){failure=error.message;throw error;}
    finally{
        try{
            await catalog(items=>items.filter(item=>item.id!==id));
            await storage('file_data_'+id,cookies.admin,null);await waitSnapshot();cleanup=true;
        }finally{
            const report={createdAt:new Date().toISOString(),completed,cleanup,failure,total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
            const file=path.join(lab,'quality-revisions-'+Date.now()+'.json');
            await fs.writeFile(file,JSON.stringify(report,null,2));
            console.log(JSON.stringify({file,completed,cleanup,total:report.total,passed:report.passed,failures:rows.filter(r=>!r.pass)}));
            if(!completed||!cleanup||report.total!==report.passed)process.exitCode=1;
        }
    }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
