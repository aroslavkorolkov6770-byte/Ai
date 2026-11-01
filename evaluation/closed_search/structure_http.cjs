/* Проверка загруженных файлов через тот же API, которым пользуется чат сайта. */
const {fs,path,lab,login,call}=require('./lab_client.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'structure_20261029.json'),'utf8'));
    const label=process.argv[2]||'structure-http';
    const cookies={a:await login('closed-a'),b:await login('closed-b'),admin:await login('closed-admin')};
    const cases=[...spec.cases,...spec.holdout,
        {id:'word-real',q:'Упаковка вскрыта. Сколько хранится товар Берилл?',has:['2 календарных'],absent:['6 календарных'],source:'lab_struct_docx'},
        {id:'html-real',q:'Упаковка вскрыта. Сколько хранится товар Селенит?',has:['2 календарных'],absent:['6 календарных'],source:'lab_struct_html'},
        {id:'summary-date',q:'Кратко перескажи выбранный файл',documentId:'lab_struct_dates',has:['7 календарных'],absent:['3 календарных','11 календарных'],source:'lab_struct_dates'},
        {id:'summary-note',q:'Кратко перескажи выбранный файл',documentId:'lab_struct_note',has:['5 календарных','1 календарный'],absent:['[1]'],source:'lab_struct_note'},
    ];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=['a','b','admin'][position%3], started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:label+'-'+item.id,
            ...(item.documentId?{documentId:item.documentId}:{}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'', errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unwanted:'+text);
        if(item.source&&!(value.sourceLinks||[]).some(link=>link.url.includes(item.source)))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('unnecessary-links');
        rows.push({id:item.id,question:item.q,actor,status:value.search_status,answer,links:value.sourceLinks||[],ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const file=path.join(lab,label+'-'+Date.now()+'.json');
    const report={createdAt:new Date().toISOString(),passed:rows.filter(row=>row.pass).length,total:rows.length,cases:rows};
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,passed:report.passed,total:report.total,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
