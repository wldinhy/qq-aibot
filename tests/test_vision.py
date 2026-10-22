import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

import aiohttp
from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.media import load_image, MediaError
from qq_aibot.memory import ConversationStore
from qq_aibot.onebot import parse_message
from test_bot import incoming


PNG = b'\x89PNG\r\n\x1a\n' + b'test'


class FakeVisionLLM:
    profile = {}

    def __init__(self):
        self.received = []

    async def complete(self, history):
        self.received.append(history)
        return '看到了图片'


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.directory.name) / 'chat.db')
        self.llm = FakeVisionLLM()
        self.bot = BotApplication(deepcopy(DEFAULT_CONFIG), self.store, self.llm, 'test-token')
        app = web.Application()
        app.router.add_get('/ws', self.bot.websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        for task in list(self.bot.tasks):
            task.cancel()
        if self.bot.tasks:
            await asyncio.gather(*list(self.bot.tasks), return_exceptions=True)
        await self.runner.cleanup()
        self.store.close()
        self.directory.cleanup()

    def event(self, stamp, message_id='1', parts=None):
        return {'post_type': 'message', 'message_type': 'private', 'self_id': 999, 'user_id': 123,
                'time': stamp, 'message_id': message_id,
                'message': parts or [{'type': 'text', 'data': {'text': '你好'}}]}

    async def test_old_messages_missing_time_and_duplicates_do_not_reply(self):
        async with self.session.ws_connect(self.base + '/ws', headers={'Authorization': 'Bearer test-token'}) as ws:
            await ws.send_json(self.event(self.bot.started_at - 10))
            await ws.send_json(self.event(0, '2'))
            with self.assertRaises(asyncio.TimeoutError):
                await ws.receive_json(timeout=0.05)
            self.assertEqual(self.llm.received, [])
            event = self.event(int(time.time()), '3')
            await ws.send_json(event)
            result = await ws.receive_json(timeout=2)
            self.assertEqual(result['params']['message'], '看到了图片')
            await ws.send_json(event)
            with self.assertRaises(asyncio.TimeoutError):
                await ws.receive_json(timeout=0.05)
            self.assertEqual(len(self.llm.received), 1)

    async def test_native_image_fallback_and_real_multimodal_payload(self):
        async with self.session.ws_connect(self.base + '/ws', headers={'Authorization': 'Bearer test-token'}) as ws:
            await ws.send_json(self.event(int(time.time()), parts=[{'type': 'image', 'data': {'file': 'qq-image-id', 'summary': '表情包'}}]))
            request = await ws.receive_json(timeout=2)
            self.assertEqual(request['action'], 'get_image')
            self.assertEqual(request['params']['file'], 'qq-image-id')
            await ws.send_json({'status': 'ok', 'retcode': 0, 'echo': request['echo'],
                                'data': {'base64': base64.b64encode(PNG).decode()}})
            reply = await ws.receive_json(timeout=2)
            self.assertEqual(reply['params']['message'], '看到了图片')
            content = self.llm.received[0][-1]['content']
            self.assertEqual(content[1]['type'], 'image_url')
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
            saved = self.store.history('private:123', 10)
            self.assertIsInstance(saved[0]['content'], str)
            self.assertNotIn('base64', saved[0]['content'])

    async def test_native_local_file_fallback_and_signature_validation(self):
        path = Path(self.directory.name) / 'image.png'
        path.write_bytes(PNG)

        async def resolve(file):
            return {'file': str(path)}

        self.assertEqual(await load_image('invalid-link', 'qq-file', DEFAULT_CONFIG['vision'], resolve), PNG)
        path.write_bytes(b'not an image')
        with self.assertRaises(MediaError):
            await load_image('invalid-link', 'qq-file', DEFAULT_CONFIG['vision'], resolve)

    def test_face_and_meme_summary_are_preserved(self):
        message = parse_message(self.event(int(time.time()), parts=[{'type': 'face', 'data': {'id': '5'}},
                   {'type': 'image', 'data': {'file': 'a.gif', 'url': 'https://example.com/a.gif', 'summary': '笑哭'}}]))
        self.assertIn('流泪', message.text)
        self.assertIn('笑哭', message.text)
        self.assertEqual(message.image_files, ('a.gif',))


if __name__ == '__main__':
    unittest.main()
