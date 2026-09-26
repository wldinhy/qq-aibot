import unittest
import tempfile
from pathlib import Path

from qq_aibot.bot import BotApplication
from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.memory import ConversationStore
from qq_aibot.onebot import IncomingMessage


def incoming(message_type="private", mentioned=False, user_id=100, group_id=None):
    return IncomingMessage(
        message_type=message_type,
        user_id=user_id,
        group_id=group_id,
        self_id=999,
        text="你好",
        nickname="测试用户",
        mentioned=mentioned,
    )


class BotRuleTests(unittest.TestCase):
    def make_bot(self):
        config = {
            section: dict(values) for section, values in DEFAULT_CONFIG.items()
        }
        return BotApplication(config, store=None, llm=None, access_token="token")

    def test_private_messages_are_public_by_default(self):
        bot = self.make_bot()
        self.assertTrue(bot._allowed(incoming()))

    def test_groups_require_mention_by_default(self):
        bot = self.make_bot()
        self.assertFalse(bot._allowed(incoming("group", False, group_id=200)))
        self.assertTrue(bot._allowed(incoming("group", True, group_id=200)))

    def test_user_allowlist_and_blocklist(self):
        bot = self.make_bot()
        bot.bot_config["allowed_users"] = [100, 101]
        self.assertTrue(bot._allowed(incoming(user_id=100)))
        self.assertFalse(bot._allowed(incoming(user_id=102)))
        bot.bot_config["blocked_users"] = [100]
        self.assertFalse(bot._allowed(incoming(user_id=100)))


class FakeDeepSeek:
    profile = {"model": "old-model"}

    async def complete(self, history):
        return "测试回复"


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


class BotReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_is_sent_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "chat.db")
            try:
                config = {
                    section: dict(values) for section, values in DEFAULT_CONFIG.items()
                }
                bot = BotApplication(config, store, FakeDeepSeek(), "token")
                ws = FakeWebSocket()
                await bot._handle(incoming(user_id=123), ws)
                self.assertEqual(ws.sent[0]["action"], "send_private_msg")
                self.assertEqual(ws.sent[0]["params"]["message"], "测试回复")
                history = store.history("private:123", 10)
                self.assertEqual([item["role"] for item in history], ["user", "assistant"])
            finally:
                store.close()

    async def test_provider_is_hot_reloaded_before_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "chat.db")
            try:
                config = {section: dict(values) for section, values in DEFAULT_CONFIG.items()}
                llm = FakeDeepSeek()
                bot = BotApplication(
                    config,
                    store,
                    llm,
                    "token",
                    provider_loader=lambda: {"model": "new-model"},
                )
                await bot._handle(incoming(user_id=321), FakeWebSocket())
                self.assertEqual(llm.profile["model"], "new-model")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
