'use strict';

let state = null;
let lastMessage = 0;
let previousStage = null;
let activeTab = 'room';
let toastTimer;
let disconnected = false;
let stopped = false;
let lastVoiceError = '';
let voiceRequest;
let playingVoice;
let hasPenguinSprite;
let voiceOptionsReady = false;
let actionVersion = 0;
let pendingActions = 0;
let previousCaseEvent = null;
let caseAnimation;
let previousStateBody;
let latestEarbuds;
let earbudStream;
const el = (id) => document.getElementById(id);

function toast(text) {
  el('toast').textContent = text;
  el('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el('toast').hidden = true; }, 4200);
}

async function act(action, fields = {}) {
  actionVersion++;
  pendingActions++;
  try {
    const response = await fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, ...fields }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '这次互动没有完成。');
    previousStateBody = undefined;
    render({ ...state, ...result });
    return true;
  } catch (error) {
    toast(error.message || '暂时没连上企鹅的房间。');
    return false;
  } finally {
    pendingActions--;
  }
}

function timeLabel(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function renderCase() {
  const life = state.case_companion;
  if (!life) return;
  const simulation = life.source === 'simulation';
  const home = el('case-home');
  home.dataset.phase = life.phase;
  home.dataset.lid = life.lid;
  el('case-model').style.setProperty('--home-light', life.warmth / 100);
  el('case-phase').textContent = life.label;
  el('case-source').textContent = simulation ? '模拟体验' : '跟随耳机';
  el('case-follow-live').hidden = !simulation;
  const earLabels = { unknown: '未知', docked: '在盒里', out: '已取出', worn: '已戴上' };
  for (const ear of ['left', 'right']) {
    el(`case-${ear}-status`).textContent = `${ear === 'left' ? '左耳' : '右耳'} · ${earLabels[life.ears[ear]]}`;
    const bud = el(`case-ear-${ear}`);
    const docked = life.ears[ear] === 'docked';
    bud.dataset.docked = String(docked);
    bud.dataset.simulate = `${docked ? 'take' : 'return'}_${ear}`;
    bud.setAttribute('aria-label', `体验${docked ? '取出' : '放回'}${ear === 'left' ? '左耳' : '右耳'}`);
  }
  el('case-lid-status').textContent = '盒盖 · ' + ({ open: '打开', closed: '合上', unknown: '未知' })[life.lid];
  el('case-lid').dataset.simulate = life.lid === 'open' ? 'lid_close' : 'lid_open';
  el('case-lid').setAttribute('aria-label', life.lid === 'open' ? '体验合盖' : '体验开盖');
  el('case-model-caption').textContent = simulation ? '点击盒盖与耳机，也能体验动作' : '盒内小灯是精灵的虚拟灯光';
  el('case-moment').textContent = life.last_event?.text || '开盖探头，取出时跟你出门，回盒后收拾自己的小屋。';
  el('case-collection-count').textContent = life.postcards.length;
  el('case-card-title').textContent = life.selected_postcard?.title || '给下次见面留个位置';
  el('case-card-text').textContent = life.selected_postcard?.text || '回盒、合盖后，他会准备一张自己的小画，再见面时拿给你。';
  el('case-warmth').textContent = life.warmth + '%';
  el('case-light-level').textContent = life.warmth + '%';
  el('case-light-fill').style.width = life.warmth + '%';
  el('case-swipe-pad').setAttribute('aria-valuenow', life.warmth);
  el('case-swipe-pad').setAttribute('aria-valuetext', `小屋灯光 ${life.warmth}%`);
  el('scene').dataset.casePhase = life.phase;
  const inCase = ['peeking', 'home', 'sleeping'].includes(life.phase);
  el('scene-activity').textContent = inCase ? life.label : state.activity_label;
  el('profile-status').textContent = life.phase === 'together' || inCase ? life.label : state.activity_label;
  const worn = ['left', 'right'].filter((ear) => life.ears[ear] === 'worn');
  const side = worn.length === 2 ? 'both' : worn[0];
  el('room-pet').dataset.wearing = side || '';
  if (hasPenguinSprite) {
    const path = side ? `/assets/penguin-${side}.png` : '/assets/penguin.png';
    const sprite = el('room-pet').querySelector('.penguin-image');
    if (sprite.getAttribute('src') !== path) sprite.src = path;
  }
  if (previousCaseEvent?.source !== life.source || previousCaseEvent?.id !== life.event_id) {
    const previousId = previousCaseEvent?.source === life.source ? previousCaseEvent.id : 0;
    const freshLights = life.events.filter((event) => event.kind === 'light' && event.id > previousId && Date.now() / 1000 - event.time < 8);
    if (previousCaseEvent && life.source === 'device' && freshLights.length) {
      const changes = freshLights.slice(-3).map((event) => `${event.label.startsWith('上滑') ? '上滑' : '下滑'} ${Number.isFinite(event.light_percent) ? event.light_percent + '%' : ''}`);
      toast('小屋灯光 · ' + changes.join(' → '));
    }
    previousCaseEvent = { source: life.source, id: life.event_id };
    home.dataset.motion = life.animation;
    el('room-pet').dataset.motion = life.animation;
    caseAnimation?.cancel();
    if (life.animation !== 'idle' && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const target = document.querySelector(inCase ? '.case-pet .penguin-image' : '#room-pet .penguin-image');
      const motion = life.animation === 'jumping'
        ? [{ translate: '0 0' }, { translate: '0 -22px', offset: 0.5 }, { translate: '0 0' }]
        : [{ rotate: '-5deg' }, { rotate: '5deg', offset: 0.5 }, { rotate: '-5deg' }];
      caseAnimation = target.animate(motion, { duration: 650, iterations: 2 });
    }
    el('case-events').replaceChildren();
    for (const event of life.events.slice(-3).reverse()) {
      const item = document.createElement('li');
      const time = document.createElement('time');
      time.textContent = timeLabel(event.time);
      const level = Number.isFinite(event.light_percent) ? ` · ${event.light_percent}%` : '';
      item.append(time, document.createTextNode(event.label + level));
      el('case-events').append(item);
    }
  }
}

function renderJournal() {
  const list = el('journal-list');
  list.replaceChildren();
  const items = state.timeline;
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-memory';
    empty.textContent = '这一页还空着。去码头坐坐，留一句话，或者一起选个灯位。';
    list.append(empty);
    return;
  }
  for (const item of items) {
    const article = document.createElement('article');
    article.className = 'journal-entry';
    const time = document.createElement('time');
    time.dateTime = new Date(item.time * 1000).toISOString();
    time.textContent = new Date(item.time * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
    const body = document.createElement('div');
    const text = document.createElement('p');
    text.textContent = item.text;
    const tag = document.createElement('small');
    tag.textContent = item.label;
    body.append(text, tag);
    article.append(time, body);
    list.append(article);
  }
}

function stopVoice() {
  voiceRequest?.abort();
  voiceRequest = null;
  if (playingVoice) {
    playingVoice.pause();
    URL.revokeObjectURL(playingVoice.src);
    playingVoice = null;
  }
}

async function speak(message) {
  stopVoice();
  const request = new AbortController();
  voiceRequest = request;
  try {
    const response = await fetch('/api/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: message.id }),
      signal: request.signal,
    });
    if (!response.ok) {
      if (response.status === 409) return;
      const error = await response.json();
      throw new Error(error.error);
    }
    const blob = await response.blob();
    if (request.signal.aborted) return;
    const player = new Audio(URL.createObjectURL(blob));
    playingVoice = player;
    player.addEventListener('ended', () => {
      if (playingVoice === player) stopVoice();
    }, { once: true });
    await player.play();
  } catch (error) {
    if (!request.signal.aborted) {
      stopVoice();
      toast(error.name === 'NotAllowedError' ? '浏览器阻止了自动播放，请点「试听」。' : error.message);
    }
  }
}

function render(next) {
  if (latestEarbuds?.earbud_revision > (next.earbud_revision ?? -1)) {
    next = { ...next, ...latestEarbuds };
  }
  state = next;
  el('day-label').textContent = `守灯第 ${state.day} 天`;
  const scale = state.pet_scale ?? 1;
  const scalePercent = Math.round(scale * 100);
  if (document.activeElement !== el('pet-size')) el('pet-size').value = scalePercent;
  el('scene').style.setProperty('--pet-scale', Number(el('pet-size').value) / 100);
  el('pet-size-label').textContent = `${el('pet-size').value}%`;
  el('size-summary').textContent = `${scalePercent}%`;
  if (state.voice_error && state.voice_error !== lastVoiceError) toast(state.voice_error);
  if (hasPenguinSprite !== state.penguin_sprite) {
    hasPenguinSprite = state.penguin_sprite;
    document.querySelectorAll('.penguin-image').forEach((sprite) => {
      if (hasPenguinSprite) sprite.src = '/assets/penguin.png';
      sprite.hidden = !hasPenguinSprite;
    });
    document.querySelectorAll('.penguin-placeholder').forEach((placeholder) => { placeholder.hidden = hasPenguinSprite; });
  }
  if (!voiceOptionsReady) {
    el('voice-select').replaceChildren(...Object.entries(state.voice_options).map(([value, name]) => new Option(name, value)));
    voiceOptionsReady = true;
  }
  if (!el('voice-select').disabled) el('voice-select').value = state.voice_id;
  lastVoiceError = state.voice_error || '';
  el('scene').dataset.activity = state.activity;
  el('scene-activity').textContent = state.activity_label;
  el('profile-status').textContent = state.activity_label;
  el('project-title').textContent = state.project_title;
  const site = { pier: '码头', cliff: '风崖' }[state.room_choice];
  el('project-detail').textContent = site ? `你们选的灯位在${site}。` : '给夜里进港的小船指路。';
  el('catch-total').textContent = `渔获 · ${state.catches}`;
  el('scene').classList.toggle('signal-lit', state.project_stage === 4);
  el('progress').setAttribute('aria-valuenow', state.project_progress);
  el('progress').firstElementChild.style.width = `${state.project_progress}%`;
  el('memory-count').textContent = state.timeline.length;
  const voice = el('voice-toggle');
  voice.setAttribute('aria-pressed', String(state.voice));
  voice.setAttribute('aria-label', state.voice ? '关闭企鹅的声音' : '开启企鹅的声音');
  voice.title = state.voice ? '关闭企鹅的声音' : '开启企鹅的声音';
  if ((!state.voice && !state.message?.preview) || state.message?.audible === false) stopVoice();
  el('desktop-toggle').innerHTML = `<span class="desktop-icon" aria-hidden="true">▱</span>${state.pet_visible ? '收起桌面精灵' : '来桌面陪我'}`;

  if (previousStage !== null && state.project_stage > previousStage) {
    el('new-leaf').hidden = false;
    setTimeout(() => { el('new-leaf').hidden = true; }, 6000);
  }
  previousStage = state.project_stage;
  const message = state.message;
  if (message && message.id !== lastMessage) {
    lastMessage = message.id;
    if (!message.preview) {
      el('dialog-topic').textContent = message.topic;
      el('dialog-text').textContent = message.text;
      el('dialog-text').classList.remove('changing');
      void el('dialog-text').offsetWidth;
      el('dialog-text').classList.add('changing');
      el('room-pet').classList.add('is-talking');
      setTimeout(() => el('room-pet').classList.remove('is-talking'), 1500);
      el('choices').replaceChildren();
      for (const choice of message.choices) {
        const button = document.createElement('button');
        button.textContent = choice.label;
        button.addEventListener('click', () => act('choose', { value: choice.value }));
        el('choices').append(button);
      }
    }
    if (message.audible !== false && (state.voice || message.preview) && !state.native_voice) speak(message);
  }
  const recent = el('recent-life');
  recent.replaceChildren();
  const entries = state.journal.slice(-2).reverse();
  if (!entries.length) {
    const p = document.createElement('p');
    p.textContent = '企鹅捡回了被风吹灭的信号灯。';
    recent.append(p);
  }
  for (const item of entries) {
    const p = document.createElement('p');
    const time = document.createElement('time');
    time.textContent = timeLabel(item.time);
    p.append(time, document.createTextNode(item.text.length > 65 ? item.text.slice(0, 65) + '…' : item.text));
    recent.append(p);
  }
  renderEarbuds();
  if (activeTab === 'memories') renderJournal();
}

function renderEarbuds() {
  const device = state.device;
  el('device-dot').className = 'device-dot ' + device.status;
  const wornEars = [['left', '左耳'], ['right', '右耳']]
    .filter(([ear]) => device.position?.[ear]?.in_ear)
    .map(([, label]) => label);
  const connectedLabel = wornEars.length ? `耳机已连接 · ${wornEars.join('、')}佩戴中` : '耳机已连接';
  el('device-label').textContent = ({ connected: connectedLabel, syncing: '正在读取耳机状态', connecting: '正在连接耳机', disconnected: '耳机暂未连接', unavailable: '耳机通道待连接' })[device.status] || '耳机待连接';
  el('device-detail').textContent = device.detail;
  el('last-touch').textContent = device.last_touch ? '最近的动作 · ' + device.last_touch : '等待第一次触摸';
  el('device-battery').textContent = Object.entries(device.battery).map(([key, value]) => `${({ left: '左耳', right: '右耳', case: '盒子' })[key]} ${value.percent}%${value.charging ? ' · 充电中' : ''}`).join('　');
  el('unclassified-touch').hidden = !device.unclassified_touch;
  if (device.unclassified_touch) el('unclassified-touch').textContent = '待验证触摸回包 · ' + (device.unclassified_touch.raw || device.unclassified_touch.gesture);
  renderCase();
}

function followEarbuds() {
  if (typeof EventSource !== 'function') return;
  earbudStream = new EventSource('/api/earbuds/events');
  earbudStream.onmessage = ({ data }) => {
    latestEarbuds = JSON.parse(data);
    if (!state || latestEarbuds.earbud_revision <= (state.earbud_revision ?? -1)) return;
    Object.assign(state, latestEarbuds);
    renderEarbuds();
  };
}

async function poll() {
  if (stopped) return;
  const version = actionVersion;
  try {
    const response = await fetch('/api/state', { cache: 'no-store', signal: AbortSignal.timeout(3500) });
    if (!response.ok) throw new Error();
    const body = await response.text();
    if (!stopped && !pendingActions && version === actionVersion && (body !== previousStateBody || disconnected)) {
      render(JSON.parse(body));
      previousStateBody = body;
    }
    disconnected = false;
  } catch {
    if (!disconnected) toast('港湾暂时离线，请重新启动应用。');
    disconnected = true;
    el('device-label').textContent = '房间已离线';
  } finally {
    const streaming = earbudStream?.readyState === EventSource.OPEN;
    setTimeout(poll, streaming || disconnected || document.hidden ? 1200 : 300);
  }
}

class HarborSound {
  constructor() { this.context = null; this.preset = 'leaves'; this.gain = null; this.source = null; }
  async start(preset = this.preset) {
    if (!this.context) this.context = new AudioContext();
    await this.context.resume();
    this.stop();
    this.preset = preset;
    const buffer = this.context.createBuffer(2, this.context.sampleRate * 12, this.context.sampleRate);
    for (let channel = 0; channel < 2; channel++) {
      const data = buffer.getChannelData(channel);
      let previous = 0;
      for (let index = 0; index < data.length; index++) {
        const white = Math.random() * 2 - 1;
        previous = (previous + 0.025 * white) / 1.025;
        const phase = index / data.length * Math.PI * 2;
        const swell = (0.55 + Math.sin(phase * 3 + channel * 0.35) * 0.40) ** 2;
        data[index] = preset === 'roof' ? previous * (2 + swell * 4)
          : white * (0.012 + swell * (preset === 'window' ? 0.23 : 0.13)) + previous * (1.4 + swell * 5);
      }
    }
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    const filter = this.context.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = ({ leaves: 1900, window: 2600, roof: 1000 })[preset];
    this.gain = this.context.createGain();
    this.gain.gain.value = Number(el('rain-volume').value) / 100;
    source.connect(filter).connect(this.gain).connect(this.context.destination);
    source.start();
    this.source = source;
    el('rain-player').hidden = false;
    el('rain-toggle').setAttribute('aria-pressed', 'true');
    el('rain-toggle').innerHTML = '<span aria-hidden="true">♫</span> 浪声中';
    document.querySelectorAll('[data-rain]').forEach((button) => button.classList.toggle('selected', button.dataset.rain === preset));
  }
  stop() {
    if (this.source) { this.source.stop(); this.source.disconnect(); this.source = null; }
    el('rain-player').hidden = true;
    el('rain-toggle').setAttribute('aria-pressed', 'false');
    el('rain-toggle').innerHTML = '<span aria-hidden="true">♫</span> 听海';
  }
}

const rain = new HarborSound();
document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', async () => {
  if (button.dataset.action === 'harbor_sound') {
    try { await rain.start(); } catch { toast('浏览器暂时无法播放声音。'); }
  }
  await act(button.dataset.action);
}));
document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => {
  activeTab = button.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach((tab) => { tab.classList.toggle('active', tab === button); tab.setAttribute('aria-selected', String(tab === button)); });
  el('room-view').hidden = activeTab !== 'room';
  el('memories-view').hidden = activeTab !== 'memories';
  el('page-title').textContent = activeTab === 'room' ? '晚一点，港口见。' : '会话记录';
  if (state) renderJournal();
}));
document.querySelectorAll('[data-simulate]').forEach((button) => button.addEventListener('click', async () => {
  await act('simulate', { value: button.dataset.simulate });
}));

let swipeStart = null;
const swipePad = el('case-swipe-pad');
swipePad.addEventListener('pointerdown', (event) => {
  swipeStart = event.clientY;
  swipePad.setPointerCapture(event.pointerId);
});
swipePad.addEventListener('pointerup', (event) => {
  if (swipeStart === null) return;
  const distance = swipeStart - event.clientY;
  swipeStart = null;
  if (Math.abs(distance) >= 18) act('simulate', { value: distance > 0 ? 'swipe_up' : 'swipe_down' });
});
swipePad.addEventListener('pointercancel', () => { swipeStart = null; });
swipePad.addEventListener('keydown', (event) => {
  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  act('simulate', { value: ['ArrowUp', 'ArrowRight'].includes(event.key) ? 'swipe_up' : 'swipe_down' });
});
document.querySelectorAll('[data-rain]').forEach((button) => button.addEventListener('click', () => rain.start(button.dataset.rain).catch(() => toast('海浪暂时没有播放成功。'))));
el('rain-toggle').addEventListener('click', () => rain.source ? rain.stop() : rain.start().catch(() => toast('海浪暂时没有播放成功。')));
el('rain-stop').addEventListener('click', () => rain.stop());
el('rain-volume').addEventListener('input', (event) => { if (rain.gain) rain.gain.gain.setTargetAtTime(Number(event.target.value) / 100, rain.context.currentTime, 0.1); });
el('advance').addEventListener('click', () => act('advance'));
el('pet-size').addEventListener('input', (event) => {
  el('pet-size-label').textContent = `${event.target.value}%`;
  el('scene').style.setProperty('--pet-scale', Number(event.target.value) / 100);
});
el('pet-size').addEventListener('change', (event) => act('scale', { value: Number(event.target.value) / 100 }));
el('pet-smaller').addEventListener('click', () => { if (state) act('scale', { value: Math.max(0.5, Math.round(((state.pet_scale ?? 1) - 0.1) * 100) / 100) }); });
el('pet-larger').addEventListener('click', () => { if (state) act('scale', { value: Math.min(2, Math.round(((state.pet_scale ?? 1) + 0.1) * 100) / 100) }); });
el('pet-size-reset').addEventListener('click', () => act('scale', { value: 1 }));
el('voice-toggle').addEventListener('click', () => { if (state) act('voice', { enabled: !state.voice }); });
el('voice-select').addEventListener('change', async (event) => {
  if (!state) return;
  const select = event.target;
  select.disabled = true;
  await act('voice_select', { value: select.value });
  select.value = state.voice_id;
  select.disabled = false;
});
el('voice-preview').addEventListener('click', () => act('voice_preview'));
el('desktop-toggle').addEventListener('click', async () => {
  if (state && await act(state.pet_visible ? 'hide_pet' : 'show_pet')) toast(state.pet_visible ? '企鹅回房间了，随时可以叫他来。' : '企鹅会出现在桌面右下角，可以拖动。');
});
el('earbud-toggle').addEventListener('click', () => { const open = el('earbud-panel').hidden; el('earbud-panel').hidden = !open; el('earbud-toggle').setAttribute('aria-expanded', String(open)); if (open) el('earbud-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); });
el('reconnect').addEventListener('click', async () => { if (await act('connect')) toast('正在重新查找耳机。'); });
el('chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = el('chat-input').value.trim();
  if (!text) { el('chat-input').focus(); return; }
  el('chat-send').disabled = true;
  if (await act('chat', { text })) { el('chat-input').value = ''; el('saved-note').textContent = '刚刚那句话，他已经收好了。'; }
  el('chat-send').disabled = false;
});
el('chat-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); el('chat-form').requestSubmit(); } });
el('quit').addEventListener('click', async () => {
  rain.stop();
  stopVoice();
  if (await act('quit')) { stopped = true; earbudStream?.close(); toast('今天的小事都保存好了。下次见。'); el('quit').disabled = true; }
});
followEarbuds();
poll();
