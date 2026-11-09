(() => {
'use strict';
const PROTOCOL='ai-office/1';
const STATUS=['QUEUED','WORKING','IN_REVIEW','CHANGES_REQUESTED','APPROVED','MERGING','MERGED','WAITING_FOR_HUMAN','PAUSED'];
const rules={
 'work.started':['QUEUED','WORKING','Orchestrator','Maker','tasks'],
 'review.requested':['WORKING','IN_REVIEW','Maker','Reviewer','review'],
 'review.approved':['IN_REVIEW','APPROVED','Reviewer','Merger','merge'],
 'review.rejected':['IN_REVIEW','CHANGES_REQUESTED','Reviewer','Maker','review'],
 'work.retried':['CHANGES_REQUESTED','WORKING','Orchestrator','Maker','tasks'],
 'merge.started':['APPROVED','MERGING','Orchestrator','Merger','merge'],
 'merge.completed':['MERGING','MERGED','Merger','Auditor','audit'],
 'human.required':['CHANGES_REQUESTED','WAITING_FOR_HUMAN','Orchestrator','Auditor','audit']
};
const scenarios={
 approve:[['work.started','review.requested'],['review.approved','merge.started'],['merge.completed']],
 reject:[['work.started','review.requested'],['review.rejected'],['work.retried','review.requested'],['review.rejected'],['work.retried','review.requested'],['review.rejected','human.required']]
};
const key='ai-office-workflow-checkpoint-v1';
const empty=()=>({id:null,scenario:null,state:'QUEUED',rejects:0,step:0,journal:[]});
let model=empty(), mode='idle', persistence='';
try{
 const raw=JSON.parse(localStorage.getItem(key));
 if(raw&&typeof raw.id==='string'&&Object.hasOwn(scenarios,raw.scenario)&&Number.isInteger(raw.step)&&raw.step>=0&&raw.step<=scenarios[raw.scenario].length&&STATUS.includes(raw.state)&&Number.isInteger(raw.rejects)&&Array.isArray(raw.journal)){
  model=raw;mode=raw.step<scenarios[raw.scenario].length?'paused':'done';
 }
}catch{}
const panel=document.getElementById('workflow');
panel.innerHTML=`<header><div class="eyebrow">AI Office · Workflow Lab</div><h2>Điều phối và theo dõi thông điệp</h2><p>Chạy mô phỏng từ văn phòng 3D. Mỗi bước chuyển trạng thái tạo một sự kiện có thể đối chiếu trong Journal.</p></header>
<div class="wf-grid">
<section><h3>Board & Status</h3><strong id="wf-state"></strong><p id="wf-summary"></p><p id="wf-checkpoint" role="status"></p></section>
<section><h3>Protocol & Channel</h3><p><code>ai-office/1</code> · Message envelope có mã phiên và số thứ tự.</p><div class="wf-channels"></div></section>
<section><h3>Orchestration</h3><p>Orchestrator giao việc và điều phối retry. Reviewer quyết định approve hoặc reject. Chỉ merge sau approve. Lần reject thứ ba chuyển sang chờ người.</p><p><code>QUEUED → WORKING → IN_REVIEW</code><br><code>APPROVED → MERGING → MERGED</code><br><code>CHANGES_REQUESTED → WORKING / WAITING_FOR_HUMAN</code></p></section>
</div>
<div class="wf-grid wf-bottom">
<section><h3>Journal</h3><p>Chuỗi sự kiện của phiên hiện tại được bổ sung theo thứ tự.</p><div id="wf-journal"></div></section>
<section><h3>API Contract</h3><p>Hợp đồng đề xuất cho backend. Bản mô phỏng chạy trong trình duyệt.</p><ul><li><code>POST /v1/runs</code> tạo phiên</li><li><code>GET /v1/runs/{id}</code> đọc Board và Status</li><li><code>GET /v1/runs/{id}/events?after=0</code> đọc Journal theo số thứ tự</li><li><code>POST /v1/runs/{id}/commands</code> nhận lệnh điều phối</li></ul><details open><summary>Message envelope</summary><pre id="wf-envelope"></pre></details></section>
</div>`;
const by=id=>document.getElementById(id);
const channels={tasks:'Giao việc và retry',review:'Yêu cầu duyệt và phản hồi',merge:'Approve và hợp nhất',audit:'Kết quả và bàn giao cho người'};
function render(){
 by('wf-state').textContent=mode==='paused'?'PAUSED · '+model.state:model.state;
 by('wf-summary').textContent=(model.id?model.id.slice(0,12)+' · ':'Chưa tạo phiên · ')+'Reject '+model.rejects+'/3';
 by('wf-checkpoint').textContent=persistence||(mode==='paused'?'Đã tạm dừng. Chọn Tiếp tục mô phỏng để chạy từ checkpoint.':model.id?'Checkpoint lưu sau mỗi bước.':'Checkpoint sẽ được ghi khi bắt đầu.');
 const channelList=panel.querySelector('.wf-channels');channelList.replaceChildren();
 for(const [name,label] of Object.entries(channels)){
  const row=document.createElement('div');row.textContent=name+' · '+label+' ('+model.journal.filter(e=>e.channel===name).length+')';channelList.append(row);
 }
 const list=by('wf-journal');list.replaceChildren();
 if(!model.journal.length)list.textContent='Chưa có sự kiện.';
 for(const e of model.journal){
  const row=document.createElement('button');row.type='button';row.textContent='#'+e.seq+' '+e.type+' · '+e.from+' → '+e.to;row.onclick=()=>show(e);list.append(row);
 }
 show(model.journal.at(-1));
}
function show(e){by('wf-envelope').textContent=JSON.stringify(e??{protocol:PROTOCOL,run_id:'<id>',seq:1,type:'work.started',channel:'tasks',from:'Orchestrator',to:'Maker',status:'WORKING',payload:{rejects:0},at:'<UTC ISO 8601>'},null,2);}
function persist(){
 try{localStorage.setItem(key,JSON.stringify(model));persistence='';}
 catch{persistence='Không ghi được checkpoint vào trình duyệt.';}
 render();
}
function event(type){
 const [from,status,sender,to,channel]=rules[type];
 if(model.state!==from||(type==='human.required'&&model.rejects!==3)||(type==='work.retried'&&model.rejects>=3))throw Error('Chuyển trạng thái không hợp lệ: '+type);
 if(type==='review.rejected')model.rejects++;
 model.state=status;
 model.journal.push({protocol:PROTOCOL,run_id:model.id,seq:model.journal.length+1,type,channel,from:sender,to,status,payload:{rejects:model.rejects},at:new Date().toISOString()});
}
window.officeWorkflow={
 pending:()=>mode==='paused'?{scenario:model.scenario,step:model.step}:null,
 begin(scenario){
  if(mode!=='paused'||model.scenario!==scenario)model={...empty(),id:crypto.randomUUID(),scenario};
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
