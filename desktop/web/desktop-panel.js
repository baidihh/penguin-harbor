const sheet=document.createElement('link');
sheet.rel='stylesheet'; sheet.href='desktop-panel.css'; document.head.append(sheet);
const progress=document.createElement('progress');
progress.id='seek';progress.setAttribute('aria-label','播放进度');
$('#seek').replaceWith(progress);
const post=(action,value)=>console.info('PENGUIN '+JSON.stringify({action,value}));
const titleOf=s=>[s?.name,s?.artist].filter(Boolean).join(' · ');
const clock=s=>[Math.floor(s/60),Math.floor(s%60)].map(n=>String(n).padStart(2,'0')).join(':');
let current={},tab='queue',lastList='',menuKey='';
const mainRows=[...menu,['chat','会话记录','history'],['more','更多操作','options']];
const options=[['mic','语音唤醒','wake'],['tune','放大字幕','captionSize',.1],['tune','缩小字幕','captionSize',-.1],...([.5,.75,1,1.5,2].map(n=>['more','精灵大小 '+Math.round(n*100)+'%','size',n])),['pause','停止播放','stop'],['music','去企鹅的港湾','harbor'],['close','收起企鹅','hidePet'],['close','退出','quit']];
function button(label,action,value,symbol){
 const b=document.createElement('button');b.type='button';b.title=label;b.setAttribute('aria-label',label);
 if(symbol)b.innerHTML=icon(symbol);else b.textContent=label;
 b.onclick=()=>post(action,value);return b;
}
function menuRows(rows){
 $('.menu').replaceChildren(...rows.map(([sym,label,action,value])=>{
  const b=button(label,action,value,sym);const span=document.createElement('span');span.textContent=label;b.append(span);return b;
 }));
}
function renderMenu(mode){
 const rows=mode==='menu'?mainRows:mode==='options'?options:mode==='voices'?Object.entries(current.voices||{}).map(([id,label])=>['tune',label,'selectVoice',id]):[];
 const key=JSON.stringify([mode,rows,current.voice,current.lyrics,current.wake]);
 if(key===menuKey)return;menuKey=key;
 menuRows(rows.map(([sym,label,act,val])=>[sym,label+((act==='selectVoice'&&val===current.voice||act==='lyrics'&&current.lyrics||act==='wake'&&current.wake)?'　✓':''),act,val]));
}
function musicBody(){
 const area=$('.content'),m=current.music||{};
 const songs=tab==='search'?(current.results||[]):(m.songs||[]);
 const key=JSON.stringify([tab,songs,m.current?.id,m.playing,m.lyric,current.notice]);
 if(key===lastList)return;lastList=key;
 if(tab!=='search'||!area.querySelector('form')){
  area.replaceChildren();
  if(tab==='search'){
   const f=document.createElement('form');f.className='search';const input=document.createElement('input');input.placeholder='输入歌名或歌手';input.maxLength=120;
   const submit=document.createElement('button');submit.textContent='搜索';f.append(input,submit);f.onsubmit=e=>{e.preventDefault();if(input.value.trim())post('search',input.value.trim())};area.append(f);
  }
 }
 area.querySelector('.entries')?.remove();const entries=document.createElement('div');entries.className='entries';area.append(entries);
 if(tab==='lyrics'){entries.textContent=m.lyric||'暂无同步歌词';return}
 if(!songs.length)entries.textContent=tab==='queue'?'播放队列为空':current.notice||'';
 songs.forEach(s=>{
  const row=document.createElement('div');row.className='queue';const label=document.createElement('span');label.textContent=titleOf(s);
  const active=tab==='queue'&&s.id===m.current?.id&&m.playing;
  row.append(label,button(active?'暂停':'播放',active?'pause':tab==='queue'?'select':'playResult',s.id,active?'pause':'play'));
  row.append(button(tab==='search'?'＋':'移出',tab==='search'?'enqueue':'remove',s.id));entries.append(row);
 });
}
const historyView = document.createElement('section');
historyView.className = 'history glass';
const historyHeader = document.createElement('header');
historyHeader.textContent = '会话记录';
historyHeader.append(button('关闭', 'dismiss', null, 'close'));
const log = document.createElement('div');
log.className = 'history-log';
log.setAttribute('role', 'log');
historyView.append(historyHeader, log);
$('.stage').append(historyView);
let lastHistory = '';
function renderHistory(rows) {
 const signature = JSON.stringify(rows);
 if (signature === lastHistory) return;
 const follow = !lastHistory || log.scrollHeight - log.scrollTop - log.clientHeight < 24;
 lastHistory = signature;
 log.replaceChildren(...rows.map(row => {
  const item = document.createElement('article');
  const meta = document.createElement('small');
  const date = new Date(row.time * 1000);
  meta.textContent = row.label + ' · ' + date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const body = document.createElement('p');
  body.textContent = row.text;
  if (row.label === '你') item.className = 'from-user';
  item.append(meta, body);
  return item;
 }));
 if (!rows.length) log.textContent = '今天想聊什么？';
 if (follow) log.scrollTop = log.scrollHeight;
}
window.panelUpdate=function(data){
 current=data;const mode=document.body.dataset.panel;
 if(['menu','options','voices'].includes(mode))renderMenu(mode);
 if(mode==='caption'){ $('.caption').style.fontSize=18*(data.captionScale||1)+'px';$('.caption').textContent=data.caption||''; }
 if(mode==='history')renderHistory(data.timeline||[]);
 if(mode!=='music')return;
 const m=data.music||{}, song=m.current||{}, pos=data.position||0, dur=song.duration||0;
 $('#song').textContent=titleOf(song)||'还没有播放歌曲';$('#pause').innerHTML=icon(m.playing?'pause':'play');
 $('#pause').title=m.playing?'暂停':'继续';$('#seek').max=Math.max(dur,pos,1);$('#seek').value=pos;
 $('#elapsed').textContent=clock(pos);$('.time span:last-child').textContent=dur?clock(dur):'—';
 document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));musicBody();
};
window.panelShow=(mode,data)=>{document.body.dataset.panel=mode;const compose=$('.compose');if(mode==='history'){historyView.append(compose);lastHistory=''}else{$('.chat').append(compose)}window.panelUpdate(data);if(mode==='dock')$('.dock input').focus()};
$('.dock').onsubmit=e=>{e.preventDefault();const i=$('.dock input');if(i.value.trim()){post('send',i.value.trim());i.value=''}};
$('#more').title='会话记录';
$('#more').setAttribute('aria-label','会话记录');
$('#more').onclick=()=>post('history');
document.querySelector('.player').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.tab){tab=b.dataset.tab;window.panelUpdate(current)}else if(b.dataset.action)post(b.dataset.action)});
document.addEventListener('keydown',e=>{if(e.key==='Escape')post('dismiss')});
$('.caption').onpointerdown=e=>{if(e.button===0)post('dragCaption')};
window.panelReady=true;
const sizing=new ResizeObserver(()=>{
 const mode=document.body.dataset.panel;
 const el=mode==='history'?historyView:mode==='music'?$('.player'):mode==='dock'?$('.dock'):mode==='caption'?$('.caption'):$('.menu');
 if(mode&&el.offsetHeight)post('contentHeight',el.offsetHeight)
});
for(const node of ['.player','.dock','.caption','.menu'])sizing.observe($(node));

sizing.observe(historyView);
