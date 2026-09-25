"""Room protocol checks that can run with pytest or plain Python."""
import asyncio
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from poker import Player
from server import ROOMS, SESSIONS, Room, app


async def receive_type(ws, expected, predicate=lambda _message: True):
    for _ in range(30):
        message = await ws.receive_json(timeout=3)
        if message.get("type") == expected and predicate(message):
            return message
    raise AssertionError(f"未收到 {expected} 消息")


async def chat_and_log_flow():
    async with TestClient(TestServer(app())) as client:
        host = await (await client.post("/api/create", json={"name": "房主", "buyin": 100})).json()
        guest = await (await client.post("/api/join", json={
            "room": host["room"], "name": "玩家", "buyin": 100
        })).json()
        host_ws = await client.ws_connect("/ws?token=" + host["token"])
        guest_ws = await client.ws_connect("/ws?token=" + guest["token"])
        guest_state = (await receive_type(guest_ws, "state"))["state"]
        assert any("加入房间" in item["text"] for item in guest_state["events"])

        await guest_ws.send_json({"type": "chat", "text": "<b>你好</b>", "sender": "冒名者"})
        guest_chat = (await receive_type(guest_ws, "chat"))["message"]
        host_chat = (await receive_type(host_ws, "chat"))["message"]
        assert guest_chat == host_chat
        assert guest_chat["sender"] == "玩家"
        assert guest_chat["text"] == "<b>你好</b>"

        await guest_ws.send_json({"type": "chat", "text": "x" * 201})
        assert (await receive_type(guest_ws, "error"))["message"] == "聊天内容须为 1～200 个字符"
        assert len(ROOMS[host["room"]].chat_messages) == 1

        await host_ws.send_json({"type": "start"})
        first = (await receive_type(host_ws, "state", lambda m: m["state"]["handNo"] == 1))["state"]
        assert any("第 1 局开始" in item["text"] for item in first["events"])
        assert any("小盲" in item["text"] for item in first["events"])
        await host_ws.send_json({"type": "action", "action": "fold"})
        await receive_type(host_ws, "state", lambda m: m["state"]["phase"] == "complete")
        await host_ws.send_json({"type": "start"})
        second = (await receive_type(host_ws, "state", lambda m: m["state"]["handNo"] == 2))["state"]
        texts = [item["text"] for item in second["events"]]
        assert "第 1 局开始" in texts and "第 2 局开始" in texts
        assert any("弃牌" in text for text in texts)
        assert second["chat"][0]["text"] == "<b>你好</b>"
        await host_ws.close()
        await guest_ws.close()


def test_chat_and_full_room_log():
    try:
        asyncio.run(chat_and_log_flow())
    finally:
        ROOMS.clear()
        SESSIONS.clear()


def test_fresh_assets_and_host_url():
    async def check():
        with patch("server.webbrowser.open") as open_browser:
            async with TestClient(TestServer(app(auto_open=True))) as client:
                page = await client.get("/?fresh=test")
                assert page.status == 200
                assert "no-store" in page.headers["Cache-Control"]
                assert "chat-messages" in await page.text()
                script = await client.get("/static/app.js?v=20260925-rebuy-settle")
                assert "no-store" in script.headers["Cache-Control"]
                assert "appendChat" in await script.text()
                await asyncio.sleep(0.6)
            opened_url = open_browser.call_args.args[0]
            assert opened_url.startswith("http://localhost:8765/?fresh=")

    asyncio.run(check())


async def rebuy_return_and_settlement_flow():
    async with TestClient(TestServer(app())) as client:
        host = await (await client.post("/api/create", json={"name": "房主", "buyin": 100})).json()
        guest = await (await client.post("/api/join", json={
            "room": host["room"], "name": "玩家", "buyin": 100
        })).json()
        room = ROOMS[host["room"]]
        host_ws = await client.ws_connect("/ws?token=" + host["token"])
        guest_ws = await client.ws_connect("/ws?token=" + guest["token"])
        guest_id = SESSIONS[guest["token"]][1]
        player = room.members[guest_id]

        # A player with no chips can queue while a later hand is in progress.
        player.stack = 0
        room.table.phase = "preflop"
        player.in_hand = False
        await guest_ws.send_json({"type": "rebuy", "buyin": 50})
        queued = (await receive_type(guest_ws, "state", lambda m:
                  m["state"]["players"][1]["pendingBuyin"] == 50))["state"]
        assert queued["players"][1]["stack"] == 0
        assert not queued["canRebuy"]
        room.table.phase = "complete"
        room.table.turn = None
        await room.broadcast()
        ready = (await receive_type(host_ws, "state", lambda m: m["state"]["canStart"]))["state"]
        assert ready["canStart"]
        await host_ws.send_json({"type": "start"})
        started = (await receive_type(guest_ws, "state", lambda m:
                   m["state"]["handNo"] == 1))["state"]
        seat = next(p for p in started["players"] if p["id"] == guest_id)
        assert seat["inHand"] and seat["pendingBuyin"] == 0
        assert seat["buyin"] == 150

        # Leaving invalidates the session, but the return key restores identity and accounting.
        response = await client.post("/api/leave", json={"token": guest["token"]})
        assert response.status == 200
        assert guest["token"] not in SESSIONS
        settled_stack = player.stack
        duplicate = await client.post("/api/join", json={
            "room": host["room"], "name": "玩家", "buyin": 100
        })
        assert duplicate.status == 400
        invalid_key = await client.post("/api/join", json={
            "room": host["room"], "returnKey": "invalid"
        })
        assert invalid_key.status == 400
        restored = await (await client.post("/api/join", json={
            "room": host["room"], "returnKey": guest["returnKey"]
        })).json()
        assert SESSIONS[restored["token"]][1] == guest_id
        assert room.members[guest_id].name == "玩家"
        assert room.members[guest_id].stack == settled_stack
        assert room.members[guest_id].buyin == 150
        assert len(room.members) == 2
        returned_ws = await client.ws_connect("/ws?token=" + restored["token"])
        await receive_type(returned_ws, "state")

        # The room can only close after a hand; every connected player gets one ranking.
        room.table.phase = "preflop"
        await host_ws.send_json({"type": "settle"})
        assert "等待当前牌局结算" in (await receive_type(host_ws, "error"))["message"]
        room.table.phase = "complete"
        room.table.turn = None
        await host_ws.send_json({"type": "settle"})
        host_result = (await receive_type(host_ws, "settlement"))["ranking"]
        guest_result = (await receive_type(returned_ws, "settlement"))["ranking"]
        assert host_result == guest_result
        assert next(p for p in host_result if p["name"] == "玩家")["net"] == player.stack - 150
        assert host["room"] not in ROOMS
        assert not any(value[0] == host["room"] for value in SESSIONS.values())


def test_rebuy_return_and_settlement():
    try:
        asyncio.run(rebuy_return_and_settlement_flow())
    finally:
        ROOMS.clear()
        SESSIONS.clear()


def test_rebuy_after_hand_and_bot_restriction():
    host = Player("host", "房主", 100, 100)
    room = Room("ABCDEF", host)
    guest = Player("guest", "玩家", 0, 100)
    bot = Player("bot", "电脑玩家", 0, 100, bot=True)
    for player in (guest, bot):
        room.table.add(player)
        room.members[player.id] = player
    room.table.phase = "complete"
    room.rebuy(guest.id, 50)
    assert guest.stack == 50 and guest.buyin == 150 and guest.pending_buyin == 0
    try:
        room.rebuy(bot.id, 50)
    except ValueError as error:
        assert "不能重新入座" in str(error)
    else:
        raise AssertionError("电脑玩家不应能重新入座")
