(() => {
'use strict';
const PROTOCOL='ai-office/2';
const {profiles}=window.officeSquads;
const STATUS=['QUEUED','SCOPING','READY','WORKING','IN_REVIEW','CHANGES_REQUESTED','APPROVED','DELIVERING','DELIVERED','WAITING_FOR_HUMAN'];
const rules={
 'task.scoped':['QUEUED','SCOPING',4,5,'tasks'],
 'inputs.prepared':['SCOPING','READY',5,0,'tasks'],
 'work.started':['READY','WORKING',4,0,'tasks'],
 'review.requested':['WORKING','IN_REVIEW',0,1,'review'],
 'review.approved':['IN_REVIEW','APPROVED',1,2,'delivery'],
 'review.rejected':['IN_REVIEW','CHANGES_REQUESTED',1,0,'review'],
 'work.retried':['CHANGES_REQUESTED','WORKING',4,0,'tasks'],
 'delivery.started':['APPROVED','DELIVERING',4,2,'delivery'],
 'delivery.completed':['DELIVERING','DELIVERED',2,3,'audit'],
 'human.required':['CHANGES_REQUESTED','WAITING_FOR_HUMAN',1,3,'audit']
};
const intake=[['task.scoped'],['inputs.prepared']];
const scenarios={
 approve:[...intake,['work.started','review.requested'],['review.approved','delivery.started'],['delivery.completed']],
 reject:[...intake,['work.started','review.requested'],['review.rejected'],['work.retried','review.requested'],['review.rejected'],['work.retried','review.requested'],['review.rejected','human.required']]
};
const key='ai-office-workflow-checkpoint-v2';
const empty=()=>({id:null,scenario:null,squad:null,state:'QUEUED',rejects:0,step:0,journal:[]});
let model=empty(), mode='idle', persistence='';
try{
 const raw=JSON.parse(localStorage.getItem(key));
 if(raw&&typeof raw.id==='string'&&Object.hasOwn(profiles,raw.squad)&&Object.hasOwn(scenarios,raw.scenario)&&Number.isInteger(raw.step)&&raw.step>=0&&raw.step<=scenarios[raw.scenario].length&&STATUS.includes(raw.state)&&Number.isInteger(raw.rejects)&&Array.isArray(raw.journal)){
  model=raw;mode=raw.step<scenarios[raw.scenario].length?'paused':'done';
 }
}catch{}
const panel=document.getElementById('workflow');
panel.innerHTML=`<header><div class="eyebrow">AI Office · Workflow Lab</div><h2>Điều phối và theo dõi thông điệp</h2><p>Chạy mô phỏng từ văn phòng 3D. Mỗi bước chuyển trạng thái tạo một sự kiện có thể đối chiếu trong Journal.</p></header>
<div class="wf-grid">
<section><h3>Board & Status</h3><strong id="wf-state"></strong><p id="wf-summary"></p><p id="wf-checkpoint" role="status"></p></section>
<section><h3>Protocol & Channel</h3><p><code>ai-office/2</code> · Message envelope có mã phiên và số thứ tự.</p><div class="wf-channels"></div></section>
<section><h3>Orchestration</h3><p>Tiếp nhận làm rõ yêu cầu, chuyên gia chuẩn bị đầu vào, người thực hiện tạo sản phẩm. Người kiểm duyệt quyết định duyệt hoặc trả lại. Chỉ bàn giao sau khi duyệt. Lần reject thứ ba chuyển sang chờ người.</p><p><code>QUEUED → SCOPING → READY → WORKING → IN_REVIEW</code><br><code>APPROVED → DELIVERING → DELIVERED</code><br><code>CHANGES_REQUESTED → WORKING / WAITING_FOR_HUMAN</code></p></section>
</div>
<div class="wf-grid wf-bottom">
<section><h3>Journal</h3><p>Chuỗi sự kiện của phiên hiện tại được bổ sung theo thứ tự.</p><div id="wf-journal"></div></section>
<section><details><summary>API Contract</summary><p>Hợp đồng đề xuất cho backend. Bản mô phỏng chạy trong trình duyệt.</p><ul><li><code>POST /v1/runs</code> tạo phiên</li><li><code>GET /v1/runs/{id}</code> đọc Board và Status</li><li><code>GET /v1/runs/{id}/events?after=0</code> đọc Journal theo số thứ tự</li><li><code>POST /v1/runs/{id}/commands</code> nhận lệnh điều phối</li></ul></details><details id="wf-envelope-panel" hidden><summary>Thông điệp đã chọn</summary><pre id="wf-envelope"></pre></details></section>
</div>`;
const by=id=>document.getElementById(id);
const channels={tasks:'Giao việc và retry',review:'Yêu cầu duyệt và phản hồi',delivery:'Duyệt và bàn giao',audit:'Kết quả và bàn giao cho người'};
function render(){
 by('wf-state').textContent=mode==='paused'?'PAUSED · '+model.state:model.state;
 by('wf-summary').textContent=(model.id?profiles[model.squad][0]+' · '+model.id.slice(0,12)+' · ':'Chưa tạo phiên · ')+'Reject '+model.rejects+'/3';
 by('wf-checkpoint').textContent=persistence||(mode==='paused'?'Đã tạm dừng. Chọn Tiếp tục mô phỏng để chạy từ checkpoint.':model.id?'Checkpoint lưu sau mỗi bước.':'Checkpoint sẽ được ghi khi bắt đầu.');
 const channelList=panel.querySelector('.wf-channels');channelList.replaceChildren();
 for(const [name,label] of Object.entries(channels)){
  const row=document.createElement('div');row.textContent=name+' · '+label+' ('+model.journal.filter(e=>e.channel===name).length+')';channelList.append(row);
 }
 const list=by('wf-journal');list.replaceChildren();
 if(!model.journal.length)list.textContent='Chưa có sự kiện.';
 for(const e of model.journal){
  const row=document.createElement('button');row.type='button';row.textContent='#'+e.seq+' '+e.type+' · '+e.from+' → '+e.to;row.onclick=()=>show(e,true);list.append(row);
 }
 show(model.journal.at(-1));
}
function show(e,open=false){
 const panel=by('wf-envelope-panel');
 panel.hidden=!e;
 if(open)panel.open=true;
 by('wf-envelope').textContent=e?JSON.stringify(e,null,2):'';
}
function persist(){
 try{localStorage.setItem(key,JSON.stringify(model));persistence='';}
 catch{persistence='Không ghi được checkpoint vào trình duyệt.';}
 render();
}
function event(type){
 const [from,status,sender,to,channel]=rules[type];
 if(!window.officePolicies[model.squad][sender].events.includes(type))throw Error('Vai trò không có quyền phát sự kiện: '+type);
 if(model.state!==from||(type==='human.required'&&model.rejects!==3)||(type==='work.retried'&&model.rejects>=3))throw Error('Chuyển trạng thái không hợp lệ: '+type);
 if(type==='review.rejected')model.rejects++;
 model.state=status;
 const [,artifact,roles]=profiles[model.squad];
 model.journal.push({protocol:PROTOCOL,run_id:model.id,squad:model.squad,seq:model.journal.length+1,type,channel,from:roles[sender],to:roles[to],status,payload:{artifact,rejects:model.rejects},at:new Date().toISOString()});
}
window.officeWorkflow={
 pending:()=>mode==='paused'?{scenario:model.scenario,step:model.step,squad:model.squad}:null,
 begin(scenario,squad){
  if(mode!=='paused'||model.scenario!==scenario||model.squad!==squad)model={...empty(),id:crypto.randomUUID(),scenario,squad};
  mode='running';persist();return model.step;
 },
 advance(){
  for(const type of scenarios[model.scenario][model.step])event(type);
  model.step++;
  if(model.step===scenarios[model.scenario].length)mode='done';
  persist();
 },
 pause(){mode='paused';persist();}
};
render();
})();
