/* Фиксированный набор; контрольные формулировки запускаются отдельным режимом. */
const {fs,path}=require('./lab_client.cjs');
const {run}=require('./paraphrase_probe.cjs');
async function main(){
    const spec=JSON.parse(await fs.readFile(path.join(__dirname,'reasoning_20261029.json'),'utf8'));
    const holdout=process.argv.includes('--holdout');
    await run(holdout?spec.holdout:spec.cases,process.argv[2]||'reasoning-final-http');
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
