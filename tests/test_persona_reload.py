import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from aiohttp import web

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.llm import LLMClient
from qq_aibot.memory import ConversationStore, persona_key
from test_bot import incoming, FakeWebSocket


class PersonaReloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.payloads = []
        async def complete(request):
            self.payloads.append(await request.json())
            return web.json_response({'choices': [{'message': {'content': f'回答{len(self.payloads)}'}}]})
        app = web.Application()
        app.router.add_post('/chat/completions', complete)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.profile = {'api_base': f'http://127.0.0.1:{port}', 'model': 'test'}

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_same_running_bot_reads_new_persona_without_restart_or_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            config = deepcopy(DEFAULT_CONFIG)
            config['stickers']['enabled'] = False
            config['llm']['system_prompt'] = 'Persona A'
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            store = ConversationStore(Path(directory) / 'chat.db')
            store.add('private:100', 'assistant', 'legacy persona reply', 20)
            try:
                async with LLMClient(self.profile, config['llm']) as llm:
                    bot = BotApplication(config, store, llm, '', configuration_loader=lambda: json.loads(path.read_text(encoding='utf-8')))
                    await bot._handle(incoming(), FakeWebSocket())
                    config['llm']['system_prompt'] = 'Persona B'
                    path.write_text(json.dumps(config), encoding='utf-8')
                    await bot._handle(incoming(), FakeWebSocket())
                self.assertIn('Persona A', self.payloads[0]['messages'][0]['content'])
                self.assertIn('Persona B', self.payloads[1]['messages'][0]['content'])
                self.assertNotIn('legacy persona reply', json.dumps(self.payloads))
                self.assertNotIn('回答1', json.dumps(self.payloads[1], ensure_ascii=False))
                all_rows = store.history('private:100', 20)
                self.assertIn('legacy persona reply', [row['content'] for row in all_rows])
                self.assertEqual(len(all_rows), 5)
            finally:
                store.close()

    async def test_concurrent_requests_keep_separate_generation_snapshots(self):
        base = deepcopy(DEFAULT_CONFIG['llm'])
        async with LLMClient(self.profile, base) as llm:
            async def request(name, tokens):
                config = {**base, 'system_prompt': name, 'max_tokens': tokens}
                with llm.using_generation(config):
                    await asyncio.sleep(0)
                    return await llm.complete([])
            await asyncio.gather(request('Persona A', 111), request('Persona B', 222))
        expected = {'Persona A': 111, 'Persona B': 222}
        for payload in self.payloads:
            name = payload['messages'][0]['content'].split('\n')[0]
            self.assertEqual(payload['max_tokens'], expected[name])
        self.assertEqual(base['max_tokens'], DEFAULT_CONFIG['llm']['max_tokens'])

    async def test_natural_chat_rules_reach_model_but_not_image_optimizer(self):
        generation = deepcopy(DEFAULT_CONFIG['llm'])
        generation['max_reply_messages'] = 1
        generation['max_reply_chars_per_message'] = 80
        async with LLMClient(self.profile, generation) as llm:
            await llm.reply_plan([{'role': 'user', 'content': '朋友刚说周六去看展，你觉得呢'}], [], False, allow_generation=False)
            await llm._completion([], system_prompt='IMAGE_OPTIMIZER', max_tokens=1000)
            with llm.using_generation({**generation, 'natural_style': False}):
                await llm.complete([])
        chat = self.payloads[0]
        self.assertIn('[对话依据]', chat['messages'][0]['content'])
        self.assertIn('[聊天表达]', chat['messages'][0]['content'])
        self.assertIn('自己的旧回复只用于衔接', chat['messages'][0]['content'])
        self.assertIn('1 条文字，每条 80 字符', chat['messages'][0]['content'])
        self.assertEqual(chat['tools'][0]['function']['parameters']['properties']['messages']['maxItems'], 1)
        self.assertNotIn('[聊天表达]', self.payloads[1]['messages'][0]['content'])
        self.assertEqual(self.payloads[1]['max_tokens'], 1000)
        self.assertNotIn('[聊天表达]', self.payloads[2]['messages'][0]['content'])

    def test_legacy_database_is_backed_up_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chat.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,created_at INTEGER)')
                conn.execute("INSERT INTO messages VALUES(1,'private:100','assistant','old reply',0)")
            conn.close()
            store = ConversationStore(path)
            try:
                key = persona_key('new persona')
                self.assertEqual(store.history('private:100', 10, persona=key), [])
                store.add('private:100', 'user', 'new question', 2, persona=key)
                store.add('private:100', 'assistant', 'new reply', 2, persona=key)
                self.assertEqual(len(store.history('private:100', 10)), 3)
                backup_path = next((Path(directory) / 'backups').glob('*.db'))
                with sqlite3.connect(backup_path) as conn:
                    self.assertEqual(conn.execute('SELECT content FROM messages').fetchone()[0], 'old reply')
                conn.close()
            finally:
                store.close()
