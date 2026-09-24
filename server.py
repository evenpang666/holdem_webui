"""Local network poker room server. Run: python3 server.py"""
from __future__ import annotations

import asyncio
import json
import secrets
import socket
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from aiohttp import web, WSMsgType

from poker import Player, Table, score

ROOT = Path(__file__).parent
ROOMS = {}
SESSIONS = {}


def code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(6))
        if value not in ROOMS:
            return value


def validate_player(data):
    name = str(data.get("name", "")).strip()[:18]
    amount = data.get("buyin")
    if not name:
        raise ValueError("请填写昵称")
    if type(amount) is not int or amount < 5 or amount > 1000 or amount % 5:
        raise ValueError("带入金额须为 5～1000 元，且为 5 的倍数")
    return name, amount


class Room:
    def __init__(self, room_code, host):
        self.code = room_code
        self.host = host.id
        self.table = Table()
        self.table.add(host)
        self.sockets = {}
        self.bot_task = None
        self.bot_delay_ms = 1100
        self.table.events.append(f"{host.name} 创建房间")
        self.event_times = []
        self.chat_messages = []
        self.chat_sequence = 0

    def event_log(self):
        while len(self.event_times) < len(self.table.events):
            self.event_times.append(datetime.now().strftime("%H:%M:%S"))
        return [{"id": index + 1, "time": self.event_times[index], "text": event}
                for index, event in enumerate(self.table.events)]

    def payload(self, viewer):
        state = self.table.view(viewer)
        state.update({"room": self.code, "you": viewer, "host": self.host,
                      "botDelayMs": self.bot_delay_ms,
                      "events": self.event_log(), "chat": self.chat_messages[-200:],
                      "canStart": self.table.phase in ("waiting", "complete")
                      and len(self.table.eligible()) >= 2})
        return state

    async def send_chat(self, pid, text):
        player = next((p for p in self.table.players if p.id == pid and not p.departed), None)
        if player is None:
            raise ValueError("玩家不在房间内")
        if type(text) is not str or not text.strip() or len(text.strip()) > 200:
            raise ValueError("聊天内容须为 1～200 个字符")
        self.chat_sequence += 1
        entry = {"id": self.chat_sequence, "senderId": pid, "sender": player.name,
                 "text": text.strip(), "time": datetime.now().strftime("%H:%M")}
        self.chat_messages.append(entry)
        self.chat_messages = self.chat_messages[-200:]
        for ws in list(self.sockets.values()):
            if not ws.closed:
                try:
                    await ws.send_json({"type": "chat", "message": entry})
                except ConnectionError:
                    pass

    async def broadcast(self):
        for pid, ws in list(self.sockets.items()):
            if not ws.closed:
                try:
                    await ws.send_json({"type": "state", "state": self.payload(pid)})
                except ConnectionError:
                    pass
        self.schedule_bot()

    def schedule_bot(self):
        actor = next((p for p in self.table.players if p.id == self.table.turn), None)
        if actor and actor.bot and (not self.bot_task or self.bot_task.done()):
            self.bot_task = asyncio.create_task(self.run_bot())

    async def run_bot(self):
        await asyncio.sleep(self.bot_delay_ms / 1000)
        actor = next((p for p in self.table.players if p.id == self.table.turn), None)
        if not actor or not actor.bot:
            return
        action, amount = bot_decision(self.table, actor)
        try:
            self.table.act(actor.id, action, amount)
        except ValueError:
            self.table.act(actor.id, "call")
        # Clear before broadcast so the next bot can be scheduled.
        self.bot_task = None
        await self.broadcast()

    async def remove_member(self, pid, reason=None):
        player = next((p for p in self.table.players if p.id == pid and not p.departed), None)
        if player is None:
            raise ValueError("玩家不在房间内")
        ws = self.sockets.pop(pid, None)
        if ws and not ws.closed:
            if reason:
                await ws.send_json({"type": "kicked", "reason": reason})
            await ws.close()
        for token, value in list(SESSIONS.items()):
            if value == (self.code, pid):
                del SESSIONS[token]
        if player.bot and self.bot_task and not self.bot_task.done():
            self.bot_task.cancel()
            self.bot_task = None
        self.table.remove(pid, "被房主移出房间" if reason else "离开房间")
        if pid == self.host:
            successor = next((p for p in self.table.players if not p.bot and not p.departed and p.connected), None)
            if successor is None:
                successor = next((p for p in self.table.players if not p.bot and not p.departed), None)
            if successor:
                self.host = successor.id
            else:
                ROOMS.pop(self.code, None)
                if self.bot_task:
                    self.bot_task.cancel()
                    self.bot_task = None
                return
        await self.broadcast()


def bot_decision(table, p):
    opt = table.options(p)
    if not opt:
        return "call", None
    call = opt["call"]
    hole = p.cards
    ranks = sorted([c[0] for c in hole], reverse=True)
    suited = hole[0][1] == hole[1][1]
    if table.phase == "preflop":
        strength = (0.67 if ranks[0] == ranks[1] else 0.12) + ranks[0] / 32 + ranks[1] / 55
        strength += 0.10 if suited else 0
        strength += 0.09 if ranks[0] - ranks[1] <= 2 else 0
    else:
        hand = score(hole + table.board)
        strength = [0.23, 0.49, 0.68, 0.77, 0.87, 0.91, 0.96, 0.99, 1.0][hand[0]]
        if hand[0] == 0:
            strength += (ranks[0] - 8) * 0.018
        suits = [c[1] for c in hole + table.board]
        if len(table.board) < 5 and max(suits.count(s) for s in "shdc") >= 4:
            strength += 0.13
    pot = max(10, sum(x.total_bet for x in table.players))
    pressure = call / (pot + call) if call else 0
    jitter = secrets.randbelow(21) / 100 - 0.1
    if call and strength + jitter < max(0.3, pressure + 0.14):
        return "fold", None
    if opt["canRaise"] and strength + jitter > (0.76 if call else 0.7):
        target = max(opt["minTo"], table.current_bet + max(10, (pot // 4 // 5) * 5))
        target = min(target, opt["maxTo"])
        if target >= opt["minTo"] or target == opt["maxTo"]:
            return "raise", target
    return "call", None


async def read_json(request):
    try:
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text="无效请求")


def fail(error):
    return web.json_response({"error": str(error)}, status=400)


async def create(request):
    try:
        name, buyin = validate_player(await read_json(request))
        player = Player(secrets.token_urlsafe(12), name, buyin, buyin)
        room_code = code()
        ROOMS[room_code] = Room(room_code, player)
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = (room_code, player.id)
        return web.json_response({"room": room_code, "token": token})
    except ValueError as error:
        return fail(error)


async def join(request):
    try:
        data = await read_json(request)
        name, buyin = validate_player(data)
        room_code = str(data.get("room", "")).strip().upper()
        room = ROOMS.get(room_code)
        if not room:
            raise ValueError("找不到该房间")
        player = Player(secrets.token_urlsafe(12), name, buyin, buyin)
        room.table.add(player)
        room.table.events.append(f"{player.name} 加入房间")
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = (room_code, player.id)
        await room.broadcast()
        return web.json_response({"room": room_code, "token": token})
    except ValueError as error:
        return fail(error)


async def leave_room(request):
    data = await read_json(request)
    session = SESSIONS.get(data.get("token"))
    if not session:
        raise web.HTTPUnauthorized(text="会话已失效")
    room = ROOMS.get(session[0])
    if not room:
        raise web.HTTPNotFound(text="房间已关闭")
    await room.remove_member(session[1])
    return web.json_response({"ok": True})


async def websocket(request):
    session = SESSIONS.get(request.query.get("token"))
    if not session:
        raise web.HTTPUnauthorized(text="登录已失效")
    room = ROOMS.get(session[0])
    if not room:
        raise web.HTTPNotFound(text="房间已关闭")
    pid = session[1]
    player = next((p for p in room.table.players if p.id == pid), None)
    if not player:
        raise web.HTTPUnauthorized(text="已离开房间")
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    old = room.sockets.get(pid)
    if old and not old.closed:
        await old.close()
    room.sockets[pid] = ws
    if not player.connected:
        room.table.events.append(f"{player.name} 重新连接")
    player.connected = True
    await room.broadcast()
    async for message in ws:
        if message.type != WSMsgType.TEXT:
            continue
        try:
            data = json.loads(message.data)
            if not isinstance(data, dict):
                raise ValueError("无效操作")
            kind = data.get("type")
            if kind == "action":
                room.table.act(pid, data.get("action"), data.get("amount"))
            elif kind == "chat":
                await room.send_chat(pid, data.get("text"))
                continue
            elif kind == "start":
                if pid != room.host:
                    raise ValueError("只有房主可以发牌")
                room.table.start()
            elif kind == "bot":
                if pid != room.host:
                    raise ValueError("只有房主可以添加电脑玩家")
                number = 1 + sum(p.bot for p in room.table.players)
                bot = Player(secrets.token_urlsafe(12), f"电脑玩家 {number}", 1000, 1000, bot=True)
                room.table.add(bot)
                room.table.events.append(f"{bot.name} 加入房间")
            elif kind == "bot_speed":
                if pid != room.host:
                    raise ValueError("只有房主可以调整电脑速度")
                delay = data.get("delayMs")
                if type(delay) is not int or delay < 200 or delay > 3000 or delay % 100:
                    raise ValueError("电脑速度参数无效")
                room.bot_delay_ms = delay
                if room.bot_task and not room.bot_task.done():
                    room.bot_task.cancel()
                    room.bot_task = None
            elif kind == "kick":
                if pid != room.host:
                    raise ValueError("只有房主可以踢出玩家")
                target = data.get("playerId")
                if target == pid:
                    raise ValueError("请使用退出房间按钮")
                victim = next((p for p in room.table.players if p.id == target and not p.departed), None)
                if victim is None:
                    raise ValueError("玩家不在房间内")
                await room.remove_member(target, "你已被房主移出房间")
                continue
            elif kind == "restart":
                if pid != room.host:
                    raise ValueError("只有房主可以重新开始游戏")
                # Reset the host and invalidate every other session.
                for other_id, other_ws in list(room.sockets.items()):
                    if other_id != pid and not other_ws.closed:
                        await other_ws.send_json({"type": "kicked", "reason": "房主重新开始了游戏，请重新加入"})
                        await other_ws.close()
                for token, value in list(SESSIONS.items()):
                    if value[0] == room.code and value[1] != pid:
                        del SESSIONS[token]
                previous_events = room.table.events[:]
                player.stack = player.buyin
                room.table = Table()
                room.table.add(player)
                room.table.events = previous_events + ["房主重新开始游戏"]
                room.sockets = {pid: ws}
                if room.bot_task:
                    room.bot_task.cancel()
                    room.bot_task = None
            else:
                raise ValueError("未知操作")
            await room.broadcast()
        except (ValueError, json.JSONDecodeError) as error:
            await ws.send_json({"type": "error", "message": str(error)})
    if room.sockets.get(pid) is ws:
        room.sockets.pop(pid, None)
        player.connected = False
        room.table.events.append(f"{player.name} 断开连接")
        if room.table.turn == pid:
            room.table.act(pid, "fold")
        await room.broadcast()
    return ws


async def index(_request):
    return web.FileResponse(ROOT / "index.html")


async def network(_request):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            ip = probe.getsockname()[0]
    except OSError:
        ip = socket.gethostbyname(socket.gethostname())
    return web.json_response({"ip": ip})


async def session_status(request):
    session = SESSIONS.get(request.query.get("token"))
    room = ROOMS.get(session[0]) if session else None
    if not room or not any(p.id == session[1] for p in room.table.players):
        raise web.HTTPUnauthorized()
    return web.json_response({"ok": True})


def app(auto_open=False):
    @web.middleware
    async def fresh_assets(request, handler):
        response = await handler(request)
        if request.path == "/" or request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    application = web.Application(client_max_size=1024 * 1024, middlewares=[fresh_assets])
    application.router.add_get("/", index)
    application.router.add_get("/ws", websocket)
    application.router.add_post("/api/create", create)
    application.router.add_post("/api/join", join)
    application.router.add_post("/api/leave", leave_room)
    application.router.add_get("/api/health", lambda _: web.json_response({"ok": True}))
    application.router.add_get("/api/network", network)
    application.router.add_get("/api/session", session_status)
    application.router.add_static("/static", ROOT / "static")
    async def open_host_page(_application):
        async def open_when_ready():
            await asyncio.sleep(0.5)
            webbrowser.open(f"http://localhost:8765/?fresh={time.time_ns()}")
        asyncio.create_task(open_when_ready())
    if auto_open:
        application.on_startup.append(open_host_page)
    return application


if __name__ == "__main__":
    web.run_app(app(auto_open=True), host="0.0.0.0", port=8765, print=lambda text: print(text, flush=True))
