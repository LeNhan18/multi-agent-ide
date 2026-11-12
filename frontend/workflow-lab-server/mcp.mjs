// Streamable HTTP MCP client. Configuration and credentials are server-owned.
const VERSION = '2025-06-18';
const MAX_BYTES = 512 * 1024;
class McpError extends Error { constructor(code, status=400) { super(code); this.code=code; this.status=status; } }
const fail = (code,status) => {throw new McpError(code,status);};
const object = v => v && typeof v==='object' && !Array.isArray(v);
function config(env) {
 let servers;try{servers=JSON.parse(env.MCP_SERVERS_JSON||'[]');}catch{fail('config_invalid',503);}
 if(!Array.isArray(servers)||servers.length>20)fail('config_invalid',503);
 const ids=new Set();
 return servers.map(s=>{
  if(!object(s)||typeof s.id!=='string'||!/^[a-z0-9_-]{1,40}$/.test(s.id)||ids.has(s.id)||typeof s.name!=='string'||!object(s.grants))fail('config_invalid',503);
  ids.add(s.id);
  let url;try{url=new URL(s.url);}catch{fail('config_invalid',503);}
  const host=url.hostname.toLowerCase();
  const local=env.MCP_ALLOW_LOOPBACK==='true'&&['127.0.0.1','localhost'].includes(host)&&url.protocol==='http:';
  if(!local&&(url.protocol!=='https:'||!host.includes('.')||/^[\d.]+$/.test(host)||host.includes(':')||/\.(local|internal|localhost)$/.test(host)))fail('config_invalid',503);
  if(url.username||url.password||url.hash||url.search)fail('config_invalid',503);
  if(s.tokenEnv!==undefined&&(typeof s.tokenEnv!=='string'||!/^MCP_[A-Z0-9_]+_TOKEN$/.test(s.tokenEnv)))fail('config_invalid',503);
  for(const [agent,grants] of Object.entries(s.grants)){
   if(!/^[a-z0-9_-]+:[0-5]$/.test(agent)||!object(grants)||Object.values(grants).some(v=>!['read','write'].includes(v)))fail('config_invalid',503);
  }
  return {...s,url:url.href,token:s.tokenEnv?env[s.tokenEnv]?.trim():null};
 });
}
async function limitedText(response) {
 const reader=response.body?.getReader();if(!reader)return '';
 const decoder=new TextDecoder();let result='',size=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES)fail('response_too_large',502);result+=decoder.decode(value,{stream:true});}return result+decoder.decode();}
 finally{await reader.cancel().catch(()=>{});}
}
async function readRpc(response,id) {
 if(!response.headers.get('content-type')?.includes('text/event-stream')){
  let value;try{value=JSON.parse(await limitedText(response));}catch(e){if(e instanceof McpError)throw e;fail('protocol_error',502);}
  return value;
 }
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',size=0;
 try{
  while(true){
   const {done,value}=await reader.read();if(done)fail('protocol_error',502);
   size+=value.byteLength;if(size>MAX_BYTES)fail('response_too_large',502);
   buffer+=decoder.decode(value,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');
   let end;while((end=buffer.indexOf('\n\n'))>=0){
    const event=buffer.slice(0,end);buffer=buffer.slice(end+2);
    const data=event.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
    if(!data)continue;let msg;try{msg=JSON.parse(data);}catch{fail('protocol_error',502);}
    if(msg.id===id)return msg;
    // Notifications are harmless; server-initiated requests need unsupported capabilities.
    if(msg.id!==undefined&&msg.method)fail('unsupported_server_request',502);
   }
  }
 }finally{await reader.cancel().catch(()=>{});}
}
async function session(server,signal,operation) {
 let sessionId,version=VERSION;
 const headers=()=>({'Content-Type':'application/json',Accept:'application/json, text/event-stream','MCP-Protocol-Version':version,...(sessionId?{'Mcp-Session-Id':sessionId}:{}),...(server.token?{Authorization:'Bearer '+server.token}:{})});
 async function rpc(method,params,notification=false){
  const id=notification?undefined:crypto.randomUUID();
  const res=await fetch(server.url,{method:'POST',headers:headers(),redirect:'error',signal,body:JSON.stringify({jsonrpc:'2.0',...(id?{id}:{}),method,...(params?{params}:{})})});
  if(!res.ok){await res.body?.cancel();fail([401,403].includes(res.status)?'auth_required':res.status===429?'rate_limited':'upstream_error',502);}
  if(method==='initialize')sessionId=res.headers.get('mcp-session-id')||undefined;
  if(notification){await res.body?.cancel();return;}
  const msg=await readRpc(res,id);
  if(msg?.jsonrpc!=='2.0'||msg.id!==id)fail('protocol_error',502);
  if(msg.error)fail('rpc_error',502);
  if(!object(msg.result))fail('protocol_error',502);
  return msg.result;
 }
 try{
  const init=await rpc('initialize',{protocolVersion:VERSION,capabilities:{},clientInfo:{name:'ai-office',version:'1.0.0'}});
  if(!['2025-06-18','2025-11-25'].includes(init.protocolVersion)||!init.capabilities?.tools)fail('unsupported_protocol',502);
  version=init.protocolVersion;await rpc('notifications/initialized',undefined,true);
  return await operation(rpc);
 }finally{
  if(sessionId)try{const res=await fetch(server.url,{method:'DELETE',headers:headers(),redirect:'error',signal:AbortSignal.timeout(2000)});await res.body?.cancel();}catch{}
 }
}
function scrub(value,token){
 if(typeof value==='string')return token?value.split(token).join('[REDACTED]'):value;
 if(Array.isArray(value))return value.map(v=>scrub(v,token));
 if(object(value))return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,scrub(v,token)]));
 return value;
}
export async function mcpRoute(request,env,officeConfig){
 const reply=(body,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
 try{
  if(!request.headers.get('oai-authenticated-user-id'))fail('unauthorized',401);
  const url=new URL(request.url);
  if(request.method==='POST'&&request.headers.get('origin')!==url.origin)fail('forbidden_origin',403);
  if(!['GET','POST'].includes(request.method))fail('method_not_allowed',405);
  let input=Object.fromEntries(url.searchParams);
  if(request.method==='POST'){const raw=await limitedText(request);if(raw.length>16000)fail('invalid_input',413);try{input=JSON.parse(raw);}catch{fail('invalid_input');}}
  if(!object(input)||!Object.hasOwn(officeConfig.officeSquads.profiles,input.squad)||!Number.isInteger(Number(input.role))||Number(input.role)<0||Number(input.role)>5)fail('invalid_agent');
  const agent=input.squad+':'+Number(input.role),servers=config(env);
  if(url.pathname==='/api/mcp/servers'&&request.method==='GET')return reply({servers:servers.map(s=>({id:s.id,name:s.name,configured:!s.tokenEnv||Boolean(s.token),allowed:Object.hasOwn(s.grants,agent),toolCount:Object.keys(s.grants[agent]||{}).length})),transport:'streamable-http'});
  if(request.method!=='POST'||!['/api/mcp/tools','/api/mcp/call'].includes(url.pathname))fail('not_found',404);
  const server=servers.find(s=>s.id===input.server);if(!server)fail('server_not_found',404);
  const grants=server.grants[agent];if(!grants)fail('agent_forbidden',403);
  if(server.tokenEnv&&!server.token)fail('auth_required',503);
  const calling=url.pathname==='/api/mcp/call';
  if(calling){
   if(typeof input.tool!=='string'||!Object.hasOwn(grants,input.tool))fail('tool_forbidden',403);
   if(!object(input.arguments))fail('invalid_arguments');
   if(input.confirmed!==true)fail('confirmation_required',409);
  }
  const started=Date.now(),traceId=crypto.randomUUID();
  const output=await session(server,AbortSignal.any([request.signal,AbortSignal.timeout(30000)]),async rpc=>{
   // Rediscover inside the same MCP session; never accept browser-supplied tool definitions.
   const all=[];let cursor;const seen=new Set();
   for(let page=0;page<10;page++){
    const list=await rpc('tools/list',cursor?{cursor}:{});
    if(!Array.isArray(list.tools))fail('protocol_error',502);
    all.push(...list.tools);if(all.length>500)fail('too_many_tools',502);
    cursor=list.nextCursor;if(!cursor)break;if(typeof cursor!=='string'||seen.has(cursor)||page===9)fail('pagination_limit',502);seen.add(cursor);
   }
   const tools=all.filter(t=>object(t)&&typeof t.name==='string'&&Object.hasOwn(grants,t.name));
   if(!calling)return {tools:tools.map(t=>({name:t.name,description:String(t.description||'').slice(0,2000),inputSchema:t.inputSchema,access:grants[t.name]}))};
   if(!tools.some(t=>t.name===input.tool))fail('tool_not_available',409);
   const result=await rpc('tools/call',{name:input.tool,arguments:input.arguments,...(grants[input.tool]==='write'?{_meta:{'ai-office/confirmed':true}}:{})});
   return {result,access:grants[input.tool]};
  });
  return reply({...scrub(output,server.token),trace:{id:traceId,server:server.id,agent,tool:calling?input.tool:null,durationMs:Date.now()-started,at:new Date().toISOString()}});
 }catch(e){return reply({status:e.code||(e.name==='TimeoutError'?'timeout':e.name==='AbortError'?'cancelled':'connection_failed')},e.status||502);}
}
