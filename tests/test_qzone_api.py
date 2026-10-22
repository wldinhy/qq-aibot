from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.llm import LLMClient, LLMError
from qq_aibot.qzone_api import QZoneBridge, QZoneError


class QZoneApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.requests = []
        self.bridge_account = "900"
        self.comment_availability = "available"

        async def api(request):
            action = request.match_info["action"]
            body = await request.json()
            self.requests.append((action, body, request.headers.get("Authorization")))
            if action == "get_login_info":
                data = {"user_id": self.bridge_account}
            elif action == "get_emotion_list":
                data = {"msglist": [{"tid": "post", "content": "原动态", "created_time": 100}]}
            elif action == "get_comment_list":
                data = {"availability": self.comment_availability,
                        "comments": [{"commentid": "1", "uin": "101", "createtime": 101, "content": "评论"}]}
            elif action == "broken":
                return web.json_response({"status": "failed", "retcode": 1401, "message": "p_skey=secret-that-must-not-be-logged"})
            else:
                data = {"ok": True}
            return web.json_response({"status": "ok", "retcode": 0, "data": data})

        app = web.Application()
        app.router.add_post("/{action}", api)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

        async def rpc(action, params):
            self.assertEqual(action, "get_cookies")
            self.assertEqual(params, {"domain": "qzone.qq.com"})
            return {"cookies": "uin=o900; p_skey=test-only-cookie; skey=test"}

        self.bridge = QZoneBridge(Path(self.temp.name), rpc, "test-only-onebot-token")

    async def asyncTearDown(self):
        await self.bridge.close()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_real_bridge_response_shapes_and_reply_parameters(self):
        await self.bridge.authenticate(self.base, "900")
        posts = await self.bridge.posts("100")
        comments = await self.bridge.comments("900", "post")
        await self.bridge.comment("900", "post", "自然回复", "1", "101")
        self.assertEqual(posts[0]["tid"], "post")
        self.assertEqual(comments[0]["createtime"], 101)
        action, params, auth = self.requests[-1]
        self.assertEqual(action, "send_comment")
        self.assertEqual(params, {"target_uin": "900", "target_tid": "post", "content": "自然回复", "reply_comment_id": "1", "reply_uin": "101"})
        self.assertEqual(auth, "Bearer test-only-onebot-token")
        reads = [body for action, body, _ in self.requests if action == "get_emotion_list"]
        self.assertFalse(reads[0]["include_image_data"])

    async def test_wrong_bridge_account_never_receives_cookie_update_or_comment(self):
        self.bridge_account = "901"
        with self.assertRaises(QZoneError):
            await self.bridge.authenticate(self.base, "900")
        self.assertFalse(any(action in {"update_cookie", "send_comment"} for action, _, _ in self.requests))

    async def test_account_switch_after_authentication_prevents_comment(self):
        await self.bridge.authenticate(self.base, "900")
        self.bridge_account = "901"
        with self.assertRaises(QZoneError):
            await self.bridge.comment("900", "post", "不应发送")
        self.assertFalse(any(action == "send_comment" for action, _, _ in self.requests))

    async def test_unreadable_comment_detail_is_not_reported_as_empty_comments(self):
        await self.bridge.authenticate(self.base, "900")
        self.comment_availability = "not_embedded"
        with self.assertRaises(QZoneError):
            await self.bridge.comments("900", "post")

    async def test_upstream_error_never_includes_cookie_in_exception(self):
        self.bridge.base = self.base
        with self.assertRaises(QZoneError) as caught:
            await self.bridge.request("broken")
        self.assertNotIn("secret-that", str(caught.exception))
        self.assertNotIn("p_skey", str(caught.exception))

    async def test_disabled_credential_shape_is_rejected_without_launching_process(self):
        async def missing(*_):
            return {"cookies": "uin=o900; skey=only"}
        self.bridge.rpc = missing
        with patch.object(self.bridge, "start_process") as launch:
            with self.assertRaises(QZoneError):
                await self.bridge.authenticate(self.base, "900")
            launch.assert_not_called()


class QZoneLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_receives_only_current_space_context_and_can_choose_silence(self):
        generation = deepcopy(DEFAULT_CONFIG["llm"])
        client = LLMClient({"model": "test"}, generation)
        result = {"tool_calls": [{"function": {"name": "qzone_text", "arguments": json.dumps({"send": True, "text": "明天记得带水"})}}]}
        with patch.object(client, "_completion", return_value=result) as complete:
            text = await client.qzone_text("comment", {"post": "明天去爬山"}, 120)
        self.assertEqual(text, "明天记得带水")
        history, tools, instruction = complete.call_args.args
        self.assertEqual(history, [{"role": "user", "content": json.dumps({"post": "明天去爬山"}, ensure_ascii=False)}])
        self.assertIn("不引用", instruction)
        self.assertEqual(tools[0]["function"]["parameters"]["properties"]["text"]["maxLength"], 120)
        result["tool_calls"][0]["function"]["arguments"] = '{"send":false,"text":""}'
        with patch.object(client, "_completion", return_value=result):
            self.assertIsNone(await client.qzone_text("comment", {"post": "广告"}, 120))

    async def test_invalid_or_overlong_model_plan_never_produces_publishable_text(self):
        client = LLMClient({"model": "test"}, deepcopy(DEFAULT_CONFIG["llm"]))
        for result in ({"content": "ordinary unstructured text"},
                       {"tool_calls": [{"function": {"name": "qzone_text", "arguments": json.dumps({"send": True, "text": "x" * 121})}}]}):
            with self.subTest(result=result), patch.object(client, "_completion", return_value=result):
                with self.assertRaises(LLMError):
                    await client.qzone_text("comment", {}, 120)


class OneBotQZoneRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_action_correlates_echo_and_does_not_leak_upstream_errors(self):
        bot = BotApplication(deepcopy(DEFAULT_CONFIG), None, None, "test")

        class Ws:
            closed = False
            fail = False

            async def send_json(self, payload):
                response = {"retcode": 1401, "status": "failed", "message": "p_skey=secret"} if self.fail else {"retcode": 0, "data": {"user_id": 900}}
                bot.pending_actions[payload["echo"]][1].set_result(response)

        ws = Ws()
        bot.active_ws = ws
        self.assertEqual(await bot.call_action("get_login_info", {}), {"user_id": 900})
        self.assertEqual(bot.pending_actions, {})
        ws.fail = True
        with self.assertRaises(RuntimeError) as caught:
            await bot.call_action("get_cookies", {})
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(bot.pending_actions, {})
