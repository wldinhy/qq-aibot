import unittest

from aiohttp import web

from qq_aibot.llm import LLMClient, LLMError, parse_model_ids


class ModelListTests(unittest.TestCase):
    def test_openai_model_list_is_sorted_and_unique(self):
        body = {"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}]}
        self.assertEqual(parse_model_ids(body), ["model-a", "model-b"])

    def test_self_hosted_string_list_is_supported(self):
        self.assertEqual(parse_model_ids({"models": ["local-b", "local-a"]}), ["local-a", "local-b"])

    def test_invalid_model_list_is_rejected(self):
        with self.assertRaises(LLMError):
            parse_model_ids({"data": "not-a-list"})


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        app = web.Application()

        async def models(request):
            self.requests.append(("models", request.headers.get("Authorization")))
            return web.json_response({"data": [{"id": "test-model"}]})

        async def completion(request):
            body = await request.json()
            self.requests.append(("completion", body["model"]))
            return web.json_response({"choices": [{"message": {"content": "hello"}}]})

        app.router.add_get("/v1/models", models)
        app.router.add_post("/v1/chat/completions", completion)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.profile = {
            "api_base": f"http://127.0.0.1:{port}/v1",
            "api_key": "secret-key",
            "model": "test-model",
            "api_key_required": True,
        }
        self.generation = {
            "timeout_seconds": 5,
            "temperature": 0.5,
            "max_tokens": 100,
            "system_prompt": "test",
        }

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_model_discovery_and_completion_paths(self):
        async with LLMClient(self.profile, self.generation) as client:
            self.assertEqual(await client.list_models(), ["test-model"])
            self.assertEqual(await client.complete([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(self.requests[0], ("models", "Bearer secret-key"))
        self.assertEqual(self.requests[1], ("completion", "test-model"))


if __name__ == "__main__":
    unittest.main()
