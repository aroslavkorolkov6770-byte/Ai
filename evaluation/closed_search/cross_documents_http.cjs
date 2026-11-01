/* Реальные загрузка, две сессии сайта и восстановление составного диалога. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const specPath=path.join(__dirname,'cross_documents_20261029.json');
const pendingPath=path.join(lab,'cross-http-pending.json');

function contains(text,fragment){
    const escaped=fragment.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return new RegExp((/^\d/.test(fragment)?'(?<!\\d)':'')+escaped,'i').test(text);
}

async function main(){
    const spec=JSON.parse(await fs.readFile(specPath,'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const restored=process.argv.includes('--restored');
    const rows=[],rateLimitWaits=[];
    async function ask(item,chat){
        const actor=item.user||'a';
        let started=performance.now();
        const body={chatId:chat,messages:[{role:'user',content:item.q}]};
        let result=await call('/api/ai-chat',body,cookies[actor]);
        if(result.response.status===429){
            // Не отключаем лимит стенда. Один повтор только после указанного
            // сервером срока; ожидание записывается отдельно от скорости ИИ.
            const seconds=Number(result.response.headers.get('retry-after'))||300;
            if(seconds<0||seconds>360)throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({id:item.id,actor,seconds});
            console.log(JSON.stringify({event:'respecting-rate-limit',id:item.id,seconds}));
            for(let remaining=seconds*1000+100;remaining>0;remaining-=Math.min(remaining,30000))await new Promise(resolve=>setTimeout(resolve,Math.min(remaining,30000)));
            started=performance.now();
            result=await call('/api/ai-chat',body,cookies[actor]);
        }
        const {response,value}=result;
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(!(item.allowed_statuses||[item.status||'answer']).includes(value.search_status))errors.push('status:'+value.search_status);
        for(const fragment of item.has||[])if(!contains(answer,fragment))errors.push('missing:'+fragment);
        for(const fragment of item.absent||[])if(contains(answer,fragment))errors.push('unexpected:'+fragment);
        const links=value.sourceLinks||[];
        if(item.sources && (links.length!==item.sources.length||item.sources.some(id=>!links.some(link=>link.url.includes(id)))))errors.push('sources');
        if(!['answer','partial'].includes(value.search_status)&&links.length)errors.push('links-on-refusal');
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    let ready;
    if(!restored){
        const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
        for(const item of spec.documents){
            const bytes=Buffer.from(item.text,'utf8'),data='data:text/markdown;base64,'+bytes.toString('base64');
            const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Составные вопросы ВатэсAi',size:bytes.length+' B',date:'Локальный тест',searchAuthority:'official',
                ...(item.allowedUserIds?{allowedUserIds:item.allowedUserIds}:{}),...(item.status?{status:item.status}:{})};
            const prior=catalog.find(doc=>doc.id===item.id);
            if(prior){
                if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Frozen site fixture changed '+item.id);
            }else{
                await storage('file_data_'+item.id,cookies.admin,data);catalog.push(entry);
            }
        }
        await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
        ready=await waitSnapshot();
        const prefix='cross-http-'+Date.now();
        for(const item of [...spec.cases,...spec.holdout])await ask(item,prefix+'-'+(item.chat||item.id));
        const saved={conditions:prefix+'-restart-conditions',compound:prefix+'-restart-compound'};
        await ask({id:'seed-condition',user:'b',q:'Сколько хранится товар Танзанит?',status:'clarify',has:['упаковка']},saved.conditions);
        await ask({id:'seed-compound',user:'b',q:'Кто принимает поставку Содалит и сколько дней она обрабатывается?',status:'partial',has:['координатор склада','Уточните'],sources:['lab_cross_receiver']},saved.compound);
        await fs.writeFile(pendingPath,JSON.stringify(saved,null,2));
    }else{
        const saved=JSON.parse(await fs.readFile(pendingPath,'utf8'));
        await ask({id:'restored-other-user',q:'А если упаковка вскрыта?',status:'clarify'},saved.conditions);
        await ask({id:'restored-other-chat',user:'b',q:'А если упаковка вскрыта?',status:'clarify'},saved.conditions+'-other');
        await ask({id:'restored-false',user:'b',q:'Упаковка не вскрыта.',has:['7 календарных'],sources:['lab_cross_public']},saved.conditions);
        await ask({id:'restored-conflict',user:'b',q:'А если упаковка вскрыта?',status:'conflict'},saved.conditions);
        await ask({id:'restored-compound',user:'b',q:'Упаковка вскрыта.',has:['2 рабочих'],absent:['6 рабочих'],sources:['lab_cross_processing']},saved.compound);
        await ask({id:'restored-correction',user:'b',q:'А если упаковка не вскрыта?',has:['6 рабочих'],absent:['2 рабочих'],sources:['lab_cross_processing']},saved.compound);
    }
    const report={createdAt:new Date().toISOString(),restored,ready,rateLimitWaits,specSHA256:createHash('sha256').update(await fs.readFile(specPath)).digest('hex'),
        total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'cross-http-'+(restored?'restored-':'')+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
