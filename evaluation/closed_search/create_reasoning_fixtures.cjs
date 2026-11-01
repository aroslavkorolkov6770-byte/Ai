/* Добавляет только отдельные вымышленные материалы через штатное API стенда. */
const {fs,path,lab,login,storage}=require('./lab_client.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'reasoning_20261029.json'),'utf8'));
    const admin=await login('closed-admin');
    const topics=await storage('tea_hub_dynamic_route_v2',admin);
    const catalog=await storage('tea_hub_urgent_files_v1',admin);
    for(const item of spec.topics){
        const previous=topics.find(row=>row.id===item.id);
        if(previous&&JSON.stringify(previous)!==JSON.stringify(item))throw new Error('Existing fixture changed; refusing overwrite');
        if(!previous)topics.push(item);
    }
    for(const fixture of spec.documents){
        const {text,...metadata}=fixture;
        const item={...metadata,hasSeparateData:true,section:'Проверка источников ВатэсAi',size:Buffer.byteLength(text)+' B',date:'Локальный тест'};
        const previous=catalog.find(row=>row.id===item.id);
        if(previous&&JSON.stringify(previous)!==JSON.stringify(item))throw new Error('Existing document changed; refusing overwrite');
        if(!previous){
            await storage('file_data_'+item.id,admin,'data:text/plain;base64,'+Buffer.from(text,'utf8').toString('base64'));
            catalog.push(item);
        }
    }
    await storage('tea_hub_dynamic_route_v2',admin,topics);
    await storage('tea_hub_urgent_files_v1',admin,catalog);
    require(path.join(lab,'tea-app-main/node_modules/@next/env')).loadEnvConfig(path.join(lab,'tea-app-main'));
    const expected=require('./export_site.cjs').build().sources.length;
    let ready=false;
    for(let attempt=0;attempt<20;attempt++){
        const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/status',{headers:{'X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY},signal:AbortSignal.timeout(5000)});
        const status=await response.json();
        if(status.state==='ready'&&status.sources===expected){ready=true;break;}
        await new Promise(resolve=>setTimeout(resolve,500));
    }
    if(!ready)throw new Error('Waiting for background indexing timed out');
    console.log(JSON.stringify({addedTopics:spec.topics.length,addedDocuments:spec.documents.length,indexedSources:expected}));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
