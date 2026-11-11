import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import { assets, officeConfig } from '../assets.mjs';
const source = 'const assets = '+JSON.stringify(assets)+'; const officeConfig = '+JSON.stringify(officeConfig)+';\n'+(await readFile(new URL('../handler.mjs', import.meta.url),'utf8')).replace("import { assets, officeConfig } from './assets.mjs';",'');
let calls=[];let status=200;
const plan={title:'Đối chiếu BOQ',criteria:'Đối chiếu mã hàng',risk:'Thiếu bản vẽ',next:'Thu thập tài liệu',priority:'Vừa',hours:2};
const context={Response,Request,URL,AbortSignal,crypto:webcrypto,fetch:async(url,options)=>{
 calls.push({url,options,body:JSON.parse(options.body)});
 if(status!==200)return Response.json({}, {status});
 const raw=JSON.stringify(plan);
 return Response.json(url.includes('anthropic')?{stop_reason:'end_turn',content:[{type:'text',text:raw}]}:url.includes('googleapis')?{candidates:[{finishReason:'STOP',content:{parts:[{text:raw}]}}]}:{status:'completed',output:[{content:[{type:'output_text',text:raw}]}]});
}};
vm.runInNewContext(source.replace('export default {','globalThis.worker = {'),context);
const input={prompt:'Đối chiếu báo giá',squad:'general',role:4,model:'gpt-5-mini'};
const invoke=(body,env={},auth=true,origin='https://office.test')=>context.worker.fetch(new Request('https://office.test/api/ai/task-plan',{method:'POST',headers:{Origin:origin,...(auth?{'oai-authenticated-user-id':'test'}:{})},body:JSON.stringify(body)}),env);
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
status=404;assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'model_unavailable');
status=429;assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'limited');
context.fetch=async()=>{throw new DOMException('Timeout','TimeoutError');};
assert.equal((await (await invoke(input,{OPENAI_API_KEY:'shared'})).json()).status,'timeout');
const html=await readFile(new URL('../../public/workflow-lab/index.html', import.meta.url),'utf8');
for(const [,script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(script);
console.log('PASS: auth, origin, validation, 3 adapters, reasoning, provenance, dedicated isolation, provider errors, timeout, inline syntax');
