"""Room protocol checks that can run with pytest or plain Python."""
import asyncio
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from server import ROOMS, SESSIONS, app


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
                script = await client.get("/static/app.js?v=20260925-chat-log")
                assert "no-store" in script.headers["Cache-Control"]
                assert "appendChat" in await script.text()
                await asyncio.sleep(0.6)
            opened_url = open_browser.call_args.args[0]
            assert opened_url.startswith("http://localhost:8765/?fresh=")

    asyncio.run(check())
