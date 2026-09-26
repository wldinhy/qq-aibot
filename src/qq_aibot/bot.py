from __future__ import annotations

import asyncio
import errno
import json
import os
from collections import defaultdict
from typing import Any, Callable

from aiohttp import WSMsgType, web

from .llm import LLMClient, LLMError
from .memory import ConversationStore
from .onebot import IncomingMessage, parse_message, send_action


class BotApplication:
    def __init__(
        self,
        config: dict[str, Any],
        store: ConversationStore,
        llm: LLMClient,
        access_token: str,
        provider_loader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.bot_config = config["bot"]
        self.store = store
        self.llm = llm
        self.provider_loader = provider_loader
        self.access_token = access_token
        self.semaphore = asyncio.Semaphore(int(self.bot_config["max_concurrency"]))
        self.session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task[None]] = set()

    def _allowed(self, message: IncomingMessage) -> bool:
        expected_qq = int(self.config["onebot"].get("qq_id", 0))
        if message.user_id == message.self_id or (expected_qq and message.self_id != expected_qq):
            return False
        blocked_users = {str(item) for item in self.bot_config["blocked_users"]}
        allowed_users = {str(item) for item in self.bot_config["allowed_users"]}
        if str(message.user_id) in blocked_users:
            return False
        if allowed_users and str(message.user_id) not in allowed_users:
            return False
        if message.message_type == "private":
            return bool(self.bot_config["private_reply"])
        blocked_groups = {str(item) for item in self.bot_config["blocked_groups"]}
        allowed_groups = {str(item) for item in self.bot_config["allowed_groups"]}
        group_id = str(message.group_id)
        if group_id in blocked_groups or (allowed_groups and group_id not in allowed_groups):
            return False
        mode = self.bot_config["group_mode"]
        return mode == "always" or (mode == "mention" and message.mentioned)

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        auth = request.headers.get("Authorization", "")
        query_token = request.query.get("access_token", "")
        if self.access_token and auth != f"Bearer {self.access_token}" and query_token != self.access_token:
            raise web.HTTPUnauthorized(text="invalid OneBot access token")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        peer = request.remote or "unknown"
        print(f"[OneBot] 已连接：{peer}")
        try:
            async for frame in ws:
                if frame.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(frame.data)
                except json.JSONDecodeError:
                    continue
                incoming = parse_message(payload)
                if incoming and self.config["onebot"].get("qq_id") and incoming.self_id != int(self.config["onebot"]["qq_id"]):
                    print(f"[OneBot] 忽略 QQ {incoming.self_id} 的消息；当前绑定 QQ {self.config['onebot']['qq_id']}")
                if incoming and self._allowed(incoming) and incoming.text:
                    task = asyncio.create_task(self._handle(incoming, ws))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        finally:
            print(f"[OneBot] 已断开：{peer}")
        return ws

    async def _handle(self, message: IncomingMessage, ws: web.WebSocketResponse) -> None:
        async with self.semaphore, self.session_locks[message.session_id]:
            if message.text.lower() in {"/reset", "/clear", "/清空"}:
                self.store.clear(message.session_id)
                await ws.send_json(send_action(message, "这段对话的记忆已清空。"))
                return
            if message.text.lower() in {"/help", "/帮助"}:
                await ws.send_json(
                    send_action(message, "直接发消息即可聊天；发送 /reset 可清空当前对话记忆。")
                )
                return
            max_input = int(self.bot_config["max_input_chars"])
            user_text = message.text[:max_input]
            if message.message_type == "group":
                user_text = f"{message.nickname}（QQ {message.user_id}）说：{user_text}"
            history_limit = int(self.bot_config["history_messages"])
            history = self.store.history(message.session_id, history_limit)
            history.append({"role": "user", "content": user_text})
            try:
                if self.provider_loader is not None:
                    self.llm.profile = self.provider_loader()
                reply = await self.llm.complete(history)
            except LLMError as exc:
                print(f"[模型服务] {exc}")
                await ws.send_json(send_action(message, "我暂时无法连接 AI 服务，请稍后再试。"))
                return
            reply = reply[: int(self.bot_config["max_reply_chars"])]
            keep = max(2, history_limit)
            self.store.add(message.session_id, "user", user_text, keep)
            self.store.add(message.session_id, "assistant", reply, keep)
            if not ws.closed:
                await ws.send_json(send_action(message, reply))
            print(f"[{message.session_id}] {message.nickname}: {message.text[:80]}")


async def run_bot(
    config: dict[str, Any],
    store: ConversationStore,
    provider: dict[str, Any],
    provider_loader: Callable[[], dict[str, Any]] | None = None,
) -> None:
    access_token = os.environ["ONEBOT_ACCESS_TOKEN"]
    async with LLMClient(provider, config["llm"]) as client:
        bot = BotApplication(config, store, client, access_token, provider_loader)
        app = web.Application()
        path = config["onebot"]["path"]
        app.router.add_get(path, bot.websocket)
        runner = web.AppRunner(app)
        await runner.setup()
        host = config["onebot"]["host"]
        port = int(config["onebot"]["port"])
        site = web.TCPSite(runner, host, port)
        try:
            await site.start()
        except OSError as exc:
            await runner.cleanup()
            if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
                raise ValueError(
                    f"监听端口 {host}:{port} 已被占用。机器人可能已在另一个终端运行；"
                    "请先停止旧实例，再启动新的实例。"
                ) from exc
            raise
        print(f"QQ AI Bot 已启动：ws://{host}:{port}{path}")
        print("按 Ctrl+C 停止。")
        try:
            await asyncio.Event().wait()
        finally:
            for task in bot.tasks:
                task.cancel()
            await runner.cleanup()
