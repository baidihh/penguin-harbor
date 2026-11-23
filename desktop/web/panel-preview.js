let tab='lyrics',playing=true,lyrics=true,index=0;
let songs=['我记得 · 赵雷','成都 · 赵雷','理想三旬 · 陈鸿宇'];
const setCaption=text=>$('.caption').textContent=text;
function render(){
$('#song').textContent=songs[index];$('#pause').innerHTML=icon(playing?'pause':'play');$('#pause').title=playing?'暂停':'继续';$('#pause').setAttribute('aria-label',$('#pause').title);$('#lyricCheck').textContent=lyrics?'✓':'';
document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
$('.content').innerHTML=tab==='lyrics'?'<p>海风吹过，故事慢慢讲。</p>':tab==='search'?'<form class="search"><input aria-label="歌名或歌手" placeholder="输入歌名或歌手"><button>搜索</button></form><p id="result"></p>':songs.map((song,i)=>`<div class="queue"><span>${song}</span><button data-song="${i}" aria-label="播放 ${song}">${icon(i===index&&playing?'pause':'play')}</button></div>`).join('');
}
function more(open){$('.tools').hidden=!open;$('#more').setAttribute('aria-expanded',String(open))}
$('#more').onclick=()=>more($('.tools').hidden);
$('.dock').onsubmit=e=>{e.preventDefault();const input=$('.dock input');if(input.value.trim()){setCaption('听到了。我们慢慢聊。');input.value='';}};
document.addEventListener('submit',e=>{if(e.target.matches('.search')){e.preventDefault();$('#result').textContent=e.target.querySelector('input').value.trim()?'预览结果：成都 · 赵雷':'';}});
document.addEventListener('click',e=>{
const b=e.target.closest('button');if(!b)return;
if(b.dataset.tone){$('.stage').dataset.tone=b.dataset.tone;document.querySelectorAll('.themes button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));return;}
if(b.dataset.tab)tab=b.dataset.tab;
if(b.dataset.song!==undefined){index=Number(b.dataset.song);playing=true;}
switch(b.dataset.action){
case 'chat':$('.dock').hidden=false;$('.dock input').focus();break;
case 'voice':setCaption('正在等待语音输入…');break;
case 'voiceTone':setCaption('湾湾参考 · 当前音色');break;
case 'music':$('.player').hidden=false;tab='queue';break;
case 'hideMusic':$('.player').hidden=true;break;
case 'lyrics':lyrics=!lyrics;setCaption(lyrics?'海风吹过，故事慢慢讲。':'今天想聊什么？');break;
case 'close':$('.dock').hidden=true;break;
case 'pause':playing=!playing;break;
case 'prev':index=(index+songs.length-1)%songs.length;break;
case 'next':index=(index+1)%songs.length;break;
}
if(b.dataset.action||b.dataset.tab||b.dataset.song!==undefined){more(false);render();}
});
$('#seek').oninput=e=>{const seconds=Number(e.target.value);$('#elapsed').textContent=[Math.floor(seconds/60),seconds%60].map(x=>String(x).padStart(2,'0')).join(':')};
document.addEventListener('keydown',e=>{if(e.key==='Escape')more(false)});
render();
