(() => {
'use strict';
const storageKey='ai-office-terminals-v1';
let records=[],filter='all',persistError='';
try{const saved=JSON.parse(sessionStorage.getItem(storageKey));if(Array.isArray(saved))records=saved.filter(e=>e&&typeof e.squad==='string'&&Number.isInteger(e.role)&&typeof e.text==='string'&&typeof e.at==='string'&&['prompt','result','log','simulation','mcp'].includes(e.type)).slice(-300);}catch{}
const panel=document.createElement('section');panel.className='agent-terminal';
panel.setAttribute('aria-label','Terminal của agent');
panel.innerHTML=`<div class="terminal-head"><div><span class="terminal-dot">●</span> <strong id="terminal-title">Agent terminal</strong><p id="terminal-caption"></p></div><div class="actions"><button type="button" id="terminal-export">Xuất log</button><button type="button" id="terminal-clear">Xóa log agent</button></div></div><div class="terminal-toolbar"><label>Hiển thị <select id="terminal-filter"><option value="all">Tất cả</option><option value="prompt">Prompt đã gửi</option><option value="result">Phản hồi AI</option><option value="log">Log API</option><option value="simulation">Mô phỏng</option><option value="mcp">Công cụ MCP</option></select></label><label><input type="checkbox" id="terminal-follow" checked> Theo dõi mới nhất</label><span id="terminal-count" role="status"></span></div><div id="terminal-output" tabindex="0" aria-label="Nội dung terminal"></div><details><summary>Chỉ dẫn theo vai trò</summary><pre id="terminal-policy"></pre></details><p class="terminal-foot">Lịch sử trong tab này · tối đa 300 mục cho toàn văn phòng. Đây là trình xem prompt/log, không phải shell. Phản hồi là đầu ra AI, không phải chuỗi suy luận nội bộ.</p>`;
document.getElementById('office').after(panel);
const by=id=>document.getElementById(id);
const selected=()=>window.officeActiveAgent;
const matching=()=>{const a=selected();return a?records.filter(e=>e.squad===a.squad&&e.role===a.role):[];};
const labels={prompt:'PROMPT · API',result:'OUTPUT · API',log:'LOG · API',simulation:'SIM · MÔ PHỎNG',mcp:'MCP · CÔNG CỤ'};
function persist(){try{sessionStorage.setItem(storageKey,JSON.stringify(records));persistError='';}catch{persistError=' · Không lưu được lịch sử; chỉ giữ trong bộ nhớ.';}}
function render(){
 const a=selected();if(!a)return;
 by('terminal-title').textContent='Terminal / '+a.name;
 by('terminal-caption').textContent=window.officeSquads.profiles[a.squad][0]+' · agent '+(a.role+1);
 const entries=matching().filter(e=>filter==='all'||e.type===filter),output=by('terminal-output');
 const previous=output.scrollTop;output.replaceChildren();
 if(!entries.length){const empty=document.createElement('p');empty.className='terminal-empty';empty.textContent='Chưa có log phù hợp. Dùng AI đề xuất để xem prompt/phản hồi thật, hoặc chạy mô phỏng để xem thông điệp.';output.append(empty);}
 for(const e of entries){const row=document.createElement('article');row.className='terminal-entry '+e.type;const meta=document.createElement('div');meta.className='terminal-meta';meta.textContent=e.at+'  ['+labels[e.type]+']'+(e.requestId?'  #'+e.requestId:'');const body=document.createElement('pre');body.textContent=e.text;row.append(meta,body);output.append(row);}
 output.scrollTop=by('terminal-follow').checked?output.scrollHeight:previous;
 by('terminal-count').textContent=entries.length+' mục'+persistError;
 const policy=window.officePolicies[a.squad][a.role];
 by('terminal-policy').textContent='Cấu hình kỹ năng và giới hạn hiện tại (không phải bản chụp system prompt từng request):\n'+JSON.stringify(policy,null,2);
}
function append(agent,type,text,requestId=''){
 if(!agent||!Object.hasOwn(window.officeSquads.profiles,agent.squad)||!Number.isInteger(agent.role)||agent.role<0||agent.role>5||!Object.hasOwn(labels,type))return;
 records.push({squad:agent.squad,role:agent.role,type,text:String(text).slice(0,12000),requestId:String(requestId).slice(0,80),at:new Date().toISOString()});records=records.slice(-300);persist();render();
}
window.officeTerminal={append};
by('terminal-filter').onchange=e=>{filter=e.target.value;render();};
by('terminal-clear').onclick=()=>{const a=selected();if(a&&confirm('Xóa lịch sử terminal của agent này trong tab hiện tại?')){records=records.filter(e=>e.squad!==a.squad||e.role!==a.role);persist();render();}};
by('terminal-export').onclick=()=>{const a=selected();if(!a)return;const blob=new Blob([JSON.stringify({version:1,agent:a,records:matching()},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='terminal-'+a.squad+'-'+a.role+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
document.addEventListener('office-agent-changed',render);render();
})();
