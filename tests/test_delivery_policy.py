import json
import tempfile
from pathlib import Path
from copy import deepcopy
import unittest

from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.llm import LLMClient
from qq_aibot.memory import ConversationStore
from test_bot import incoming, FakeWebSocket


class PlannedLLM:
    profile = {}

    async def reply_plan(self, history, *args, **kwargs):
        return {'type': 'text', 'messages': ['第一条', '第二条'], 'sticker_id': 'happy'}


class DeliveryPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_texts_then_sticker_are_sent_and_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / 'chat.db')
            try:
                bot = BotApplication(deepcopy(DEFAULT_CONFIG), store, PlannedLLM(), '')
                ws = FakeWebSocket()
                await bot._handle(incoming(), ws)
                self.assertEqual(ws.sent[0]['params']['message'], '第一条')
                self.assertEqual(ws.sent[1]['params']['message'], '第二条')
                self.assertEqual(ws.sent[2]['params']['message'][0]['data']['sub_type'], 1)
                saved = store.history('private:100', 10)[-1]['content']
                self.assertIn('第一条', saved)
                self.assertIn('表情包：开心', saved)
            finally:
                store.close()

    async def test_unsent_parts_are_not_saved(self):
        class FailingSocket(FakeWebSocket):
            async def send_json(self, payload):
                if self.sent:
                    raise ConnectionError('test disconnected')
                await super().send_json(payload)
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / 'chat.db')
            try:
                bot = BotApplication(deepcopy(DEFAULT_CONFIG), store, PlannedLLM(), '')
                with self.assertRaises(ConnectionError):
                    await bot._handle(incoming(), FailingSocket())
                self.assertEqual(store.history('private:100', 10)[-1]['content'], '第一条')
            finally:
                store.close()

    async def test_optimizer_budget_is_independent_and_no_gen_tool_when_disabled(self):
        payloads = []
        async def completion(request):
            payload = await request.json()
            payloads.append(payload)
            if payload['max_tokens'] == 1000:
                content = json.dumps({'prompt': 'cat', 'negative_prompt': 'blur', 'denoise': 1})
                message = {'content': content}
            else:
                message = {'content': '普通回复'}
            return web.json_response({'choices': [{'message': message}]})
        app = web.Application()
        app.router.add_post('/chat/completions', completion)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        generation = deepcopy(DEFAULT_CONFIG['llm'])
        try:
            async with LLMClient({'api_base': f'http://127.0.0.1:{port}', 'model': 'test'}, generation) as llm:
                await llm.optimize_image_prompt([], {'mode': 'text_to_image', 'prompt': 'cat'}, 1000)
                await llm.reply_plan([], [], False, allow_generation=False)
            self.assertEqual([p['max_tokens'] for p in payloads], [1000, 700])
            self.assertEqual(generation['max_tokens'], 700)
            self.assertEqual([t['function']['name'] for t in payloads[1]['tools']], ['reply_to_user'])
            self.assertIn('只能由本地管理员', payloads[1]['messages'][0]['content'])
        finally:
            await runner.cleanup()
