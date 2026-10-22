from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from qq_aibot.llm import LLMClient, LLMError


class TestReplyPlan(unittest.IsolatedAsyncioTestCase):
    async def test_reply_plan_text_only(self) -> None:
        profile = {"api_base": "http://test", "model": "test-model", "api_key": ""}
        generation = {"temperature": 0.8, "max_tokens": 700, "timeout_seconds": 90, "system_prompt": "test"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "reply_to_user",
                                "arguments": '{"messages": ["Hello", "How are you?"], "sticker_id": ""}'
                            }
                        }]
                    }
                }]
            })
            mock_session.post.return_value.__aenter__.return_value = mock_response

            async with LLMClient(profile, generation) as client:
                result = await client.reply_plan([], ["happy", "sad"], False)
                self.assertEqual(result["type"], "text")
                self.assertEqual(result["messages"], ["Hello", "How are you?"])
                self.assertEqual(result["sticker_id"], "")

    async def test_reply_plan_with_sticker(self) -> None:
        profile = {"api_base": "http://test", "model": "test-model", "api_key": ""}
        generation = {"temperature": 0.8, "max_tokens": 700, "timeout_seconds": 90, "system_prompt": "test"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "reply_to_user",
                                "arguments": '{"messages": ["Great!"], "sticker_id": "happy"}'
                            }
                        }]
                    }
                }]
            })
            mock_session.post.return_value.__aenter__.return_value = mock_response

            async with LLMClient(profile, generation) as client:
                result = await client.reply_plan([], ["happy", "sad"], False)
                self.assertEqual(result["type"], "text")
                self.assertEqual(result["messages"], ["Great!"])
                self.assertEqual(result["sticker_id"], "happy")

    async def test_reply_plan_image_generation(self) -> None:
        profile = {"api_base": "http://test", "model": "test-model", "api_key": ""}
        generation = {"temperature": 0.8, "max_tokens": 700, "timeout_seconds": 90, "system_prompt": "test"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "generate_image",
                                "arguments": '{"mode": "text_to_image", "prompt": "a cat in the garden"}'
                            }
                        }]
                    }
                }]
            })
            mock_session.post.return_value.__aenter__.return_value = mock_response

            async with LLMClient(profile, generation) as client:
                result = await client.reply_plan([], ["happy"], True)
                self.assertEqual(result["type"], "image")
                self.assertEqual(result["mode"], "text_to_image")
                self.assertEqual(result["prompt"], "a cat in the garden")

    async def test_reply_plan_message_limit(self) -> None:
        profile = {"api_base": "http://test", "model": "test-model", "api_key": ""}
        generation = {"temperature": 0.8, "max_tokens": 700, "timeout_seconds": 90, "system_prompt": "test"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "function": {
                                "name": "reply_to_user",
                                "arguments": '{"messages": ["A", "B", "C", "D", "E"], "sticker_id": ""}'
                            }
                        }]
                    }
                }]
            })
            mock_session.post.return_value.__aenter__.return_value = mock_response

            async with LLMClient(profile, generation) as client:
                result = await client.reply_plan([], [], False)
                self.assertEqual(result["type"], "text")
                self.assertEqual(len(result["messages"]), 3)

    async def test_reply_plan_empty_fallback(self) -> None:
        profile = {"api_base": "http://test", "model": "test-model", "api_key": ""}
        generation = {"temperature": 0.8, "max_tokens": 700, "timeout_seconds": 90, "system_prompt": "test"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Direct text response"
                    }
                }]
            })
            mock_session.post.return_value.__aenter__.return_value = mock_response

            async with LLMClient(profile, generation) as client:
                result = await client.reply_plan([], [], False)
                self.assertEqual(result["type"], "text")
                self.assertEqual(result["messages"], ["Direct text response"])


if __name__ == "__main__":
    unittest.main()
