/* Реальный рестарт ядра при остановленном экспортёре. Изменения прав обратимы. */
const {fs,path,lab,root,call,login,storage}=require('./lab_client.cjs');
const {spawn}=require('node:child_process');
require(path.join(lab,'tea-app-main/node_modules/@next/env')).loadEnvConfig(path.join(lab,'tea-app-main'));
const headers={'X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY};
async function status(){
    const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/status',{headers,signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Status unavailable');
    return response.json();
}
async function ready(){
    for(let i=0;i<40;i++){
        try{const value=await status();if(value.state==='ready')return value;}catch{}
        await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw new Error('Search did not become ready');
}
async function powershell(script){
    // Дочерний сервер не должен удерживать pipe и мешать завершению оболочки.
    const prefix=path.join(lab,'restart-shell-'+Date.now());
    const out=await fs.open(prefix+'.out.log','w'),err=await fs.open(prefix+'.err.log','w');
    try{
        await new Promise((resolve,reject)=>{
            const child=spawn(process.env.VATES_TEST_POWERSHELL||'pwsh.exe',['-NoProfile','-Command',script],{cwd:root,windowsHide:true,stdio:['ignore',out.fd,err.fd]});
            const timer=setTimeout(()=>{child.kill();reject(new Error('Local shell timed out'));},45000);
            child.once('error',error=>{clearTimeout(timer);reject(error);});
            child.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error('Local shell failed: '+code));});
        });
        return {stdout:await fs.readFile(prefix+'.out.log','utf8')};
    }finally{await out.close();await err.close();}
}
async function main(){
    const cookies={a:await login('closed-a'),b:await login('closed-b'),admin:await login('closed-admin')};
    const rows=[];
    const prefix='restart-'+Date.now();
    const original=await storage('tea_hub_urgent_files_v1',cookies.admin);
    const target=original.find(item=>item.name.endsWith('заявок.docx'));
    if(!target)throw new Error('Fixture document missing');
    let changed=false;
    async function ask(id,actor,chat,question,check){
        const answer=await call('/api/ai-chat',{chatId:prefix+chat,messages:[{role:'user',content:question}]},cookies[actor]);
        const value=answer.value;
        rows.push({id,status:value.search_status,answer:value.output_text,pass:answer.response.ok&&check(value)});
        console.log(JSON.stringify({id,pass:rows.at(-1).pass}));
        return value;
    }
    try{
        await ready();
        await ask('seed-a-north','a','one','Кому направить заявку Север?',v=>v.output_text?.includes('старшему'));
        await ask('seed-a-lira','a','two','Как хранить образцы Лира?',v=>v.output_text?.includes('16'));
        await ask('seed-b-comet','b','one','Как отменить заявку Комета?',v=>v.output_text?.includes('диспетчеру'));
        const before=await status();
        // Останавливается только Node с точным путём нашего экспортёра.
        await powershell("$scriptPath=Join-Path (Get-Location) 'evaluation\\closed_search\\export_site.cjs'; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like ('*'+$scriptPath+'*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }");
        const start=performance.now();
        await powershell('& .\\scripts\\start_closed_search_lab.ps1 -RestartSearch -SkipExporter');
        const after=await ready();
        const exporter=await powershell("$scriptPath=Join-Path (Get-Location) 'evaluation\\closed_search\\export_site.cjs'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like ('*'+$scriptPath+'*') }).Count");
        rows.push({id:'disk-restore-without-exporter',pass:after.restored_from_disk===true&&after.sources===before.sources&&after.passages===before.passages&&after.snapshot===before.snapshot&&Number(exporter.stdout.trim())===0,
            beforeSources:before.sources,afterSources:after.sources,passages:after.passages,restoreMs:after.restore_ms,restartUntilReadyMs:Math.round(performance.now()-start),exporterRunning:Number(exporter.stdout.trim())});
        await ask('restored-north','a','one','А сколько дней?',v=>v.context_used&&v.output_text?.includes('трёх'));
        await ask('restored-lira','a','two','А сколько дней?',v=>v.context_used&&v.output_text?.includes('9 календарных'));
        await ask('restored-comet','b','one','А сколько дней?',v=>v.context_used&&v.output_text?.includes('2 рабочих'));
        await ask('other-user-chat','b','two','А сколько дней?',v=>v.search_status==='clarify');
        await ask('new-chat','a','fresh','А сколько дней?',v=>v.search_status==='clarify');
        changed=true;
        await storage('tea_hub_urgent_files_v1',cookies.admin,original.map(item=>item.id===target.id?{...item,accessPolicy:{version:1,visibility:'admin'}}:item));
        await ask('access-revoked-live','a','one','А сколько дней?',v=>v.search_status!=='answer'&&!v.sourceLinks?.length);
        await storage('tea_hub_urgent_files_v1',cookies.admin,original.map(item=>item.id===target.id?{...item,name:'Изменённый_'+item.name}:item));
        await ask('revision-changed-live','a','fresh','Сколько обрабатывается заявка Север?',v=>v.search_status==='processing');
        await storage('tea_hub_urgent_files_v1',cookies.admin,original.filter(item=>item.id!==target.id));
        await ask('deleted-document-live','a','fresh','Сколько обрабатывается заявка Север?',v=>v.search_status==='not_found'&&!v.sourceLinks?.length);
        await storage('tea_hub_urgent_files_v1',cookies.admin,original);
        changed=false;
        await powershell('& .\\scripts\\start_closed_search_lab.ps1 -RestartSearch -SkipExporter');
        await ready();
        await ask('revoked-context-stays-cleared','a','one','А сколько дней?',v=>v.search_status==='clarify');
    }finally{
        try{
            if(changed)await storage('tea_hub_urgent_files_v1',cookies.admin,original);
        }finally{
            await powershell('& .\\scripts\\start_closed_search_lab.ps1');
        }
    }
    const report={createdAt:new Date().toISOString(),passed:rows.filter(row=>row.pass).length,total:rows.length,cases:rows};
    const file=path.join(lab,'restart-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,passed:report.passed,total:report.total,failures:rows.filter(row=>!row.pass),restore:rows.find(row=>row.id==='disk-restore-without-exporter')}));
    if(report.passed!==report.total)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
