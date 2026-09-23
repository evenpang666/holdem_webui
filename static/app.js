const $ = id => document.getElementById(id);
const home = $('home'), game = $('game'), modal = $('modal');
const views = ['create-form', 'join-form', 'settings-form'];
let state = null, socket = null, session = null, lastResultHand = 0, reconnectTimer = null;

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
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '请求失败');
  return body;
}
function enter(result) {
  session = result;
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
    if (data.type === 'error') toast(data.message);
    if (data.type === 'kicked') leave(data.reason);
  };
  socket.onclose = async event => {
    if (!session) return;
    $('connection').classList.add('offline'); $('connection').lastChild.textContent = ' 重连中';
    if (event.code === 1008 || event.code === 1002) { leave('会话已失效，请重新加入'); return; }
    try {
      const check = await fetch(`/api/session?token=${encodeURIComponent(session.token)}`);
      if (check.status === 401) { leave('房间会话已失效，请重新加入'); return; }
    } catch {}
    reconnectTimer = setTimeout(connect, 1800);
  };
}
function send(type, extra = {}) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type, ...extra})); else toast('连接尚未恢复'); }
function leave(message = '') {
  session = null; state = null;
  localStorage.removeItem('river-session');
  $('resume').classList.add('hidden');
  clearTimeout(reconnectTimer);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  game.classList.add('hidden'); home.classList.remove('hidden');
  $('result-banner').classList.add('hidden');
  if (message) toast(message);
}
function card(text, delay = 0) {
  if (text === '??') return '<div class="card back"></div>';
  const rank = text[0] === 'T' ? '10' : text[0];
  const suit = {s:'♠',h:'♥',d:'♦',c:'♣'}[text[1]];
  return `<div class="card ${'hd'.includes(text[1]) ? 'red' : ''}" style="animation-delay:${delay}ms">${rank}<span>${suit}</span></div>`;
}
const seatPositions = [[50,86],[25,79],[9,63],[13,38],[31,17],[50,11],[69,17],[87,38],[91,63],[75,79]];
function render(next) {
  const previous = state;
  state = next;
  $('room-code').textContent = next.room;
  $('pot').textContent = `¥${next.pot}`;
  $('hand-number').textContent = `第 ${next.handNo} 局 · 5 / 10 盲注`;
  $('street-label').textContent = {waiting:'等待开局',preflop:'翻牌前',flop:'翻牌圈',turn:'转牌圈',river:'河牌圈',complete:'本局结束'}[next.phase];
  $('board').innerHTML = Array.from({length:5}, (_, i) => next.board[i] ? card(next.board[i], i * 65) : '<div class="card empty"></div>').join('');
  const viewerIndex = Math.max(0, next.players.findIndex(p => p.id === next.you));
  const winnerIds = new Set((next.result?.pots || []).flatMap(p => p.winners));
  $('seat-layer').innerHTML = next.players.map((_, j) => {
    const p = next.players[(viewerIndex + j) % next.players.length];
    const position = seatPositions[Math.round(j * 10 / next.players.length)];
    const classes = ['seat', next.turn === p.id ? 'active' : '', next.phase === 'complete' && winnerIds.has(p.id) ? 'winner' : '', p.stack === 0 ? 'bust' : '', p.folded ? 'folded' : ''].join(' ');
    const dealer = next.dealer === p.id ? '<span class="dealer-button">D</span>' : '';
    const status = p.stack === 0 && p.inHand ? '破产' : (p.lastAction || (p.bot ? '电脑玩家' : p.connected ? '已入座' : '离线'));
    return `<div class="${classes}" style="--x:${position[0]}%;--y:${position[1]}%"><div class="seat-head">${dealer}<span class="seat-name">${escapeHTML(p.name)}${p.id === next.you ? ' · 你' : ''}</span>${p.bot ? '<span class="seat-meta">AI</span>' : ''}</div><div class="seat-stack">¥${p.stack}</div><div class="seat-cards">${p.cards.map(c => card(c)).join('')}</div><div class="seat-action">${escapeHTML(status)}</div>${p.streetBet ? `<div class="seat-bet">● ¥${p.streetBet}</div>` : ''}</div>`;
  }).join('');
  const my = next.players.find(p => p.id === next.you);
  const actor = next.players.find(p => p.id === next.turn);
  $('status-text').textContent = next.phase === 'waiting' ? '等待玩家加入' : next.phase === 'complete' ? (my?.stack === 0 ? '筹码已用尽，等待房主重新开始' : '本局结束，准备下一局') : actor ? `${actor.name} 正在行动` : '正在发牌';
  $('events').textContent = next.events.slice(-3).join('　·　') || '等待第一张牌发出…';
  const isHost = next.host === next.you;
  $('host-controls').classList.toggle('hidden', !isHost);
  $('add-bot').disabled = next.players.length >= 10;
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
$('start-hand').onclick = () => send('start');
$('restart').onclick = () => { if (confirm('重新开始会踢出所有其他玩家，并重置你的带入筹码。确定继续吗？')) send('restart'); };
$('leave').onclick = () => {
  clearTimeout(reconnectTimer);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  game.classList.add('hidden'); home.classList.remove('hidden');
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
