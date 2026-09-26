import unittest

from qq_aibot.onebot import parse_message, send_action


class OneBotTests(unittest.TestCase):
    def test_group_mention_is_parsed(self) -> None:
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 10000,
            "user_id": 20000,
            "group_id": 30000,
            "raw_message": "[CQ:reply,id=1][CQ:at,qq=10000] 你好 [CQ:image,file=x]",
            "sender": {"nickname": "小明"},
        }
        message = parse_message(event)
        assert message is not None
        self.assertTrue(message.mentioned)
        self.assertEqual(message.text, "你好 [图片]")
        self.assertEqual(message.session_id, "group:30000")
        self.assertEqual(send_action(message, "你好")["action"], "send_group_msg")

    def test_ignores_non_message_event(self) -> None:
        self.assertIsNone(parse_message({"post_type": "meta_event"}))


if __name__ == "__main__":
    unittest.main()
