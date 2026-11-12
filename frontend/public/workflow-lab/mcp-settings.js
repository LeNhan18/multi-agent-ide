(() => {
'use strict';
const panel=document.createElement('section');panel.className='panel mcp-panel';
panel.innerHTML=`<div class="line"><div><div class="eyebrow">MCP · Công cụ bên ngoài</div><h2 id="mcp-title">Kết nối công cụ</h2></div><button type="button" id="mcp-refresh">Làm mới</button></div><p class="mcp-note">Word · Notion · Outlook hoặc server MCP khác. Công cụ được cấp quyền theo từng agent; bạn xem lại tham số trước mỗi lần chạy.</p><div class="mcp-controls"><label>Kết nối<select id="mcp-server" aria-label="Kết nối MCP"></select></label><button type="button" id="mcp-discover">Kiểm tra & tải công cụ</button></div><p id="mcp-status" role="status"></p><form id="mcp-form" hidden><label>Công cụ<select id="mcp-tool"></select></label><p id="mcp-description"></p><div id="mcp-fields" class="mcp-fields"></div><details><summary>Tham số JSON nâng cao</summary><label><input type="checkbox" id="mcp-raw-mode"> Dùng JSON thay cho các ô phía trên</label><textarea id="mcp-raw" rows="5" spellcheck="false" aria-label="Tham số JSON">{}</textarea><pre id="mcp-schema"></pre></details><p class="mcp-note">Nội dung trả về là dữ liệu từ công cụ, không phải chỉ dẫn để thay đổi quyền của agent.</p><button type="submit" class="primary" id="mcp-review">Xem lại & chạy</button></form><details class="mcp-result" id="mcp-result-wrap" hidden><summary>Kết quả công cụ</summary><pre id="mcp-result"></pre></details><details><summary>Thiết lập kết nối lần đầu</summary><p>Trong Environment variables của Site (hoặc môi trường server local), thêm <code>MCP_SERVERS_JSON</code> theo mẫu bên dưới. Thay URL bằng backend của bạn. Đặt <code>MCP_OFFICE_TOKEN</code> dưới dạng Secret; giá trị khớp với <code>MCP_BRIDGE_TOKEN</code> ở backend.</p><p>Backend trong repo cung cấp <code>/mcp/word</code>, <code>/mcp/notion</code>, <code>/mcp/outlook</code>. Word cần thư mục <code>MCP_WORD_WORKSPACE</code>; Notion cần <code>NOTION_API_KEY</code>; Outlook cần token Microsoft Graph <code>OUTLOOK_ACCESS_TOKEN</code>. Đây chưa phải màn hình đăng nhập OAuth tự động.</p><p>Site cần endpoint HTTPS truy cập được. Word desktop/stdio không chạy trực tiếp trên Site. Không dán key vào ô tham số. Mẫu dưới đây chỉ cấp quyền đọc cho agent đang chọn; quyền ghi phải được chủ hệ thống cấp rõ theo tên công cụ.</p><pre id="mcp-config-example"></pre></details><dialog id="mcp-confirm"><h2>Xác nhận gọi công cụ</h2><p id="mcp-confirm-caption"></p><pre id="mcp-confirm-body"></pre><p>Thao tác sẽ chạy thật trên dịch vụ đã kết nối. Nếu mất kết nối sau khi gửi, kiểm tra dịch vụ trước khi thử lại.</p><div class="actions"><button type="button" id="mcp-confirm-cancel">Hủy</button><button type="button" class="primary" id="mcp-confirm-run">Xác nhận chạy</button></div></dialog>`;
(document.querySelector('.agent-terminal')||document.getElementById('office')).after(panel);
const by=id=>document.getElementById(id);
let tools=[],active=null,sequence=0,busy=false,pending=null;
const errors={config_invalid:'Cấu hình MCP_SERVERS_JSON không hợp lệ. Kiểm tra URL, grants và tên Secret.',auth_required:'Thiếu token, token hết hạn hoặc dịch vụ từ chối quyền truy cập.',agent_forbidden:'Agent này chưa được cấp quyền.',tool_forbidden:'Công cụ chưa được cấp quyền cho agent.',tool_not_available:'Công cụ không còn khả dụng. Tải lại danh sách.',connection_failed:'Không kết nối được MCP server. Kiểm tra URL và chứng chỉ HTTPS.',timeout:'Quá thời gian chờ. Kết quả thao tác ghi chưa xác định; kiểm tra dịch vụ trước khi thử lại.',rate_limited:'Dịch vụ đang giới hạn yêu cầu.',confirmation_required:'Cần xác nhận trước khi chạy.',protocol_error:'Phản hồi MCP không hợp lệ.',unsupported_protocol:'Server chưa hỗ trợ phiên bản MCP hoặc công cụ tương thích.',rpc_error:'MCP server từ chối lời gọi; kiểm tra tham số hoặc quyền.',unauthorized:'Cần đăng nhập để sử dụng công cụ.'};
const note=text=>{by('mcp-status').textContent=text;};
function lock(value){
 busy=value;
 for(const id of ['mcp-refresh','mcp-server','mcp-review','mcp-tool'])by(id).disabled=value;
 by('mcp-discover').disabled=value||!by('mcp-server').value;
}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(40000)}:{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(errors[data.status]||'Yêu cầu thất bại: '+(data.status||response.status));return data;}
function snapshot(){const a=window.officeActiveAgent;return a?{squad:a.squad,role:a.role,name:a.name}:null;}
function example(agent){return ['word','notion','outlook'].map((id,i)=>({id,name:['Word / DOCX','Notion','Outlook'][i],url:'https://YOUR-BACKEND/mcp/'+id,tokenEnv:'MCP_OFFICE_TOKEN',grants:{[agent.squad+':'+agent.role]:{[['word_list','notion_search','outlook_list'][i]]:'read'}}}));}
async function refresh(){
 active=snapshot();if(!active)return;const seq=++sequence;tools=[];pending=null;by('mcp-confirm').close();by('mcp-form').hidden=true;by('mcp-result-wrap').hidden=true;
 by('mcp-title').textContent='Công cụ của '+active.name;by('mcp-config-example').textContent=JSON.stringify(example(active),null,2);by('mcp-server').replaceChildren();note('Đang đọc cấu hình…');lock(true);
 try{const data=await api('/api/mcp/servers?'+new URLSearchParams({squad:active.squad,role:active.role}));if(seq!==sequence)return;
  for(const s of data.servers){const option=new Option(s.name+' · '+(!s.allowed?'Chưa cấp quyền':!s.configured?'Thiếu token':'Đã cấu hình · chưa kiểm tra'),s.id);option.disabled=!s.allowed||!s.configured;by('mcp-server').add(option);}
  const usable=[...by('mcp-server').options].find(o=>!o.disabled);
  if(usable)by('mcp-server').value=usable.value;
  else{const placeholder=new Option('Chưa có kết nối khả dụng','');placeholder.disabled=true;by('mcp-server').add(placeholder,0);by('mcp-server').value='';}
  note(data.servers.length?'Chọn kết nối và kiểm tra để lấy danh sách công cụ thật.':'Chưa có kết nối. Mở “Thiết lập kết nối lần đầu” để cấu hình.');
 }catch(e){if(seq===sequence)note(e.message);}finally{if(seq===sequence)lock(false);}
}
function renderTool(){
 const t=tools.find(t=>t.name===by('mcp-tool').value);if(!t)return;
 by('mcp-description').textContent=(t.access==='write'?'Quyền ghi · cần xác nhận':'Quyền đọc · cần xác nhận')+' — '+t.description;
 by('mcp-schema').textContent=JSON.stringify(t.inputSchema,null,2);by('mcp-fields').replaceChildren();by('mcp-raw-mode').checked=false;by('mcp-raw').value='{}';
 for(const [key,schema] of Object.entries(t.inputSchema?.properties||{})){
  const label=document.createElement('label');label.textContent=key+(t.inputSchema.required?.includes(key)?' *':'');
  let input;if(schema.type==='boolean'){input=document.createElement('select');input.add(new Option('Không điền',''));input.add(new Option('true','true'));input.add(new Option('false','false'));}
  else if(Array.isArray(schema.enum)){input=document.createElement('select');input.add(new Option('Không điền',''));for(const value of schema.enum)input.add(new Option(String(value),JSON.stringify(value)));}
  else {input=document.createElement('textarea');input.rows=2;input.placeholder=String(schema.description||schema.type||'Giá trị');}
  input.dataset.field=key;label.append(input);by('mcp-fields').append(label);
 }
}
by('mcp-discover').onclick=async()=>{
 if(busy||!by('mcp-server').value)return;const agent={...active},server=by('mcp-server').value,seq=sequence;lock(true);by('mcp-form').hidden=true;note('Đang kết nối MCP và đọc tools/list…');
 try{const data=await api('/api/mcp/tools',{...agent,server});window.officeTerminal?.append(agent,'mcp','tools/list · '+server+' · '+data.tools.length+' công cụ được cấp quyền',data.trace.id);if(seq!==sequence)return;tools=data.tools;by('mcp-tool').replaceChildren();for(const t of tools)by('mcp-tool').add(new Option(t.name,t.name));by('mcp-form').hidden=!tools.length;renderTool();note(tools.length?'Đã kết nối · '+tools.length+' công cụ.':'Server đã trả lời nhưng không có công cụ nào khớp quyền đã cấp.');}
 catch(e){window.officeTerminal?.append(agent,'mcp','tools/list thất bại · '+e.message);if(seq===sequence)note(e.message);}finally{if(seq===sequence)lock(false);}
};
by('mcp-server').onchange=()=>{tools=[];by('mcp-form').hidden=true;by('mcp-result-wrap').hidden=true;};
by('mcp-tool').onchange=renderTool;by('mcp-refresh').onclick=refresh;
by('mcp-form').onsubmit=e=>{
 e.preventDefault();if(busy)return;const tool=tools.find(t=>t.name===by('mcp-tool').value);if(!tool)return;
 try{let args={};if(by('mcp-raw-mode').checked)args=JSON.parse(by('mcp-raw').value);else for(const input of by('mcp-fields').querySelectorAll('[data-field]')){if(input.value==='')continue;const schema=tool.inputSchema.properties[input.dataset.field];args[input.dataset.field]=schema.type==='string'&&!schema.enum?input.value:JSON.parse(input.value);}
  if(!args||Array.isArray(args)||typeof args!=='object')throw Error('Tham số phải là một JSON object.');
  for(const key of tool.inputSchema?.required||[])if(!(key in args))throw Error('Thiếu tham số: '+key);
  pending={squad:active.squad,role:active.role,server:by('mcp-server').value,tool:tool.name,arguments:args,confirmed:true};
  by('mcp-confirm-caption').textContent=active.name+' · '+pending.server+' · '+tool.name+' · '+(tool.access==='write'?'GHI DỮ LIỆU':'ĐỌC DỮ LIỆU');by('mcp-confirm-body').textContent=JSON.stringify(args,null,2);by('mcp-confirm').showModal();
 }catch(err){note(err.message||'Tham số JSON không hợp lệ.');}
};
by('mcp-confirm-cancel').onclick=()=>{pending=null;by('mcp-confirm').close();};
by('mcp-confirm-run').onclick=async()=>{
 if(!pending||busy)return;const body=pending,agent={...active},seq=sequence;pending=null;by('mcp-confirm').close();lock(true);note('Đang chạy công cụ…');const trace=crypto.randomUUID();
 window.officeTerminal?.append(agent,'mcp','CALL '+body.server+'/'+body.tool+'\n'+JSON.stringify(body.arguments,null,2),trace);
 try{const data=await api('/api/mcp/call',body);window.officeTerminal?.append(agent,'mcp',(data.result.isError?'TOOL ERROR':'RESULT')+'\n'+JSON.stringify(data,null,2),trace);if(seq!==sequence)return;by('mcp-result-wrap').hidden=false;by('mcp-result-wrap').open=true;by('mcp-result').textContent=JSON.stringify(data.result,null,2);note(data.result.isError?'Công cụ báo lỗi; xem kết quả.':'Công cụ đã trả kết quả · '+data.trace.durationMs+' ms.');}
 catch(e){window.officeTerminal?.append(agent,'mcp','ERROR · '+e.message+' · Nếu thao tác ghi đã gửi, kết quả có thể chưa xác định.',trace);if(seq===sequence)note(e.message+' Nếu đã gửi thao tác ghi, kiểm tra dịch vụ trước khi thử lại.');}finally{if(seq===sequence)lock(false);}
};
document.addEventListener('office-agent-changed',refresh);refresh();
})();
