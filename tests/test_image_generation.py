import base64
from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.image_generation import ComfyImageGenerator, ImageGenerationError
from qq_aibot.llm import LLMClient, LLMError
from qq_aibot.memory import ConversationStore
from qq_aibot.onebot import parse_message
from test_bot import incoming, FakeWebSocket


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


class ImageIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.graphs = []
        self.uploads = []
        self.llm_payloads = []
        self.fail = False
        self.empty = False
        self.pending = False
        self.bad_tool = False
        self.chat = False
        app = web.Application()

        async def upload(request):
            body = await request.post()
            self.uploads.append(body['image'].file.read())
            return web.json_response({'name': 'reference.png', 'subfolder': 'qq_aibot'})

        async def prompt(request):
            self.graphs.append((await request.json())['prompt'])
            return web.json_response({'prompt_id': 'job'})

        async def history(request):
            if self.pending:
                return web.json_response({})
            return web.json_response({'job': {'status': {'status_str': 'error' if self.fail else 'success', 'completed': True},
                                             'outputs': {} if self.empty else {'7': {'images': [{'filename': 'result.png', 'subfolder': '', 'type': 'output'}]}}}})

        async def view(request):
            self.assertEqual(request.query['filename'], 'result.png')
            return web.Response(body=PNG, content_type='image/png')

        async def completion(request):
            self.llm_payloads.append(await request.json())
            if self.chat:
                message = {'content': '你好喵'}
            else:
                message = {'content': None, 'tool_calls': [{'function': {'name': 'generate_image', 'arguments': 'bad-json' if self.bad_tool else '{"mode":"text_to_image","prompt":"a cat"}'}}]}
            return web.json_response({'choices': [{'message': message}]})

        async def reference(request):
            return web.Response(body=PNG, content_type='image/png')

        app.router.add_post('/upload/image', upload)
        app.router.add_get('/reference.png', reference)
        app.router.add_post('/prompt', prompt)
        app.router.add_get('/history/{id}', history)
        app.router.add_get('/view', view)
        app.router.add_post('/chat/completions', completion)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.config = deepcopy(DEFAULT_CONFIG['image_generation'])
        self.config['api_base'] = self.base
        self.generator = ComfyImageGenerator(self.config)

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_text_and_reference_image_paths(self):
        self.assertEqual(await self.generator.generate('text_to_image', 'a cat'), PNG)
        self.assertEqual(self.graphs[0]['2']['inputs']['text'], 'a cat')
        self.assertEqual(self.graphs[0]['4']['inputs']['denoise'], 1.0)
        self.assertEqual(self.uploads, [])
        source = 'base64://' + base64.b64encode(PNG).decode()
        self.assertEqual(await self.generator.generate('image_to_image', 'blue sky', source), PNG)
        self.assertEqual(self.uploads, [PNG])
        self.assertEqual(self.graphs[1]['9']['inputs']['image'], 'qq_aibot/reference.png')
        self.assertEqual(self.graphs[1]['4']['inputs']['denoise'], 0.45)
        self.assertNotEqual(self.graphs[0]['4']['inputs']['seed'], self.graphs[1]['4']['inputs']['seed'])

    async def test_missing_reference_and_local_paths_are_rejected(self):
        for source in (None, 'file:///C:/private.txt'):
            with self.assertRaises(ImageGenerationError):
                await self.generator.generate('image_to_image', 'cat', source)
        self.assertEqual(self.graphs, [])

    async def test_qq_image_url_is_downloaded_and_uploaded(self):
        self.assertEqual(await self.generator.generate('image_to_image', 'cat', self.base + '/reference.png'), PNG)
        self.assertEqual(self.uploads, [PNG])

    async def test_execution_error_empty_output_and_deadline(self):
        self.fail = True
        with self.assertRaises(ImageGenerationError):
            await self.generator.generate('text_to_image', 'cat')
        self.fail = False
        self.empty = True
        with self.assertRaises(ImageGenerationError):
            await self.generator.generate('text_to_image', 'cat')
        self.pending = True
        self.config['timeout_seconds'] = 0.05
        with self.assertRaises(ImageGenerationError):
            await self.generator.generate('text_to_image', 'cat')

    async def test_image_size_limit(self):
        self.config['max_image_bytes'] = 4
        with self.assertRaises(ImageGenerationError):
            await self.generator.generate('text_to_image', 'cat')

    async def test_deepseek_selects_tool_or_chat_and_validates_arguments(self):
        profile = {'api_base': self.base, 'model': 'test', 'api_key': ''}
        async with LLMClient(profile, DEFAULT_CONFIG['llm']) as llm:
            reply, tool = await llm.complete_with_image([{'role': 'user', 'content': '画猫'}], False)
            self.assertEqual(tool, {'mode': 'text_to_image', 'prompt': 'a cat'})
            self.assertEqual(reply, '')
            self.assertEqual(self.llm_payloads[0]['tool_choice'], 'auto')
            self.bad_tool = True
            with self.assertRaises(LLMError):
                await llm.complete_with_image([], True)
            self.chat = True
            self.assertEqual(await llm.complete_with_image([], False), ('你好喵', None))

    async def test_bot_sends_image_and_records_only_success(self):
        profile = {'api_base': self.base, 'model': 'test', 'api_key': ''}
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / 'chat.db')
            try:
                config = deepcopy(DEFAULT_CONFIG)
                config['image_generation'] = dict(self.config, enabled=True)
                async with LLMClient(profile, config['llm']) as llm:
                    bot = BotApplication(config, store, llm, '')
                    ws = FakeWebSocket()
                    await bot._handle(incoming(), ws)
                    segment = ws.sent[1]['params']['message'][0]
                    self.assertEqual(segment['type'], 'image')
                    self.assertEqual(base64.b64decode(segment['data']['file'][9:]), PNG)
                    self.assertEqual(len(store.history('private:100', 10)), 2)
                    self.fail = True
                    await bot._handle(replace(incoming(), user_id=101), ws)
                    self.assertEqual(store.history('private:101', 10), [])
                    self.assertIn('图片生成失败', ws.sent[-1]['params']['message'])
            finally:
                store.close()


class ImageMessageTests(unittest.TestCase):
    def test_array_message_without_raw_message(self):
        msg = parse_message({'post_type': 'message', 'message_type': 'group', 'self_id': 9, 'user_id': 1, 'group_id': 2,
                             'message': [{'type': 'at', 'data': {'qq': '9'}}, {'type': 'text', 'data': {'text': '改成蓝色'}},
                                         {'type': 'image', 'data': {'url': 'https://example.com/a.png', 'file': 'a'}}]})
        self.assertTrue(msg.mentioned)
        self.assertEqual(msg.images, ('https://example.com/a.png',))
        self.assertEqual(msg.text, '改成蓝色 [图片]')

    def test_cq_url_escaping(self):
        msg = parse_message({'post_type': 'message', 'message_type': 'private', 'self_id': 9, 'user_id': 1,
                             'raw_message': '画图[CQ:image,file=x,url=https://example.com/a?a=1&amp;b=2&#44;3]'})
        self.assertEqual(msg.images, ('https://example.com/a?a=1&b=2,3',))


if __name__ == '__main__':
    unittest.main()
