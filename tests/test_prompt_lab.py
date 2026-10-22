import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from qq_aibot.config import DEFAULT_CONFIG, load_config, save_config
from qq_aibot.llm import LLMError
from qq_aibot.prompt_lab import CASES, PromptLab, PromptLabError, _evaluate, _run_codex, optimize, redact, style_metrics


class FakeClient:
    requests = []
    verdict = {"baseline_score": 3, "candidate_score": 4, "identity_safe": True, "reasons": ["更简洁"]}

    def __init__(self, profile, generation):
        self.generation = generation

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @contextmanager
    def using_generation(self, generation):
        previous = self.generation
        self.generation = generation
        try:
            yield
        finally:
            self.generation = previous

    async def reply_plan(self, history, stickers, has_image, **kwargs):
        self.requests.append((deepcopy(history), deepcopy(self.generation), kwargs))
        return {"messages": ["简洁回复" if self.generation.get("learned_prompt") else "普通回复"]}

    async def complete(self, history):
        return json.dumps(self.verdict)


class PromptLabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = deepcopy(DEFAULT_CONFIG)
        save_config(self.config, self.root)
        self.candidate = {"guidance": "先理解真实用户上下文，简洁接话。", "rationale": "减少套话", "risks": []}
        FakeClient.requests = []
        FakeClient.verdict = {"baseline_score": 3, "candidate_score": 4, "identity_safe": True, "reasons": ["更简洁"]}

    def tearDown(self):
        self.temporary.cleanup()

    async def run_fake(self, corpus=None):
        with patch("qq_aibot.prompt_lab._run_codex", AsyncMock(return_value=self.candidate)) as runner, patch("qq_aibot.prompt_lab.LLMClient", FakeClient):
            result = await optimize(self.root, self.config, {"api_key": "never-share-this"}, corpus or [], "自然聊天")
        return result, runner

    async def test_generate_evaluate_apply_rollback_preserves_persona(self):
        result, runner = await self.run_fake()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(FakeClient.requests), 2 * len(CASES))
        self.assertTrue(all(row[2]["allow_generation"] is False for row in FakeClient.requests))
        self.assertNotIn("never-share-this", runner.call_args.args[1])
        self.assertFalse(load_config(self.root)["llm"].get("learned_prompt"))
        lab = PromptLab(self.root)
        self.assertEqual(len(lab.tasks()), 1)
        lab.apply(result["task_id"])
        current = load_config(self.root)
        self.assertEqual(current["llm"]["system_prompt"], self.config["llm"]["system_prompt"])
        self.assertEqual(current["llm"]["learned_prompt"], self.candidate["guidance"])
        lab.rollback()
        self.assertFalse(load_config(self.root)["llm"].get("learned_prompt"))

    async def test_public_corpus_only_and_redaction(self):
        self.config["llm"]["system_prompt"] = "MY_PRIVATE_PERSONA_名字绝不外传"
        self.config["llm"]["learned_prompt"] = "MY_PRIVATE_LEARNED_STYLE"
        corpus = [{"scope": "session", "approved": True, "text": "PRIVATE_CHAT"}, {"scope": "global", "approved": False, "text": "NOT_APPROVED"}, {"scope": "global", "approved": True, "text": "公共风格 sample@example.com QQ:123456789"}]
        _, runner = await self.run_fake(corpus)
        prompt = runner.call_args.args[1]
        for text in ("PRIVATE_CHAT", "NOT_APPROVED", "sample@example.com", "123456789", "MY_PRIVATE_PERSONA", "MY_PRIVATE_LEARNED_STYLE"):
            self.assertNotIn(text, prompt)
        self.assertIn("公共风格", prompt)
        self.assertNotIn("MY_PRIVATE", json.dumps(PromptLab(self.root).tasks()))
        for _, generation, _ in FakeClient.requests:
            self.assertNotIn("MY_PRIVATE", generation["system_prompt"])
            self.assertNotIn("MY_PRIVATE", generation["learned_prompt"])

    async def test_report_uses_hash_not_private_persona(self):
        self.config["llm"]["system_prompt"] = "LOCAL_PRIVATE_SECRET_PERSONA"
        result, _ = await self.run_fake()
        self.assertEqual(len(result["persona_sha256"]), 64)
        self.assertNotIn("LOCAL_PRIVATE_SECRET_PERSONA", json.dumps(result))
        self.assertNotIn("persona_snapshot", result)

    async def test_failed_cli_persisted_and_no_apply(self):
        with patch("qq_aibot.prompt_lab._run_codex", AsyncMock(side_effect=OSError("api_key=secret"))):
            result = await optimize(self.root, self.config, {}, [], "test")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("secret", result["error"])
        with self.assertRaises(PromptLabError):
            PromptLab(self.root).apply(result["task_id"])

    async def test_cancel_persisted(self):
        with patch("qq_aibot.prompt_lab._run_codex", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await optimize(self.root, self.config, {}, [], "test")
        self.assertEqual(PromptLab(self.root).tasks()[0]["status"], "cancelled")

    async def test_persona_change_prevents_stale_apply(self):
        result, _ = await self.run_fake()
        self.config["llm"]["system_prompt"] = "新的人设，只能本地配置"
        save_config(self.config, self.root)
        with self.assertRaises(PromptLabError):
            PromptLab(self.root).apply(result["task_id"])

    async def test_expression_change_prevents_stale_apply(self):
        result, _ = await self.run_fake()
        self.config["llm"]["learned_prompt"] = "管理员刚刚配置的新表达"
        save_config(self.config, self.root)
        with self.assertRaises(PromptLabError):
            PromptLab(self.root).apply(result["task_id"])

    async def test_api_failure_detail_redacts_provider_key(self):
        with patch("qq_aibot.prompt_lab._run_codex", AsyncMock(return_value=self.candidate)), patch("qq_aibot.prompt_lab._evaluate", AsyncMock(side_effect=LLMError("HTTP 401 key=private_secret"))):
            result = await optimize(self.root, self.config, {"api_key": "private_secret"}, [], "test")
        self.assertIn("401", result["error"])
        self.assertNotIn("private_secret", result["error"])

    async def test_rollback_does_not_overwrite_new_administrator_setting(self):
        result, _ = await self.run_fake()
        lab = PromptLab(self.root)
        lab.apply(result["task_id"])
        self.config["llm"]["learned_prompt"] = "管理员手动设置"
        save_config(self.config, self.root)
        with self.assertRaises(PromptLabError):
            lab.rollback()

    async def test_unsafe_or_worse_candidate_cannot_be_applied(self):
        for verdict in ({"baseline_score": 4, "candidate_score": 3, "identity_safe": True, "reasons": []}, {"baseline_score": 3, "candidate_score": 5, "identity_safe": False, "reasons": []}):
            FakeClient.verdict = verdict
            result, _ = await self.run_fake()
            self.assertFalse(result["evaluation"]["recommended"])
            with self.assertRaises(PromptLabError):
                PromptLab(self.root).apply(result["task_id"])

    async def test_invalid_judge_is_failure(self):
        FakeClient.verdict = {"baseline_score": 99, "candidate_score": "perfect", "identity_safe": True}
        with patch("qq_aibot.prompt_lab.LLMClient", FakeClient):
            with self.assertRaises(PromptLabError):
                await _evaluate(self.config, {}, "简洁")

    async def test_equal_scores_without_observable_change_not_recommended(self):
        FakeClient.verdict = {"baseline_score": 5, "candidate_score": 5, "identity_safe": True, "reasons": []}
        result, _ = await self.run_fake()
        self.assertFalse(result["evaluation"]["recommended"])

    async def test_new_service_closing_blocks_recommendation_even_with_higher_score(self):
        original = FakeClient.reply_plan

        async def reply(client, *args, **kwargs):
            if client.generation.get("learned_prompt"):
                return {"messages": ["不可以。其他事照常聊，随时说。"]}
            return await original(client, *args, **kwargs)

        with patch.object(FakeClient, "reply_plan", reply):
            result, _ = await self.run_fake()
        evaluation = result["evaluation"]
        self.assertGreater(evaluation["judge"]["candidate_score"], evaluation["judge"]["baseline_score"])
        self.assertFalse(evaluation["recommended"])
        self.assertTrue(evaluation["style_metrics"]["new_service_closings"])

    def test_style_metrics_counts_same_defect_across_wording_without_hiding_it(self):
        metrics = style_metrics([{"id": "x", "baseline": "随时问", "candidate": "其他事照常聊"}])
        self.assertEqual(metrics["baseline_chars"], 3)
        self.assertEqual(metrics["candidate_chars"], 6)
        self.assertFalse(metrics["new_service_closings"])
        self.assertEqual(metrics["remaining_service_closings"][0]["matches"], ["其他事照常聊"])
        self.assertEqual(metrics["baseline_service_count"], 1)
        self.assertEqual(metrics["candidate_service_count"], 1)

    async def test_retry_evaluation_reuses_candidate_and_preserves_failure(self):
        result, _ = await self.run_fake()
        lab = PromptLab(self.root)
        result.update(status="failed", error="API暂时不可用")
        lab._path(result["task_id"]).write_text(json.dumps(result), encoding="utf-8")
        with patch("qq_aibot.prompt_lab.LLMClient", FakeClient), patch("qq_aibot.prompt_lab._run_codex", AsyncMock()) as codex:
            retried = await lab.retry_evaluation(result["task_id"], self.config, {})
        codex.assert_not_called()
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(retried["previous_attempts"][-1]["error"], "API暂时不可用")
        self.assertNotIn("error", retried)

    async def test_evaluation_cache_reuses_case_outputs_only_judge_repeated(self):
        path = self.root / "cases.json"
        with patch("qq_aibot.prompt_lab.LLMClient", FakeClient):
            await _evaluate(self.config, {}, self.candidate["guidance"], cases_path=path)
            await _evaluate(self.config, {}, self.candidate["guidance"], cases_path=path)
        self.assertEqual(len(FakeClient.requests), 2 * len(CASES))
        with patch("qq_aibot.prompt_lab.LLMClient", FakeClient):
            await _evaluate(self.config, {}, "另一个指导", cases_path=path)
        self.assertEqual(len(FakeClient.requests), 4 * len(CASES))

    async def test_codex_command_sandbox_no_shell_no_provider_environment(self):
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (b"", b"irrelevant diagnostics")
        job = self.root / "job"
        job.mkdir()
        (job / "candidate.json").write_text(json.dumps(self.candidate), encoding="utf-8")
        with patch("qq_aibot.prompt_lab.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as create, patch.dict("os.environ", {"DEEPSEEK_API_KEY": "privatekey", "ONEBOT_ACCESS_TOKEN": "privatetoken"}):
            result = await _run_codex(job, "safe input", "fake-codex", 2)
        args = create.call_args
        self.assertIn("read-only", args.args)
        self.assertIn("--ephemeral", args.args)
        self.assertIn("shell_tool", args.args)
        self.assertIn("multi_agent", args.args)
        self.assertNotIn("--model", args.args)
        self.assertNotIn("shell", args.kwargs)
        self.assertNotIn("DEEPSEEK_API_KEY", args.kwargs["env"])
        self.assertNotIn("ONEBOT_ACCESS_TOKEN", args.kwargs["env"])
        self.assertEqual(result, self.candidate)

    async def test_timeout_terminates_process(self):
        process = AsyncMock()
        process.communicate.side_effect = asyncio.TimeoutError()
        job = self.root / "job"
        job.mkdir()
        with patch("qq_aibot.prompt_lab.asyncio.create_subprocess_exec", AsyncMock(return_value=process)), patch("qq_aibot.prompt_lab._terminate", AsyncMock()) as terminate:
            with self.assertRaises(asyncio.TimeoutError):
                await _run_codex(job, "safe", "fake", 1)
        terminate.assert_awaited_once_with(process)

    def test_task_id_cannot_escape_directory(self):
        with self.assertRaises(ValueError):
            PromptLab(self.root).get("../providers.json")

    def test_redact_contacts_credentials(self):
        result = redact("QQ:12345678 电话13800138000 邮箱abc@example.com api_key=abcdefghi")
        for private in ("12345678", "13800138000", "abc@example.com", "abcdefghi"):
            self.assertNotIn(private, result)


if __name__ == "__main__":
    unittest.main()
