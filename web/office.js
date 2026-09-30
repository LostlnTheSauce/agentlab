/* Isometric pixel office. Exposes window.Office and window.sprite. Draws from data; owns no state beyond camera + selection. */
(function(){
"use strict";
const REDUCED=matchMedia('(prefers-reduced-motion: reduce)').matches;

function shade(hex,amt){const n=parseInt(hex.slice(1),16);const r=n>>16,g=n>>8&255,b=n&255;const f=v=>Math.max(0,Math.min(255,Math.round(v+(amt<0?v*amt:(255-v)*amt))));return `rgb(${f(r)},${f(g)},${f(b)})`}
function sprite(cv,look){
  const g=cv.getContext('2d');g.clearRect(0,0,13,16);
  const r=(x,y,w,h,f)=>{g.fillStyle=f;g.fillRect(x,y,w,h)};
  r(3,1,7,2,look.hair);r(3,3,7,5,look.skin);r(3,3,1,3,look.hair);r(5,5,1,1,'#10181b');r(8,5,1,1,'#10181b');
  r(2,8,9,5,look.shirt);r(8,8,3,5,shade(look.shirt,-.22));r(1,9,1,3,look.skin);r(11,9,1,3,look.skin);
  r(3,13,3,3,'#2d3b4a');r(7,13,3,3,'#2d3b4a');
}
window.sprite=sprite;

const T=16,HH=8,W=24,D=26,WALL=96;
const iso=(x,y,z=0)=>[(x-y)*T,(x+y)*HH-z];
const SCN={x0:-430,x1:400,y0:-150,y1:410};
const DESK_POS={nfl:{x0:10,y:3},cfb:{x0:10,y:8},mlb:{x0:10,y:13},hs:{x0:12,y:18},lab:{x0:2,y:19}};
const DESKC={t:'#cfe9dc',l:'#7fa596',r:'#9cc2b2',e:'#e9fff4'};
const CHAIR={t:'#2e3f48',l:'#1a252c',r:'#232f37'};

let cv,ctx,cw=0,ch=0,dpr=1,cam={x:40,y:150,z:1},last=0,onSelect=()=>{},ts=1;
const ZMIN=0.3,ZMAX=3;
let data={desks:[],people:[],staff:[],jail:[],screen:null},selected=null,hover=null,visible=true;

function poly(pts,fill,stroke){ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();if(fill){ctx.fillStyle=fill;ctx.fill()}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke()}}
function box(x,y,z,w,d,h,c){
  poly([iso(x,y+d,z),iso(x+w,y+d,z),iso(x+w,y+d,z+h),iso(x,y+d,z+h)],c.l);
  poly([iso(x+w,y,z),iso(x+w,y+d,z),iso(x+w,y+d,z+h),iso(x+w,y,z+h)],c.r);
  poly([iso(x,y,z+h),iso(x+w,y,z+h),iso(x+w,y+d,z+h),iso(x,y+d,z+h)],c.t,c.e);
}
function pill(x,y,text,bg,fg,font,pad=4){
  /* drawn around its bottom-centre anchor at scale ts, so labels stay legible when zoomed out */
  ctx.font=font;const w=Math.ceil(ctx.measureText(text).width)+pad*2,h=parseInt(font)+4;
  ctx.save();ctx.translate(Math.round(x),Math.round(y));ctx.scale(ts,ts);
  const bx=Math.round(-w/2),by=-h;
  ctx.fillStyle=bg;ctx.fillRect(bx,by,w,h);ctx.fillStyle='#0008';ctx.fillRect(bx,by+h,w,2);
  ctx.fillStyle=fg;ctx.textBaseline='top';ctx.fillText(text,bx+pad,by+2);
  ctx.restore();
  return {bx:x+bx*ts,by:y+by*ts,w:w*ts,h:h*ts};
}
function person(p,x,y,t){
  box(x-0.22,y-0.3,0,0.44,0.36,14,CHAIR);
  const [px,py]=iso(x,y,0);
  const bob=p.working&&!REDUCED?(Math.sin(t*5+p.i*1.7)>0.55?1:0):0;
  const bx=Math.round(px),by=Math.round(py)-5+bob;
  const r=(dx,dy,w,h,f)=>{ctx.fillStyle=f;ctx.fillRect(bx+dx,by+dy,w,h)};
  const L=p.look;
  r(-5,-12,10,12,L.shirt);r(2,-12,3,12,shade(L.shirt,-.25));
  r(-4,-21,8,9,L.skin);r(-4,-22,8,3,L.hair);r(-4,-19,2,3,L.hair);
  r(-1,-17,1,1,'#0e1519');r(2,-17,1,1,'#0e1519');
  p.sx=bx;p.sy=by-12;
  return by;
}
function laptop(x,y){
  poly([iso(x-0.32,y+0.2,10),iso(x+0.32,y+0.2,10),iso(x+0.32,y+0.55,10),iso(x-0.32,y+0.55,10)],'#2a3438');
  poly([iso(x-0.32,y+0.2,10),iso(x+0.32,y+0.2,10),iso(x+0.32,y+0.2,19),iso(x-0.32,y+0.2,19)],'#46555c','#6e8088');
  const [lx,ly]=iso(x,y+0.2,15);ctx.fillStyle='#c7f78c';ctx.fillRect(Math.round(lx)-1,Math.round(ly)-1,2,2);
}
function bubble(p,topY,t){
  const b=p.bubble||{text:'—',tone:0};
  const bg=b.tone>0?'#2b7a4f':b.tone<0?'#9a3b34':'#33494a';
  const q=pill(p.sx,topY-8,b.text,bg,'#f2fff6','12px VT323');
  ctx.fillStyle=bg;ctx.fillRect(p.sx-2,topY-6,4,3);
  if(p.hasPick){ctx.fillStyle=p.realPick?'#f0c281':'#c7f78c';ctx.fillRect(q.bx+q.w+2,q.by+3,4,6);ctx.fillRect(q.bx+q.w+2,q.by+10,4,2)}
  else if(p.working&&!REDUCED){const k=Math.floor(t*3+p.i)%4;ctx.fillStyle='#c7f78c';for(let d=0;d<k;d++)ctx.fillRect(q.bx+q.w+3+d*3,q.by+8,2,2)}
  if(p.broke){ctx.strokeStyle='#ff8e80';ctx.strokeRect(q.bx-.5,q.by-.5,q.w+1,q.h+1)}
  if(selected===p.id||hover===p.id)pill(p.sx,q.by-3,p.name.toUpperCase(),'#c7f78c','#10230f','8px Silkscreen',3);
}
function label(x,y,z,t1,t2){
  const [lx,ly]=iso(x,y,z);
  ctx.strokeStyle='#c7f78c88';ctx.beginPath();ctx.moveTo(Math.round(lx)+.5,ly);ctx.lineTo(Math.round(lx)+.5,ly+z-14);ctx.stroke();
  const p=pill(lx,ly,t1,'#0b1f21','#e4ede5','10px Silkscreen',6);
  ctx.strokeStyle='#5ed3b4';ctx.strokeRect(p.bx+.5,p.by+.5,p.w-1,p.h-1);
  if(t2){ctx.font='13px VT323';ctx.fillStyle='#94aba1';ctx.textAlign='center';ctx.fillText(t2,lx,ly+1);ctx.textAlign='left'}
}
function floor(){
  for(let x=0;x<W;x++)for(let y=0;y<D;y++)poly([iso(x,y),iso(x+1,y),iso(x+1,y+1),iso(x,y+1)],(Math.floor(x/2)%2)?'#12382a':'#154030');
  ctx.strokeStyle='#dfffe91c';ctx.lineWidth=1;
  for(let x=0;x<=W;x+=4){const a=iso(x,0),b=iso(x,D);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()}
  ctx.save();const o=iso(0,0);ctx.transform(1,0.5,-1,0.5,o[0],o[1]);
  ctx.fillStyle='#0e2e22';ctx.fillRect(0,23*T,W*T,3*T);
  ctx.font='700 26px Silkscreen';ctx.fillStyle='#c7f78c30';ctx.textBaseline='middle';ctx.fillText('AGENT LAB',5.2*T,24.5*T);
  ctx.font='16px Silkscreen';ctx.fillStyle='#dfffe930';[['10',4],['20',8],['30',12],['40',16],['50',20]].forEach(([n,x])=>ctx.fillText(n,x*T-9,21.9*T));
  ctx.restore();
}
function walls(){
  poly([iso(0,0,0),iso(W,0,0),iso(W,0,WALL),iso(0,0,WALL)],'#0f2a2b');
  [[0,JY0],[JY1,D]].forEach(([a,b])=>poly([iso(0,a,0),iso(0,b,0),iso(0,b,WALL),iso(0,a,WALL)],'#0b2223'));
  poly([iso(0,0,WALL),iso(W,0,WALL),iso(W,0,WALL+4),iso(0,0,WALL+4)],'#2c4a48');
  [[0,JY0],[JY1,D]].forEach(([a,b])=>poly([iso(0,a,WALL),iso(0,b,WALL),iso(0,b,WALL+4),iso(0,a,WALL+4)],'#223c3b'));
  const s=data.screen||{};
  ctx.save();let o=iso(11,0,86);ctx.transform(1,0.5,0,1,o[0],o[1]);
  const SW=10*T,SH=58;
  ctx.fillStyle='#051011';ctx.fillRect(-3,-3,SW+6,SH+6);ctx.strokeStyle='#5ed3b4';ctx.lineWidth=2;ctx.strokeRect(-3,-3,SW+6,SH+6);
  ctx.fillStyle='#0a1d1e';ctx.fillRect(0,0,SW,SH);
  ctx.textBaseline='top';ctx.font='8px Silkscreen';ctx.fillStyle='#94aba1';ctx.fillText(s.title||'',6,4);
  ctx.font='26px VT323';ctx.fillStyle='#e4ede5';ctx.fillText(s.big||'',6,13);
  ctx.font='13px VT323';ctx.fillStyle=s.up===false?'#ff8e80':'#7be3a4';ctx.fillText(s.line1||'',6,34);
  ctx.fillStyle='#f0c281';ctx.fillText(s.line2||'',6,44);
  const ser=s.series||[];
  if(ser.length>1){const mn=Math.min(0,...ser),mx=Math.max(0.01,...ser);ctx.strokeStyle='#c7f78c';ctx.lineWidth=1.5;ctx.beginPath();
    ser.forEach((v,i)=>{const X=100+i*(54/(ser.length-1)),Y=50-(v-mn)/(mx-mn)*40;i?ctx.lineTo(X,Y):ctx.moveTo(X,Y)});ctx.stroke()}
  ctx.restore();
  ctx.save();o=iso(0,17.2,80);ctx.transform(1,-0.5,0,1,o[0],o[1]);
  ctx.fillStyle='#b8692c';ctx.fillRect(0,0,52,40);ctx.fillStyle='#8a4a1c';ctx.fillRect(0,36,52,4);
  ctx.font='8px Silkscreen';ctx.fillStyle='#ffe2b8';ctx.textBaseline='top';ctx.fillText('BOARD',11,5);
  const bd=s.board||{cleared:0,flagged:0,vetoed:0};
  ctx.fillStyle='#fff3dc';ctx.font='13px VT323';ctx.fillText(`${bd.cleared} OK`,6,15);ctx.fillText(`${bd.flagged} ? ${bd.vetoed} X`,6,24);
  ctx.restore();
  ctx.save();o=iso(0,25.4,74);ctx.transform(1,-0.5,0,1,o[0],o[1]);
  ctx.font='700 12px Silkscreen';ctx.fillStyle='#c7f78c';ctx.textBaseline='top';ctx.fillText('AGENT LAB',0,0);
  ctx.font='12px VT323';ctx.fillStyle='#94aba1';ctx.fillText('PAPER BETS · REAL ODDS',0,16);
  ctx.restore();
}
function glassRoom(x0,y0,x1,y1,h,back){
  const glass='#9fe8d614',edge='#9fe8d655';
  if(back){poly([iso(x0,y0),iso(x1,y0),iso(x1,y1),iso(x0,y1)],'#0d2a25');
    poly([iso(x0,y0,0),iso(x1,y0,0),iso(x1,y0,h),iso(x0,y0,h)],glass,edge);
    poly([iso(x0,y0,0),iso(x0,y1,0),iso(x0,y1,h),iso(x0,y0,h)],glass,edge);}
  else{poly([iso(x1,y0,0),iso(x1,y1,0),iso(x1,y1,h),iso(x1,y0,h)],glass,edge);
    poly([iso(x0,y1,0),iso(x1,y1,0),iso(x1,y1,h),iso(x0,y1,h)],glass,edge);}
}
function deskSign(x,y,len,text,n){
  /* a name plate on the desk's front panel, facing the viewer */
  ctx.save();const o=iso(x,y,10);ctx.transform(1,0.5,0,1,o[0],o[1]);
  ctx.font='8px Silkscreen';const label=`${text} · ${n}`;const w=Math.min(Math.ceil(ctx.measureText(label).width)+8,len*T-4);
  ctx.fillStyle='#0b1f21';ctx.fillRect(4,1,w,8);ctx.fillStyle='#5ed3b4';ctx.fillRect(4,1,2,8);
  ctx.fillStyle='#c7f78c';ctx.textBaseline='top';ctx.fillText(label,9,1.5,w-7);
  ctx.restore();
}
/* ---- tipster jail: a concrete annex off the left wall; fired tipsters wander in stripes */
const JX0=-6.5,JX1=0,JY0=1.2,JY1=7.8,JH=62;
let jailTops=[];
function bars(x0,y0,x1,y1,h){
  const n=Math.round(Math.hypot(x1-x0,y1-y0)*4);
  ctx.strokeStyle='#8fa7a3';ctx.lineWidth=1;
  for(let i=0;i<=n;i++){const x=x0+(x1-x0)*i/n,y=y0+(y1-y0)*i/n,a=iso(x,y,0),b=iso(x,y,h);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()}
  ctx.strokeStyle='#b9ccc8';ctx.lineWidth=2;[h,h*0.55].forEach(z=>{const a=iso(x0,y0,z),b=iso(x1,y1,z);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()});ctx.lineWidth=1;
}
function convict(p,x,y,t){
  const [px,py]=iso(x,y,0);
  const step=REDUCED?0:(Math.sin(t*6+p.i)>0?1:0);
  const bx=Math.round(px),by=Math.round(py);
  const r=(dx,dy,w,h,f)=>{ctx.fillStyle=f;ctx.fillRect(bx+dx,by+dy,w,h)};
  ctx.fillStyle='#0006';ctx.beginPath();ctx.ellipse(bx,by,6,3,0,0,7);ctx.fill();
  r(-4,-7,3,7-step,'#2d3b4a');r(1,-7,3,6+step,'#2d3b4a');
  r(-5,-19,10,12,'#e9e4d0');for(let k=0;k<4;k++)r(-5,-18+k*3,10,1,'#1b1f22');
  r(-4,-28,8,9,p.look.skin);r(-4,-29,8,3,p.look.hair);r(-4,-26,2,3,p.look.hair);
  r(-1,-24,1,1,'#0e1519');r(2,-24,1,1,'#0e1519');
  p.sx=bx;p.sy=by-16;
  return by-30;
}
function jail(t){
  // floor, walls and trim of the annex
  for(let x=Math.floor(JX0);x<JX1;x++)for(let y=Math.floor(JY0);y<JY1;y++){
    const x0=Math.max(x,JX0),y0=Math.max(y,JY0),x1=Math.min(x+1,JX1),y1=Math.min(y+1,JY1);
    poly([iso(x0,y0),iso(x1,y0),iso(x1,y1),iso(x0,y1)],(x+y)%2?'#232d2c':'#1f2827');
  }
  poly([iso(JX0,JY0,0),iso(JX1,JY0,0),iso(JX1,JY0,JH),iso(JX0,JY0,JH)],'#1c2524');
  poly([iso(JX0,JY0,0),iso(JX0,JY1,0),iso(JX0,JY1,JH),iso(JX0,JY0,JH)],'#161e1d');
  poly([iso(JX0,JY0,JH),iso(JX1,JY0,JH),iso(JX1,JY0,JH+4),iso(JX0,JY0,JH+4)],'#3a4847');
  poly([iso(JX0,JY0,JH),iso(JX0,JY1,JH),iso(JX0,JY1,JH+4),iso(JX0,JY0,JH+4)],'#303c3b');
  // a little barred window and a cot
  ctx.save();const w=iso(JX0+2.2,JY0,44);ctx.transform(1,0.5,0,1,w[0],w[1]);
  ctx.fillStyle='#0a1112';ctx.fillRect(0,0,26,14);ctx.fillStyle='#8fa7a3';for(let i=4;i<26;i+=5)ctx.fillRect(i,0,1,14);ctx.restore();
  box(JX0+0.3,JY0+0.4,0,2.4,0.9,5,{t:'#8d9a98',l:'#566361',r:'#6b7876',e:'#b4c1bf'});
  box(JX0+0.5,JY0+0.45,5,0.7,0.8,2,{t:'#e9e4d0',l:'#bdb8a4',r:'#d3ceba'});
  // inmates wander, sorted back to front
  const cx=(JX0+JX1)/2,cy=(JY0+JY1)/2+0.4;
  const spots=data.jail.map((p,i)=>{
    const x=cx+Math.sin(t*0.29+i*2.3)*2.2,y=cy+Math.cos(t*0.21+i*1.7)*2.3;
    return [p,Math.min(Math.max(x,JX0+.6),JX1-.6),Math.min(Math.max(y,JY0+1.6),JY1-.5)];
  }).sort((a,b)=>(a[1]+a[2])-(b[1]+b[2]));
  jailTops=spots.map(([p,x,y])=>[p,convict(p,x,y,t)]);
  // bars: the side facing the office floor, and the front
  bars(JX1,JY0,JX1,JY1,JH-6);bars(JX0,JY1,JX1,JY1,JH-6);
}
function jailLabels(){
  jailTops.forEach(([p,top])=>{
    const b=p.bubble||{text:'FIRED',tone:-1};
    pill(p.sx,top-2,b.text,b.tone<0?'#9a3b34':'#33494a','#f2fff6','12px VT323');
    if(selected===p.id||hover===p.id)pill(p.sx,top-18*ts,p.name.toUpperCase(),'#c7f78c','#10230f','8px Silkscreen',3);
  });
  label((JX0+JX1)/2,JY0+0.2,JH+26,'TIPSTER JAIL',data.jail.length?`${data.jail.length} fired`:'no inmates yet');
}
function plant(x,y){box(x-0.25,y-0.25,0,0.5,0.5,8,{t:'#6b4a2e',l:'#4a311d',r:'#5a3c24'});const [px,py]=iso(x,y,8);ctx.fillStyle='#3fa36a';ctx.fillRect(px-6,py-14,12,12);ctx.fillStyle='#57c985';ctx.fillRect(px-3,py-18,6,8)}

function scene(t){
  ts=Math.min(Math.max(0.9/cam.z,1),2.2);
  ctx.setTransform(1,0,0,1,0,0);ctx.fillStyle='#07130f';ctx.fillRect(0,0,cv.width,cv.height);
  ctx.setTransform(dpr*cam.z,0,0,dpr*cam.z,dpr*(cw/2-cam.x*cam.z),dpr*(ch/2-cam.y*cam.z));
  floor();walls();jail(t);
  const staff=Object.fromEntries(data.staff.map(s=>[s.id,s]));
  glassRoom(1,1,7.5,6,34,true);
  if(staff.commish){person(staff.commish,4.2,2.7,t);}
  box(2.8,3.1,0,3,1.1,10,{t:'#e0c89a',l:'#8a7350',r:'#a88c62',e:'#fff2d0'});laptop(4.2,3.1);
  glassRoom(1,1,7.5,6,34,false);
  glassRoom(1,8,7.5,13,34,true);
  [['mara',3.2],['barb',4.5],['carl',5.8]].forEach(([k,x])=>{if(staff[k])person(staff[k],x,9.6,t)});
  box(2.6,10,0,3.8,1.5,10,{t:'#bcd9cf',l:'#6f9486',r:'#8db3a4',e:'#e9fff4'});
  glassRoom(1,8,7.5,13,34,false);
  const tops=[];
  data.desks.forEach(d=>{
    const pos=DESK_POS[d.key];if(!pos)return;
    const ppl=data.people.filter(p=>p.desk===d.key),len=Math.max(1,ppl.length)*2;
    ppl.forEach((p,j)=>{p.fx=pos.x0+j*2+1;p.fy=pos.y-0.4;tops.push([p,person(p,p.fx,p.fy,t)])});
    box(pos.x0,pos.y,0,len,1,10,DESKC);
    deskSign(pos.x0,pos.y+1,len,d.label,ppl.length);
    ppl.forEach(p=>laptop(p.fx,pos.y));
    const sel=ppl.find(p=>p.id===selected);
    if(sel)poly([iso(sel.fx-0.6,sel.fy-0.5),iso(sel.fx+0.6,sel.fy-0.5),iso(sel.fx+0.6,sel.fy+0.2),iso(sel.fx-0.6,sel.fy+0.2)],null,'#c7f78c');
  });
  plant(22.5,1.5);plant(9,1);plant(22.5,22.5);plant(1.5,16);
  label(4.2,1.4,62,'CEO',staff.commish?staff.commish.name:'');label(4.2,8.4,60,'RISK BOARD','3 members');
  tops.forEach(([p,by])=>bubble(p,by-22,t));
  jailLabels();
  data.staff.forEach(s=>{if(s.sx!=null&&(selected===s.id||hover===s.id))pill(s.sx,s.sy-14,s.name.toUpperCase(),'#c7f78c','#10230f','8px Silkscreen',3)});
}
function resize(){const r=cv.getBoundingClientRect();cw=r.width;ch=r.height;dpr=Math.min(window.devicePixelRatio||1,2);cv.width=Math.round(cw*dpr);cv.height=Math.round(ch*dpr);last=0}
function fit(){const z=Math.min(cw/(SCN.x1-SCN.x0),ch/(SCN.y1-SCN.y0))*0.98;cam.z=Math.max(z,ZMIN);cam.x=(SCN.x0+SCN.x1)/2;cam.y=(SCN.y0+SCN.y1)/2;last=0}
function startView(){fit();if(cw<600){cam.z=Math.min(ZMAX,Math.max(cam.z,0.8));cam.x=60;cam.y=170;clampCam()}}
function clampCam(){
  /* keep at least part of the office on screen */
  const hx=cw/2/cam.z,hy=ch/2/cam.z;
  cam.x=Math.min(Math.max(cam.x,SCN.x0+Math.min(hx,(SCN.x1-SCN.x0)/2)-hx*0.5),SCN.x1-Math.min(hx,(SCN.x1-SCN.x0)/2)+hx*0.5);
  cam.y=Math.min(Math.max(cam.y,SCN.y0+Math.min(hy,(SCN.y1-SCN.y0)/2)-hy*0.5),SCN.y1-Math.min(hy,(SCN.y1-SCN.y0)/2)+hy*0.5);
  last=0;
}
function zoomAt(f,px,py){
  /* zoom by f keeping the scene point under (px,py) (canvas css px) fixed */
  const sx=(px-cw/2)/cam.z+cam.x,sy=(py-ch/2)/cam.z+cam.y;
  cam.z=Math.min(Math.max(cam.z*f,ZMIN),ZMAX);
  cam.x=sx-(px-cw/2)/cam.z;cam.y=sy-(py-ch/2)/cam.z;clampCam();
}
function loop(ts){if(visible&&ts-last>90){last=ts;scene(ts/1000)}requestAnimationFrame(loop)}
function toScene(e){const r=cv.getBoundingClientRect();return[(e.clientX-r.left-cw/2)/cam.z+cam.x,(e.clientY-r.top-ch/2)/cam.z+cam.y]}
function hit(e){const [x,y]=toScene(e);let best=null,bd=14;[...data.people,...data.staff,...data.jail].forEach(a=>{if(a.sx==null)return;const d=Math.hypot(a.sx-x,a.sy-y);if(d<bd){bd=d;best=a}});return best}

window.Office={
  init(canvas,cb){
    cv=canvas;ctx=cv.getContext('2d');onSelect=cb;
    /* one pointer drags, two pointers pinch; double-tap zooms in; ctrl+wheel / trackpad pinch zooms */
    const pts=new Map();let drag=null,pinch=null,lastTap=0;
    const local=e=>{const r=cv.getBoundingClientRect();return[e.clientX-r.left,e.clientY-r.top]};
    const startDrag=e=>{const [x,y]=local(e);drag={x,y,cx:cam.x,cy:cam.y,moved:false}};
    const startPinch=()=>{const [a,b]=[...pts.values()];pinch={d:Math.hypot(a[0]-b[0],a[1]-b[1])||1,z:cam.z,mx:(a[0]+b[0])/2,my:(a[1]+b[1])/2,cx:cam.x,cy:cam.y};drag=null};
    cv.addEventListener('pointerdown',e=>{
      try{cv.setPointerCapture(e.pointerId)}catch(_){}pts.set(e.pointerId,local(e));
      if(pts.size===2)startPinch();else if(pts.size===1)startDrag(e);
    });
    cv.addEventListener('pointermove',e=>{
      if(pts.has(e.pointerId))pts.set(e.pointerId,local(e));
      if(pinch&&pts.size>=2){
        const [a,b]=[...pts.values()];const d=Math.hypot(a[0]-b[0],a[1]-b[1]),mx=(a[0]+b[0])/2,my=(a[1]+b[1])/2;
        const sx=(pinch.mx-cw/2)/pinch.z+pinch.cx,sy=(pinch.my-ch/2)/pinch.z+pinch.cy;
        cam.z=Math.min(Math.max(pinch.z*d/pinch.d,ZMIN),ZMAX);
        cam.x=sx-(mx-cw/2)/cam.z;cam.y=sy-(my-ch/2)/cam.z;clampCam();return;
      }
      if(drag){const [x,y]=local(e),dx=x-drag.x,dy=y-drag.y;if(Math.abs(dx)+Math.abs(dy)>6){drag.moved=true;cv.classList.add('drag')}
        if(drag.moved){cam.x=drag.cx-dx/cam.z;cam.y=drag.cy-dy/cam.z;clampCam()}return}
      if(e.pointerType==='mouse'){const h=hit(e);const id=h?h.id:null;if(id!==hover){hover=id;last=0;cv.style.cursor=h?'pointer':''}}
    });
    const end=e=>{
      const wasPinch=!!pinch;pts.delete(e.pointerId);cv.classList.remove('drag');
      if(pinch&&pts.size<2){pinch=null;if(pts.size===1){const [x,y]=[...pts.values()][0];drag={x,y,cx:cam.x,cy:cam.y,moved:true}}return}
      if(drag&&!drag.moved&&!wasPinch&&e.type==='pointerup'){
        const now=Date.now(),[x,y]=local(e);
        if(e.pointerType!=='mouse'&&now-lastTap<300){zoomAt(1.6,x,y);lastTap=0}
        else{lastTap=now;const h=hit(e);selected=h?h.id:null;last=0;onSelect(h?h.id:null)}
      }
      if(!pts.size)drag=null;
    };
    cv.addEventListener('pointerup',end);cv.addEventListener('pointercancel',end);
    cv.addEventListener('wheel',e=>{if(!e.ctrlKey)return;e.preventDefault();const [x,y]=local(e);zoomAt(Math.exp(-e.deltaY*0.01),x,y)},{passive:false});
    cv.addEventListener('dblclick',e=>{const [x,y]=local(e);zoomAt(1.6,x,y)});
    addEventListener('resize',()=>{if(visible)resize()});
    resize();startView();requestAnimationFrame(loop);
    if(document.fonts&&document.fonts.load)Promise.all([document.fonts.load('10px Silkscreen'),document.fonts.load('12px VT323')]).then(()=>{last=0},()=>{});
  },
  set(d){data=d;last=0},
  select(id){selected=id;last=0},
  show(on){visible=on;if(on){resize()}},
  zoom(f){zoomAt(f,cw/2,ch/2)},
  fit,
};
})();
