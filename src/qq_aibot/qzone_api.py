"""QZone read/comment bridge; publishing stays on NapCat's native action."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit

import aiohttp

from .installer import read_profile, system_name


class QZoneError(RuntimeError):
    pass


class QZoneBridge:
    def __init__(self, root: Path, rpc, token: str):
        self.root, self.rpc, self.token = root, rpc, token
        self.process = None
        self.log = None
        self.session = None
        self.auth_hash = ""
        self.base = ""
        self.account = ""
        self.auth_at = 0.0

    async def request(self, action: str, params: dict | None = None):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        try:
            async with self.session.post(self.base + "/" + action, json=params or {},
                                         headers={"Authorization": "Bearer " + self.token}, allow_redirects=False) as response:
                if response.status != 200:
                    raise QZoneError(f"空间桥接 HTTP {response.status}")
                payload = await response.json(content_type=None)
            if not isinstance(payload, dict) or payload.get("status") != "ok" or payload.get("retcode", 0) != 0:
                code = payload.get("retcode", "invalid") if isinstance(payload, dict) else "invalid"
                raise QZoneError(f"空间接口 {action} 失败（{code}）；检查 data/logs/qzone-bridge.log")
            return payload.get("data")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise QZoneError(f"空间接口 {action} 未能确认结果") from None

    async def authenticate(self, base: str, account: str) -> None:
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise QZoneError("空间桥接地址无效")
        if self.base and (base.rstrip("/") != self.base or self.account != account):
            await self.stop_process()
            self.auth_hash = ""
        self.base, self.account = base.rstrip("/"), account
        if self.process and self.process.returncode is not None:
            await self.stop_process()
        if self.auth_hash and time.monotonic() - self.auth_at < 600:
            return
        cookies = (await self.rpc("get_cookies", {"domain": "qzone.qq.com"})).get("cookies", "")
        if not isinstance(cookies, str) or not any(part.strip().startswith("p_skey=") and part.split("=", 1)[1].strip() for part in cookies.split(";")):
            raise QZoneError("NapCat 未提供有效的 QQ 空间 p_skey；空间任务暂停")
        digest = hashlib.sha256(cookies.encode()).hexdigest()
        local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        running = False
        if local:
            try:
                with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.2):
                    running = True
            except OSError:
                pass
            if not running:
                await self.start_process(cookies, account, parsed.port or 80)
        if self.process and self.process.returncode is not None:
            raise QZoneError("空间桥接已退出，检查 data/logs/qzone-bridge.log")
        login = await self.request("get_login_info")
        if not isinstance(login, dict) or str(login.get("user_id")) != account:
            raise QZoneError("空间桥接登录账号与 NapCat 不一致，未执行任何空间任务")
        if digest != self.auth_hash:
            await self.request("update_cookie", {"cookie_string": cookies})
        self.auth_hash, self.auth_at = digest, time.monotonic()

    async def start_process(self, cookies: str, account: str, port: int) -> None:
        await self.stop_process()
        profile = read_profile(self.root, system_name())
        directory = Path(profile.get("qzone_root", str(self.root / ".local/qzone-bridge")))
        node = profile.get("qzone_node")
        if not node or not (directory / "dist/main.js").is_file():
            raise QZoneError("评论功能需要安装空间组件：python install.py --with-qzone（Linux 用 python3）")
        logs = self.root / "data/logs"
        logs.mkdir(parents=True, exist_ok=True)
        self.log = (logs / "qzone-bridge.log").open("ab")
        environment = {**os.environ, "ONEBOT_HOST": "127.0.0.1", "ONEBOT_PORT": str(port),
                       "ONEBOT_ACCESS_TOKEN": self.token, "QZONE_COOKIE_STRING": cookies,
                       "QZONE_CACHE_PATH": str(self.root / ".local/qzone-state" / account),
                       "QZONE_ENABLE_QR": "0", "QZONE_STARTUP_WRITE_PROBE": "0",
                       "QZONE_SKIP_REFRESH_ON_START": "1", "QZONE_PLAYWRIGHT_HEADLESS": "1",
                       "ONEBOT_HTTP_POST_URLS": "", "ONEBOT_WS_REVERSE_URLS": "",
                       "ONEBOT_WS_REVERSE_API_URLS": "", "ONEBOT_WS_REVERSE_EVENT_URLS": ""}
        for kind in ("MESSAGE", "COMMENT", "LIKE", "FRIEND_FEED", "HEARTBEAT"):
            environment[f"ONEBOT_EMIT_{kind}_EVENTS"] = "0"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.process = await asyncio.create_subprocess_exec(node, str(directory / "dist/main.js"), cwd=directory,
                                                           env=environment, stdout=self.log, stderr=subprocess.STDOUT, **options)
        # No publishing probes: readiness is checked with a read-only endpoint.
        for _ in range(90):
            if self.process.returncode is not None:
                break
            try:
                await self.request("get_login_info")
                return
            except QZoneError:
                await asyncio.sleep(1)
        await self.stop_process()
        raise QZoneError("空间组件未完成登录，检查 data/logs/qzone-bridge.log")

    async def posts(self, owner: str) -> list[dict]:
        data = await self.request("get_emotion_list", {"user_id": owner, "num": 10, "max_pages": 1, "include_image_data": False})
        if not isinstance(data, dict) or not isinstance(data.get("msglist"), list):
            raise QZoneError("空间动态响应格式无效")
        return [item for item in data["msglist"] if isinstance(item, dict)]

    async def comments(self, owner: str, tid: str) -> list[dict]:
        data = await self.request("get_comment_list", {"user_id": owner, "tid": tid, "num": 30, "fast_mode": 1})
        if not isinstance(data, dict) or data.get("availability") == "not_embedded":
            raise QZoneError("这条说说的评论内容暂时不可读取，未将其视作空评论")
        items = data.get("comments", data.get("commentlist"))
        if not isinstance(items, list):
            raise QZoneError("空间评论响应格式无效")
        return [item for item in items if isinstance(item, dict)]

    async def comment(self, owner: str, tid: str, text: str, reply_id: str = "", reply_uin: str = ""):
        identity = await self.request("get_login_info")
        if not isinstance(identity, dict) or str(identity.get("user_id")) != self.account:
            raise QZoneError("评论前空间账号发生变化，未发送")
        params = {"target_uin": owner, "target_tid": tid, "content": text}
        if reply_id:
            params.update(reply_comment_id=reply_id, reply_uin=reply_uin)
        return await self.request("send_comment", params)

    async def stop_process(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None
        if self.log:
            self.log.close()
            self.log = None
        self.auth_hash = ""

    async def close(self) -> None:
        await self.stop_process()
        if self.session:
            await self.session.close()
            self.session = None
