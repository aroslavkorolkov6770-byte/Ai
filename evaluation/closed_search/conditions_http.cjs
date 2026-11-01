/* Проверка условий и резюме через настоящий серверный адаптер сайта. */
const {fs,path,lab,call,login,storage}=require('./lab_client.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'conditions_20261029.json'),'utf8'));
    const label=process.argv[2]||'conditions-http';
    const cookies={a:await login('closed-a'),b:await login('closed-b'),admin:await login('closed-admin')};
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    const doc=suffix=>catalog.find(item=>item.name.endsWith(suffix)).id;
    const suite=process.argv.includes('--holdout')?spec.holdout:[...spec.cases,
        {id:'summary-scan',q:'Кратко расскажи, о чем документ, и выдели главное. Используй только этот документ.',documentId:doc('скан.pdf'),has:['накладную','число мест','фото','старшего'],absent:['7193'],source:doc('скан.pdf')},
        {id:'summary-exceptions',q:'Кратко перескажи выбранный файл',documentId:'lab_cond_aurora',has:['5 календарных','10 календарных','не вскрыта','1 календарный','координатор'],source:'lab_cond_aurora'},
        {id:'summary-docx',q:'Выдели главное в документе',documentId:doc('заявок.docx'),has:['Север','трёх','старшему'],source:doc('заявок.docx')},
        {id:'summary-private-a',q:'Перескажи выбранный файл',user:'a',documentId:'policy-admin',status:'not_found',absent:['8246','Борей']},
        {id:'summary-private-b',q:'Перескажи выбранный файл',user:'b',documentId:'policy-admin',status:'not_found',absent:['8246','Борей']},
        {id:'summary-missing',q:'Перескажи выбранный файл',documentId:'missing-document',status:'not_found'},
        {id:'summary-without-selection',q:'Перескажи выбранный файл',status:'clarify',has:['Выберите документ']},
    ];
    const rows=[];
    for(const [position,item] of suite.entries()){
        const actor=item.user||['a','b','admin'][position%3];
        const started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:label+'-'+(item.chat||item.id),
            ...(item.documentId?{documentId:item.documentId}:{}),messages:[{role:'user',content:item.q}]},cookies[actor]);
        const text=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const expected of item.has||[])if(!text.toLowerCase().includes(expected.toLowerCase()))errors.push('missing:'+expected);
        for(const unwanted of item.absent||[])if(text.toLowerCase().includes(unwanted.toLowerCase()))errors.push('unwanted:'+unwanted);
        if(item.source&&!(value.sourceLinks||[]).some(link=>link.url.includes(item.source)))errors.push('source');
        if(['clarify','not_found','conflict'].includes(value.search_status)&&value.sourceLinks?.length)errors.push('unnecessary-links');
        if(item.documentId&&value.search_status==='answer'&&(value.sourceLinks||[]).some(link=>!link.url.includes(item.documentId)))errors.push('other-document');
        rows.push({id:item.id,question:item.q,actor,answer:text,status:value.search_status,links:value.sourceLinks||[],pass:!errors.length,errors,ms:Math.round(performance.now()-started)});
    }
    const report={label,createdAt:new Date().toISOString(),passed:rows.filter(row=>row.pass).length,total:rows.length,cases:rows};
    const file=path.join(lab,label+'-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,passed:report.passed,total:report.total,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
