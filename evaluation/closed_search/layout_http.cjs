/* Сквозная приёмка: настоящие PDF, сайт, авторизация, OCR и собственный поиск. */
const {fs,path,lab,login,storage,call}=require('./lab_client.cjs');
const {waitSnapshot}=require('./scan_http.cjs');
const assets=path.join(__dirname,'fixtures/layout');
const negative=new Set(['lab_layout_missing','lab_layout_wrong_continued','lab_layout_wrong_notes']);
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(assets,'spec.json'),'utf8'));
    const cookies={admin:await login('closed-admin'),a:await login('closed-a'),b:await login('closed-b')};
    const catalog=await storage('tea_hub_urgent_files_v1',cookies.admin);
    for(const item of spec.documents){
        const bytes=await fs.readFile(path.join(assets,item.name));
        const data='data:application/pdf;base64,'+bytes.toString('base64');
        // Намеренно испорченные документы видит только испытательный администратор.
        // Они не должны влиять на обычный поиск сотрудника по тем же названиям.
        const entry={...item,hasSeparateData:true,section:'Проверка страниц ВатэсAi',size:bytes.length+' B',date:'Локальный тест',
            ...(negative.has(item.id)?{adminOnly:true}:{})};
        const prior=catalog.find(doc=>doc.id===item.id);
        if(prior){
            if(JSON.stringify(prior)!==JSON.stringify(entry)||await storage('file_data_'+item.id,cookies.admin)!==data)throw new Error('Layout fixture differs '+item.id);
        }else{
            await storage('file_data_'+item.id,cookies.admin,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookies.admin,catalog);
    const ready=await waitSnapshot();
    const cases=[...spec.cases,...spec.holdout,
        {id:'summary-borderless',source:'lab_layout_borderless',q:'Кратко перескажи выбранный файл',has:['Амазонит','6 календарных','Кианит','14 календарных']},
        {id:'summary-continued',source:'lab_layout_continued',q:'Кратко перескажи выбранный файл',has:['сделайте фото','уведомите старшего смены'],absent:['уведомите.']},
        {id:'summary-note',source:'lab_layout_notes',q:'Кратко перескажи выбранный файл',has:['8 календарных','2 календарных','если упаковка вскрыта'],absent:['[t8-1]']},
        {id:'a-denied-bad',actor:'a',source:'lab_layout_missing',q:'Сколько хранится товар Амазонит?',status:'not_found',absent:['6 календарных','14 календарных']},
        {id:'b-denied-bad',actor:'b',source:'lab_layout_wrong_notes',q:'Упаковка вскрыта. Сколько хранится товар Данбурит?',status:'not_found',absent:['2 календарных','8 календарных']}];
    const rows=[];
    for(const [position,item] of cases.entries()){
        const actor=item.actor||(negative.has(item.source)?'admin':['a','b'][position%2]),started=performance.now();
        const {response,value}=await call('/api/ai-chat',{chatId:'layout-'+Date.now()+'-'+item.id,documentId:item.source,
            messages:[{role:'user',content:item.q}]},cookies[actor]);
        const answer=value.output_text||'',errors=[];
        if(response.status!==200)errors.push('http:'+response.status);
        if(value.search_status!==(item.status||'answer'))errors.push('status:'+value.search_status);
        for(const text of item.has||[])if(!answer.toLowerCase().includes(text.toLowerCase()))errors.push('missing:'+text);
        for(const text of item.absent||[])if(answer.toLowerCase().includes(text.toLowerCase()))errors.push('unexpected:'+text);
        if(value.search_status==='answer'&&(!(value.sourceLinks||[]).length||value.sourceLinks.some(link=>!link.url.includes(item.source))))errors.push('source');
        if(value.search_status!=='answer'&&value.sourceLinks?.length)errors.push('links-on-refusal');
        rows.push({id:item.id,actor,question:item.q,answer,status:value.search_status,links:value.sourceLinks,ms:Math.round(performance.now()-started),pass:!errors.length,errors});
    }
    const report={createdAt:new Date().toISOString(),ready,total:rows.length,passed:rows.filter(row=>row.pass).length,cases:rows};
    const file=path.join(lab,'layout-http-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,total:report.total,passed:report.passed,failures:rows.filter(row=>!row.pass)}));
    if(report.passed!==report.total)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={assets};
