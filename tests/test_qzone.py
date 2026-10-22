import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.qzone import QZoneService, QZoneStore, timestamp, validate_settings
from qq_aibot.qzone_api import QZoneError


class FakeLLM:
    profile = {}

    def __init__(self):
        self.contexts = []
        self.result = "这条说得很有意思"
        self.before_return = None

    @contextmanager
    def using_generation(self, generation):
        self.generation = generation
        yield

    async def qzone_text(self, kind, context, limit):
        self.contexts.append((kind, context, limit))
        if self.before_return:
            self.before_return()
        return self.result


class FakeBridge:
    def __init__(self):
        self.feed = {"100": [], "900": [{"tid": "my-post", "content": "自己的说说"}]}
        self.replies = {"my-post": []}
        self.sent = []
        self.owners = []
        self.authenticated = 0
        self.fail_write = False

    async def authenticate(self, *_):
        self.authenticated += 1

    async def posts(self, owner):
        self.owners.append(owner)
        return self.feed.get(owner, [])

    async def comments(self, owner, tid):
        return self.replies[tid]

    async def comment(self, owner, tid, text, reply_id="", reply_uin=""):
        self.sent.append((owner, tid, text, reply_id, reply_uin))
        if self.fail_write:
            raise asyncio.TimeoutError()

    async def close(self):
        pass


class QZoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["qzone"].update(enabled=True, auto_comment=True, auto_reply=True, friend_ids=[100],
                                     min_action_seconds=30, poll_seconds=30)
        self.bridge = FakeBridge()
        self.llm = FakeLLM()
        self.native = []
        self.login_id = 900
        self.now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0).timestamp()

        async def rpc(action, params):
            if action == "get_login_info":
                return {"user_id": self.login_id}
            if action == "send_qzone_msg":
                self.native.append(params)
                return {"tid": "published"}
            raise AssertionError(action)

        self.rpc = rpc
        self.service = QZoneService(self.root, lambda: self.config, rpc, self.llm,
                                    self.root / "qzone.db", bridge=self.bridge)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def tick(self, delta=0):
        now = self.now + delta
        with patch("qq_aibot.qzone.time.time", return_value=now):
            await self.service.tick(self.config, now)

    async def test_disabled_default_never_reads_or_writes_space(self):
        self.config["qzone"]["enabled"] = False
        await self.tick()
        self.assertEqual(self.bridge.authenticated, 0)
        self.assertEqual(self.native, [])
        self.assertEqual(self.bridge.sent, [])

    async def test_initial_posts_are_baselined_then_only_new_allowlisted_posts_are_commented(self):
        self.bridge.feed["100"] = [{"tid": "old", "content": "旧动态", "created_time": self.now - 100}]
        await self.tick()
        self.bridge.feed["100"].append({"tid": "new", "content": "明天去爬山", "created_time": self.now + 20})
        self.bridge.feed["200"] = [{"tid": "not-allowed", "content": "不在指定好友中", "created_time": self.now + 20}]
        await self.tick(31)
        await self.tick(70)
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertEqual(self.bridge.sent[0][:2], ("100", "new"))
        self.assertNotIn("200", self.bridge.owners)
        self.assertEqual(self.llm.contexts[0][1]["post"], "明天去爬山")

    async def test_new_comment_reply_uses_real_bridge_createtime_and_correct_parent(self):
        await self.tick()
        self.bridge.replies["my-post"] = [
            {"commentid": "bot", "uin": "900", "content": "自己的评论", "createtime": self.now + 20},
            {"commentid": "old", "uin": "101", "content": "旧评论", "createtime": self.now - 100},
            {"commentid": "new", "uin": "101", "content": "你说得对", "createtime": self.now + 20},
        ]
        await self.tick(31)
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertEqual(self.bridge.sent[0][0:2], ("900", "my-post"))
        self.assertEqual(self.bridge.sent[0][3:], ("new", "101"))
        self.assertEqual(self.llm.contexts[0][1]["comment"], "你说得对")

    async def test_subreply_targets_root_thread_and_only_replies_addressed_to_bot(self):
        await self.tick()
        self.bridge.replies["my-post"] = [
            {"commentid": "2_r_3_102", "parent_comment_id": "2", "is_reply": True, "reply_to_uin": "900",
             "uin": "102", "content": "我也是这么想的", "createtime": self.now + 20},
            {"commentid": "2_r_4_103", "parent_comment_id": "2", "is_reply": True, "reply_to_uin": "102",
             "uin": "103", "content": "我们私下聊", "createtime": self.now + 20},
        ]
        await self.tick(31)
        self.assertEqual(len(self.bridge.sent), 1)
        self.assertEqual(self.bridge.sent[0][3:], ("2", "102"))

    async def test_timeout_is_persisted_and_never_retried_after_restart(self):
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "ambiguous", "content": "新动态", "created_time": self.now + 20}]
        self.bridge.fail_write = True
        with self.assertRaises(asyncio.TimeoutError):
            await self.tick(31)
        status = self.service.store.db.execute("SELECT status FROM jobs WHERE job_key='friend:100:ambiguous'").fetchone()[0]
        self.assertEqual(status, "uncertain")
        self.bridge.fail_write = False
        await self.tick(70)
        await self.service.close()
        self.service = QZoneService(self.root, lambda: self.config, self.rpc, self.llm, self.root / "qzone.db", bridge=self.bridge)
        await self.tick(100)
        self.assertEqual(len(self.bridge.sent), 1)

    async def test_daily_limit_and_cooldown_apply_to_comments_and_replies_together(self):
        self.config["qzone"]["max_comments_per_day"] = 1
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "first", "content": "新动态", "created_time": self.now + 20}]
        self.bridge.replies["my-post"] = [{"commentid": "second", "uin": "101", "content": "新评论", "createtime": self.now + 20}]
        await self.tick(31)
        await self.tick(200)
        self.assertEqual(len(self.bridge.sent), 1)

    async def test_blocklist_and_missing_timestamp_are_not_replied_to(self):
        self.config["bot"]["blocked_users"] = ["101"]
        await self.tick()
        self.bridge.replies["my-post"] = [
            {"commentid": "blocked", "uin": "101", "content": "不应回复", "createtime": self.now + 20},
            {"commentid": "unknown-time", "uin": "102", "content": "无时间戳"},
        ]
        await self.tick(31)
        self.assertEqual(self.bridge.sent, [])

    async def test_owner_disabling_feature_during_generation_prevents_write(self):
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "new", "content": "新动态", "created_time": self.now + 20}]
        self.llm.before_return = lambda: self.config["qzone"].update(enabled=False)
        await self.tick(31)
        self.assertEqual(self.bridge.sent, [])

    async def test_changing_login_account_before_send_prevents_write(self):
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "new", "content": "新动态", "created_time": self.now + 20}]
        self.llm.before_return = lambda: setattr(self, "login_id", 901)
        await self.tick(31)
        self.assertEqual(self.bridge.sent, [])

    async def test_model_can_decide_silence_without_repeated_requests(self):
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "ad", "content": "广告", "created_time": self.now + 20}]
        self.llm.result = None
        await self.tick(31)
        await self.tick(100)
        self.assertEqual(len(self.llm.contexts), 1)
        self.assertEqual(self.bridge.sent, [])

    async def test_post_schedule_uses_native_privacy_and_no_chat_history_or_bridge(self):
        self.config["qzone"].update(auto_comment=False, auto_reply=False, auto_post=True, post_times=["10:01"])
        await self.tick()
        await self.tick(60)
        await self.tick(100)
        self.assertEqual(len(self.native), 1)
        self.assertEqual(self.native[0]["ugc_right"], 4)
        self.assertEqual(self.bridge.authenticated, 0)
        context = self.llm.contexts[0][1]
        self.assertEqual(set(context), {"local_time", "topic"})
        self.assertEqual(context["topic"], self.config["qzone"]["post_topic"])

    async def test_starting_after_scheduled_time_does_not_catch_up(self):
        self.config["qzone"].update(auto_comment=False, auto_reply=False, auto_post=True, post_times=["10:00"])
        await self.tick(10)
        await self.tick(90)
        self.assertEqual(self.native, [])

    async def test_daily_post_limit_and_duplicate_text_prevent_extra_posts(self):
        self.config["qzone"].update(auto_comment=False, auto_reply=False, auto_post=True, post_times=["10:01", "10:02"], max_posts_per_day=1)
        await self.tick()
        await self.tick(60)
        await self.tick(120)
        await self.tick(86400 + 60)
        self.assertEqual(len(self.native), 1)

    async def test_oversized_model_output_never_reaches_space(self):
        await self.tick()
        self.bridge.feed["100"] = [{"tid": "new", "content": "新动态", "created_time": self.now + 20}]
        self.llm.result = "x" * 1000
        await self.tick(31)
        self.assertEqual(self.bridge.sent, [])

    def test_settings_reject_bad_times_limits_and_string_switches(self):
        for change in ({"post_times": ["24:00"]}, {"poll_seconds": 0}, {"enabled": "false"}, {"friend_ids": ["abc"]},
                       {"bridge_url": "file:///tmp/bridge"}, {"bridge_url": "http://localhost:invalid"}):
            settings = {**self.config["qzone"], **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings(settings)

    def test_store_reservation_is_atomic_and_timestamp_milliseconds_are_supported(self):
        self.assertTrue(self.service.store.reserve("900", "unique", "post", self.now))
        self.assertFalse(self.service.store.reserve("900", "unique", "post", self.now))
        self.assertEqual(timestamp({"createtime": self.now * 1000}), self.now)
        self.assertEqual(timestamp({"created_time": "inf"}), 0)
        self.assertEqual(timestamp({"created_time": "nan"}), 0)
