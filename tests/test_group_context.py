import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import aiohttp
from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.context import GroupContext
from qq_aibot.memory import ConversationStore, persona_key
from qq_aibot.onebot import parse_message
from test_bot import incoming, FakeWebSocket


class CapturingLLM:
    profile = {}

    def __init__(self):
        self.received = []

    async def complete(self, history):
        self.received.append(history)
        return "明白了"


class GroupContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.directory.name) / "chat.db")
        self.llm = CapturingLLM()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["stickers"]["enabled"] = False
        self.bot = BotApplication(self.config, self.store, self.llm, "token")
        app = web.Application()
        app.router.add_get("/ws", self.bot.websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"

    async def asyncTearDown(self):
        for task in list(self.bot.tasks):
            task.cancel()
        await asyncio.gather(*self.bot.tasks, return_exceptions=True)
        await self.runner.cleanup()
        self.store.close()
        self.directory.cleanup()

    def event(self, ident, text, *, user=100, group=200, mention=False, stamp=None, quote=""):
        parts = []
        if quote:
            parts.append({"type": "reply", "data": {"id": quote}})
        if mention:
            parts.append({"type": "at", "data": {"qq": 999}})
        parts.append({"type": "text", "data": {"text": text}})
        return {"post_type": "message", "message_type": "group", "self_id": 999,
                "user_id": user, "group_id": group, "message_id": ident,
                "time": self.bot.started_at if stamp is None else stamp,
                "sender": {"nickname": f"成员{user}"}, "message": parts}

    async def test_unmentioned_group_chat_is_context_without_triggering_a_reply(self):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url, headers={"Authorization": "Bearer token"}) as ws:
                await ws.send_json(self.event("old", "旧消息不应出现", stamp=self.bot.started_at - 10))
                await ws.send_json(self.event("other", "隔壁群的秘密", group=201))
                await ws.send_json(self.event("first", "明天我们去打羽毛球", user=101))
                with self.assertRaises(asyncio.TimeoutError):
                    await ws.receive_json(timeout=0.05)
                await ws.send_json(self.event("second", "你觉得几点合适？", user=102, mention=True, quote="first"))
                response = await ws.receive_json(timeout=2)
                self.assertEqual(response["params"]["message"], "明白了")
        history = self.llm.received[0]
        transcript = str(history)
        self.assertIn("明天我们去打羽毛球", transcript)
        self.assertIn("QQ 101", transcript)
        self.assertIn("QQ 102", transcript)
        self.assertIn("引用消息", history[-1]["content"])
        self.assertNotIn("隔壁群的秘密", transcript)
        self.assertNotIn("旧消息不应出现", transcript)
        self.assertEqual(len(self.llm.received), 1)

    async def test_blocked_user_is_excluded_from_observed_context(self):
        self.bot.bot_config["blocked_users"] = [101]
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url, headers={"Authorization": "Bearer token"}) as ws:
                await ws.send_json(self.event("blocked", "被屏蔽的消息", user=101))
                await ws.send_json(self.event("request", "讨论什么呢", mention=True))
                await ws.receive_json(timeout=2)
        self.assertNotIn("被屏蔽的消息", str(self.llm.received))

    async def test_queued_request_uses_its_own_position_in_real_conversation(self):
        first = self.bot._observe(replace(incoming("group", True, group_id=200), text="第一条问题"))
        self.bot._observe(replace(incoming("group", True, group_id=200), text="后来的另一条问题"))
        await self.bot._handle(first, FakeWebSocket())
        self.assertNotIn("后来的另一条问题", str(self.llm.received[0]))
        self.assertEqual(self.llm.received[0][-1]["content"], "测试用户（QQ 100）说：第一条问题")

    def test_persona_change_keeps_peoples_words_and_excludes_old_bot_persona(self):
        context = self.bot.group_context
        context.add("group:200", "user", "大家说周六去看展")
        context.add("group:200", "assistant", "旧性格的回复", persona="old")
        cursor = context.add("group:200", "user", "你觉得呢")
        history = context.history("group:200", cursor, 20, "new")
        self.assertEqual(history, [{"role": "user", "content": "大家说周六去看展"}])

    async def test_reply_limits_are_hot_reloaded_in_running_bot(self):
        current = deepcopy(self.config)
        current["bot"]["max_reply_chars_per_message"] = 2
        self.bot.configuration_loader = lambda: current
        ws = FakeWebSocket()
        await self.bot._handle(incoming(), ws)
        self.assertEqual(ws.sent[0]["params"]["message"], "明白")

    def test_context_has_bounded_sessions_and_events(self):
        context = GroupContext(max_sessions=2, max_events=2)
        for session in ("a", "b", "c"):
            for index in range(3):
                context.add(session, "user", str(index))
        self.assertEqual(list(context.sessions), ["b", "c"])
        self.assertEqual(len(context.sessions["c"]), 2)
