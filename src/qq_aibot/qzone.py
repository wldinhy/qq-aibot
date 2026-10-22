"""Locally configured QZone jobs, durable deduplication, and bounded automation."""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

from .qzone_api import QZoneBridge, QZoneError


def timestamp(item: dict) -> float:
    for key in ("created_time", "create_time", "createtime", "timestamp", "abstime", "time", "createTime"):
        value = item.get(key)
        try:
            value = float(value)
            if value > 10**12:
                value /= 1000
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0


def text_content(item: dict) -> str:
    value = item.get("content", item.get("text", ""))
    if isinstance(value, str) and value:
        return value[:6000]
    if isinstance(item.get("conlist"), list):
        return "".join(str(part.get("con", part.get("text", ""))) for part in item["conlist"] if isinstance(part, dict))[:6000]
    return ""


def post_id(item: dict) -> str:
    return str(item.get("tid") or item.get("t1_tid") or item.get("id") or "")


def validate_settings(settings: dict) -> None:
    for key in ("enabled", "auto_comment", "auto_reply", "auto_post"):
        if not isinstance(settings.get(key), bool):
            raise ValueError(f"空间开关 {key} 必须为 true 或 false")
    for key, low, high in (("poll_seconds", 30, 3600), ("min_action_seconds", 30, 86400),
                          ("max_comments_per_day", 0, 100), ("max_posts_per_day", 0, 10),
                          ("max_comment_chars", 10, 500), ("max_post_chars", 20, 2000)):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"空间设置 {key} 应为 {low}–{high} 的整数")
    if not isinstance(settings.get("post_times"), list) or len(settings["post_times"]) > 10:
        raise ValueError("空间发布时间应为最多 10 个 HH:MM 时间")
    if any(not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in settings["post_times"]):
        raise ValueError("空间发布时间格式应为 HH:MM，例如 12:00")
    ids = settings.get("friend_ids")
    if not isinstance(ids, list) or any(isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0 for value in ids) or len(ids) > 50:
        raise ValueError("空间好友列表应为最多 50 个 QQ 号；留空不会评论任何好友")
    if settings.get("post_visibility") not in {"friends", "public", "private"}:
        raise ValueError("空间可见范围应为 friends、public 或 private")
    if not isinstance(settings.get("post_topic"), str) or len(settings["post_topic"]) > 3000:
        raise ValueError("空间主题必须是最多 3000 字符的文字")
    try:
        address = urlsplit(settings.get("bridge_url", ""))
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.username or address.password or address.query or address.fragment):
            raise ValueError
        address.port  # Validate the port before accepting terminal settings.
    except (ValueError, TypeError, AttributeError):
        raise ValueError("空间桥接地址应为有效的 HTTP 或 HTTPS 地址") from None


class QZoneStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs(
            account TEXT, job_key TEXT, kind TEXT, status TEXT, content TEXT DEFAULT '',
            created REAL, updated REAL, PRIMARY KEY(account,job_key))""")
        self.db.commit()

    def seen(self, account: str, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE account=? AND job_key=?", (account, key)).fetchone() is not None

    def reserve(self, account: str, key: str, kind: str, now: float, status: str = "pending") -> bool:
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO jobs(account,job_key,kind,status,created,updated) VALUES(?,?,?,?,?,?)",
                                     (account, key, kind, status, now, now))
        return cursor.rowcount == 1

    def finish(self, account: str, key: str, status: str, now: float, content: str = "") -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,updated=?,content=? WHERE account=? AND job_key=?", (status, now, content, account, key))

    def count(self, account: str, kind: str, since: float) -> int:
        return self.db.execute("SELECT COUNT(*) FROM jobs WHERE account=? AND kind=? AND created>=? AND status IN ('pending','sent','uncertain')",
                               (account, kind, since)).fetchone()[0]

    def last_action(self, account: str) -> float:
        return self.db.execute("SELECT COALESCE(MAX(updated),0) FROM jobs WHERE account=? AND status IN ('pending','sent','uncertain')", (account,)).fetchone()[0]

    def duplicate(self, account: str, text: str, since: float) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE account=? AND content=? AND created>=? AND status IN ('sent','uncertain')", (account, text, since)).fetchone() is not None

    def close(self):
        self.db.close()


class QZoneService:
    def __init__(self, root: Path, config_loader, rpc, llm, database: Path, provider_loader=None, bridge=None):
        self.root, self.config_loader, self.rpc, self.llm = root, config_loader, rpc, llm
        self.provider_loader = provider_loader
        self.store = QZoneStore(database)
        self.bridge = bridge
        self.activated = 0.0
        self.account = ""
        self.warmed: set[str] = set()
        self.active_signature = ""

    async def run(self):
        try:
            while True:
                delay = 30
                try:
                    config = self.config_loader()
                    settings = config["qzone"]
                    if settings.get("enabled"):
                        validate_settings(settings)
                        await self.tick(config, time.time())
                        delay = settings["poll_seconds"]
                    else:
                        self.activated = 0
                        self.active_signature = ""
                        self.warmed.clear()
                        if self.bridge:
                            await self.bridge.close()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Exception text can contain credentials in HTTP libraries.
                    detail = str(exc) if isinstance(exc, QZoneError) else type(exc).__name__
                    print(f"[QQ空间] 本轮暂停：{detail}")
                await asyncio.sleep(delay)
        finally:
            if self.bridge:
                await self.bridge.close()

    async def tick(self, config: dict, now: float):
        settings = config["qzone"]
        validate_settings(settings)
        if not settings.get("enabled"):
            return
        login = await self.rpc("get_login_info", {})
        account = str(login.get("user_id", ""))
        if not account.isdigit() or int(account) <= 0:
            raise QZoneError("NapCat 登录账号无效")
        expected = str(config["onebot"].get("qq_id") or "")
        if expected and expected != account:
            raise QZoneError("NapCat 登录账号与本地绑定不一致")
        signature = json.dumps([account, settings["auto_comment"], settings["auto_reply"], settings["auto_post"], settings["friend_ids"], settings["post_times"]], sort_keys=True)
        if not self.activated or signature != self.active_signature:
            self.activated = now
            self.warmed.clear()
            self.account, self.active_signature = account, signature
        midnight = datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if settings["auto_post"]:
            await self.post_due(config, now, midnight)
        if not (settings["auto_comment"] or settings["auto_reply"]):
            return
        if self.bridge is None:
            import os
            self.bridge = QZoneBridge(self.root, self.rpc, os.environ.get("ONEBOT_ACCESS_TOKEN", ""))
        await self.bridge.authenticate(settings["bridge_url"], account)
        if settings["auto_comment"]:
            for friend in settings["friend_ids"]:
                friend = str(friend)
                if friend == account or friend in {str(value) for value in config["bot"].get("blocked_users", [])}:
                    continue
                try:
                    await self.friend_posts(config, friend, now, midnight)
                except QZoneError:
                    print(f"[QQ空间] 好友 {friend} 的动态暂不可读，本轮跳过。")
        if settings["auto_reply"]:
            for post in (await self.bridge.posts(account))[:10]:
                try:
                    await self.own_comments(config, post, now, midnight)
                except QZoneError:
                    print("[QQ空间] 一条说说的评论暂不可读，本轮跳过。")

    def allowed(self, settings: dict, kind: str, now: float, midnight: float) -> bool:
        cap = settings["max_posts_per_day" if kind == "post" else "max_comments_per_day"]
        return (self.store.count(self.account, kind, midnight) < cap
                and now - self.store.last_action(self.account) >= settings["min_action_seconds"])

    async def compose(self, config: dict, kind: str, context: dict) -> str | None:
        if self.provider_loader:
            self.llm.profile = self.provider_loader()
        generation = dict(config["llm"])
        limit = config["qzone"]["max_post_chars" if kind == "post" else "max_comment_chars"]
        with self.llm.using_generation(generation):
            result = await self.llm.qzone_text(kind, context, limit)
        if result is not None and (not isinstance(result, str) or not result.strip() or len(result) > limit):
            raise QZoneError("空间文案超出限制，未执行发布")
        return result.strip() if result else None

    async def execute(self, key: str, kind: str, text: str, now: float, operation):
        current = self.config_loader()["qzone"]
        flag = "auto_post" if kind == "post" else ("auto_reply" if key.startswith("reply:") else "auto_comment")
        if not current.get("enabled") or not current.get(flag):
            return
        if key.startswith("friend:") and key.split(":", 2)[1] not in {str(value) for value in current["friend_ids"]}:
            return
        identity = await self.rpc("get_login_info", {})
        if str(identity.get("user_id")) != self.account:
            raise QZoneError("发送前登录账号发生变化，任务已停止")
        # Reserve durably before the network write. Ambiguous outcomes are never
        # retried on reconnect/restart; the owner can inspect the local job log.
        if not self.store.reserve(self.account, key, kind, now):
            return
        self.store.finish(self.account, key, "pending", now, text)
        try:
            await operation()
        except BaseException:
            self.store.finish(self.account, key, "uncertain", time.time(), text)
            raise
        self.store.finish(self.account, key, "sent", time.time(), text)
        print(f"[QQ空间] {kind} 已完成。")

    async def friend_posts(self, config: dict, friend: str, now: float, midnight: float):
        posts = await self.bridge.posts(friend)
        stream = "friend:" + friend
        warm = stream in self.warmed
        for post in sorted(posts, key=timestamp):
            tid = post_id(post)
            if not tid:
                continue
            key = f"friend:{friend}:{tid}"
            if self.store.seen(self.account, key):
                continue
            if not warm or timestamp(post) < self.activated or not timestamp(post):
                self.store.reserve(self.account, key, "comment", now, "baseline")
                continue
            if not self.allowed(config["qzone"], "comment", now, midnight):
                continue
            text = await self.compose(config, "comment", {"author_qq": friend, "post": text_content(post)})
            if not text:
                self.store.reserve(self.account, key, "comment", now, "skipped")
                continue
            await self.execute(key, "comment", text, now, lambda: self.bridge.comment(friend, tid, text))
        self.warmed.add(stream)

    async def own_comments(self, config: dict, post: dict, now: float, midnight: float):
        tid = post_id(post)
        if not tid:
            return
        comments = await self.bridge.comments(self.account, tid)
        stream = "comments:" + tid
        warm = stream in self.warmed
        for comment in sorted(comments, key=timestamp):
            author = str(comment.get("uin") or comment.get("user_id") or comment.get("comment_uin") or "")
            cid = str(comment.get("commentid") or comment.get("comment_id") or comment.get("id") or "")
            if not cid or not author.isdigit() or author == self.account or author in {str(value) for value in config["bot"].get("blocked_users", [])}:
                continue
            if comment.get("is_reply") and str(comment.get("reply_to_uin", "")) != self.account:
                continue
            fingerprint = hashlib.sha256(f"{author}:{timestamp(comment)}:{text_content(comment)}".encode()).hexdigest()[:20]
            key = f"reply:{tid}:{cid}:{fingerprint}"
            if self.store.seen(self.account, key):
                continue
            if not warm or timestamp(comment) < self.activated or not timestamp(comment):
                self.store.reserve(self.account, key, "comment", now, "baseline")
                continue
            if not self.allowed(config["qzone"], "comment", now, midnight):
                continue
            text = await self.compose(config, "reply", {"post": text_content(post), "comment_author_qq": author, "comment": text_content(comment)})
            if not text:
                self.store.reserve(self.account, key, "comment", now, "skipped")
                continue
            thread_id = str(comment.get("parent_comment_id") or cid)
            await self.execute(key, "comment", text, now, lambda: self.bridge.comment(self.account, tid, text, thread_id, author))
        self.warmed.add(stream)

    async def post_due(self, config: dict, now: float, midnight: float):
        settings = config["qzone"]
        moment = datetime.fromtimestamp(now).astimezone()
        for value in sorted(set(settings["post_times"])):
            hour, minute = map(int, value.split(":"))
            scheduled = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
            when = scheduled.timestamp()
            if when < self.activated or not 0 <= now - when <= 300:
                continue
            key = f"post:{scheduled.date().isoformat()}:{value}"
            if self.store.seen(self.account, key) or not self.allowed(settings, "post", now, midnight):
                continue
            text = await self.compose(config, "post", {"local_time": moment.isoformat(), "topic": settings["post_topic"]})
            if not text or self.store.duplicate(self.account, text, now - 7 * 86400):
                self.store.reserve(self.account, key, "post", now, "skipped")
                continue
            privacy = {"public": 1, "friends": 4, "private": 64}[settings["post_visibility"]]

            async def publish():
                result = await self.rpc("send_qzone_msg", {"content": text, "ugc_right": privacy})
                if not result.get("tid"):
                    raise QZoneError("空间发布结果未返回 tid")

            await self.execute(key, "post", text, now, publish)

    async def close(self):
        if self.bridge:
            await self.bridge.close()
        self.store.close()
