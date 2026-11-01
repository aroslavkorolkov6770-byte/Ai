/* Тестовый сайт: ссылки, длинный диалог, перезапуск и живые редакции файлов. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const {createHash}=require('node:crypto');
const specPath=path.join(__dirname,'document_links_20261029.json');
const pendingPath=path.join(lab,'links-http-pending.json');
const encode=text=>'data:text/markdown;base64,'+Buffer.from(text,'utf8').toString('base64');

async function main(){
    const spec=JSON.parse(await fs.readFile(specPath,'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const mode=process.argv.includes('--restored')?'restored':process.argv.includes('--revisions')?'revisions':'main';
    const rows=[],rateLimitWaits=[];
    let cleanup=true,ready;
    const prefix='links-http-'+Date.now();
    async function ask(item,chat=prefix+'-'+item.id,actor=item.user||'b'){
        const body={chatId:chat,...(item.source?{documentId:item.source}:{}),messages:[{role:'user',content:item.q}]};
        let started=performance.now(),result=await call('/api/ai-chat',body,cookies[actor]);
        if(result.response.status===429){
            const seconds=Number(result.response.headers.get('retry-after'))||300;
            if(seconds<0||seconds>360)throw new Error('Unexpected rate-limit interval');
            rateLimitWaits.push({id:item.id,actor,seconds});console.log(JSON.stringify({event:'rate-limit-wait',seconds}));
            for(let remaining=seconds*1000+100;remaining>0;remaining-=Math.min(remaining,30000))await new Promise(resolve=>setTimeout(resolve,Math.min(remaining,30000)));
            started=performance.now();result=await call('/api/ai-chat',body,cookies[actor]);
        }
        const {response,value}=result,answer=value.output_text||'',errors=[];
        const contains=text=>new RegExp((/^\d/.test(text)?'(?<!\\d)':'')+text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(answer);
        if(response.status!==200)errors.push('http:'+response.status);
        if(!(item.allowed_statuses||[item.status||'answer']).includes(value.search_status))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!contains(text))errors.push('missing:'+text);
        for(const text of item.absent||[])if(contains(text))errors.push('unexpected:'+text);
        const links=value.sourceLinks||[];
        if(!['answer','partial'].includes(value.search_status)&&links.length)errors.push('links-on-refusal');
        if(item.evidence_ids && value.search_status==='answer' && item.evidence_ids.some(id=>!links.some(link=>link.url.includes(id))))errors.push('wrong-sources');
        rows.push({id:item.id,actor,question:item.q,status:value.search_status,answer,links,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    async function catalog(change){await storage('tea_hub_urgent_files_v1',cookies.admin,change(await storage('tea_hub_urgent_files_v1',cookies.admin)));}
    if(mode==='main'){
        const entries=await storage('tea_hub_urgent_files_v1',cookies.admin);
        for(const item of spec.documents){
            const entry={id:item.id,name:item.name,hasSeparateData:true,section:'Ссылки между документами ВатэсAi',searchAuthority:'official',
                size:Buffer.byteLength(item.text)+' B',date:'Локальный тест',...(item.allowedUserIds?{allowedUserIds:item.allowedUserIds}:{})};
            const prior=entries.find(doc=>doc.id===item.id),data=encode(item.text);
            if(prior){if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Frozen fixture changed '+item.id);}
            else{await storage('file_data_'+item.id,cookies.admin,data);entries.push(entry);}
        }
        await storage('tea_hub_urgent_files_v1',cookies.admin,entries);ready=await waitSnapshot();
        for(const item of [...spec.cases,...spec.holdout]){
            // В полном каталоге уже есть старый контрольный скан о Нефрите
            // с другим сроком. Проверка ссылки явно выбирает свой корневой файл.
            const linked=['unknown','closed','opened','direct-open','lowercase','unknown-again'].includes(item.id);
            const scenario=item.id==='compound'?{id:item.id,q:item.q,status:'conflict'}:{...item,...(linked?{source:'link_base'}:{})};
            await ask(scenario,prefix+'-'+(item.chat||item.id),['hidden-dependency','selected-hidden'].includes(item.id)?'a':item.user||'b');
        }
        for(const item of spec.dialogue)await ask({...item,...(['d17','d18'].includes(item.id)?{source:'link_base'}:{})},prefix+'-long','a');
        await ask({id:'global-independent-conflict',q:'Упаковка вскрыта. Сколько хранится товар Нефрит?',status:'conflict'});
        await ask({id:'lowercase-independent-conflict',q:'Упаковка не вскрыта. Сколько хранится товар нефрит?',status:'conflict'});
        const saved={linked:prefix+'-restore-link',topic:prefix+'-restore-topic'};
        await ask({id:'seed-linked',q:'Сколько хранится товар Нефрит?',source:'link_base',status:'clarify'},saved.linked,'b');
        await ask({id:'seed-topic',q:'Кто принимает товар Танзанит?',has:['диспетчер']},saved.topic,'a');
        await fs.writeFile(pendingPath,JSON.stringify(saved,null,2));
    }else if(mode==='restored'){
        const saved=JSON.parse(await fs.readFile(pendingPath,'utf8'));
        await ask({id:'restored-closed',q:'Упаковка не вскрыта.',has:['8 календарных'],absent:['2 календарных']},saved.linked,'b');
        await ask({id:'restored-open',q:'А если упаковка вскрыта?',has:['2 календарных'],absent:['8 календарных']},saved.linked,'b');
        await ask({id:'other-user',q:'А если упаковка вскрыта?',status:'clarify'},saved.linked,'a');
        await ask({id:'other-chat',q:'А если упаковка вскрыта?',status:'clarify'},saved.linked+'-other','b');
        await ask({id:'restored-new-operation',q:'А сколько он хранится?',has:['7 календарных']},saved.topic,'a');
    }else{
        const rootId='link_live_root',targetId='link_live_target';
        const rootName='Правило_Линкорит_проверка.md',targetName='Ограничение_Линкорит_проверка.md';
        const existing=await storage('tea_hub_urgent_files_v1',cookies.admin);
        if(existing.some(item=>[rootId,targetId].includes(item.id)))throw new Error('Unfinished revision fixture exists');
        const rootText=edition=>'Товар Линкорит хранится 9 календарных дней. См. документ «'+targetName+'», раздел 2, редакция '+edition+'.';
        const targetText=(edition,days)=>'Редакция: '+edition+'\n# 2. Хранение товара Линкорит\nИсключение: если упаковка вскрыта, товар Линкорит хранится '+days+' календарных дня.';
        const question='Упаковка вскрыта. Сколько хранится товар Линкорит?';
        cleanup=false;
        try{
            await storage('file_data_'+rootId,cookies.admin,encode(rootText(1)));
            await storage('file_data_'+targetId,cookies.admin,encode(targetText(1,2)));
            await catalog(items=>[...items,{id:rootId,name:rootName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official'},
                {id:targetId,name:targetName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official',allowedUserIds:['closed_lab_b']}]);
            await waitSnapshot();
            await ask({id:'v1',q:question,has:['2 календарных'],absent:['9 календарных']});
            await ask({id:'hidden',q:question,status:'unavailable',absent:['2 календарных',targetName]},prefix+'-hidden','a');
            await storage('file_data_'+targetId,cookies.admin,encode(targetText(2,4)));
            await ask({id:'edition-immediate',q:question,allowed_statuses:['processing','unavailable'],absent:['2 календарных','4 календарных']});
            await waitSnapshot();await ask({id:'wrong-edition',q:question,status:'unavailable'});
            await storage('file_data_'+rootId,cookies.admin,encode(rootText(2)));await waitSnapshot();
            await ask({id:'matched-edition',q:question,has:['4 календарных'],absent:['2 календарных','9 календарных']});
            await storage('file_data_'+rootId,cookies.admin,encode('Товар Линкорит хранится 9 календарных дней. См. [Уточнение](/tasks?tab=docs&documentId='+targetId+'#section-2), редакция 2.'));
            await waitSnapshot();
            await ask({id:'matched-markdown-link',q:question,has:['4 календарных'],absent:['2 календарных','9 календарных']});
            await ask({id:'base-condition',q:'Упаковка не вскрыта. Сколько хранится товар Линкорит?',has:['9 календарных'],absent:['4 календарных']});
            await catalog(items=>items.map(item=>item.id===targetId?{id:targetId,name:targetName,section:'Проверка редакций ссылок',hasSeparateData:true,searchAuthority:'official',adminOnly:true}:item));
            await ask({id:'revoked-immediate',q:question,allowed_statuses:['processing','unavailable'],absent:['4 календарных']});
            await waitSnapshot();await ask({id:'revoked',q:question,status:'unavailable'});
            await catalog(items=>items.filter(item=>item.id!==targetId));await waitSnapshot();
            await ask({id:'deleted-target',q:question,status:'unavailable'});
        }finally{
            await catalog(items=>items.filter(item=>![rootId,targetId].includes(item.id)));
            await storage('file_data_'+rootId,cookies.admin,null);await storage('file_data_'+targetId,cookies.admin,null);
            await waitSnapshot();cleanup=true;
        }
    }
    const report={createdAt:new Date().toISOString(),mode,ready,cleanup,rateLimitWaits,
        specSHA256:createHash('sha256').update(await fs.readFile(specPath)).digest('hex'),total:rows.length,passed:rows.filter(r=>r.pass).length,cases:rows};
    const file=path.join(lab,'links-http-'+mode+'-'+Date.now()+'.json');await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,cleanup,failures:rows.filter(r=>!r.pass)}));
    if(report.total!==report.passed||!cleanup)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
