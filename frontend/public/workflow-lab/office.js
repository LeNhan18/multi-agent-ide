(() => {
const {slots,profiles}=window.officeSquads;
const positions=[
 ['#53e2a6',0,-2.4],['#79afff',3,-2.4],['#f6bf74',3,2.4],
 ['#cf9cf5',0,2.4],['#6ce4ed',-3,2.4],['#f18eab',-3,-2.4]
];
let agents=[];
const host=document.getElementById('office');
host.innerHTML='<div class="office-head"><div><div class="eyebrow">Mô phỏng tương tác · AI Office</div><h2>Văn phòng agent 3D</h2></div><div class="office-actions"><button id="office-run">▶ Chạy mô phỏng</button><button id="office-reset">Đặt lại góc nhìn</button></div></div><div class="office-layout"><div class="office-stage"><canvas aria-label="Mô hình 3D sáu agent cùng các máy tính được kết nối"></canvas><div class="office-hint">Kéo để xoay · Cuộn để thu phóng · Theo dõi kiện hàng giữa các trạm</div></div><aside><label>Đội theo ngành <select id="office-squad"></select></label><p id="office-artifact"></p><div class="office-picks"></div><h3 id="office-agent"></h3><a class="office-workspace-link" href="#agent-workspace">Công cụ &amp; nhật ký ↓</a><details class="office-policy" id="agent-model-panel"><summary>Model &amp; kết nối</summary><div id="agent-model-config"></div></details><details class="office-policy"><summary>Kỹ năng và ràng buộc</summary><dl id="office-policy"></dl></details><label>Kịch bản <select id="office-scenario"><option value="approve">Duyệt → Bàn giao</option><option value="reject">3 lần reject → Chờ người</option></select></label><p id="office-status" role="status">Sẵn sàng chạy mô phỏng.</p></aside></div>';
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
 window.officeActiveAgent={squad:squad.value,role:i,name:agents[i][0]};
 document.dispatchEvent(new Event('office-agent-changed'));
 [...picks.children].forEach((b,j)=>b.setAttribute('aria-pressed',i===j));
 by('office-agent').textContent=agents[i][0]+' · '+agents[i][1];
 const {specialty,skills,scope,limit,industryLimit,output}=window.officePolicies[squad.value][i];
 const policy=by('office-policy');
 policy.replaceChildren();
 for(const [label,value] of [
  ['Kỹ năng ngành',specialty],['Kỹ năng vai trò',skills],['Phạm vi được phép',scope],
  ['Ràng buộc vị trí',limit],['Ràng buộc ngành',industryLimit],['Đầu ra cần bàn giao',output]
 ]){
  const term=document.createElement('dt'),description=document.createElement('dd');
  term.textContent=label;description.textContent=value;policy.append(term,description);
 }
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
 const now=performance.now();
 const size=Math.min(w/12.7,h/8.8)*zoom, ca=Math.cos(angle),sa=Math.sin(angle),cp=Math.cos(pitch),sp=Math.sin(pitch);
 function project(x,y,z){const u=x*ca-z*sa,v=x*sa+z*ca;return {x:w/2+u*size,y:h*.54+(v*sp-y*cp)*size,depth:v*cp+y*sp};}
 const floor=(x,z)=>project(x,0,z);
 const floorAt=(x,y,z)=>project(x,y,z);
 function floorShadow(cx,cz,rx,rz,alpha=.36){
  const pts=[];
  for(let k=0;k<12;k++){const a=k*Math.PI/6;pts.push(project(cx+Math.cos(a)*rx,.003,cz+Math.sin(a)*rz));}
  path(pts,`rgba(2,6,16,${alpha})`);
 }
 const hubColor=(packet?.route!=null&&routes[packet.route]?.[2])||agents[selected]?.[2]||'#34d399';
 // 3D studio floor slab (skirt faces back-to-front, then top deck) to avoid painter z-fighting with furniture
 path([floorAt(-5.36,-.25,-4.76),floorAt(5.36,-.25,-4.76),floorAt(5.36,-.25,4.76),floorAt(-5.36,-.25,4.76)],'rgba(2,6,15,.46)');
 const slabSides=[
  {pts:[floorAt(-5.18,0,-4.58),floorAt(5.18,0,-4.58),floorAt(5.18,-.22,-4.58),floorAt(-5.18,-.22,-4.58)],c:'#15243d'},
  {pts:[floorAt(-5.18,0,4.58),floorAt(5.18,0,4.58),floorAt(5.18,-.22,4.58),floorAt(-5.18,-.22,4.58)],c:'#1b2e4d'},
  {pts:[floorAt(-5.18,0,-4.58),floorAt(-5.18,0,4.58),floorAt(-5.18,-.22,4.58),floorAt(-5.18,-.22,-4.58)],c:'#142239'},
  {pts:[floorAt(5.18,0,-4.58),floorAt(5.18,0,4.58),floorAt(5.18,-.22,4.58),floorAt(5.18,-.22,-4.58)],c:'#1e3354'}
 ];
 slabSides.sort((a,b)=>(a.pts.reduce((s,p)=>s+p.depth,0)/4)-(b.pts.reduce((s,p)=>s+p.depth,0)/4))
  .forEach(s=>path(s.pts,s.c,'#2c4368',.8));
 const deck=[floor(-5.18,-4.58),floor(5.18,-4.58),floor(5.18,4.58),floor(-5.18,4.58)];
 path(deck,'#0c172a','#3c5a85',1.2);
 const innerDeck=[floor(-5,-4.4),floor(5,-4.4),floor(5,4.4),floor(-5,4.4)];
 path(innerDeck,'#101d34','#2d4468',1);
 ctx.save();
 ctx.beginPath();innerDeck.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.clip();
 const cPt=floor(0,0);
 const glow=ctx.createRadialGradient(cPt.x,cPt.y,size*.2,cPt.x,cPt.y,size*6.4);
 glow.addColorStop(0,'rgba(45, 212, 191, 0.12)');
 glow.addColorStop(.55,'rgba(30, 58, 102, 0.08)');
 glow.addColorStop(1,'rgba(4, 9, 20, 0.34)');
 ctx.fillStyle=glow;ctx.fillRect(0,0,w,h);
 ctx.restore();
 for(let x=-5;x<=5;x++)path([floor(x,-4.4),floor(x,4.4)],null,'rgba(48,72,108,.45)',.8);
 for(let z=-4;z<=4;z++)path([floor(-5,z),floor(5,z)],null,'rgba(48,72,108,.45)',.8);
 // Central LangGraph Core floor conduits & concentric rings
 const hubOuter=[],hubInner=[];
 for(let k=0;k<20;k++){const a=k*Math.PI/10;hubOuter.push(project(Math.cos(a)*.95,.008,Math.sin(a)*.95));hubInner.push(project(Math.cos(a)*.74,.01,Math.sin(a)*.74));}
 path(hubOuter,'rgba(15,29,52,.68)',hubColor+'66',1.3);
 path(hubInner,null,hubColor+'aa',1.1);
 floorShadow(0,0,.85,.85,.44);
 ctx.setLineDash([3,5]);
 agents.forEach(([,,c,ax,az])=>path([project(0,.012,0),project(ax,.012,az-.53)],null,c+'28',1));
 ctx.setLineDash([]);
 const pc=i=>[agents[i][3],agents[i][4]-.53];
 function wire(a,b){
  const [x,z]=pc(a),[u,v]=pc(b);
  const dx=u-x,dz=v-z,len=Math.hypot(dx,dz),offset=a===1&&b===0?.21:0;
  return [[x,z],[x+dx*.23-dz/len*offset,z+dz*.23+dx/len*offset],[x+dx*.77-dz/len*offset,z+dz*.77+dx/len*offset],[u,v]];
 }
 routes.forEach(([a,b,color],i)=>{
  const points=wire(a,b).map(([x,z])=>project(x,.035,z));
  const activeRoute=packet?.route===i;
  ctx.setLineDash(i===2?[5,5]:i===3||i===4?[2,5]:[]);
  if(activeRoute)path(points,null,color+'3d',6.5);
  path(points,null,activeRoute?color:color+'88',activeRoute?2.8:1.6);
  ctx.setLineDash([]);
  if(activeRoute){
   const segT=((now*.0014)%1)*3,idx=Math.min(2,Math.floor(segT)),f=segT-idx;
   const px=points[idx].x+(points[idx+1].x-points[idx].x)*f,py=points[idx].y+(points[idx+1].y-points[idx].y)*f;
   ctx.fillStyle=color+'55';ctx.beginPath();ctx.arc(px,py,6.5,0,Math.PI*2);ctx.fill();
   ctx.fillStyle='#ffffff';ctx.beginPath();ctx.arc(px,py,2.8,0,Math.PI*2);ctx.fill();
  }
  const a1=points[1],b1=points[2],mx=(a1.x+b1.x)/2,my=(a1.y+b1.y)/2,theta=Math.atan2(b1.y-a1.y,b1.x-a1.x);
  path([{x:mx+5*Math.cos(theta),y:my+5*Math.sin(theta)},{x:mx+5*Math.cos(theta+2.4),y:my+5*Math.sin(theta+2.4)},{x:mx+5*Math.cos(theta-2.4),y:my+5*Math.sin(theta-2.4)}],color);
 });
 const polygons=[];
 function box(x,y,z,a,b,c,color){
  const vertices=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]].map(([u,v,w])=>project(x+u*a/2,y+v*b/2,z+w*c/2));
  faces.forEach((f,i)=>{const points=f.map(j=>vertices[j]);const [p,q,r]=points;
   if((q.x-p.x)*(r.y-p.y)-(q.y-p.y)*(r.x-p.x)>0)polygons.push({points,color:tint(color,[.68,1,.52,1.12,.79,.88][i]),depth:points.reduce((n,p)=>n+p.depth,0)/4});
  });
 }
 if(packet){
  const [from,to,color]=routes[packet.route];
  const points=wire(from,to);
  const smooth=t=>t*t*(3-2*t);
  const t=packet.t;
  const travel=smooth(Math.max(0,Math.min(1,(t-.17)/.66)));
  const lift=smooth(Math.min(1,t/.17,(1-t)/.17));
  const breaks=[0,.23,.77,1];
  const segment=travel<breaks[1]?0:travel<breaks[2]?1:2;
  const f=(travel-breaks[segment])/(breaks[segment+1]-breaks[segment]);
  const [ax,az]=points[segment], [bx,bz]=points[segment+1];
  const x=ax+(bx-ax)*f, z=az+(bz-az)*f, y=1.20+.57*lift;
  const shadow=floor(x,z);
  ctx.beginPath();
  ctx.ellipse(shadow.x,shadow.y,size*.30,size*.11,0,0,Math.PI*2);
  ctx.fillStyle='#0308159e';ctx.fill();
  if(t>=.83){
   const ring=(t-.83)/.17;
   const center=floor(...points[3]);
   ctx.beginPath();
   ctx.ellipse(center.x,center.y,size*(.25+.47*ring),size*(.10+.18*ring),0,0,Math.PI*2);
   ctx.globalAlpha=1-.65*ring;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();ctx.globalAlpha=1;
  }
  const cardboard=packet.route===2?'#cc9669':'#d8a46c';
  box(x,y,z,.63,.46,.53,cardboard);
  box(x,y+.231,z,.15,.012,.535,'#f5d29c');
  box(x,y,z+.267,.15,.46,.009,'#f5d29c');
  box(x+.16,y+.025,z+.274,.135,.11,.012,'#fff5db');
  box(x-.19,y-.10,z+.273,.14,.055,.012,color);
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
 // Central LangGraph Core Hub at room center
 const hubHover=packet?Math.sin(now*.006)*.045:0;
 box(0,.07,0,1.32,.14,1.32,'#1e304c');
 box(0,.21,0,.94,.14,.94,'#294063');
 box(0,.305,0,.98,.045,.98,hubColor);
 box(0,.54,0,.52,.42,.52,'#152540');
 box(0,.77,0,.62,.06,.62,'#4b668f');
 for(const hx of [-.21,.21])for(const hz of [-.21,.21])box(hx,.81,hz,.07,.04,.07,hubColor);
 box(0,.99+hubHover,0,.28,.28,.28,hubColor);

 agents.forEach(([,,color,x,z],i)=>{
  if(selected===i){
   const halo=[floor(x-1.36,z-1.46),floor(x+1.36,z-1.46),floor(x+1.36,z+1.46),floor(x-1.36,z+1.46)];
   path(halo,null,color+'55',3.8);
  }
  const platform=[floor(x-1.25,z-1.35),floor(x+1.25,z-1.35),floor(x+1.25,z+1.35),floor(x-1.25,z+1.35)];
  path(platform,color+(selected===i?'28':'11'),color+(selected===i?'ff':'5a'),selected===i?2.2:1.1);
  // Floor contact shadows under desk, tower, avatar & chair
  floorShadow(x,z-.53,.96,.52,.36);
  floorShadow(x+.67,z-.55,.25,.32,.32);
  floorShadow(x,z+.30,.48,.64,.38);
  // Workstation desk & steel legs
  box(x,.87,z-.53,1.72,.13,.88,'#617695');
  for(const dx of [-.72,.72])for(const dz of [-.84,-.24])box(x+dx,.42,z+dz,.09,.84,.09,'#455675');
  // Main monitor
  box(x,1.03,z-.72,.2,.22,.18,'#637b9e');
  box(x,1.37,z-.76,1.01,.59,.13,'#253652');
  box(x,1.37,z-.683,.86,.45,.022,'#142542');
  box(x,1.49,z-.669,.68,.038,.012,color);
  box(x-.14,1.37,z-.669,.39,.034,.012,'#7dd3fc');
  box(x-.21,1.26,z-.669,.25,.027,.012,color);
  // Side dashboard monitor (left)
  box(x-.64,1.01,z-.70,.12,.16,.12,'#536888');
  box(x-.64,1.29,z-.69,.38,.48,.09,'#253652');
  box(x-.64,1.29,z-.638,.31,.39,.018,'#13233e');
  box(x-.64,1.39,z-.624,.21,.034,.01,color);
  box(x-.64,1.30,z-.624,.21,.028,.01,'#8fb2d9');
  box(x-.64,1.21,z-.624,.16,.028,.01,color);
  // Desk mat, keyboard & mouse
  box(x+.12,.941,z-.26,1.04,.012,.33,'#1b2a44');
  box(x,.959,z-.27,.65,.038,.21,'#afc5d6');
  box(x+.49,.961,z-.25,.12,.04,.19,'#cbd5e1');
  // Coffee mug (front-left)
  box(x-.66,.99,z-.22,.12,.11,.12,'#e2e8f0');
  box(x-.66,1.046,z-.22,.09,.012,.09,'#451a03');
  box(x-.66,.99,z-.158,.07,.04,.01,color);
  // Desk lamp (back-right)
  box(x+.68,.955,z-.77,.17,.04,.17,'#334766');
  box(x+.68,1.14,z-.77,.045,.34,.045,'#4c6387');
  box(x+.62,1.30,z-.73,.17,.055,.13,'#253652');
  box(x+.62,1.268,z-.73,.12,.016,.09,'#fde68a');
  // PC tower & LED strip
  box(x+.67,.37,z-.55,.31,.69,.47,'#263957');
  box(x+.67,.56,z-.308,.17,.045,.02,color);
  box(x+.67,.35,z-.308,.045,.26,.015,color);
  // Ergonomic 3D swivel chair behind avatar
  const zc=z+.62;
  box(x,.035,zc,.52,.05,.10,'#2a3b57');
  box(x,.035,zc,.10,.05,.52,'#2a3b57');
  box(x,.21,zc,.09,.30,.09,'#475f82');
  box(x,.40,zc-.04,.54,.09,.50,'#2b3d59');
  box(x,.40,zc-.295,.44,.05,.02,color);
  box(x,.68,zc+.22,.12,.52,.07,'#3b5070');
  box(x,.78,zc+.17,.48,.54,.09,'#243550');
  box(x,.92,zc+.12,.38,.10,.02,color);
  box(x-.29,.54,zc-.03,.06,.18,.06,'#3b5070');
  box(x-.29,.64,zc-.05,.08,.04,.34,'#253652');
  box(x+.29,.54,zc-.03,.06,.18,.06,'#3b5070');
  box(x+.29,.64,zc-.05,.08,.04,.34,'#253652');
  avatar(x,z,color,i);
 });
 polygons.sort((a,b)=>a.depth-b.depth).forEach(p=>path(p.points,p.color,p.surface?p.color:undefined,.45));
 ctx.font='600 12px system-ui';
 const labels=agents.map(([name,,color,x,z],i)=>{
  const projected=project(x,2.1,z+.25);
  const width=Math.min(ctx.measureText(name).width+28,w-8);
  const anchor={
   x:Math.max(width/2+4,Math.min(w-width/2-4,projected.x)),
   y:Math.max(18,Math.min(h-15,projected.y))
  };
  return {name,color,anchor,center:project(x,.9,z),i,width};
 });
 // Draw the selected label last so it stays visible when names overlap.
 hits=[...labels.filter(label=>label.i!==selected),labels[selected]].filter(Boolean);
 for(const {name,color,anchor,i,width} of hits){
  const active=i===selected;
  ctx.fillStyle=active?color:'#111d31';
  ctx.beginPath();ctx.roundRect(anchor.x-width/2,anchor.y-14,width,25,6);ctx.fill();
  ctx.strokeStyle=color;ctx.stroke();
  ctx.fillStyle=active?'#101a28':color;
  ctx.beginPath();ctx.arc(anchor.x-width/2+10,anchor.y-1.5,3,0,Math.PI*2);ctx.fill();
  ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillText(name,anchor.x+4,anchor.y-1,Math.max(1,width-24));
 }
}
new ResizeObserver(draw).observe(canvas);
canvas.onpointerdown=e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,angle,pitch};canvas.setPointerCapture(e.pointerId);};
canvas.onpointermove=e=>{if(!drag)return;angle=drag.angle+(e.clientX-drag.x)*.009;pitch=Math.max(.26,Math.min(1.26,drag.pitch+(e.clientY-drag.y)*.007));draw();};
canvas.onpointerup=e=>{if(!drag)return;
 if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<5){
  const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  const tag=hits.findLast(o=>Math.abs(x-o.anchor.x)<o.width/2&&Math.abs(y-o.anchor.y)<15);
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
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  const duration=reduced?650:1450;
  const t=Math.min(1,(now-run.start)/duration);
  packet={route:steps[run.index][0],t:reduced?1:t};draw();
  if(t===1){workflow.advance();run.index++;run.start=null;
   if(run.index===steps.length){run=null;packet=null;button.textContent='▶ Chạy mô phỏng';scenario.disabled=false;squad.disabled=false;status.textContent=reject?'Chờ người xử lý sau 3 lần reject.':'Đã bàn giao và ghi nhận sự kiện.';draw();return;}
  }
  frame=requestAnimationFrame(animate);
 }
 frame=requestAnimationFrame(animate);
};
loadSquad();
})();
