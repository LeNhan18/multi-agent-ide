import assert from 'node:assert/strict';
import {mcpRoute} from '../mcp.mjs';
const office={officeSquads:{profiles:{general:[],health:[]}}};
const server={id:'office',name:'Office',url:'https://bridge.example.com/mcp/word',tokenEnv:'MCP_OFFICE_TOKEN',grants:{'general:0':{word_read:'read',word_create_draft:'write'}}};
const env={MCP_SERVERS_JSON:JSON.stringify([server]),MCP_OFFICE_TOKEN:'test-secret-value'};
const call=(path,input,options={})=>mcpRoute(new Request('https://site.test/api/mcp/'+path,{method:options.get?'GET':'POST',headers:{...(!options.anonymous?{'oai-authenticated-user-id':'owner'}:{}),origin:options.origin||'https://site.test'},...(!options.get?{body:JSON.stringify({squad:'general',role:0,server:'office',...input})}:{})}),env,office);
let traffic=[],mode='json',pages=0;
const originalFetch=globalThis.fetch;
globalThis.fetch=async(url,options)=>{
 if(options.method==='DELETE'){traffic.push({method:'DELETE'});return new Response(null,{status:204});}
 const msg=JSON.parse(options.body);traffic.push(msg);
 assert.equal(options.headers.Authorization,'Bearer test-secret-value');assert.equal(options.redirect,'error');
 if(mode==='unauthorized')return new Response(null,{status:401});
 if(msg.method==='notifications/initialized')return new Response(null,{status:202});
 let result=msg.method==='initialize'?{protocolVersion:'2025-06-18',capabilities:{tools:{}}}:msg.method==='tools/list'?{tools:[{name:'word_read',inputSchema:{type:'object'}},{name:'word_create_draft',inputSchema:{type:'object'}},{name:'ungranted',inputSchema:{type:'object'}}]}:{content:[{type:'text',text:'Result test-secret-value'}]};
 if(mode==='pagination'&&msg.method==='tools/list'){pages++;result.nextCursor='repeated';}
 if(mode==='tool-error'&&msg.method==='tools/call')result.isError=true;
 const body=JSON.stringify({jsonrpc:'2.0',id:mode==='wrong-id'?'other':msg.id,result});
 if(mode==='sse')return new Response('event: message\r\ndata: '+body+'\r\n\r\n',{headers:{'Content-Type':'text/event-stream','Mcp-Session-Id':'session'}});
 return new Response(body,{headers:{'Content-Type':'application/json','Mcp-Session-Id':'session'}});
};
try{
 assert.equal((await call('tools',{}, {anonymous:true})).status,401);
 assert.equal((await call('tools',{}, {origin:'https://evil.test'})).status,403);
 assert.equal((await call('tools',{role:3})).status,403);
 assert.equal((await call('call',{tool:'ungranted',arguments:{},confirmed:true})).status,403);
 assert.equal((await call('call',{tool:'word_create_draft',arguments:{}})).status,409);
 assert.equal(traffic.length,0);
 let res=await call('tools');assert.equal(res.status,200);assert.equal((await res.json()).tools.length,2);
 assert.equal(traffic.at(-1).method,'DELETE');
 res=await call('call',{tool:'word_create_draft',arguments:{title:'Test'},confirmed:true});
 assert.equal(res.status,200);assert.ok(!(await res.text()).includes('test-secret-value'));
 assert.equal(traffic.find(m=>m.method==='tools/call').params._meta['ai-office/confirmed'],true);
 mode='sse';assert.equal((await call('tools')).status,200);
 mode='wrong-id';assert.equal((await (await call('tools')).json()).status,'protocol_error');
 mode='pagination';assert.equal((await (await call('tools')).json()).status,'pagination_limit');assert.equal(pages,2);
 mode='unauthorized';assert.equal((await (await call('tools')).json()).status,'auth_required');
 mode='tool-error';assert.equal((await (await call('call',{tool:'word_read',arguments:{},confirmed:true})).json()).result.isError,true);
 env.MCP_SERVERS_JSON=JSON.stringify([{...server,url:'http://169.254.169.254/metadata'}]);assert.equal((await call('tools')).status,503);
 env.MCP_SERVERS_JSON=JSON.stringify([{...server,url:'https://example.com/mcp?token=secret'}]);assert.equal((await call('tools')).status,503);
 env.MCP_SERVERS_JSON=JSON.stringify([server]);delete env.MCP_OFFICE_TOKEN;assert.equal((await call('tools')).status,503);
 console.log('PASS MCP: auth/origin, agent/tool grants, confirmation, JSON/SSE, session cleanup, write metadata, secret redaction, pagination, provider errors, URL policy');
}finally{globalThis.fetch=originalFetch;}
