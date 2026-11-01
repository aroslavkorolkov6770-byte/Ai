/* Отдельные вымышленные документы: прежние темы и файлы не перезаписываются. */
const {fs,path,login,storage}=require('./lab_client.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'conditions_20261029.json'),'utf8'));
    const cookie=await login('closed-admin');
    const catalog=await storage('tea_hub_urgent_files_v1',cookie);
    for(const fixture of spec.documents){
        const {text,...metadata}=fixture;
        const entry={...metadata,hasSeparateData:true,section:'Проверка условий ВатэсAi',size:Buffer.byteLength(text)+' B',date:'Локальный тест'};
        const existing=catalog.find(item=>item.id===entry.id);
        if(existing&&JSON.stringify(existing)!==JSON.stringify(entry))throw new Error('Existing fixture differs: '+entry.id);
        const data='data:text/plain;base64,'+Buffer.from(text,'utf8').toString('base64');
        if(existing){
            if(await storage('file_data_'+entry.id,cookie)!==data)throw new Error('Existing fixture text differs: '+entry.id);
        }else{
            await storage('file_data_'+entry.id,cookie,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookie,catalog);
    console.log(JSON.stringify({fixtureDocuments:spec.documents.length,catalogDocuments:catalog.filter(item=>!item.isDocPlaceholder&&!item.isTest).length}));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
