const $ = id => document.getElementById(id);
const home = $('home'), game = $('game'), modal = $('modal');
const views = ['create-form', 'join-form', 'settings-form'];
let state = null, socket = null, session = null, lastResultHand = 0, reconnectTimer = null;
let selectedHandId = null, leavingRoom = false, speedTimer = null;
const actionPops = new Map();
const seenChatIds = new Set();
let renderedEventCount = 0, logVisible = true;

function showModal(which) {
  views.forEach(id => $(id).classList.toggle('hidden', id !== which));
  $('modal-error').classList.add('hidden');
  modal.classList.remove('hidden');
  if (which === 'join-form') {
    const recent = JSON.parse(localStorage.getItem('river-addresses') || '[]');
    $('recent-addresses').innerHTML = recent.map(address => `<option value="${escapeHTML(address)}"></option>`).join('');
    $('join-ip').value ||= recent[0] || location.host;
  }
}
function closeModal() { modal.classList.add('hidden'); }
function error(text) { $('modal-error').textContent = text; $('modal-error').classList.remove('hidden'); }
function toast(text) { const el = $('toast'); el.textContent = text; el.classList.remove('hidden'); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add('hidden'), 3200); }
function escapeHTML(text) { return String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function rememberAddress(address) { const old = JSON.parse(localStorage.getItem('river-addresses') || '[]'); localStorage.setItem('river-addresses', JSON.stringify([address, ...old.filter(x => x !== address)].slice(0, 6))); }
function validBuyin(value) { const n = Number(value); return Number.isInteger(n) && n >= 5 && n <= 1000 && n % 5 === 0 ? n : null; }
async function post(path, data) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const failure = new Error(body.error || '请求失败');
    failure.status = response.status;
    throw failure;
  }
  return body;
}
function enter(result) {
  session = result;
  lastResultHand = 0;
  selectedHandId = null;
  leavingRoom = false;
  actionPops.clear();
  seenChatIds.clear();
  renderedEventCount = 0;
  $('chat-messages').innerHTML = '<p class="panel-empty">发送一条消息，和牌友聊聊。</p>';
  $('events').innerHTML = '<li class="panel-empty">等待牌局开始…</li>';
  setLogVisible(true);
  localStorage.setItem('river-session', JSON.stringify(session));
  $('resume').classList.remove('hidden');
  home.classList.add('hidden'); game.classList.remove('hidden'); closeModal();
  connect();
}
function connect() {
  if (!session) return;
  clearTimeout(reconnectTimer);
  const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws?token=${encodeURIComponent(session.token)}`;
  socket = new WebSocket(url);
  $('connection').classList.add('offline'); $('connection').lastChild.textContent = ' 连接中';
  socket.onopen = () => { $('connection').classList.remove('offline'); $('connection').lastChild.textContent = ' 已连接'; };
  socket.onmessage = event => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    if (data.type === 'state') render(data.state);
    if (data.type === 'chat') appendChat(data.message);
    if (data.type === 'error') toast(data.message);
    if (data.type === 'kicked') leave(data.reason);
  };
  socket.onclose = async event => {
    if (!session || leavingRoom) return;
    $('connection').classList.add('offline'); $('connection').lastChild.textContent = ' 重连中';
    if (event.code === 1008 || event.code === 1002) { leave('会话已失效，请重新加入'); return; }
    try {
      const check = await fetch(`/api/session?token=${encodeURIComponent(session.token)}`);
      if (check.status === 401) { leave('房间会话已失效，请重新加入'); return; }
    } catch {}
    reconnectTimer = setTimeout(connect, 1800);
  };
}
function send(type, extra = {}) {
  if (socket?.readyState === WebSocket.OPEN) { socket.send(JSON.stringify({type, ...extra})); return true; }
  toast('连接尚未恢复');
  return false;
}
function leave(message = '') {
  session = null; state = null;
  selectedHandId = null;
  actionPops.clear();
  seenChatIds.clear();
  renderedEventCount = 0;
  leavingRoom = false;
  localStorage.removeItem('river-session');
  $('resume').classList.add('hidden');
  clearTimeout(reconnectTimer);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  game.classList.add('hidden'); home.classList.remove('hidden');
  $('result-banner').classList.add('hidden');
  if (message) toast(message);
}
function setLogVisible(visible) {
  logVisible = visible;
  $('event-panel').classList.toggle('collapsed', !visible);
  $('toggle-log').textContent = visible ? '隐藏' : '展示';
  $('toggle-log').setAttribute('aria-expanded', String(visible));
}
function renderEvents(entries) {
  if (entries.length === renderedEventCount) return;
  const list = $('events');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
  if (entries.length < renderedEventCount) {
    list.replaceChildren();
    renderedEventCount = 0;
  }
  if (entries.length) list.querySelector('.panel-empty')?.remove();
  const fragment = document.createDocumentFragment();
  for (const entry of entries.slice(renderedEventCount)) {
    const item = document.createElement('li');
    const time = document.createElement('time');
    time.textContent = entry.time;
    const text = document.createElement('span');
    text.textContent = entry.text;
    item.append(time, text);
    fragment.append(item);
  }
  if (!entries.length) {
    const empty = document.createElement('li');
    empty.className = 'panel-empty';
    empty.textContent = '等待牌局开始…';
    fragment.append(empty);
  }
  list.append(fragment);
  renderedEventCount = entries.length;
  if (atBottom) list.scrollTop = list.scrollHeight;
}
function appendChat(message) {
  if (!message || seenChatIds.has(message.id)) return;
  seenChatIds.add(message.id);
  const list = $('chat-messages');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
  list.querySelector('.panel-empty')?.remove();
  const item = document.createElement('div');
  item.className = `chat-message ${message.senderId === state?.you ? 'mine' : ''}`;
  item.dataset.chatId = message.id;
  const meta = document.createElement('div');
  meta.className = 'chat-meta';
  const sender = document.createElement('strong');
  sender.textContent = message.sender;
  const time = document.createElement('time');
  time.textContent = message.time;
  meta.append(sender, time);
  const body = document.createElement('div');
  body.className = 'chat-text';
  body.textContent = message.text;
  item.append(meta, body);
  const following = [...list.children].find(node => Number(node.dataset.chatId) > message.id);
  list.insertBefore(item, following || null);
  while (list.children.length > 200) {
    list.firstElementChild.remove();
  }
  if (atBottom || message.senderId === state?.you) list.scrollTop = list.scrollHeight;
}
function card(text, delay = 0, glowing = false) {
  if (text === '??') return '<div class="card back"></div>';
  const rank = text[0] === 'T' ? '10' : text[0];
  const suit = {s:'♠',h:'♥',d:'♦',c:'♣'}[text[1]];
  return `<div class="card ${'hd'.includes(text[1]) ? 'red' : ''} ${glowing ? 'best-card' : ''}" style="animation-delay:${delay}ms">${rank}<span>${suit}</span></div>`;
}
const seatPositions = [[50,86],[25,79],[9,63],[13,38],[31,17],[50,11],[69,17],[87,38],[91,63],[75,79]];
function render(next) {
  const previous = state;
  state = next;
  if (previous?.handNo !== next.handNo) {
    if (previous && next.handNo <= previous.handNo) lastResultHand = 0;
    selectedHandId = null;
    actionPops.clear();
    $('result-banner').classList.add('hidden');
  }
  const hands = next.phase === 'complete' ? (next.result?.hands || {}) : {};
  const contenders = next.players.filter(p => hands[p.id]);
  if (contenders.length && !contenders.some(p => p.id === selectedHandId)) {
    selectedHandId = hands[next.you] ? next.you : contenders[0].id;
  }
  const glowingCards = new Set(next.result?.bestCards?.[selectedHandId] || []);
  const now = Date.now();
  for (const p of next.players) {
    const before = previous?.players.find(old => old.id === p.id);
    const newBlind = previous?.handNo !== next.handNo && /^(小盲|大盲)/.test(p.lastAction);
    if (previous && p.lastAction && (p.actionSeq > (before?.actionSeq || 0) || newBlind)) {
      actionPops.set(p.id, {label:p.lastAction, until:now + 2100});
      setTimeout(() => { if (state) render(state); }, 2150);
    }
  }
  $('room-code').textContent = next.room;
  $('pot').textContent = `¥${next.pot}`;
  $('hand-number').textContent = `第 ${next.handNo} 局 · 5 / 10 盲注`;
  $('street-label').textContent = {waiting:'等待开局',preflop:'翻牌前',flop:'翻牌圈',turn:'转牌圈',river:'河牌圈',complete:'本局结束'}[next.phase];
  $('board').innerHTML = Array.from({length:5}, (_, i) => next.board[i] ? card(next.board[i], i * 65, glowingCards.has(next.board[i])) : '<div class="card empty"></div>').join('');
  const viewerIndex = Math.max(0, next.players.findIndex(p => p.id === next.you));
  const winnerIds = new Set((next.result?.pots || []).flatMap(p => p.winners));
  $('seat-layer').innerHTML = next.players.map((_, j) => {
    const p = next.players[(viewerIndex + j) % next.players.length];
    const position = seatPositions[Math.round(j * 10 / next.players.length)];
    const classes = ['seat', next.turn === p.id ? 'active' : '', next.phase === 'complete' && winnerIds.has(p.id) ? 'winner' : '', p.stack === 0 ? 'bust' : '', p.folded ? 'folded' : ''].join(' ');
    const dealer = next.dealer === p.id ? '<span class="dealer-button">D</span>' : '';
    const status = p.stack === 0 && p.inHand ? '破产' : (p.lastAction || (p.bot ? '电脑玩家' : p.connected ? '已入座' : '离线'));
    const popup = actionPops.get(p.id);
    const kick = next.host === next.you && p.id !== next.you ? `<button class="kick-player" data-player-id="${escapeHTML(p.id)}" data-player-name="${escapeHTML(p.name)}" title="移出 ${escapeHTML(p.name)}" aria-label="移出 ${escapeHTML(p.name)}">×</button>` : '';
    return `<div class="${classes}" style="--x:${position[0]}%;--y:${position[1]}%">${popup?.until > now ? `<div class="action-pop">${escapeHTML(popup.label)}</div>` : ''}<div class="seat-head">${dealer}<span class="seat-name">${escapeHTML(p.name)}${p.id === next.you ? ' · 你' : ''}</span>${p.bot ? '<span class="seat-meta">AI</span>' : ''}${kick}</div><div class="seat-stack">¥${p.stack}</div><div class="seat-cards">${p.cards.map(c => card(c, 0, selectedHandId === p.id && glowingCards.has(c))).join('')}</div><div class="seat-action">${escapeHTML(status)}</div>${p.streetBet ? `<div class="seat-bet">● ¥${p.streetBet}</div>` : ''}</div>`;
  }).join('');
  $('showdown').classList.toggle('hidden', contenders.length === 0);
  $('showdown-players').innerHTML = contenders.map(p => `<button class="showdown-player ${p.id === selectedHandId ? 'selected' : ''}" data-hand-id="${escapeHTML(p.id)}"><span>${escapeHTML(p.name)}</span><strong>${escapeHTML(hands[p.id])}</strong><small>${(next.result?.bestCards?.[p.id] || []).map(escapeHTML).join(' · ')}</small></button>`).join('');
  const my = next.players.find(p => p.id === next.you);
  const actor = next.players.find(p => p.id === next.turn);
  $('status-text').textContent = next.phase === 'waiting' ? '等待玩家加入' : next.phase === 'complete' ? (my?.stack === 0 ? '筹码已用尽，等待房主重新开始' : '本局结束，准备下一局') : actor ? `${actor.name} 正在行动` : '正在发牌';
  renderEvents(next.events || []);
  for (const message of next.chat || []) appendChat(message);
  const isHost = next.host === next.you;
  $('host-controls').classList.toggle('hidden', !isHost);
  $('add-bot').disabled = next.players.length >= 10;
  $('bot-speed').value = next.botDelayMs;
  $('bot-speed-value').textContent = `${(next.botDelayMs / 1000).toFixed(1)} 秒`;
  $('start-hand').disabled = !next.canStart;
  $('start-hand').textContent = next.phase === 'complete' ? '下一局' : '开始发牌';
  $('action-controls').classList.toggle('hidden', !next.options);
  if (next.options) {
    const o = next.options;
    $('call').textContent = o.check ? '过牌' : `跟注 ¥${o.call}`;
    $('raise').disabled = !o.canRaise;
    const min = o.shortAllIn ? o.maxTo : o.minTo;
    $('raise-amount').min = Math.min(min, o.maxTo);
    $('raise-amount').max = o.maxTo;
    $('raise-range').textContent = o.shortAllIn ? `仅可全下 ¥${o.maxTo}` : `¥${o.minTo}–¥${o.maxTo}`;
    const prior = Number($('raise-value').value);
    const value = previous?.turn === next.turn && prior >= min && prior <= o.maxTo ? prior : min;
    $('raise-amount').value = value;
    $('raise-value').value = value;
    $('raise').textContent = o.shortAllIn ? '全下' : '加注';
  }
  if (next.phase === 'complete' && next.handNo !== lastResultHand) {
    lastResultHand = next.handNo;
    const winners = [...winnerIds].map(id => next.players.find(p => p.id === id)?.name).filter(Boolean);
    const gain = Object.entries(next.result?.awards || {}).filter(([, amount]) => amount > 0).map(([id, amount]) => `${next.players.find(p => p.id === id)?.name} +¥${amount}`).join(' · ');
    const banner = $('result-banner');
    banner.classList.toggle('bust-banner', my?.stack === 0);
    banner.innerHTML = my?.stack === 0 ? '<strong>筹码已用尽</strong><span>等待房主重新开始游戏</span>' : `<strong>${escapeHTML(winners.join('、') || '本局结束')} 获胜</strong><span>${escapeHTML(gain)}</span>`;
    banner.classList.remove('hidden');
    setTimeout(() => banner.classList.add('hidden'), 4200);
  }
}

$('open-create').onclick = () => showModal('create-form');
$('open-join').onclick = () => showModal('join-form');
$('open-settings').onclick = () => showModal('settings-form');
$('close-modal').onclick = closeModal;
modal.onclick = e => { if (e.target === modal) closeModal(); };
$('create-submit').onclick = async () => {
  const buyin = validBuyin($('create-buyin').value);
  if (!buyin) return error('带入金额须为 5～1000 元，且为 5 的倍数');
  try { enter(await post('/api/create', {name:$('create-name').value.trim(), buyin})); }
  catch (e) { error(e.message); }
};
$('join-submit').onclick = async () => {
  const buyin = validBuyin($('join-buyin').value);
  if (!buyin) return error('带入金额须为 5～1000 元，且为 5 的倍数');
  const room = $('join-room').value.trim().toUpperCase();
  if (!/^[A-Z2-9]{6}$/.test(room)) return error('请输入 6 位房间码');
  let origin;
  try { origin = new URL(/^https?:\/\//i.test($('join-ip').value.trim()) ? $('join-ip').value.trim() : `http://${$('join-ip').value.trim()}`).origin; }
  catch { return error('房主 IP 地址格式不正确'); }
  rememberAddress(new URL(origin).host);
  if (origin !== location.origin) {
    const url = new URL(origin);
    url.searchParams.set('join', room);
    url.searchParams.set('name', $('join-name').value.trim());
    url.searchParams.set('buyin', buyin);
    location.href = url.href;
    return;
  }
  try { enter(await post('/api/join', {room, name:$('join-name').value.trim(), buyin})); }
  catch (e) { error(e.message); }
};
$('fold').onclick = () => send('action', {action:'fold'});
$('call').onclick = () => send('action', {action:'call'});
$('raise').onclick = () => {
  const amount = Number($('raise-value').value);
  if (!Number.isInteger(amount) || amount % 5) return toast('下注须为 5 的倍数');
  send('action', {action:'raise', amount});
};
$('raise-amount').oninput = () => { $('raise-value').value = $('raise-amount').value; };
$('raise-value').onchange = () => { $('raise-amount').value = $('raise-value').value; };
$('add-bot').onclick = () => send('bot');
$('bot-speed').oninput = () => {
  const delayMs = Number($('bot-speed').value);
  $('bot-speed-value').textContent = `${(delayMs / 1000).toFixed(1)} 秒`;
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => send('bot_speed', {delayMs}), 100);
};
$('start-hand').onclick = () => send('start');
$('restart').onclick = () => { if (confirm('重新开始会踢出所有其他玩家，并重置你的带入筹码。确定继续吗？')) send('restart'); };
$('seat-layer').onclick = event => {
  const button = event.target.closest('.kick-player');
  if (!button) return;
  if (confirm(`确定将 ${button.dataset.playerName} 移出房间吗？`)) send('kick', {playerId:button.dataset.playerId});
};
$('showdown-players').onclick = event => {
  const button = event.target.closest('[data-hand-id]');
  if (!button || !state) return;
  selectedHandId = button.dataset.handId;
  render(state);
};
$('toggle-log').onclick = () => setLogVisible(!logVisible);
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (send('chat', {text})) input.value = '';
};
$('leave').onclick = async () => {
  if (!session || leavingRoom) return;
  leavingRoom = true;
  try {
    await post('/api/leave', {token:session.token});
    leave('已退出房间');
  } catch (e) {
    if (e.status === 401 || e.status === 404) { leave('已退出房间'); return; }
    leavingRoom = false;
    toast(e.message);
  }
};
$('resume').onclick = () => { home.classList.add('hidden'); game.classList.remove('hidden'); connect(); };
$('copy-link').onclick = async () => {
  try {
    const response = await fetch('/api/network');
    const data = await response.json();
    const url = `${location.protocol}//${data.ip || location.hostname}:${location.port || 8765}/?room=${state.room}`;
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
    else {
      const field = document.createElement('textarea');
      field.value = url; document.body.appendChild(field); field.select();
      if (!document.execCommand('copy')) throw new Error('copy');
      field.remove();
    }
    toast('邀请链接已复制');
  } catch { toast(`房间码：${state.room}`); }
};

(async function init() {
  const params = new URLSearchParams(location.search);
  const invitedRoom = params.get('join') || params.get('room');
  const invitedName = params.get('name');
  const invitedBuyin = Number(params.get('buyin'));
  if (invitedRoom) {
    history.replaceState(null, '', location.pathname);
    $('join-room').value = invitedRoom.toUpperCase();
    $('join-ip').value = location.host;
    if (invitedName) $('join-name').value = invitedName;
    if (validBuyin(invitedBuyin)) $('join-buyin').value = invitedBuyin;
    rememberAddress(location.host);
    if (invitedName && validBuyin(invitedBuyin) && params.has('join')) {
      try { enter(await post('/api/join', {room:invitedRoom.toUpperCase(), name:invitedName, buyin:invitedBuyin})); return; }
      catch (e) { showModal('join-form'); error(e.message); return; }
    }
    showModal('join-form');
    return;
  }
  try { const saved = JSON.parse(localStorage.getItem('river-session') || 'null'); if (saved?.token) enter(saved); }
  catch { localStorage.removeItem('river-session'); }
})();
