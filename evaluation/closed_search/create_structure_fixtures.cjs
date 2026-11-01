/* Дополняем только изолированный каталог оригинальными проверочными файлами. */
const {fs,path,lab,login,storage}=require('./lab_client.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'structure_20261029.json'),'utf8'));
    const extra=JSON.parse(await fs.readFile(path.join(lab,'structure-assets/manifest.json'),'utf8'));
    const cookie=await login('closed-admin');
    const catalog=await storage('tea_hub_urgent_files_v1',cookie);
    for(const fixture of [...spec.documents,...extra]){
        const {text,data:saved,...metadata}=fixture;
        const data=saved||'data:text/markdown;base64,'+Buffer.from(text,'utf8').toString('base64');
        const entry={...metadata,hasSeparateData:true,section:'Проверка структуры ВатэсAi',size:Buffer.from(data.split(',')[1],'base64').length+' B',date:'Локальный тест'};
        const existing=catalog.find(item=>item.id===entry.id);
        if(existing&&JSON.stringify(existing)!==JSON.stringify(entry))throw new Error('Existing structure fixture differs: '+entry.id);
        if(existing){
            if(await storage('file_data_'+entry.id,cookie)!==data)throw new Error('Existing structure bytes differ: '+entry.id);
        }else{
            await storage('file_data_'+entry.id,cookie,data);
            catalog.push(entry);
        }
    }
    await storage('tea_hub_urgent_files_v1',cookie,catalog);
    console.log(JSON.stringify({addedFixtures:spec.documents.length+extra.length,catalogDocuments:catalog.length}));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
