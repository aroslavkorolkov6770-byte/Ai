/* Создание настоящих тем через авторизованное API тестового сайта. */
const {fs,path,lab,login,storage}=require('./lab_client.cjs');
const {run}=require('./paraphrase_probe.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'new_topics_20261029.json'),'utf8'));
    const admin=await login('closed-admin');
    const key='tea_hub_dynamic_route_v2';
    const existing=await storage(key,admin);
    for(const item of spec.topics){
        const previous=existing.find(row=>row.id===item.id);
        if(previous && JSON.stringify(previous)!==JSON.stringify(item))throw new Error('Fixture topic already changed; do not overwrite');
        if(!previous)existing.push(item);
    }
    const started=performance.now();
    await storage(key,admin,existing);
    require(path.join(lab,'tea-app-main/node_modules/@next/env')).loadEnvConfig(path.join(lab,'tea-app-main'));
    let ready=false;
    for(let attempt=0;attempt<15;attempt++){
        const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/status',{headers:{'X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY},signal:AbortSignal.timeout(5000)});
        const status=await response.json();
        if(status.state==='ready'&&status.sources===87){ready=true;break;}
        await new Promise(resolve=>setTimeout(resolve,600));
    }
    if(!ready)throw new Error('New topics were not indexed');
    await fs.writeFile(path.join(lab,'new-topics-created.json'),JSON.stringify({topics:spec.topics.map(x=>({id:x.id,title:x.title})),readyAfterMs:Math.round(performance.now()-started)},null,2));
    await run(spec.cases,'new-topics-first');
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
