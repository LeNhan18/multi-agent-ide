(() => {
const {slots,profiles}=window.officeSquads;
const positions=[
 ['#53e2a6',0,-2.4],['#79afff',3,-2.4],['#f6bf74',3,2.4],
 ['#cf9cf5',0,2.4],['#6ce4ed',-3,2.4],['#f18eab',-3,-2.4]
];
let agents=[];
const host=document.getElementById('office');
host.innerHTML='<div class="office-head"><div><div class="eyebrow">Mô phỏng tương tác · AI Office</div><h2>Văn phòng agent 3D</h2></div><div class="office-actions"><button id="office-run">▶ Chạy mô phỏng</button><button id="office-reset">Đặt lại góc nhìn</button></div></div><div class="office-layout"><div class="office-stage"><canvas aria-label="Mô hình 3D sáu agent cùng các máy tính được kết nối"></canvas><div class="office-hint">Kéo để xoay · Cuộn để thu phóng · Chọn một trạm để xem vai trò</div></div><aside><label>Đội theo ngành <select id="office-squad"></select></label><p id="office-artifact"></p><div class="office-picks"></div><h3 id="office-agent"></h3><p id="office-role"></p><label>Kịch bản <select id="office-scenario"><option value="approve">Duyệt → Bàn giao</option><option value="reject">3 lần reject → Chờ người</option></select></label><p id="office-status" role="status">Sẵn sàng chạy mô phỏng.</p></aside></div>';
const canvas=host.querySelector('canvas'),ctx=canvas.getContext('2d');
const by=id=>document.getElementById(id);
let angle=-.56, pitch=.59, zoom=1, selected=0, drag=null, hits=[], run=null, frame=0, packet=null;
const picks=host.querySelector('.office-picks');
const squad=by('office-squad');
for(const [key,[label]] of Object.entries(profiles))squad.add(new Option(label,key));
function loadSquad(){
 const [,artifact,roles]=profiles[squad.value];
 by('office-artifact').textContent='Sản phẩm mô phỏng: '+artifact;
 agents=roles.map((role,i)=>[role,slots[i],...positions[i],artifact]);
 picks.replaceChildren();
 agents.forEach(([name,,color],i)=>{
  const b=document.createElement('button');b.textContent=name;b.style.setProperty('--agent',color);b.onclick=()=>select(i);picks.append(b);
 });
 select(0);
}
squad.onchange=loadSquad;
function select(i){
 selected=i;
 [...picks.children].forEach((b,j)=>b.setAttribute('aria-pressed',i===j));
 by('office-agent').textContent=agents[i][0]+' · '+agents[i][1];
 by('office-role').textContent='Sản phẩm phụ trách: '+agents[i][5];
 draw();
}
const routes=[[0,1,'#53e2a6'],[1,2,'#79afff'],[1,0,'#f6bf74'],[2,3,'#cf9cf5'],[1,3,'#cf9cf5'],[4,5,'#6ce4ed'],[5,0,'#f18eab']];
const faces=[[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[0,3,7,4],[1,5,6,2]];
function tint(hex,k){
 const v=parseInt(hex.slice(1),16);
 return 'rgb('+[v>>16,(v>>8)&255,v&255].map(x=>Math.min(255,Math.round(x*k))).join(',')+')';
}
function path(points,fill,stroke,width=1){
 ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
 if(fill){ctx.closePath();ctx.fillStyle=fill;ctx.fill();}
 if(stroke){ctx.lineWidth=width;ctx.strokeStyle=stroke;ctx.stroke();}
}
function draw(){
 const w=canvas.clientWidth,h=canvas.clientHeight;if(!w||!h||!ctx)return;
 const dpr=Math.min(devicePixelRatio||1,2);
 if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
 ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 const size=Math.min(w/12.7,h/8.8)*zoom, ca=Math.cos(angle),sa=Math.sin(angle),cp=Math.cos(pitch),sp=Math.sin(pitch);
 function project(x,y,z){const u=x*ca-z*sa,v=x*sa+z*ca;return {x:w/2+u*size,y:h*.54+(v*sp-y*cp)*size,depth:v*cp+y*sp};}
 const floor=(x,z)=>project(x,0,z);
 path([floor(-5,-4.4),floor(5,-4.4),floor(5,4.4),floor(-5,4.4)],'#101d34','#304669');
 for(let x=-5;x<=5;x++)path([floor(x,-4.4),floor(x,4.4)],null,'#253653');
 for(let z=-4;z<=4;z++)path([floor(-5,z),floor(5,z)],null,'#253653');
 const pc=i=>[agents[i][3],agents[i][4]-.53];
 function wire(a,b){
  const [x,z]=pc(a),[u,v]=pc(b);
  const dx=u-x,dz=v-z,len=Math.hypot(dx,dz),offset=a===1&&b===0?.21:0;
  return [[x,z],[x+dx*.23-dz/len*offset,z+dz*.23+dx/len*offset],[x+dx*.77-dz/len*offset,z+dz*.77+dx/len*offset],[u,v]];
 }
 routes.forEach(([a,b,color],i)=>{
  const points=wire(a,b).map(([x,z])=>project(x,.035,z));
  ctx.setLineDash(i===2?[5,5]:i===3||i===4?[2,5]:[]);
  path(points,null,packet?.route===i?color:color+'83',packet?.route===i?3:1.6);
  ctx.setLineDash([]);
  const a1=points[1],b1=points[2],mx=(a1.x+b1.x)/2,my=(a1.y+b1.y)/2,theta=Math.atan2(b1.y-a1.y,b1.x-a1.x);
  path([{x:mx+5*Math.cos(theta),y:my+5*Math.sin(theta)},{x:mx+5*Math.cos(theta+2.4),y:my+5*Math.sin(theta+2.4)},{x:mx+5*Math.cos(theta-2.4),y:my+5*Math.sin(theta-2.4)}],color);
  if(packet?.route===i){const t=packet.t*3,j=Math.min(2,Math.floor(t)),f=t-j,p=points[j],q=points[j+1];ctx.beginPath();ctx.arc(p.x+(q.x-p.x)*f,p.y+(q.y-p.y)*f,5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.shadowColor=color;ctx.shadowBlur=14;ctx.fill();ctx.shadowBlur=0;}
 });
 const polygons=[];
 function box(x,y,z,a,b,c,color){
  const vertices=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]].map(([u,v,w])=>project(x+u*a/2,y+v*b/2,z+w*c/2));
  faces.forEach((f,i)=>{const points=f.map(j=>vertices[j]);const [p,q,r]=points;
   if((q.x-p.x)*(r.y-p.y)-(q.y-p.y)*(r.x-p.x)>0)polygons.push({points,color:tint(color,[.68,1,.52,1.12,.79,.88][i]),depth:points.reduce((n,p)=>n+p.depth,0)/4});
  });
 }
 function oval(x,y,z,rx,ry,rz,color,tilt=0,detail=12){
  const vertices=[], rings=detail===16?10:6;
  const ct=Math.cos(tilt),st=Math.sin(tilt);
  for(let j=0;j<=rings;j++){
   const lat=Math.PI*j/rings, radius=Math.sin(lat), v=Math.cos(lat)*ry;
   for(let k=0;k<detail;k++){
    const lon=2*Math.PI*k/detail,u=Math.cos(lon)*radius*rx,w=Math.sin(lon)*radius*rz;
    vertices.push(project(x+u*ct-v*st,y+u*st+v*ct,z+w));
   }
  }
  for(let j=0;j<rings;j++)for(let k=0;k<detail;k++){
   const n=(k+1)%detail;
   const points=[vertices[j*detail+k],vertices[(j+1)*detail+k],vertices[(j+1)*detail+n],vertices[j*detail+n]];
   const [a,b,c,d]=points;
   const area=(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x)+(c.x-a.x)*(d.y-a.y)-(c.y-a.y)*(d.x-a.x);
   if(area<=1e-5)continue;
   const lat=Math.PI*(j+.5)/rings,lon=2*Math.PI*(k+.5)/detail;
   const u=Math.sin(lat)*Math.cos(lon)/rx,v=Math.cos(lat)/ry,w=Math.sin(lat)*Math.sin(lon)/rz;
   const nx=u*ct-v*st,ny=u*st+v*ct,length=Math.hypot(nx,ny,w);
   const light=Math.max(0,(-.42*nx+.79*ny+.44*w)/length);
   polygons.push({points,color:tint(color,.57+light*.51),surface:true,depth:(a.depth+b.depth+c.depth+d.depth)/4});
  }
 }
 function avatar(x,z,color,i){
  const body='#536b91',face='#e6efff',visor='#263b5b';
  const o=(dx,y,dz,rx,ry,rz,c,tilt,detail)=>oval(x+dx,y,z+dz,rx,ry,rz,c,tilt,detail);
  for(const side of [-1,1]){
   o(side*.13,.43,.68,.12,.23,.13,body,side*.1);
   o(side*.16,.23,.7,.105,.21,.115,body,side*.1);
   o(side*.17,.09,.77,.13,.09,.19,face);
   o(side*.32,.97,.68,.125,.20,.13,color,side*.34);
   o(side*.40,.76,.73,.095,.16,.10,body,side*.20);
   o(side*.42,.63,.78,.09,.10,.095,face);
  }
  o(0,.58,.68,.25,.17,.19,body);
  o(0,.86,.68,.31,.36,.215,color,0,16);
  o(0,.92,.87,.14,.125,.055,face);
  o(0,.92,.92,.065,.061,.016,color);
  o(0,1.15,.68,.11,.14,.11,body);
  o(0,1.45,.68,.29,.27,.235,face,0,16);
  o(0,1.43,.865,.229,.142,.101,visor,0,16);
  for(const side of [-1,1]){
   o(side*.09,1.458,.952,.043,.049,.022,color);
   o(side*.278,1.45,.675,.046,.115,.105,color);
  }
  o(0,1.36,.955,.051,.012,.012,'#aabdd9');
  if(i===0){
   o(-.09,1.715,.66,.027,.096,.028,body,.26);
   o(-.113,1.804,.66,.052,.052,.052,color);
  }else if(i===1){
   o(.275,1.37,.784,.028,.122,.03,body,.64);
   o(.204,1.28,.815,.065,.033,.04,color);
  }else if(i===2){
   o(0,1.69,.683,.187,.081,.163,color);
  }else{
   o(-.10,1.72,.67,.025,.075,.024,body,.26);
   o(.10,1.72,.67,.025,.075,.024,body,-.26);
   o(-.12,1.787,.67,.042,.043,.044,color);
   o(.12,1.787,.67,.042,.043,.044,color);
  }
 }
 agents.forEach(([,,color,x,z],i)=>{
  const platform=[floor(x-1.25,z-1.35),floor(x+1.25,z-1.35),floor(x+1.25,z+1.35),floor(x-1.25,z+1.35)];
  path(platform,color+(selected===i?'24':'10'),color+(selected===i?'ff':'52'),selected===i?2:1);
  box(x,.87,z-.53,1.72,.13,.88,'#617695');
  for(const dx of [-.72,.72])for(const dz of [-.84,-.24])box(x+dx,.42,z+dz,.09,.84,.09,'#455675');
  box(x,1.03,z-.72,.2,.22,.18,'#637b9e');
  box(x,1.37,z-.76,1.01,.59,.13,'#253652');
  box(x,1.37,z-.683,.86,.45,.022,'#152745');
  box(x,1.49,z-.669,.68,.038,.012,color);
  box(x-.14,1.37,z-.669,.39,.034,.012,color);
  box(x-.21,1.26,z-.669,.25,.027,.012,color);
  box(x, .956,z-.27,.65,.038,.21,'#afc5d6');
  box(x+.49,.958,z-.25,.13,.043,.2,'#afc5d6');
  box(x+.67,.37,z-.55,.31,.69,.47,'#263957');
  box(x+.67,.56,z-.308,.17,.045,.02,color);
  avatar(x,z,color,i);
 });
 polygons.sort((a,b)=>a.depth-b.depth).forEach(p=>path(p.points,p.color,p.surface?p.color:undefined,.45));
 hits=agents.map(([,,color,x,z],i)=>{
  const anchor=project(x,2.1,z+.25),center=project(x,.9,z);
  ctx.font='600 12px system-ui';const label=slots[i];const width=ctx.measureText(label).width+20;
  ctx.fillStyle='#111d31';ctx.beginPath();ctx.roundRect(anchor.x-width/2,anchor.y-14,width,25,6);ctx.fill();
  ctx.strokeStyle=color;ctx.stroke();ctx.fillStyle=color;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(label,anchor.x,anchor.y-1);
  return {anchor,center,i,width};
 });
}
new ResizeObserver(draw).observe(canvas);
canvas.onpointerdown=e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,angle,pitch};canvas.setPointerCapture(e.pointerId);};
canvas.onpointermove=e=>{if(!drag)return;angle=drag.angle+(e.clientX-drag.x)*.009;pitch=Math.max(.26,Math.min(1.26,drag.pitch+(e.clientY-drag.y)*.007));draw();};
canvas.onpointerup=e=>{if(!drag)return;
 if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<5){
  const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  const tag=hits.find(o=>Math.abs(x-o.anchor.x)<o.width/2&&Math.abs(y-o.anchor.y)<15);
  const hit=tag??hits.map(o=>({...o,d:Math.hypot(x-o.center.x,y-o.center.y)})).sort((a,b)=>a.d-b.d)[0];
  if(hit&&(tag||hit.d<Math.min(canvas.clientWidth,canvas.clientHeight)*.11))select(hit.i);
 }
 drag=null;
};
canvas.onpointercancel=()=>drag=null;
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.65,Math.min(1.8,zoom*Math.exp(-e.deltaY*.001)));draw();},{passive:false});
by('office-reset').onclick=()=>{angle=-.56;pitch=.59;zoom=1;draw();};
const status=by('office-status'),button=by('office-run'),scenario=by('office-scenario');
const workflow=window.officeWorkflow;
const pending=workflow.pending();
if(pending){
 scenario.value=pending.scenario;scenario.disabled=true;squad.value=pending.squad;squad.disabled=true;
 button.textContent='▶ Tiếp tục mô phỏng';status.textContent='Đã tải checkpoint.';
}
button.onclick=()=>{
 if(run){cancelAnimationFrame(frame);run=null;packet=null;workflow.pause();button.textContent='▶ Tiếp tục mô phỏng';status.textContent='Đã tạm dừng tại checkpoint.';draw();return;}
 const reject=scenario.value==='reject';
 const [producer,reviewer,delivery,auditor,intake,specialist]=profiles[squad.value][2];
 const steps=[[5,intake+' chuyển yêu cầu'],[6,specialist+' chuẩn bị đầu vào'],...(reject?
 [[0,producer+' gửi bản nháp lần 1'],[2,reviewer+' trả lại lần 1'],[0,producer+' gửi bản nháp lần 2'],[2,reviewer+' trả lại lần 2'],[0,producer+' gửi bản nháp lần 3'],[4,auditor+' ghi nhận lần trả lại thứ ba']]:
 [[0,producer+' gửi bản nháp'],[1,reviewer+' duyệt và chuyển bàn giao'],[3,delivery+' hoàn tất · '+auditor+' ghi nhận']])];
 run={steps,index:workflow.begin(scenario.value,squad.value),start:null};button.textContent='■ Tạm dừng';scenario.disabled=true;squad.disabled=true;
 function animate(now){
  if(!run)return;
  if(run.start===null){run.start=now;status.textContent=steps[run.index][1];}
  const duration=matchMedia('(prefers-reduced-motion: reduce)').matches?650:1150;
  const t=Math.min(1,(now-run.start)/duration);
  packet={route:steps[run.index][0],t:duration===650?1:t};draw();
  if(t===1){workflow.advance();run.index++;run.start=null;
   if(run.index===steps.length){run=null;packet=null;button.textContent='▶ Chạy mô phỏng';scenario.disabled=false;squad.disabled=false;status.textContent=reject?'Chờ người xử lý sau 3 lần reject.':'Đã bàn giao và ghi nhận sự kiện.';draw();return;}
  }
  frame=requestAnimationFrame(animate);
 }
 frame=requestAnimationFrame(animate);
};
loadSquad();
})();
