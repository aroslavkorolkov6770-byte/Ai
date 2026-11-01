/* Ожидание готового индекса перед выдачей адреса стенда; ключ не выводится. */
const path=require('node:path');
const {lab}=require('./lab_client.cjs');
async function main(){
    require(path.join(lab,'tea-app-main/node_modules/@next/env')).loadEnvConfig(path.join(lab,'tea-app-main'));
    const deadline=Date.now()+45000;
    while(Date.now()<deadline){
        try{
            const response=await fetch(process.env.VATES_CLOSED_SEARCH_URL+'/status',{
                headers:{'X-Vates-Search-Key':process.env.VATES_CLOSED_SEARCH_KEY},signal:AbortSignal.timeout(4000)});
            if(response.ok&&(await response.json()).state==='ready'){
                console.log('Индекс готов к вопросам.');
                return;
            }
        }catch{
            // Отказ соединения ожидаем только во время запуска локального процесса.
        }
        await new Promise(resolve=>setTimeout(resolve,300));
    }
    throw new Error('Индекс не стал готов за отведённое время. Проверьте журнал обработчика.');
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
