// Requires Python backend dependencies. Creates a DOCX only in a temporary workspace.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {mcpRoute} from '../mcp.mjs';
const listener=createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
const directory=await mkdtemp(join(tmpdir(),'office-mcp-test-'));
const token='integration-test-token-not-a-real-secret-123456';
const python=spawn('python',['-c',`import uvicorn\nfrom fastapi import FastAPI\nfrom backend.app.api.mcp import router\napp=FastAPI()\napp.include_router(router)\nuvicorn.run(app,host='127.0.0.1',port=${port},log_level='warning')`],{env:{...process.env,MCP_BRIDGE_TOKEN:token,MCP_WORD_WORKSPACE:directory},stdio:['ignore','ignore','pipe']});
const env={MCP_ALLOW_LOOPBACK:'true',MCP_OFFICE_TOKEN:token,MCP_SERVERS_JSON:JSON.stringify([{id:'word',name:'Word',url:`http://127.0.0.1:${port}/mcp/word`,tokenEnv:'MCP_OFFICE_TOKEN',grants:{'general:0':{word_list:'read',word_create_draft:'write',word_read:'read'}}}])};
const office={officeSquads:{profiles:{general:[]}}};
const invoke=async(path,params={})=>{const res=await mcpRoute(new Request('http://site.local/api/mcp/'+path,{method:'POST',headers:{Origin:'http://site.local','oai-authenticated-user-id':'owner'},body:JSON.stringify({squad:'general',role:0,server:'word',...params})}),env,office);assert.equal(res.status,200);return res.json();};
try{
 let ready=false;
 for(let i=0;i<60;i++){if(python.exitCode!==null)throw Error('Python server exited');try{await fetch(`http://127.0.0.1:${port}/mcp/word`);ready=true;break;}catch{await new Promise(r=>setTimeout(r,100));}}
 assert.ok(ready,'Python bridge started');
 const tools=await invoke('tools');assert.equal(tools.tools.length,3);
 const result=await invoke('call',{tool:'word_create_draft',arguments:{title:'MCP integration',text:'Gateway → Python → DOCX'},confirmed:true});
 assert.equal(result.result.isError,false);
 const {path}=JSON.parse(result.result.content[0].text);
 const read=await invoke('call',{tool:'word_read',arguments:{path},confirmed:true});
 assert.ok(JSON.parse(read.result.content[0].text).text.includes('Gateway → Python → DOCX'));
 console.log('PASS E2E: JS gateway → MCP HTTP → FastAPI → real DOCX create/read');
}finally{python.kill();await new Promise(r=>python.exitCode!==null?r():python.once('exit',r));await rm(directory,{recursive:true,force:true});}
