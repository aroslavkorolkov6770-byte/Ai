/* Подготовка новых источников. Не вызывает ИИ, поисковый индекс или обучение. */
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const sources = [
 {id:'bp30',title:'БП30Б-Д3-х — краткое руководство',type:'technical_datasheet',file:'bp30.pdf',url:'https://owen.ru/uploads/25/kr_bp30b-d3-x_m01__033_a4.pdf'},
 {id:'tplink',title:'TL-SG1005D/TL-SG1008D — руководство по установке',type:'network_installation',file:'tplink.pdf',url:'https://static.tp-link.com/resources/document/TL_SG1005D_V6_QIG_RU_7106505336_1.pdf'},
 {id:'rsl',title:'Правила пользования Российской государственной библиотекой — редакция 10.04.2023',type:'institution_rules',file:'rsl.pdf',url:'https://www.rsl.ru/uploads/29cc879c-e3ad-46f6-999a-6b06a33061e2.pdf'},
 {id:'beko',title:'Beko WKM 4215 W — руководство пользователя',type:'appliance_manual',file:'beko.pdf',url:'https://www.beko.com/content/dam/beko-global-multichannel-mc-aem/beko-global-multichannel-mc-aemProductCatalog/product-documents/8912843200-WKM-4215-W/ro-RO-8912843200-MDM2-USER-MANUAL-FILE-ro-RO.pdf'},
 {id:'post',title:'Почта России — требования к содержимому и упаковке отправлений',type:'service_rules_html',file:'post.html',url:'https://info.pochta.ru/support/post-rules/content-package-rules'}
];
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
async function main(){
 if(fs.existsSync(path.join(__dirname,'FREEZE.json')))throw new Error('Already frozen');
 const results = await Promise.allSettled(sources.map(async source => {
  const file = path.join(__dirname,'sources',source.file);
  if(fs.existsSync(file))throw new Error('Source already exists: '+source.id);
  const response = await fetch(source.url,{signal:AbortSignal.timeout(60000)});
  if(!response.ok)throw new Error(source.id+' HTTP '+response.status);
  const bytes=Buffer.from(await response.arrayBuffer());
  if(source.file.endsWith('.pdf')&&!bytes.subarray(0,5).equals(Buffer.from('%PDF-')))throw new Error('Not PDF: '+source.id);
  fs.writeFileSync(file,bytes,{flag:'wx'});
  return {...source,downloaded_at:new Date().toISOString(),final_url:response.url,bytes:bytes.length,sha256:hash(bytes),content_type:response.headers.get('content-type')};
 }));
 const complete=[];
 for(let i=0;i<results.length;i++){const result=results[i];if(result.status==='fulfilled'){complete.push(result.value);console.log(JSON.stringify(result.value));}else console.error(sources[i].id,result.reason.message);}
 fs.writeFileSync(path.join(__dirname,'sources.json'),JSON.stringify(complete,null,2));
 if(complete.length!==5)process.exitCode=1;
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
