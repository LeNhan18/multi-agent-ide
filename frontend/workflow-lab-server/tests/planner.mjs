import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import { assets, officeConfig } from '../assets.mjs';
import {mcpRoute} from '../mcp.mjs';
const source = 'const assets = '+JSON.stringify(assets)+'; const officeConfig = '+JSON.stringify(officeConfig)+';\n'+(await readFile(new URL('../handler.mjs', import.meta.url),'utf8')).replace("import { assets, officeConfig } from './assets.mjs';",'').replace("import { mcpRoute } from './mcp.mjs';",'');
let calls=[];let status=200;
const plan={title:'Đối chiếu BOQ',criteria:'Đối chiếu mã hàng',risk:'Thiếu bản vẽ',next:'Thu thập tài liệu',priority:'Vừa',hours:2};
const context={mcpRoute,Response,Request,URL,AbortSignal,crypto:webcrypto,fetch:async(url,options)=>{
 calls.push({url,options,body:JSON.parse(options.body)});
 if(status!==200)return Response.json({}, {status});
 const raw=JSON.stringify(plan);
 return Response.json(url.includes('anthropic')?{stop_reason:'end_turn',content:[{type:'text',text:raw}]}:url.includes('googleapis')?{candidates:[{finishReason:'STOP',content:{parts:[{text:raw}]}}]}:{status:'completed',output:[{content:[{type:'output_text',text:raw}]}]});
}};
vm.runInNewContext(source.replace('export default {','globalThis.worker = {'),context);
const input={prompt:'Đối chiếu báo giá',squad:'general',role:4,model:'gpt-5-mini'};
const invoke=(body,env={},auth=true,origin='https://office.test',headers={})=>context.worker.fetch(new Request('https://office.test/api/ai/task-plan',{method:'POST',headers:{Origin:origin,...(auth?{'oai-authenticated-user-id':'test'}:{}),...headers},body:JSON.stringify(body)}),env);
assert.equal((await invoke(input,{},false)).status,401);
assert.equal((await invoke(input,{},true,'https://evil.test')).status,403);
assert.equal((await invoke({...input,reasoning:'extreme'})).status,400);
assert.equal((await invoke(input)).status,503);
assert.equal(calls.length,0);
for(const [model,secret] of [['gpt-5-mini','OPENAI_API_KEY'],['claude-sonnet-5-5','ANTHROPIC_API_KEY'],['gemini-3.8-flash','GEMINI_API_KEY']]){
 const response=await invoke({...input,model,reasoning:'medium'},{[secret]:'test-secret'});
 assert.equal(response.status,200);
 const data=await response.json();assert.equal(data.provenance.effort,'medium');assert.equal(data.provenance.model,model);assert.equal(data.plan.title,plan.title);
 assert.ok(data.provenance.requestId);assert.ok(!JSON.stringify(data).includes('test-secret'));
}
assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).provenance.effort,'high');
assert.equal((await invoke({...input,connection:'dedicated'},{OPENAI_API_KEY:'shared'})).status,503);
await invoke({...input,connection:'dedicated'},{OPENAI_API_KEY_GENERAL_INTAKE:'own'});
assert.equal(calls.at(-1).options.headers.Authorization,'Bearer own');
const pasted = 'test-pasted-key';
const pastedHeader = {'X-AI-Office-Key': pasted};
plan.risk = 'Reflected ' + pasted;
const pastedPlan = await invoke({...input,connection:'dedicated'},{OPENAI_API_KEY_GENERAL_INTAKE:'server-key'},true,'https://office.test',pastedHeader);
assert.equal(pastedPlan.status,200);
assert.equal(calls.at(-1).options.headers.Authorization,'Bearer '+pasted);
assert.ok(!(await pastedPlan.text()).includes(pasted));
assert.equal((await invoke(input,{},true,'https://office.test',{'X-AI-Office-Key':'invalid key'})).status,400);
assert.equal((await invoke(input,{},true,'https://evil.test',pastedHeader)).status,403);
const providerChecks = [
 ['openai','api.openai.com','Authorization','Bearer '+pasted],
 ['anthropic','api.anthropic.com','x-api-key',pasted],
 ['gemini','generativelanguage.googleapis.com','x-goog-api-key',pasted],
];
for(const [provider,host,header,expected] of providerChecks){
 context.fetch=async(url,options)=>{
  assert.equal(new URL(url).hostname,host);
  assert.equal(options.headers[header],expected);
  assert.equal(options.redirect,'error');
  return Response.json({});
 };
 const response=await context.worker.fetch(new Request('https://office.test/api/ai/connection?provider='+provider,{
  method:'POST',headers:{Origin:'https://office.test','oai-authenticated-user-id':'test',...pastedHeader}
 }),{});
 assert.equal((await response.json()).status,'connected');
}
context.fetch=async()=>Response.json({}, {status});
status=404;assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'model_unavailable');
status=429;assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'limited');
context.fetch=async()=>{throw new DOMException('Timeout','TimeoutError');};
assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'timeout');
const project = {skill:'edit', source:'print(1)', path:'main.py', request:'In số 2'};
const assist = (body, origin='https://office.test') => context.worker.fetch(new Request('https://office.test/api/ai/project-assist',{
 method:'POST', headers:{Origin:origin,'oai-authenticated-user-id':'test','X-AI-Office-Key':'test-pasted'},
 body:JSON.stringify(body),
}),{});
assert.equal((await assist(project,'https://other.test')).status,403);
assert.equal((await assist({...project,skill:'custom'})).status,400);
assert.equal((await assist({...project,request:''})).status,400);
context.fetch=async(url,options)=>{
 const body=JSON.parse(options.body);
 assert.equal(options.headers.Authorization,'Bearer test-pasted');
 assert.equal(JSON.parse(body.input).source,'print(1)');
 assert.match(body.instructions,/Sửa đúng yêu cầu/);
 return Response.json({status:'completed',output:[{content:[{type:'output_text',text:JSON.stringify({summary:'Đổi số được in.',replacement:'print(2)'})}]}]});
};
assert.deepEqual(await (await assist(project)).json(),{summary:'Đổi số được in.',replacement:'print(2)'});
context.fetch=async(url,options)=>{
 assert.match(JSON.parse(options.body).instructions,/Tìm lỗi có căn cứ/);
 return Response.json({status:'completed',output:[{content:[{type:'output_text',text:JSON.stringify({summary:'Không thấy lỗi.',replacement:'ignored'})}]}]});
};
assert.deepEqual(await (await assist({...project,skill:'review'})).json(),{summary:'Không thấy lỗi.',replacement:''});
const html=await readFile(new URL('../../public/workflow-lab/index.html', import.meta.url),'utf8');
for(const [,script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(script);
console.log('PASS: auth, origin, validation, 3 adapters, reasoning, provenance, dedicated isolation, provider errors, timeout, inline syntax');
