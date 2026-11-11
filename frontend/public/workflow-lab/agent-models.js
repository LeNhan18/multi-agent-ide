(() => {
'use strict';
const host=document.getElementById('agent-model-config');
const key='ai-office-agent-models-v1';
const defaultConfig={model:'gpt-5-mini',connection:'shared',reasoning:'auto'};
const names={openai:'OpenAI',anthropic:'Anthropic',gemini:'Gemini'};
let currentRequest;
let preferences={};
try {
 const saved=JSON.parse(localStorage.getItem(key));
 if(saved&&typeof saved==='object'&&!Array.isArray(saved))preferences=saved;
}catch{}
const id=agent=>agent.squad+':'+agent.role;
function get(agent){
 const saved=preferences[id(agent)];
 return saved&&typeof saved.model==='string'&&['shared','dedicated'].includes(saved.connection)?{...defaultConfig,...saved}:defaultConfig;
}
window.officeModelSettings={get};

function selectField(label,choices){
 const wrapper=document.createElement('label');
 const select=document.createElement('select');
 wrapper.append(label,select);
 for(const [value,text] of choices)select.add(new Option(text,value));
 return [wrapper,select];
}

async function render(){
 const agent=window.officeActiveAgent;
 if(!agent)return;
 currentRequest?.abort();
 const request=new AbortController();
 currentRequest=request;
 host.textContent='Đang tải model…';
 let catalog;
 try {
  const response=await fetch('/api/ai/catalog?'+new URLSearchParams({squad:agent.squad,role:agent.role}),{signal:request.signal});
  if(!response.ok)throw Error();
  catalog=await response.json();
 }catch{
  if(currentRequest===request)host.textContent='Chưa tải được model. Đóng rồi mở lại mục này để thử lại.';
  return;
 }
 if(currentRequest!==request)return;
 const [modelField,model]=selectField('Model',Object.entries(catalog.models).map(([value,[provider,label]])=>[
  value,names[provider]+' · '+label+(catalog.recommendations.includes(value)?' ★':'')
 ]));
 const [connectionField,connection]=selectField('API key',[['shared','Dùng kết nối chung'],['dedicated','Dùng key riêng cho agent']]);
 const [reasoningField,reasoning]=selectField('Mức suy luận',[['auto','Tự động · prompt ngắn suy luận sâu'],['medium','Cân bằng'],['high','Suy luận sâu · có thể chậm hơn']]);
 const hint=document.createElement('p');
 const status=document.createElement('p');status.setAttribute('role','status');
 const saved=get(agent);
 model.value=Object.hasOwn(catalog.models,saved.model)?saved.model:defaultConfig.model;
 connection.value=saved.connection;
 reasoning.value=['auto','medium','high'].includes(saved.reasoning)?saved.reasoning:'auto';
 function update(persist){
  const [provider]=catalog.models[model.value];
  const cfg=catalog.connections[provider];
  hint.textContent='Secret: '+cfg[connection.value]+' · '+(cfg[connection.value+'Ready']?'Đã cấu hình':'Chưa cấu hình')+'. ★ Model gợi ý theo vai trò.';
  if(persist){
   preferences[id(agent)]={model:model.value,connection:connection.value,reasoning:reasoning.value};
   try{localStorage.setItem(key,JSON.stringify(preferences));status.textContent='Đã lưu cấu hình trên trình duyệt.';}
   catch{status.textContent='Đã áp dụng cho phiên này.';}
  }
 }
 model.onchange=connection.onchange=reasoning.onchange=()=>update(true);
 host.replaceChildren(modelField,connectionField,reasoningField,hint,status);
 update(false);
}
const panel=document.getElementById('agent-model-panel');
panel.addEventListener('toggle',()=>{if(panel.open)render();});
document.addEventListener('office-agent-changed',()=>{if(panel.open)render();});
})();
