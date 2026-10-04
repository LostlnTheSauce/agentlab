/* Agent Lab UI. Polls /api/state and renders four views. All server text goes through esc(). */
(function(){
"use strict";
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=(v,sign)=>{const n=Number(v||0);const s=(sign&&n>0?'+':'')+(n<0?'−':'')+'$'+Math.abs(n).toFixed(2);return s};
const pct=(v,d=1)=>v==null?'—':(v>0?'+':v<0?'−':'')+Math.abs(v*100).toFixed(d)+'%';
const units=v=>(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(1)+'u';
const cls=v=>v>0?'up':v<0?'down':'';
const when=iso=>{const d=new Date(iso);return d.toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'})};
const ago=iso=>{const s=(Date.now()-new Date(iso))/1000;return s<90?Math.round(s)+'s ago':s<5400?Math.round(s/60)+'m ago':s<172800?Math.round(s/3600)+'h ago':Math.round(s/86400)+'d ago'};

let seenPicks={};try{seenPicks=JSON.parse(localStorage.getItem('lab-seen-picks')||'{}')}catch(e){}
let quickBet=true;try{quickBet=localStorage.getItem('lab-quickbet')!=='off'}catch(e){}
let openPaper=false,S=null,mode='paper',view='office',filt='all',sortKey='units',fetchedAt=0,people={};
try{mode=localStorage.getItem('lab-mode')||'paper';view=localStorage.getItem('lab-view')||'office'}catch(e){}

async function api(path,body){
  const opt=body?{method:'POST',headers:{'Content-Type':'application/json','X-Lab':'1'},body:JSON.stringify(body)}:{};
  const r=await fetch(path,opt);
  if(r.status===401){location.href='login';throw new Error('signed out')}
  const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(j.error||('HTTP '+r.status));
  return j;
}
function toast(msg,err){const t=document.createElement('div');t.className='toast'+(err?' err':'');t.textContent=msg;document.body.appendChild(t);setTimeout(()=>t.remove(),2600)}

async function load(){
  try{S=await api('api/state');fetchedAt=Date.now();render()}
  catch(e){if(e.message!=='signed out'){$('live').className='live stale';$('liveTxt').textContent='offline, retrying'}}
}
setInterval(load,30000);
setInterval(()=>{if(!S)return;const busy=S.runs.some(r=>r.status==='running');$('live').className='live'+(busy?' busy':'')+(Date.now()-fetchedAt>90000?' stale':'');
  $('liveTxt').textContent=busy?'desks are working…':'updated '+Math.round((Date.now()-fetchedAt)/1000)+'s ago'},1000);

/* ---------------------------------------------------------------- header */
function header(){
  const P=S.paper,R=S.real,today=S.picks.filter(p=>p.local_day===S.day);
  const real=today.filter(p=>p.real_pick);
  if(mode==='paper'){
    $('kLabel').textContent=`FIRM BANKROLL · ${S.roster.tipsters.length} TIPSTERS · PAPER`;
    $('bigNum').textContent=money(P.seeded+P.profit);
    $('bigChg').textContent=pct(P.profit/P.seeded);$('bigChg').className='chg '+cls(P.profit);
    $('subLine').textContent=`on ${money(P.seeded)} seeded (${money(S.config.wallet)} per tipster) · ${money(P.open)} riding on open bets`;
    $('s1l').textContent='AVG CLV';$('s1').textContent=S.paper.clv_n?pct(S.paper.clv):'—';$('s1').className=cls(S.paper.clv);
    $('s2l').textContent='RECORD';$('s2').textContent=`${P.w}–${P.l}${P.p?'–'+P.p:''}`;
    $('s3l').textContent='TODAY';$('s3').textContent=`${today.length} picks · ${real.length} real`;
  }else{
    $('kLabel').textContent='REAL CASH · WHAT YOU ACTUALLY BET';
    $('bigNum').textContent=money(R.profit,true);$('bigChg').textContent=`${R.w}–${R.l}${R.p?'–'+R.p:''}`;$('bigChg').className='chg '+cls(R.profit);
    $('subLine').textContent=`on ${money(R.staked)} risked across ${R.w+R.l+R.p} settled bets · ${money(R.open)} open`;
    $('s1l').textContent='ROI';$('s1').textContent=pct(R.roi);$('s1').className=cls(R.roi);
    $('s2l').textContent='WALLETS LEFT';$('s2').textContent=money(R.balance);
    $('s3l').textContent='TODAY';$('s3').textContent=`${S.mine.today_placed} placed · ${real.length} recommended`;
  }
  const done=real.filter(p=>p.decision).length;
  $('barFill').style.width=(real.length?done/real.length*100:0)+'%';
  $('barLab').textContent=real.length?`${done} of ${real.length} real-money picks handled today`:'No real-money picks yet today';
  const b=[];
  if(!S.config.odds)b.push('No ODDS_API_KEY set: the desks cannot see prices.');
  if(!S.config.llm)b.push('No ANTHROPIC_API_KEY: tipsters are using rule-based fallback reasoning.');
  if(S.credits.remaining!=null&&S.credits.remaining<40)b.push(`Only ${S.credits.remaining} Odds API credits left this month.`);
  const err=S.runs.find(r=>r.status==='error');if(err&&err===S.runs[0])b.push('Last run failed: '+(err.detail.error||'unknown error'));
  $('banner').hidden=!b.length;$('banner').textContent=b.join(' ');
  $('modePaper').classList.toggle('on',mode==='paper');$('modeReal').classList.toggle('on',mode==='real');
  $('modePaper').setAttribute('aria-selected',mode==='paper');$('modeReal').setAttribute('aria-selected',mode==='real');
}

/* ---------------------------------------------------------------- office */
function office(){
  const today=S.picks.filter(p=>p.local_day===S.day),byAgent=Object.fromEntries(S.agents.map(a=>[a.id,a]));
  const list=S.roster.tipsters.map((t,i)=>{
    const a=byAgent[t.id],mine=today.filter(p=>p.agent===t.id&&p.board!=='vetoed');
    const fresh=mine.filter(p=>!seenPicks[t.id]||p.created_at>seenPicks[t.id]);
    let bubble;
    if(mode==='paper')bubble=a.paper.graded?{text:units(a.paper.units),tone:Math.sign(Math.round(a.paper.units*10))}
      :{text:mine.length?`${mine.length} PICK${mine.length>1?'S':''}`:(a.paper.open?`${a.paper.open} OPEN`:'0–0'),tone:0};
    else{const pr=a.real.profit;bubble=(a.real.w+a.real.l)?{text:money(pr,true),tone:Math.sign(Math.round(pr*100))}:{text:money(a.real_wallet),tone:0}}
    return {id:t.id,name:t.name,desk:t.desk,look:t.look,i,bubble,hasPick:fresh.length>0,realPick:fresh.some(p=>p.real_pick),working:mine.length===0&&t.id!=='coin',broke:a.status==='BROKE'};
  });
  people=Object.fromEntries(list.map(p=>[p.id,p]));
  const staff=Object.values(S.roster.staff).map((s,i)=>({...s,i:40+i,working:true}));
  const series=S.paper.equity.map(e=>e.v);
  const bd={cleared:0,flagged:0,vetoed:0};today.forEach(p=>bd[p.board]=(bd[p.board]||0)+1);
  const P=S.paper;
  const jail=(S.roster.jail||[]).map((t,i)=>{const a=byAgent[t.id];return {id:t.id,name:t.name,look:t.look,i:60+i,
    bubble:a&&a.paper.graded?{text:units(a.paper.units),tone:Math.sign(Math.round(a.paper.units*10))}:{text:'FIRED',tone:-1}}});
  const latest=(S.cooler||[])[0];let seen='';try{seen=localStorage.getItem('lab-cooler-seen')||''}catch(e){}
  Office.set({cooler:{fresh:!!latest&&latest.created_at>seen},desks:S.roster.desks,people:list,staff,jail,screen:{title:'FIRM BANKROLL · PAPER',big:money(P.seeded+P.profit),up:P.profit>=0,
    line1:`${pct(P.profit/P.seeded)}  CLV ${S.paper.clv_n?pct(S.paper.clv):'—'}`,line2:`${today.length} PICKS TODAY · ${today.filter(p=>p.real_pick).length} REAL`,series,board:bd}});
}
function openSheet(id){
  const sh=$('sheet');
  if(!id){sh.hidden=true;return}
  const jailed=(S.roster.jail||[]).find(x=>x.id===id);
  const t=S.roster.tipsters.find(x=>x.id===id)||jailed,st=S.roster.staff[id],who=t||st;
  if(!who){sh.hidden=true;return}
  sh.hidden=false;sprite($('sheetAv'),who.look);
  $('sheetNm').textContent=who.name.toUpperCase();
  const desk=t?(S.roster.desks.find(d=>d.key===t.desk)||{label:''}).label:(id==='guard'?'TIPSTER JAIL':'FRONT OFFICE');
  const replacedBy=jailed&&jailed.replaced_by?[...S.roster.tipsters,...(S.roster.jail||[])].find(x=>x.id===jailed.replaced_by):null;
  $('sheetRole').textContent=jailed?`FIRED ${new Date(jailed.fired_at).toLocaleDateString([], {month:'short',day:'numeric'})} · ${jailed.fired_note||''}${replacedBy?' · replaced by '+replacedBy.name:''}`
    :`${who.role} · ${desk}${who.original?' · original five':''}${who.replaces?' · new hire':''}`;
  $('sheetMeth').textContent=who.method;
  $('sheetQuote').textContent='"'+who.quote+'"';
  const nums=$('sheetNums'),picks=$('sheetPicks');
  if(t&&!jailed){const latest=S.picks.filter(x=>x.agent===id).map(x=>x.created_at).sort().pop();
    if(latest){seenPicks[id]=latest;try{localStorage.setItem('lab-seen-picks',JSON.stringify(seenPicks))}catch(e){}office()}}
  if(t){const a=S.agents.find(x=>x.id===id),p=a.paper;
    nums.hidden=false;
    nums.innerHTML=p.graded?`<span>${p.w}–${p.l}${p.p?'–'+p.p:''}</span><span class="${cls(p.units)}">${units(p.units)}</span><span class="${cls(p.clv)}">CLV ${pct(p.clv)}</span><span>real wallet ${money(a.real_wallet)}</span>`
      :`<span class="q">No graded bets yet</span><span>real wallet ${money(a.real_wallet)}</span>`;
    const mineAll=a.recent||[];
    const st=x=>x.result?`<span class="pill ${x.result}">${x.result.toUpperCase()}</span>`:'<span class="pill passed">OPEN</span>';
    const you=x=>x.you?` <span class="tag youbet">YOU BET ${money(x.you.stake)}${x.you.result?' · '+money(x.you.profit,true):''}</span>`:'';
    const line=x=>`<div class="pickline">${st(x)} <b>${esc(x.bet)} ${esc(x.price_txt)}</b>${you(x)}${x.real_pick?' <span class="pill real">REAL $</span>':''}
      <div class="g">${esc(x.game)} · ${esc(when(x.commence))}</div>${x.final?`<div class="final">${esc(x.final)}</div>`:x.needs?`<div class="needs">${esc(x.needs)}</div>`:''}</div>`;
    picks.innerHTML=mineAll.length?'<b class="ph">RECENT PICKS</b>'+mineAll.map(line).join(''):`<div class="pickline">${jailed?'Serving time. No desk, no new picks; any open bets still get graded.':'Nothing today that clears the price bar.'}</div>`;
  }else{nums.hidden=true;picks.innerHTML=''}
}

/* ---------------------------------------------------------------- water cooler */
function openChat(i){
  const all=S.cooler||[],c=all[i],box=$('chat');
  box.hidden=false;
  if(!c){box.innerHTML=`<button class="x" id="chatX" aria-label="Close">X</button><b class="ch">WATER COOLER</b><p class="q">Nobody's talked yet. The first chatter shows up after the morning slate.</p>`;$('chatX').onclick=()=>{box.hidden=true;Office.select(null)};return}
  try{if(all[0])localStorage.setItem('lab-cooler-seen',all[0].created_at)}catch(e){}
  const who=Object.fromEntries([...S.roster.tipsters,...(S.roster.jail||[]),...Object.values(S.roster.staff).map(s=>({...s}))].map(a=>[a.id,a]));
  const when={morning:'THIS MORNING',evening:'LAST NIGHT',meeting:'AFTER THE BOARD MEETING'};
  const label=x=>{const d=new Date(x.day+'T12:00');const today=x.day===S.day;return (today?'':d.toLocaleDateString([], {weekday:'short'}).toUpperCase()+' · ')+(x.kind==='morning'?'MORNING':x.kind==='evening'?'NIGHT':'MEETING')};
  box.innerHTML=`<button class="x" id="chatX" aria-label="Close">X</button><b class="ch">WATER COOLER · ${esc(c.day===S.day?(when[c.kind]||''):label(c))}</b>
    <div class="msgs">${c.lines.map((l,j)=>{const a=who[l.speaker]||{name:l.speaker,look:{shirt:'#888',hair:'#444',skin:'#ccc'}};
      return `<div class="msg"><canvas width="13" height="16" data-i="${j}"></canvas><div><b>${esc(a.name)}</b><p>${esc(l.text)}</p></div></div>`}).join('')}</div>
    ${all.length>1?`<div class="older">${all.map((x,j)=>`<button data-c="${j}" class="${j===i?'on':''}">${esc(label(x))}</button>`).join('')}</div>`:''}`;
  box.querySelectorAll('.msg canvas').forEach(cv=>{const l=c.lines[+cv.dataset.i];const a=who[l.speaker];if(a&&a.look)sprite(cv,a.look)});
  box.querySelectorAll('.older button').forEach(b=>b.onclick=()=>openChat(+b.dataset.c));
  $('chatX').onclick=()=>{box.hidden=true;Office.select(null)};
  office();
}

/* ---------------------------------------------------------------- slate */
function card(p){
  const t=[...S.roster.tipsters,...(S.roster.jail||[])].find(x=>x.id===p.agent)||{look:{shirt:'#888',hair:'#444',skin:'#ccc'},role:''};
  const el=document.createElement('article');
  el.className='card '+(p.board==='vetoed'?'vetoed ':'')+(p.real_pick?'real ':'')+(p.decision==='placed'?'placed':'');
  const chips=[];
  if(p.result)chips.push(`<span class="pill ${p.result}">${p.result.toUpperCase()}</span>`);
  if(p.decision)chips.push(`<span class="pill ${p.decision}">${p.decision.toUpperCase()}</span>`);
  if(p.real_pick)chips.push(`<span class="pill real">REAL $${p.ceo_rank?' #'+p.ceo_rank:''}</span>`);
  if(p.late)chips.push('<span class="pill late">LATE</span>');
  if(!p.decision&&!p.result)chips.push(`<span class="pill ${p.board}">${p.board.toUpperCase()}</span>`);
  if(p.paper_only&&!p.decision)chips.push('<span class="pill paper">PAPER ONLY</span>');
  const edgeTxt=p.edge==null?'—':pct(p.edge,2);
  const rb=p.real_bet;
  el.innerHTML=`<div class="chead"><canvas width="13" height="16"></canvas><div class="who"><b>${esc(p.agent_name.toUpperCase())}</b><span>${esc(t.role)} · ${esc(p.league)}</span></div><div class="chips">${chips.join('')}</div></div>
    <div><div class="mkt">${esc(p.bet)}</div><div class="game">${esc(p.game)} · ${esc(when(p.commence))}</div>${p.needs?`<div class="needs">${esc(p.needs)}</div>`:''}</div>
    <div class="odds"><div><b>${esc((p.book_name||'Bovada').toUpperCase())}</b><span>${esc(p.price_txt)}</span></div><div><b>FAIR</b><span>${esc(p.fair_txt)}${p.estimated?'*':''}</span></div><div><b>VS FAIR</b><span class="${cls(p.edge)}">${edgeTxt}</span></div><div><b>FLOOR</b><span>${esc(p.min_txt)}</span></div></div>
    ${(p.prices||[]).length>1?`<p class="also">Also: ${p.prices.filter(x=>x.book!==p.book).map(x=>`${esc(x.name)} ${esc(x.txt)}`).join(' · ')}</p>`:''}
    <p class="why">"${esc(p.reasoning)}"</p>
    ${p.group.length?`<p class="signal">Also picked by ${esc(p.group.join(', '))}.</p>`:''}
    ${p.notes.length?`<div class="board ${p.board}">${p.notes.map(n=>`<span>${esc(n)}</span>`).join('')}</div>`:''}
    ${p.result&&p.clv!=null?`<p class="signal">Closing line value: <span class="${cls(p.clv)}">${pct(p.clv)}</span> ${p.clv>0?'(beat the close)':'(the market moved against it)'}</p>`:''}
    ${rb?betLine(p,rb):''}
    ${p.check&&!p.result?`<p class="check ${esc(p.check.status)}"><b>PRICE CHECK ${esc(ago(p.check.checked_at)).toUpperCase()}:</b> ${esc(p.check.text)}</p>`:''}
    ${p.research?`<details class="research"><summary>RESEARCH NOTES</summary><p>${esc(p.research)}</p><p>${esc(p.signal)}</p></details>`:''}
    <div class="acts"></div>`;
  sprite(el.querySelector('canvas'),t.look);
  const acts=el.querySelector('.acts');
  const btn=(label,c,fn)=>{const b=document.createElement('button');b.textContent=label;if(c)b.className=c;b.onclick=fn;acts.appendChild(b)};
  if(!p.result){
    if(!p.decision){
      btn(p.board==='vetoed'?'OVERRIDE & BET':`BET ${money(p.stake_dollars)}`,p.board==='vetoed'?'':'pri',()=>openPlace(p));
      btn('PASS','',()=>act(p,'pass'));
    }else btn('UNDO','',()=>act(p,'undo'));
    if(p.market!=='parlay'&&!p.market.startsWith('player')&&new Date(p.commence)>new Date())btn('CHECK PRICE','',e=>checkPrice(p,e.target));
  }
  return el;
}
/* "You bet $2.00 at +130 to win $2.60 (pays $4.60)", or the settled result */
const toWin=(stake,price)=>stake*(dec(price)-1);
function betLine(p,rb){
  const stake=rb.total??rb.stake,win=toWin(stake,rb.price),pr=`${rb.price>0?'+':''}${rb.price}`;
  const share=stake>rb.stake?` <span class="q">(${esc(p.agent_name)}'s share ${money(rb.stake)})</span>`:'';
  const res=p.result||rb.result;
  let tail;
  if(res==='win')tail=`<b class="up">Won ${money(win)}</b> (paid ${money(stake+win)})`;
  else if(res==='loss')tail=`<b class="down">Lost ${money(stake)}</b>`;
  else if(res==='push'||res==='void')tail=`<b>Push</b>, stake returned`;
  else tail=`to win <b class="up">${money(win)}</b> (pays ${money(stake+win)})`;
  const bk=rb.book&&(S.config.books||[]).length>1?` on ${esc(rb.book)}`:'';
  return `<p class="betline">You bet <b>${money(stake)}</b> at ${pr}${bk} · ${tail}${share}</p>`;
}
function slate(){
  const today=S.picks.filter(p=>p.local_day===S.day),open=S.picks.filter(p=>p.local_day!==S.day&&!p.result);
  const n=f=>today.filter(f).length;
  const F=[['all','ALL',()=>true],['real','REAL MONEY',p=>p.real_pick],['cleared','CLEARED',p=>p.board==='cleared'],['flagged','FLAGGED',p=>p.board==='flagged'],['vetoed','VETOED',p=>p.board==='vetoed'],['mine','MY BETS',p=>p.decision==='placed']];
  $('filters').innerHTML=F.map(([k,l,f])=>`<button data-f="${k}" class="${filt===k?'on':''}">${l} ${n(f)}</button>`).join('');
  const f=(F.find(x=>x[0]===filt)||F[0])[2];
  const order=p=>(p.real_pick?0:10)+(p.ceo_rank||0)+({cleared:20,flagged:30,vetoed:40}[p.board]||50);
  const list=today.filter(f).sort((a,b)=>order(a)-order(b)||(b.edge||0)-(a.edge||0));
  const cards=$('cards');cards.innerHTML='';
  if(!list.length)cards.innerHTML=`<div class="empty">${today.length?'Nothing in this filter.':`No picks yet today. The slate runs at ${S.config.slate_hour}:00 (${esc(S.config.tz)}), or run it now from the Ledger tab.`}</div>`;
  list.forEach(p=>cards.appendChild(card(p)));
  const oc=$('openCards');oc.innerHTML='';
  const mine=open.filter(p=>p.decision==='placed'||p.real_pick),paper=open.filter(p=>!(p.decision==='placed'||p.real_pick)&&p.board!=='vetoed');
  mine.sort((a,b)=>a.commence.localeCompare(b.commence)).forEach(p=>oc.appendChild(card(p)));
  if(paper.length){
    const d=document.createElement('details');d.className='paperopen';d.open=openPaper;
    d.innerHTML=`<summary>${paper.length} MORE ON PAPER, WAITING ON GAMES</summary><div class="cards"></div>`;
    d.addEventListener('toggle',()=>{openPaper=d.open});
    paper.sort((a,b)=>a.commence.localeCompare(b.commence)).forEach(p=>d.querySelector('.cards').appendChild(card(p)));
    oc.appendChild(d);
  }
  $('openHead').hidden=!oc.children.length;
  $('memo').textContent=S.memo?S.memo.text:'No memo yet today. The morning slate runs automatically.';
  const r=S.runs.find(x=>x.kind.startsWith('slate')&&x.status!=='running');
  $('slateWhen').textContent=r?`${new Date(S.day+'T12:00').toLocaleDateString([], {weekday:'short',month:'short',day:'numeric'})} · built ${ago(r.started_at)}`:'';
  const pending=today.filter(p=>p.real_pick&&!p.decision).length;
  $('navBadge').hidden=!pending;$('navBadge').textContent=pending;
}
async function checkPrice(p,b){
  b.disabled=true;b.textContent='CHECKING…';
  try{const r=await api('api/pick/'+encodeURIComponent(p.id)+'/check',{});toast(r.status==='good'?'Price still good':r.status==='worse'?'Price got worse':r.status==='gone'?'Bet is off the board':'Line moved');await load()}
  catch(e){toast(e.message,true);b.disabled=false;b.textContent='CHECK PRICE'}
}
async function act(p,action,extra){
  try{await api('api/pick/'+encodeURIComponent(p.id),{action,...extra});await load();
    toast({pass:'Passed',undo:'Undone',placed:'Recorded. It grades itself after the game.',grade:'Graded'}[action]||'Done')}
  catch(e){toast(e.message,true)}
}

/* place dialog */
let placing=null,placeBook='bovada';
function renderBooks(p){
  const books=S.config.books||[],box=$('placeBooks');
  box.hidden=books.length<2;if(books.length<2)return;
  const quoted=Object.fromEntries((p.prices||[]).map(x=>[x.book,x.txt]));
  box.innerHTML='<b>WHERE YOU BET</b>'+books.map(b=>`<button type="button" data-book="${esc(b.key)}" class="${b.key===placeBook?'on':''}">${esc(b.name)}${quoted[b.key]?' '+esc(quoted[b.key]):''}</button>`).join('');
}
$('placeBooks').addEventListener('click',e=>{const b=e.target.closest('button[data-book]');if(!b)return;placeBook=b.dataset.book;
  const q=(placing.prices||[]).find(x=>x.book===placeBook);if(q)$('placePrice').value=q.txt.replace('−','-');renderBooks(placing);setLink(placing.links||{});checkPlace()});
const parsePrice=s=>{const m=String(s).trim().replace('−','-').match(/^([+-]?)(\d{3,5})$/);if(!m)return null;const v=parseInt(m[2],10)*(m[1]==='-'?-1:1);return Math.abs(v)>=100?v:null};
const dec=a=>a>0?1+a/100:1+100/-a;
function openPlace(p){
  placing=p;
  $('placeWhat').textContent=`${p.bet} ${p.price_txt}`;$('placeNeeds').textContent=p.needs||'';
  placeBook=p.book||'bovada';renderBooks(p);
  $('placeSteps').innerHTML=[`Open <b>${esc(p.book_name||'Bovada')}</b> → ${esc(p.league)} → <b>${esc(p.game)}</b>.`,`Find <b>${esc(p.bet)}</b>. It was ${esc(p.price_txt)} when picked.`,
    p.min_price!=null?`Only bet if the price is <b>${esc(p.min_txt)}</b> or better. Worse than that, the edge is gone: cancel and pass.`:'No floor for this one; use your judgment.',
    `Bet ${money(p.stake_dollars)}, then enter the price you actually got.`].map(s=>`<li>${s}</li>`).join('');
  $('placePrice').value=p.price_txt.replace('−','-');$('placeStake').value=p.stake_dollars.toFixed(2);
  setLink(p.links||{});
  const qc=$('placeCheck');qc.hidden=!quickBet||p.market==='parlay';
  checkPlace();$('placeDlg').showModal();
  if(quickBet&&p.market!=='parlay'){qc.className='qcheck wait';qc.textContent='Checking live prices at your books…';quickCheck(p)}
}
function checkPlace(){
  const pr=parsePrice($('placePrice').value),st=parseFloat($('placeStake').value);
  let w='';
  if(pr==null)w='Enter an American price like -110 or +145.';
  else if(!(st>0))w='Enter a stake in dollars.';
  else if(placing.min_price!=null&&dec(pr)<dec(placing.min_price)-1e-9)w=`That's worse than the ${placing.min_txt} floor. You can still record it, but the edge is probably gone.`;
  $('placeWarn').textContent=w;
  $('placeWin').textContent=pr!=null&&st>0?`To win ${money(toWin(st,pr))} · pays ${money(st+toWin(st,pr))} total`:'';
  return {pr,st};
}
function setLink(links){
  const a=$('placeLink'),url=quickBet?links[placeBook]:null;
  a.hidden=!url;if(url){a.href=url;a.textContent='OPEN '+(bookNames[placeBook]||placeBook).toUpperCase()+' ↗'}
}
const bookNames={bovada:'Bovada',lowvig:'LowVig',betonlineag:'BetOnline',mybookieag:'MyBookie'};
async function quickCheck(p){
  const qc=$('placeCheck');
  try{
    const r=await api('api/pick/'+encodeURIComponent(p.id)+'/check',{});
    if(placing!==p)return;
    qc.className='qcheck '+r.status;qc.textContent=r.text;
    if(r.status==='good'||(r.status==='worse'&&r.same_line)){
      placeBook=r.book||placeBook;
      const pr=(r.prices||{})[placeBook]??r.price;
      $('placePrice').value=(pr>0?'+':'')+pr;
      p.prices=Object.entries(r.prices||{}).map(([k,v])=>({book:k,name:bookNames[k]||k,price:v,txt:(v>0?'+':'')+v}));
      p.links={...(p.links||{}),...(r.links||{})};
      renderBooks(p);checkPlace();
    }
    setLink(p.links||{});
  }catch(e){qc.className='qcheck moved';qc.textContent='Couldn\'t check prices ('+e.message+'). Check the price on the site before betting.'}
}
$('placePrice').addEventListener('input',checkPlace);$('placeStake').addEventListener('input',checkPlace);
$('placeCopy').onclick=()=>{const s=`${placing.bet} ${placing.price_txt} · ${placing.game} · ${money(placing.stake_dollars)}`;
  try{navigator.clipboard.writeText(s).then(()=>toast('Copied'),()=>toast(s))}catch(e){toast(s)}};
$('placeForm').addEventListener('submit',e=>{
  if(e.submitter&&e.submitter.value!=='ok')return;
  const {pr,st}=checkPlace();
  if(pr==null||!(st>0)){e.preventDefault();return}
  act(placing,'placed',{price:pr,stake:st,book:placeBook});
});

/* ---------------------------------------------------------------- tipsters */
function tipsters(){
  const byAgent=Object.fromEntries(S.agents.map(a=>[a.id,a]));
  const key={units:a=>a.paper.units,clv:a=>a.paper.clv??-9,roi:a=>a.paper.roi??-9,rec:a=>a.paper.w-a.paper.l,real:a=>a.real_wallet}[sortKey];
  const color={SHARP:'#c7f78c',HOT:'#f0c281',STEADY:'#5ed3b4',ROOKIE:'#94aba1',BENCHMARK:'#94aba1','ON NOTICE':'#ff8e80',BROKE:'#ff8e80'};
  const tb=$('tbody');tb.innerHTML='';
  [...S.roster.tipsters].sort((x,y)=>key(byAgent[y.id])-key(byAgent[x.id])).forEach(t=>{
    const a=byAgent[t.id],p=a.paper,n=p.graded,tr=document.createElement('tr');
    tr.className='trow'+(openRows.has(t.id)?' open':'');tr.title='Tap for stats by sport and bet type';
    tr.onclick=()=>{openRows.has(t.id)?openRows.delete(t.id):openRows.add(t.id);tipsters()};
    const dots=Array.from({length:10},(_,i)=>{const r=p.last10[p.last10.length-10+i];return `<i class="${r||''}"></i>`}).join('');
    tr.innerHTML=`<td><div class="agentcell"><canvas width="13" height="16"></canvas><div><b>${esc(t.name)}</b><div class="q" style="font-size:11px">${esc(t.role)}</div></div></div></td>
      <td class="q">${esc(S.roster.desks.find(d=>d.key===t.desk).label)}</td>
      <td class="n">${n?`${p.w}–${p.l}${p.p?'–'+p.p:''}`:'—'}</td>
      <td class="n ${cls(p.units)}">${n?units(p.units):'—'}</td>
      <td class="n ${cls(p.roi)}">${n?pct(p.roi):'—'}</td>
      <td class="n ${cls(p.clv)}">${pct(p.clv)}</td>
      <td><div class="form">${dots}</div></td>
      <td class="n ${a.real_wallet<S.config.wallet?'down':a.real_wallet>S.config.wallet?'up':''}">${money(a.real_wallet)}</td>
      <td><span class="status" style="color:${color[a.status]};border-color:${color[a.status]}">${a.status}</span></td>`;
    sprite(tr.querySelector('canvas'),t.look);tb.appendChild(tr);
    if(openRows.has(t.id)){const sr=document.createElement('tr');sr.className='splitrow';sr.innerHTML=`<td colspan="9">${splitsTable(a)}</td>`;tb.appendChild(sr)}
  });
  meetingBox();jailBox(byAgent);
  $('unitNote').textContent=`paper record · 1 unit = ${money(S.config.unit)} · real wallets start at ${money(S.config.wallet)}`;
  const notice=S.agents.filter(a=>a.status==='ON NOTICE').map(a=>S.roster.tipsters.find(t=>t.id===a.id).name);
  const broke=S.agents.filter(a=>a.status==='BROKE').map(a=>S.roster.tipsters.find(t=>t.id===a.id).name);
  $('tipNotice').innerHTML=[notice.length?`<b class="down">On notice:</b> ${esc(notice.join(', '))}. Paper only until their results and CLV recover.`:'',
    broke.length?`<b class="down">Broke:</b> ${esc(broke.join(', '))}. Their real wallet is empty; they keep betting on paper.`:'',
    'CLV (closing line value) says whether a tipster got a better price than where the line closed. Over a few weeks it separates skill from luck much faster than win–loss. Coin Flip picks at random: anyone below him isn\'t adding anything.'].filter(Boolean).join('<br><br>');
}

const openRows=new Set();
function splitsTable(a){
  if(!a.splits||!a.splits.length)return '<div class="q">No graded bets yet.</div>';
  return `<table class="splits"><tr><th>SPORT</th><th>BET TYPE</th><th>RECORD</th><th>UNITS</th><th>CLV</th></tr>${a.splits.map(s=>`<tr><td>${esc(s.sport)}</td><td>${esc(s.market)}</td>
    <td class="n">${s.w}–${s.l}${s.p?'–'+s.p:''}</td><td class="n ${cls(s.units)}">${units(s.units)}</td><td class="n ${cls(s.clv)}">${pct(s.clv)}</td></tr>`).join('')}</table>`;
}
const hr12=h=>h===0?'12 AM':h<12?h+' AM':h===12?'12 PM':(h-12)+' PM';
function meetingBox(){
  const box=$('meetingBox'),ms=S.meetings||[];
  if(!ms.length){box.innerHTML=`<div class="meeting"><b class="mh">SUNDAY BOARD MEETING</b><p class="q">The first meeting is Sunday at ${hr12(S.config.meeting_hour)}. The Commish reviews the week, names an MVP, and may fire one tipster who is losing money and losing to the closing line. Fired tipsters go to jail and a new hire takes the seat.</p></div>`;return}
  const chips=m=>[m.detail.mvp_name?`<span class="pill win">MVP ${esc(m.detail.mvp_name)}</span>`:'',m.detail.fired_name?`<span class="pill loss">FIRED ${esc(m.detail.fired_name)}</span>`:'',
    m.detail.hired_name?`<span class="pill real">HIRED ${esc(m.detail.hired_name)} · ${esc(m.detail.hired_role)}</span>`:''].join('');
  const wk=w=>new Date(w+'T12:00').toLocaleDateString([], {month:'short',day:'numeric'});
  const [m,...old]=ms;
  box.innerHTML=`<div class="meeting"><b class="mh">BOARD MEETING · WEEK ENDING ${esc(wk(m.week)).toUpperCase()}</b><div class="chips" style="justify-content:flex-start">${chips(m)}</div><p>${esc(m.report)}</p>
    ${old.length?`<details><summary>EARLIER MINUTES</summary>${old.map(o=>`<div class="oldmin"><b>Week ending ${esc(wk(o.week))}</b><div class="chips" style="justify-content:flex-start">${chips(o)}</div><p>${esc(o.report)}</p></div>`).join('')}</details>`:''}</div>`;
}
function jailBox(byAgent){
  const j=S.roster.jail||[],box=$('jailBox');
  box.hidden=!j.length;
  box.innerHTML=`<div class="h2">TIPSTER JAIL · ${j.length}</div><div class="jailgrid">${j.map(t=>{const a=byAgent[t.id],p=a?a.paper:null;
    const rep=[...S.roster.tipsters,...j].find(x=>x.id===t.replaced_by);
    return `<div class="jailcard"><canvas width="13" height="16" data-id="${esc(t.id)}"></canvas><div><b>${esc(t.name)}</b> <span class="q">${esc(t.role)}</span>
      <div class="g">Fired ${esc(new Date(t.fired_at).toLocaleDateString([], {month:'short',day:'numeric'}))}: ${esc(t.fired_note||'')}${rep?` · replaced by ${esc(rep.name)}`:''}</div>
      ${p&&p.graded?`<div class="g">Final paper record ${p.w}–${p.l}${p.p?'–'+p.p:''}, <span class="${cls(p.units)}">${units(p.units)}</span>, CLV <span class="${cls(p.clv)}">${pct(p.clv)}</span>${p.open?` · ${p.open} bets still riding`:''}</div>`:''}</div></div>`}).join('')}</div>`;
  box.querySelectorAll('canvas').forEach(c=>{const t=j.find(x=>x.id===c.dataset.id);if(t)sprite(c,t.look)});
}

/* ---------------------------------------------------------------- my bets */
let betFilter='all';const openGames=new Set(),openBets=new Set();
/* what the market has done since you placed a bet */
function moveHtml(b){
  const m=b.move,pt=v=>(b.bet.includes('Over')||b.bet.includes('Under'))?String(v):(v>0?'+':'')+v;
  if(!m)return `<div class="mv q">Parlays aren't tracked for price moves; each leg's own game decides it.</div>`;
  const got=`You got <b>${esc(m.then_txt)}</b> at ${esc(b.book)}.`;
  if(m.kind==='none')return `<div class="mv">${got} <span class="q">No newer price check since you bet. The next scan updates this.</span></div>`;
  if(m.kind==='closed'){
    const v=m.value;return `<div class="mv">${got} It closed at <b>${esc(m.now_txt)}</b>${m.fair_txt?` (fair ${esc(m.fair_txt)})`:''}.
      ${v!=null?`<div class="${cls(v)}">${v>0?'You beat the closing line by '+pct(v)+'.':'The closing line beat you by '+pct(-v).replace('+','')+'.'}</div>`:''}</div>`}
  const as=`<span class="q">as of ${esc(ago(m.as_of))}</span>`;
  if(m.kind==='line')return `<div class="mv">${got} The line has moved to <b>${esc(pt(m.line_now))}</b>${m.now_txt?' '+esc(m.now_txt):''} ${as}.
    <div class="${m.direction==='for'?'up':'down'}">${m.direction==='for'?'Moved toward you: your number is now better than what\'s on offer.':'Moved against you: a better number is available now.'}</div></div>`;
  const dir={for:['up','Moved toward you: the price is worse now, so the market has come around to your side.'],
    against:['down','Moved against you: the same bet pays more now than when you took it.'],flat:['q','Basically unchanged.']}[m.direction];
  return `<div class="mv">${got} Now <b>${esc(m.now_txt||'—')}</b>${m.fair_txt?` (fair ${esc(m.fair_txt)})`:''} ${as}.
    <div class="${dir[0]}">${dir[1]}</div>${m.value!=null?`<div class="q">Your price vs today's fair price: <span class="${cls(m.value)}">${pct(m.value)}</span></div>`:''}</div>`;
}
/* COMING UP: open bets by day and kickoff time; a parlay appears under each of its legs' games */
function scheduleHtml(open){
  const rows=[];
  open.forEach(b=>(b.games||[]).forEach(g=>{if(g.leg&&g.leg_result)return;rows.push({t:new Date(g.commence),game:g.game,sport:g.sport,tv:g.tv,link:g.link,venue:g.venue,
    label:g.leg?`Parlay leg: ${g.leg}`:`${b.bet} ${b.price_txt}`,needs:g.leg?g.needs:b.needs,b,isLeg:!!g.leg,sub:g.leg?`${b.book} · ${money(b.stake)} parlay to win ${money(b.to_win)}`:`${b.book} · ${money(b.stake)} to win ${money(b.to_win)}`})}));
  if(!rows.length)return '';
  rows.sort((a,b)=>a.t-b.t);
  const now=new Date(),dayKey=d=>d.getFullYear()+'-'+d.getMonth()+'-'+d.getDate();
  const tomorrow=new Date(now.getTime()+864e5);
  const dayName=d=>dayKey(d)===dayKey(now)?'TODAY':dayKey(d)===dayKey(tomorrow)?'TOMORROW':d.toLocaleDateString([], {weekday:'long'}).toUpperCase();
  const days=[];rows.forEach(r=>{let d=days.find(x=>x.k===dayKey(r.t));if(!d)days.push(d={k:dayKey(r.t),date:r.t,games:[]});
    let g=d.games.find(x=>x.game===r.game&&+x.t===+r.t);if(!g)d.games.push(g={t:r.t,game:r.game,sport:r.sport,tv:r.tv,link:r.link,venue:r.venue,bets:[]});g.bets.push(r)});
  return `<div class="sched">${days.map(d=>`<div class="sday"><div class="sdate"><b>${dayName(d.date)}</b> ${esc(d.date.toLocaleDateString([], {month:'short',day:'numeric'}).toUpperCase())}</div>
    ${d.games.map(g=>{const key=g.game+'|'+(+g.t),isOpen=openGames.has(key);return `<div class="sgame${isOpen?' open':''}" data-game="${esc(key)}" role="button" tabindex="0" aria-expanded="${isOpen}"><div class="stime">${g.t<now?`<span class="livetag">${now-g.t<4*36e5?'LIVE':'ENDED'}</span>`:esc(g.t.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}))}</div>
      <div class="sbody"><div class="sg"><b>${esc(g.game)}</b> <span class="q">${esc(g.sport)}</span></div>
        ${g.bets.map(x=>{const arrow=!x.isLeg&&x.b.move&&x.b.move.direction==='for'?' <span class="up">▲</span>':!x.isLeg&&x.b.move&&x.b.move.direction==='against'?' <span class="down">▼</span>':'';
          return `<div class="sbet">${esc(x.label)}${arrow} <span class="q">· ${esc(x.sub)}</span>${x.needs?`<div class="needs">${esc(x.needs)}</div>`:''}
          ${isOpen?`<div class="g q">Picked by ${esc(x.b.agents.join(', '))}${x.b.recommended?' · CEO real-money pick':' · your call'}</div>${x.isLeg?'':moveHtml(x.b)}`:''}</div>`}).join('')}
        ${isOpen?`<div class="watch"><div><b>WATCH</b> ${g.tv?esc(g.tv):'<span class="q">TV channel not announced yet</span>'}${g.venue?` <span class="q">· ${esc(g.venue)}</span>`:''}</div>
          ${g.link?`<a class="golink" href="${esc(g.link)}" target="_blank" rel="noopener noreferrer">LIVE SCORE ON ESPN ↗</a>`:''}</div>`
          :`<div class="tvhint q">${g.tv?esc(g.tv)+' · ':''}tap for how to watch and odds moves</div>`}</div></div>`}).join('')}</div>`).join('')}</div>`;
}
function myBetsView(){
  const M=S.mine,A=M.all,box=$('myBets');
  const open=M.bets.filter(b=>b.status==='open');
  const sched=scheduleHtml(open);
  box.innerHTML=`<div class="riding">
      <div><b>OPEN</b><span>${A.open}</span></div>
      <div><b>RIDING</b><span>${money(A.open_stake)}</span></div>
      <div><b>TO WIN</b><span class="up">${money(A.open_to_win||0)}</span></div>
      <button class="tohist" data-goto="history"><b>ALL-TIME</b><span class="${cls(A.profit)}">${A.settled?money(A.profit,true):'—'}</span><i>HISTORY ›</i></button>
    </div>
    ${sched?`<div class="h2">COMING UP <span class="q" style="font:11px var(--f-body);letter-spacing:0">· tap a game for how to watch and odds moves</span></div>${sched}`
      :`<div class="empty">${M.bets.length?'Nothing riding right now. Finished bets are in HISTORY.':'No real bets yet. Tap BET on a pick in the Slate tab, place it on your sportsbook, then tap I PLACED IT, and it shows up here.'}</div>`}`;
  box.querySelector('[data-goto]').onclick=()=>show('history');
  box.querySelectorAll('.sgame').forEach(x=>{const tog=e=>{if(e.target.closest('a'))return;const k=x.dataset.game;openGames.has(k)?openGames.delete(k):openGames.add(k);myBetsView()};
    x.onclick=tog;x.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tog(e)}}});
}

/* ---------------------------------------------------------------- history */
function historyView(){
  const M=S.mine,A=M.all,box=$('history');
  const done=M.bets.filter(b=>b.status!=='open');
  if(!done.length){box.innerHTML='<div class="empty">No finished bets yet. Once a game ends, the bet lands here with the final score.</div>';return}
  const rec=(t,label,c)=>`<div class="${c}"><b>${label}:</b> ${t.settled?`${t.w}–${t.l}${t.p?'–'+t.p:''} · <span class="${cls(t.profit)}">${money(t.profit,true)}</span>`:'nothing settled yet'}</div>`;
  const rate=A.w+A.l?Math.round(A.w/(A.w+A.l)*100)+'%':'—';
  const F=[['all','ALL',done.length],['won','WON',A.w],['lost','LOST',A.l]];
  if(A.p)F.push(['push','PUSH',A.p]);
  if(betFilter==='open'||!F.some(f=>f[0]===betFilter))betFilter='all';
  const list=done.filter(b=>betFilter==='all'||b.status===betFilter).sort((x,y)=>new Date(y.commence)-new Date(x.commence));
  const dayKey=b=>new Date(b.commence).toLocaleDateString([], {weekday:'short',month:'short',day:'numeric'}).toUpperCase();
  const days=[];list.forEach(b=>{const k=dayKey(b);let d=days.find(x=>x.k===k);if(!d)days.push(d={k,bets:[],net:0});d.bets.push(b);d.net+=b.profit||0});
  const row=b=>{
    const bkey=b.id+'|'+b.placed_at,isOpen=openBets.has(bkey);
    const amt=b.status==='push'?`$0.00<small>PUSH</small>`:`<span class="${cls(b.profit)}">${money(b.profit,true)}</span><small>${b.status==='won'?'WON':'LOST'}</small>`;
    const finals=(b.games||[]).filter(g=>g.final);
    const legs=(b.games||[]).filter(g=>g.leg);
    const short=legs.length?`${legs.filter(g=>g.leg_result==='win').length} of ${legs.length} legs won`:finals[0]?finals[0].final:'';
    const clv=b.clv!=null?` · CLV <span class="${cls(b.clv)}">${pct(b.clv)}</span>`:'';
    return `<div class="brow ${b.status}${isOpen?' open2':''}" data-bet="${esc(bkey)}" role="button" tabindex="0" aria-expanded="${isOpen}"><span class="st ${b.status}">${b.status.toUpperCase()}</span>
      <div class="what"><b>${esc(b.bet)} ${esc(b.price_txt)}</b>
        ${short?`<div class="${legs.length?'g':'final'}">${esc(short)}</div>`:''}
        ${isOpen?`<div class="more">
          <div class="g">${esc(b.book)} · ${money(b.stake)}${legs.length?'':` · ${esc(b.game)} · ${esc(when(b.commence))}`}</div>
          ${legs.map(g=>`<div class="leg"><span class="pill ${g.leg_result||'passed'}">${(g.leg_result||'open').toUpperCase()}</span> ${esc(g.leg)}${g.final?`<div class="final">${esc(g.final)}</div>`:''}</div>`).join('')}
          <div class="g">Picked by ${esc(b.agents.join(', '))}${b.recommended?' · CEO real-money pick':' · your call'}${clv}</div>
          ${b.move?moveHtml(b):''}</div>`:''}</div>
      <div class="amt">${amt}</div></div>`};
  box.innerHTML=`<div class="score">
      <div class="top"><span class="pl ${cls(A.profit)}">${money(A.profit,true)}</span><span class="sub">on ${money(A.staked)} settled${A.roi!=null?' · '+pct(A.roi)+' return':''}</span></div>
      <div class="tiles">
        <div class="tile"><b>WON</b><span class="up">${A.w}</span></div>
        <div class="tile"><b>LOST</b><span class="down">${A.l}</span></div>
        <div class="tile"><b>WIN RATE</b><span>${rate}</span></div>
      </div>
      <div class="split">${rec(M.ceo,'CEO picks','ceo')}${rec(M.mine,'Your calls','own')}</div>
    </div>
    <div class="filters">${F.map(([k,l,n])=>`<button data-bf="${k}" class="${betFilter===k?'on':''}">${l} ${n}</button>`).join('')}</div>
    ${days.map(d=>`<div class="hday"><span>${esc(d.k)}</span><span class="${cls(d.net)}">${money(d.net,true)}</span></div>
      <div class="betlist">${d.bets.map(row).join('')}</div>`).join('')||'<div class="empty">Nothing here.</div>'}
    <p class="q" style="font-size:11px;margin:10px 0 0">Tap a bet for the book, tipsters, each parlay leg, and how your price compared to the closing line.</p>`;
  box.querySelectorAll('.brow').forEach(x=>{const tog=()=>{const k=x.dataset.bet;openBets.has(k)?openBets.delete(k):openBets.add(k);historyView()};
    x.onclick=tog;x.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tog()}}});
  box.querySelectorAll('[data-bf]').forEach(x=>x.onclick=()=>{betFilter=x.dataset.bf;historyView()});
}

/* ---------------------------------------------------------------- ledger */
function ledgerView(){
  const svg=$('eq'),P=S.paper.equity,R=S.real.equity,days=[...new Set([...P,...R].map(e=>e.d))].sort();
  let g='';
  if(days.length<2){g=`<text x="160" y="80" text-anchor="middle" fill="#94aba1" font-family="VT323" font-size="16">Chart appears after two days of results</text>`}
  else{
    const vals=[0,...P.map(e=>e.v),...R.map(e=>e.v)],mn=Math.min(...vals),mx=Math.max(...vals),span=(mx-mn)||1;
    const x=d=>40+days.indexOf(d)*(272/(days.length-1)),y=v=>140-(v-mn)/span*124;
    [mn,0,mx].forEach(v=>{g+=`<line x1="40" x2="312" y1="${y(v)}" y2="${y(v)}" stroke="${v===0?'#94aba1':'#1b3634'}" ${v===0?'stroke-dasharray="3 3"':''}/><text x="36" y="${y(v)+4}" text-anchor="end" fill="#94aba1" font-family="VT323" font-size="13">${money(v,true)}</text>`});
    const line=(s,c)=>{if(!s.length)return;const pts=s.map(e=>x(e.d)+','+y(e.v)).join(' ');const e=s[s.length-1];
      g+=`<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2"/><rect x="${x(e.d)-3}" y="${y(e.v)-3}" width="6" height="6" fill="${c}"/>`};
    line(P,'#c7f78c');line(R,'#f0c281');
    g+=`<text x="40" y="156" fill="#94aba1" font-family="VT323" font-size="13">${days[0].slice(5)}</text><text x="312" y="156" text-anchor="end" fill="#94aba1" font-family="VT323" font-size="13">${days[days.length-1].slice(5)}</text>`;
  }
  svg.innerHTML=g;
  myBetsView();historyView();
  const needs=S.picks.filter(p=>S.needs_grading.includes(p.id));
  if(needs.length)$('bts').open=true;
  $('needs').innerHTML=needs.length?needs.map(p=>`<div class="li"><div><b>${esc(p.bet)}</b><div class="g">${esc(p.game)} · ${esc(p.agent_name)}</div></div>
    <div class="btnrow" style="margin:0">${['win','loss','push'].map(r=>`<button data-grade="${r}" data-id="${esc(p.id)}">${r.toUpperCase()}</button>`).join('')}</div></div>`).join('')
    :'<div class="q">Everything finished has been graded automatically.</div>';
  const c=S.credits,used=c.used||0,rem=c.remaining;
  $('credits').innerHTML=`<div class="big2">${c.today} <span class="q" style="font-size:18px">of ${c.cap} credits today</span></div>
    <div class="meter"><i style="width:${Math.min(100,c.today/c.cap*100)}%"></i></div>
    <p class="q" style="margin:0;font-size:12px">${rem!=null?`${rem} credits left this month (${used} used).`:'No odds requests yet.'} A full scan costs about 3 credits per sport. Grading and research use free sources.</p>`;
  const cl=S.claude;
  $('claude').innerHTML=S.config.llm?`<div class="big2">${money(cl.today)} <span class="q" style="font-size:18px">today · ${money(cl.week)} this week</span></div>
    <p class="q" style="margin:0;font-size:12px">About ${money(cl.per_day)} a day on average, so $10 lasts ${cl.per_day>0?'about '+Math.round(10/cl.per_day)+' days':'a while'}. Tipsters: ${esc(cl.tipster_model)}. CEO: ${esc(cl.ceo_model)}.</p>`
    :'<p class="q" style="margin:0;font-size:12px">Claude is off. Add ANTHROPIC_API_KEY to .env to give the tipsters real judgment; until then they use rule-based reasoning.</p>';
  $('runs').innerHTML=S.runs.length?S.runs.map(r=>{const d=r.detail||{};const bits=[d.picks!=null?`${d.picks} picks`:'',d.real_recommended?`${d.real_recommended} real`:'',d.llm&&d.llm.calls?`${d.llm.calls} Claude calls`:'',...(d.notes||[]).slice(0,2),d.error||''].filter(Boolean);
    return `<div class="li"><div><b>${esc(r.kind.toUpperCase())}</b><div class="g">${esc(bits.join(' · ')||'—')}</div></div><div class="r ${r.status==='error'?'down':r.status==='ok'?'up':'q'}" style="font-size:16px">${esc(r.status)}<div class="g">${ago(r.started_at)}</div></div></div>`}).join('')
    :'<div class="q">No runs yet.</div>';
  const cf=S.config;
  $('rules').innerHTML=`<div class="btnrow" style="margin:0 0 8px"><button class="toggle ${quickBet?'on':''}" id="qbToggle">QUICK BET: ${quickBet?'ON':'OFF'}</button></div>
    ${cf.alerts?'<div class="btnrow" style="margin:0 0 8px"><button id="alertTest">SEND TEST ALERT</button></div>':''}
    <p class="q" style="margin:0 0 8px;font-size:11px">${quickBet?'BET checks live prices at your books (1 credit) and links straight to the game.':'BET opens the plain manual dialog.'}</p>
    <p style="margin:0 0 6px">1 unit = ${money(cf.unit)}. Each tipster has a ${money(cf.wallet)} real wallet. The CEO recommends at most ${cf.max_real} real bets a day. Nobody bets without a Bovada price that beats the sharp consensus.</p>
    <p class="q" style="margin:0">Slate at ${cf.slate_hour}:00, rescans at ${cf.rescan_hours.map(h=>h+':00').join(' and ')} (${esc(cf.tz)}). Sports: ${esc(cf.sports.join(', '))}. Claude: ${cf.llm?'on':'off (fallback)'}. Phone alerts: ${cf.alerts?'on':'off'}.</p>`;
}
$('rules').addEventListener('click',async e=>{if(e.target.id==='alertTest'){try{await api('api/alert-test',{});toast('Test alert sent. Check your phone.')}catch(err){toast(err.message,true)}return}
  if(e.target.id!=='qbToggle')return;quickBet=!quickBet;try{localStorage.setItem('lab-quickbet',quickBet?'on':'off')}catch(_){}ledgerView();toast(quickBet?'Quick bet on':'Back to manual betting')});
$('needs').addEventListener('click',e=>{const b=e.target.closest('button[data-grade]');if(b)act({id:b.dataset.id},'grade',{result:b.dataset.grade})});
async function run(kind){try{await api('api/run',{kind});toast(kind==='grade'?'Grading…':'The desks are on it. This takes a minute or two.');setTimeout(load,4000)}catch(e){toast(e.message,true)}}
$('runSlate').onclick=()=>run('slate');$('runRescan').onclick=()=>run('rescan');$('runGrade').onclick=()=>run('grade');

/* ---------------------------------------------------------------- shell */
function ticker(){
  const names=Object.fromEntries([...S.roster.tipsters,...(S.roster.jail||[]),...Object.values(S.roster.staff)].map(a=>[a.id,a.name.toUpperCase()]));
  const items=S.feed.slice(0,14).map(f=>(f.agent?names[f.agent]+': ':'')+f.text);
  $('ticker').textContent=(items.length?items.join(' · '):'Quiet on the floor.')+' · Paper bets, real odds. You place real money yourself. ·';
}
function render(){
  if(!S)return;
  header();office();slate();tipsters();ledgerView();ticker();
  if(!$('sheet').hidden){const n=$('sheetNm').textContent;const all=[...S.roster.tipsters,...Object.values(S.roster.staff)];const w=all.find(a=>a.name.toUpperCase()===n);if(w)openSheet(w.id)}
}
function show(v){
  view=v;['office','slate','tipsters','ledger','history'].forEach(k=>$('v-'+k).hidden=k!==v);
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  Office.show(v==='office');try{localStorage.setItem('lab-view',v)}catch(e){}
}
function setMode(m){mode=m;try{localStorage.setItem('lab-mode',m)}catch(e){}render()}
document.querySelector('nav').addEventListener('click',e=>{const b=e.target.closest('button');if(b)show(b.dataset.v)});
$('filters').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;filt=b.dataset.f;slate()});
document.querySelector('thead').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;sortKey=b.dataset.s;document.querySelectorAll('th button').forEach(x=>x.classList.toggle('on',x===b));tipsters()});
$('modePaper').onclick=()=>setMode('paper');$('modeReal').onclick=()=>setMode('real');
$('sheetX').onclick=()=>{Office.select(null);openSheet(null)};
$('zIn').onclick=()=>Office.zoom(1.25);$('zOut').onclick=()=>Office.zoom(0.8);$('zFit').onclick=()=>Office.fit();$('toSlate').onclick=()=>show('slate');

Office.init($('office'),id=>{if(id==='cooler'){openSheet(null);openChat(0)}else{$('chat').hidden=true;openSheet(id)}});
sprite($('ceoAv'),{shirt:'#f0c281',hair:'#dcdcdc',skin:'#d9a57c'});
show(['office','slate','tipsters','ledger','history'].includes(view)?view:'office');
load();
})();
