import json
import unittest

from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.image_generation import ComfyImageGenerator, ImageGenerationError
from qq_aibot.llm import LLMClient, LLMError


class PlanLLM(LLMClient):
    def __init__(self, result):
        super().__init__({}, {})
        self.result = result
        self.context = None

    async def _completion(self, history, **kwargs):
        self.context = history
        return {'content': json.dumps(self.result)}


class PromptPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_uses_first_reference_and_limits_strength(self):
        llm = PlanLLM({'prompt': 'orange cat, blue sky', 'negative_prompt': 'blurry', 'denoise': 0.95})
        history = [{'role': 'user', 'content': [{'type': 'text', 'text': '改背景'},
                   {'type': 'image_url', 'image_url': {'url': 'first'}},
                   {'type': 'image_url', 'image_url': {'url': 'second'}}]}]
        plan = await llm.optimize_image_prompt(history, {'mode': 'image_to_image', 'prompt': 'cat'})
        self.assertEqual(plan['denoise'], 0.7)
        self.assertEqual(len(llm.context[0]['content']), 2)
        self.assertEqual(len(history[0]['content']), 3)
        graph = ComfyImageGenerator(DEFAULT_CONFIG['image_generation']).workflow(
            'image_to_image', plan['prompt'], 'original.png', negative_prompt=plan['negative_prompt'], denoise=0.3)
        self.assertEqual(graph['4']['inputs']['denoise'], 0.3)
        self.assertEqual(graph['8']['inputs']['text'], 'blurry')
        self.assertEqual(graph['9']['inputs']['image'], 'original.png')

    async def test_invalid_optimizer_data_is_rejected_for_fallback(self):
        for output in ({'prompt': '', 'negative_prompt': '', 'denoise': 0.3},
                       {'prompt': 'cat', 'negative_prompt': 'blur', 'denoise': float('nan')}):
            with self.assertRaises(LLMError):
                await PlanLLM(output).optimize_image_prompt([], {'mode': 'image_to_image', 'prompt': 'cat'})

    async def test_text_generation_always_uses_full_denoise(self):
        plan = await PlanLLM({'prompt': 'cat', 'negative_prompt': 'blur', 'denoise': 0.3}).optimize_image_prompt(
            [], {'mode': 'text_to_image', 'prompt': 'cat'})
        self.assertEqual(plan['denoise'], 1.0)
