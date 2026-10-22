from __future__ import annotations

import asyncio
import errno
import json
import os
import time
import uuid
import base64
from pathlib import Path
from collections import defaultdict, OrderedDict
from typing import Any, Callable
from contextlib import nullcontext
from dataclasses import replace

from aiohttp import WSMsgType, web

from .llm import LLMClient, LLMError, _is_persona_override_attempt
from .image_generation import ComfyImageGenerator, ImageGenerationError, image_segment
from .memory import ConversationStore, persona_key
from .config import load_config
from .context import GroupContext
from .media import MediaError, load_image, vision_part
from .onebot import IncomingMessage, parse_message, send_action
from .queue import RequestQueue
from .stickers import StickerLibrary, StickerError, sticker_segment


class BotApplication:
    def __init__(
        self,
        config: dict[str, Any],
        store: ConversationStore,
        llm: LLMClient,
        access_token: str,
        provider_loader: Callable[[], dict[str, Any]] | None = None,
        configuration_loader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.bot_config = config["bot"]
        self.store = store
        self.llm = llm
        self.provider_loader = provider_loader
        self.configuration_loader = configuration_loader
        self.access_token = access_token
        self.semaphore = asyncio.Semaphore(int(self.bot_config["max_concurrency"]))
        self.session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task[None]] = set()
        mode = self.bot_config.get("processing_mode", "parallel")
        if mode not in {"serial", "parallel"}:
            raise ValueError("bot.processing_mode 必须是 serial 或 parallel")
        workers = 1 if mode == "serial" else int(self.bot_config["max_concurrency"])
        self.queue = RequestQueue(self._run_request, workers, self.tasks)
        self.started_at = int(time.time())
        self.seen_messages: OrderedDict[str, None] = OrderedDict()
        self.group_context = GroupContext()
        self.pending_actions: dict[str, tuple[web.WebSocketResponse, asyncio.Future[dict[str, Any]]]] = {}
        self.active_ws: web.WebSocketResponse | None = None
        image_config = config.get("image_generation", {})
        self.image_generator = ComfyImageGenerator(image_config) if image_config.get("enabled") else None
        sticker_config = config.get("stickers", {})
        self.sticker_library: StickerLibrary | None = None
        if sticker_config.get("enabled", True):
            try:
                from .config import project_root
                manifest_path = sticker_config.get("manifest") or str(Path(__file__).parent / "stickers" / "manifest.json")
                if not os.path.isabs(manifest_path):
                    manifest_path = project_root() / manifest_path
                else:
                    manifest_path = os.path.normpath(manifest_path)
                self.sticker_library = StickerLibrary(manifest_path, int(sticker_config.get("max_file_bytes", 2097152)))
            except (StickerError, ValueError) as exc:
                print(f"[表情包] 初始化失败，已禁用：{exc}")
                self.sticker_library = None

    async def _run_request(self, job: tuple[IncomingMessage, web.WebSocketResponse]) -> None:
        message, ws = job
        try:
            await self._handle(message, ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[请求处理失败] {message.session_id}: {type(exc).__name__}: {exc}")
            if not ws.closed:
                try:
                    await ws.send_json(send_action(message, "这条请求处理失败了，后面的请求会继续处理，请稍后重试。"))
                except Exception:
                    pass

    def _fresh(self, message: IncomingMessage) -> bool:
        if self.bot_config.get("ignore_before_start", True) and message.timestamp < self.started_at:
            return False
        if message.message_id:
            key = f"{message.self_id}:{message.session_id}:{message.message_id}"
            if key in self.seen_messages:
                return False
            self.seen_messages[key] = None
            if len(self.seen_messages) > 4096:
                self.seen_messages.popitem(last=False)
        return True

    async def _get_image(self, ws: web.WebSocketResponse, file_id: str) -> dict[str, Any]:
        echo = "image:" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_actions[echo] = (ws, future)
        try:
            await ws.send_json({"action": "get_image", "params": {"file": file_id}, "echo": echo})
            payload = await asyncio.wait_for(future, timeout=45)
            if payload.get("retcode", 0) != 0 or payload.get("status") == "failed" or not isinstance(payload.get("data"), dict):
                raise MediaError("NapCat 无法下载这张图片")
            return payload["data"]
        finally:
            self.pending_actions.pop(echo, None)

    async def call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        ws = self.active_ws
        if ws is None or ws.closed:
            raise RuntimeError("NapCat 尚未连接")
        echo = "qzone:" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_actions[echo] = (ws, future)
        try:
            await ws.send_json({"action": action, "params": params, "echo": echo})
            payload = await asyncio.wait_for(future, timeout=45)
            if payload.get("retcode", 0) != 0 or payload.get("status") == "failed":
                # Do not propagate arbitrary server text which can contain cookies.
                raise RuntimeError(f"NapCat {action} 失败（{payload.get('retcode', 'unknown')}）")
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        finally:
            self.pending_actions.pop(echo, None)

    def _allowed(self, message: IncomingMessage, *, for_context: bool = False) -> bool:
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
        if for_context:
            return mode != "off"
        return mode == "always" or (mode == "mention" and message.mentioned)

    def _observe(self, message: IncomingMessage) -> IncomingMessage:
        if message.message_type != "group" or message.context_sequence:
            return message
        content = f"{message.nickname}（QQ {message.user_id}）说：{message.text[:4000]}"
        if message.reply_to:
            quote = self.group_context.quote(message.session_id, message.reply_to)
            content += f"\n[引用消息：{quote or '未收到被引用消息，无法确定其内容'}]"
        sequence = self.group_context.add(message.session_id, "user", content, message_id=message.message_id)
        return replace(message, context_sequence=sequence)

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        auth = request.headers.get("Authorization", "")
        query_token = request.query.get("access_token", "")
        if self.access_token and auth != f"Bearer {self.access_token}" and query_token != self.access_token:
            raise web.HTTPUnauthorized(text="invalid OneBot access token")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.active_ws = ws
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
                if not isinstance(payload, dict):
                    continue
                echo = payload.get("echo")
                pending = self.pending_actions.get(echo) if isinstance(echo, str) else None
                if pending and pending[0] is ws and "post_type" not in payload:
                    if not pending[1].done():
                        pending[1].set_result(payload)
                    continue
                incoming = parse_message(payload)
                if incoming and self.config["onebot"].get("qq_id") and incoming.self_id != int(self.config["onebot"]["qq_id"]):
                    print(f"[OneBot] 忽略 QQ {incoming.self_id} 的消息；当前绑定 QQ {self.config['onebot']['qq_id']}")
                if incoming and self.configuration_loader:
                    current = self.configuration_loader()
                    self.bot_config = current["bot"]
                if incoming and incoming.text and self._allowed(incoming, for_context=True) and self._fresh(incoming):
                    if self.bot_config.get("group_context", True):
                        incoming = self._observe(incoming)
                    if not self._allowed(incoming):
                        continue
                    waiting = self.queue.submit(incoming.session_id, (incoming, ws))
                    if waiting and self.bot_config.get("queue_notice", True):
                        await ws.send_json(send_action(incoming, "请求已收到，正在排队，会按顺序处理。"))
        finally:
            if self.active_ws is ws:
                self.active_ws = None
            for owner, future in list(self.pending_actions.values()):
                if owner is ws and not future.done():
                    future.set_exception(MediaError("QQ 连接已断开"))
            print(f"[OneBot] 已断开：{peer}")
        return ws

    async def _handle(self, message: IncomingMessage, ws: web.WebSocketResponse) -> None:
        async with self.session_locks[message.session_id], self.semaphore:
            settings = self.configuration_loader() if self.configuration_loader else self.config
            generation = dict(settings["llm"])
            for key in ("max_reply_messages", "max_reply_chars_per_message", "max_reply_chars", "max_reply_stickers"):
                generation[key] = settings["bot"].get(key)
            revision = persona_key(generation["system_prompt"])
            context = self.llm.using_generation(generation) if hasattr(self.llm, "using_generation") else nullcontext()
            with context:
                await self._handle_current(message, ws, revision, settings)

    async def _handle_current(self, message: IncomingMessage, ws: web.WebSocketResponse, revision: str, settings: dict[str, Any]) -> None:
        bot_config = settings["bot"]
        if message.text.lower() in {"/reset", "/clear", "/清空"}:
            self.store.clear(message.session_id)
            self.group_context.clear(message.session_id)
            await ws.send_json(send_action(message, "这段对话的记忆已清空。"))
            return
        if message.text.lower() in {"/help", "/帮助"}:
            help_text = "直接发消息即可聊天；发送 /reset 可清空当前对话记忆。"
            if self.image_generator:
                help_text += "也可以让我画图；修改图片时，请把原图和修改要求放在同一条消息中。"
            await ws.send_json(send_action(message, help_text))
            return
        max_input = int(bot_config["max_input_chars"])
        user_text = message.text[:max_input]
        if _is_persona_override_attempt(user_text):
            await ws.send_json(send_action(message, "这个设定得由管理员在本地改。"))
            return
        if message.message_type == "group":
            user_text = f"{message.nickname}（QQ {message.user_id}）说：{user_text}"
        history_limit = max(2, min(100, int(bot_config["history_messages"])))
        shared_context = message.message_type == "group" and bot_config.get("group_context", True)
        if shared_context:
            message = self._observe(message)
            history = self.group_context.history(message.session_id, message.context_sequence, history_limit, revision)
            if message.reply_to:
                quote = self.group_context.quote(message.session_id, message.reply_to)
                user_text += f"\n[引用消息：{quote or '未收到被引用消息，无法确定其内容'}]"
        else:
            history = self.store.history(message.session_id, history_limit, persona=revision)
        vision_config = settings.get("vision", {})
        loaded_images: list[bytes] = []
        reference_image: bytes | None = None
        media_errors = 0
        if message.images and vision_config.get("enabled", True):
            from .config import DEFAULT_CONFIG
            settings = {**DEFAULT_CONFIG["vision"], **vision_config}
            for index, source in enumerate(message.images[:int(settings["max_images"])]):
                file_id = message.image_files[index] if index < len(message.image_files) else ""
                try:
                    data = await load_image(source, file_id, settings, lambda file: self._get_image(ws, file))
                    loaded_images.append(data)
                    if index == 0:
                        reference_image = data
                except MediaError as exc:
                    media_errors += 1
                    print(f"[QQ 图片] {exc}")
            if media_errors:
                user_text += f"\n[有 {media_errors} 张图片下载失败：尚未看到这些图片，不要猜测其内容。]"
        content: Any = user_text
        if loaded_images:
            content = [{"type": "text", "text": user_text}, *[vision_part(data) for data in loaded_images]]
        history.append({"role": "user", "content": content})
        try:
            if self.provider_loader is not None:
                self.llm.profile = self.provider_loader()
            stickers_enabled = settings.get("stickers", {}).get("enabled", True)
            sticker_ids = [s.id for s in self.sticker_library.list_all()] if self.sticker_library and stickers_enabled else []
            has_image_gen = self.image_generator is not None and settings.get("image_generation", {}).get("enabled", False)
            if hasattr(self.llm, "reply_plan"):
                catalog = [{"id": item.id, "caption": item.caption, "tags": item.tags} for item in self.sticker_library.list_all()] if self.sticker_library and stickers_enabled else []
                plan = await self.llm.reply_plan(history, sticker_ids, bool(message.images), allow_generation=has_image_gen, sticker_catalog=catalog)
            elif self.image_generator:
                reply, request = await self.llm.complete_with_image(history, bool(message.images))
                plan = {"type": "image", **request} if request else {"type": "text", "messages": [reply]}
            else:
                reply = await self.llm.complete(history)
                plan = {"type": "text", "messages": [reply]}
        except LLMError as exc:
            print(f"[模型服务] {exc}")
            await ws.send_json(send_action(message, "我暂时无法连接 AI 服务，请稍后再试。"))
            return
        if plan.get("type") == "image" and self.image_generator:
            if plan["mode"] == "image_to_image" and not message.images:
                await ws.send_json(send_action(message, "请把原图和修改要求放在同一条消息中。"))
                return
            if plan["mode"] == "image_to_image" and vision_config.get("enabled", True) and reference_image is None:
                await ws.send_json(send_action(message, "原图下载失败，请重新发送第一张原图。"))
                return
            if ws.closed:
                return
            await ws.send_json(send_action(message, "正在用本地模型生成图片，请稍等。"))
            try:
                optimized: dict[str, Any] = {"mode": plan["mode"], "prompt": plan["prompt"]}
                if settings["image_generation"].get("optimize_prompt", True):
                    try:
                        optimizer_tokens = int(settings["image_generation"].get("optimizer_max_tokens", 1000))
                        optimized = await self.llm.optimize_image_prompt(history, optimized, optimizer_tokens)
                    except LLMError as exc:
                        print(f"[提示词优化] 优化失败，使用原提示词：{exc}")
                source = message.images[0] if message.images else None
                if reference_image is not None:
                    source = "base64://" + base64.b64encode(reference_image).decode("ascii")
                options = {key: optimized[key] for key in ("negative_prompt", "denoise") if key in optimized}
                data = await self.image_generator.generate(plan["mode"], optimized["prompt"], source, **options)
            except ImageGenerationError as exc:
                print(f"[本地生图] {exc}")
                if not ws.closed:
                    await ws.send_json(send_action(message, f"图片生成失败：{exc}"))
                return
            image_delivered = not ws.closed
            if image_delivered:
                await ws.send_json(send_action(message, image_segment(data)))
            keep = max(2, history_limit)
            self.store.add(message.session_id, "user", user_text, keep, persona=revision)
            self.store.add(message.session_id, "assistant", "已生成图片。图像提示词：" + optimized["prompt"], keep, persona=revision)
            if shared_context and image_delivered:
                self.group_context.add(message.session_id, "assistant", "[已发送生成的图片]", persona=revision)
            return
        max_messages = max(1, min(3, int(bot_config.get("max_reply_messages", 3))))
        max_chars_per = max(1, min(280, int(bot_config.get("max_reply_chars_per_message", 280))))
        max_total_chars = max(1, min(840, int(bot_config.get("max_reply_chars", 840))))
        max_stickers = max(0, min(1, int(bot_config.get("max_reply_stickers", 1)))) if stickers_enabled else 0
        messages = plan.get("messages", [])
        sticker_id = plan.get("sticker_id", "")
        if not isinstance(messages, list):
            messages = []
        messages = [str(m).strip() for m in messages if isinstance(m, str) and str(m).strip()]
        messages = messages[:max_messages]
        messages = [m[:max_chars_per] for m in messages]
        total_chars = sum(len(m) for m in messages)
        if total_chars > max_total_chars:
            messages = [m[:max_total_chars // max(1, len(messages))] for m in messages]
        if not isinstance(sticker_id, str):
            sticker_id = ""
        sticker_id = sticker_id.strip()
        if sticker_id and max_stickers < 1:
            sticker_id = ""
        sticker = self.sticker_library.get(sticker_id) if self.sticker_library and max_stickers else None
        if not sticker:
            sticker_id = ""
        if not messages and not sticker_id:
            if plan.get("silence") is True:
                return
            messages = ["这次回复没有正确生成，请再试一次。"]
        delivered: list[str] = []
        keep = max(2, history_limit)
        if ws.closed:
            return
        try:
            for msg_text in messages:
                if ws.closed:
                    break
                await ws.send_json(send_action(message, msg_text))
                delivered.append(msg_text)
            if sticker_id and self.sticker_library and not ws.closed:
                try:
                    base64_data = self.sticker_library.encode_base64(sticker_id)
                    await ws.send_json(send_action(message, sticker_segment(base64_data, sticker.caption)))
                    delivered.append(f"[表情包：{sticker.caption}]")
                except StickerError as exc:
                    print(f"[表情包] 发送失败：{exc}")
                    if not delivered:
                        fallback = "这张表情包暂时不可用，请换一张。"
                        await ws.send_json(send_action(message, fallback))
                        delivered.append(fallback)
        finally:
            if delivered:
                self.store.add(message.session_id, "user", user_text, keep, persona=revision)
                self.store.add(message.session_id, "assistant", "\n".join(delivered), keep, persona=revision)
                if shared_context:
                    self.group_context.add(message.session_id, "assistant", "\n".join(delivered), persona=revision)
        print(f"[{message.session_id}] {message.nickname}: {message.text[:80]}")

async def run_bot(
    config: dict[str, Any],
    store: ConversationStore,
    provider: dict[str, Any],
    provider_loader: Callable[[], dict[str, Any]] | None = None,
) -> None:
    access_token = os.environ["ONEBOT_ACCESS_TOKEN"]
    async with LLMClient(provider, config["llm"]) as client:
        bot = BotApplication(config, store, client, access_token, provider_loader, configuration_loader=load_config)
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
        print("=" * 58)
        print(f"模型：{provider.get('model', '未配置')} | 队列：{config['bot'].get('processing_mode', 'parallel')} | 并发：{config['bot']['max_concurrency']}")
        print(f"看图：{'开启' if config.get('vision', {}).get('enabled', True) else '关闭'} | 生图：{'开启' if bot.image_generator else '关闭'} | 表情包：{len(bot.sticker_library.stickers) if bot.sticker_library else 0} 张")
        print(f"回复：最多 {min(3, config['bot'].get('max_reply_messages', 3))} 条文字 + 1 张表情 | Token：{config['llm']['max_tokens']}")
        print("身份设定：仅本地配置可修改 | 启动前旧消息：忽略")
        print("本地性格：每条消息自动更新 | 不同设定的上下文分开保留")
        print("=" * 58)
        print("按 Ctrl+C 停止。")
        from .qzone import QZoneService
        from .config import data_dir, project_root
        qzone = QZoneService(project_root(), load_config, bot.call_action, client, data_dir() / "qzone.db", provider_loader)
        qzone_task = asyncio.create_task(qzone.run())
        try:
            await asyncio.Event().wait()
        finally:
            qzone_task.cancel()
            await asyncio.gather(qzone_task, return_exceptions=True)
            await qzone.close()
            for task in bot.tasks:
                task.cancel()
            await runner.cleanup()
