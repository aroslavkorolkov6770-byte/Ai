/* Вопросы тестов извлекаются из AST как данные, без выполнения исходного TypeScript. */
const {fs,path,lab,call,login} = require('./lab_client.cjs');
function literal(node, ts) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    if (ts.isNumericLiteral(node)) return Number(node.text);
    if (ts.isArrayLiteralExpression(node)) return node.elements.map(item=>literal(item,ts));
    if (ts.isObjectLiteralExpression(node)) return Object.fromEntries(node.properties.map(item=> {
        if (!ts.isPropertyAssignment(item)) throw new Error('Unexpected test data');
        return [item.name.text, literal(item.initializer,ts)];
    }));
    throw new Error('Only literal test data is accepted');
}
async function cases() {
    const ts = require(path.join(lab,'tea-app-main/node_modules/typescript'));
    const file = path.join(lab,'tea-app-main/app/tasks/data/defaultTrainingTests.ts');
    const ast = ts.createSourceFile(file, await fs.readFile(file,'utf8'), ts.ScriptTarget.Latest, true);
    const statement = ast.statements.find(node=>ts.isVariableStatement(node));
    const tests = literal(statement.declarationList.declarations[0].initializer,ts);
    const suite = JSON.parse(await fs.readFile(path.join(__dirname,'paraphrases_20261029.json'),'utf8'));
    return suite.map(item=>({...item,q:item.quiz ? tests[item.quiz[0]].quiz[item.quiz[1]].q : item.q}));
}
async function run(suite, label) {
    const cookies = [await login('closed-a'), await login('closed-b')];
    const rows=[];
    for(let i=0;i<suite.length;i++) {
        const item=suite[i], start=performance.now();
        const result=await call('/api/ai-chat',{chatId:label+'-'+item.id,messages:[{role:'user',content:item.q}]},cookies[i%2]);
        const answer=result.value.output_text||'', status=result.value.search_status;
        const errors=[];
        if(result.response.status!==200) errors.push('http-'+result.response.status);
        if(status!==(item.status||'answer')) errors.push('status:'+status);
        if(item.source&&!(result.value.sourceLinks||[]).some(link=>link.url.includes(item.source)))errors.push('source');
        for(const term of item.has||[])if(!answer.toLowerCase().includes(term.toLowerCase()))errors.push('missing:'+term);
        for(const term of item.absent||[])if(answer.toLowerCase().includes(term.toLowerCase()))errors.push('unwanted:'+term);
        rows.push({id:item.id,question:item.q,answer,status,links:result.value.sourceLinks||[],pass:!errors.length,errors,ms:Math.round(performance.now()-start)});
    }
    const report={label,createdAt:new Date().toISOString(),passed:rows.filter(x=>x.pass).length,total:rows.length,cases:rows};
    const file=path.join(lab,label+'-'+Date.now()+'.json');
    await fs.writeFile(file,JSON.stringify(report,null,2));
    console.log(JSON.stringify({file,passed:report.passed,total:report.total,failures:rows.filter(x=>!x.pass)}));
    return report;
}
if(require.main===module) cases().then(suite=>run(suite,process.argv[2]||'paraphrase-baseline')).catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={cases,run};
