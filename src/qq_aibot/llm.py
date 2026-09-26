from __future__ import annotations

from typing import Any

import aiohttp


class LLMError(RuntimeError):
    pass


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def parse_model_ids(body: Any) -> list[str]:
    if isinstance(body, dict):
        values = body.get("data", body.get("models", []))
    else:
        values = body
    if not isinstance(values, list):
        raise LLMError("模型列表响应中没有 data 数组")
    result: list[str] = []
    for item in values:
        model_id = item.get("id") if isinstance(item, dict) else item
        if isinstance(model_id, str) and model_id.strip():
            result.append(model_id.strip())
    return sorted(set(result), key=str.casefold)


class LLMClient:
    def __init__(self, profile: dict[str, Any], generation: dict[str, Any]) -> None:
        self.profile = profile
        self.generation = generation
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "LLMClient":
        timeout = aiohttp.ClientTimeout(total=float(self.generation["timeout_seconds"]))
        # On Windows, aiohttp may pick aiodns' AsyncResolver by default. Some
        # VPN/TUN DNS setups expose working results through the system resolver
        # while direct DNS queries time out, so use the OS resolver explicitly.
        connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()

    async def list_models(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("LLMClient must be used as an async context manager")
        url = self.profile["api_base"].rstrip("/") + "/models"
        try:
            async with self._session.get(url, headers=_headers(self.profile.get("api_key", ""))) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    message = _error_message(body)
                    raise LLMError(f"模型检索失败（HTTP {response.status}）：{message}")
        except aiohttp.ClientError as exc:
            raise LLMError(f"无法连接服务商：{exc}") from exc
        models = parse_model_ids(body)
        if not models:
            raise LLMError("服务商返回了空模型列表")
        return models

    async def complete(self, history: list[dict[str, str]]) -> str:
        if self._session is None:
            raise RuntimeError("LLMClient must be used as an async context manager")
        url = self.profile["api_base"].rstrip("/") + "/chat/completions"
        payload = {
            "model": self.profile["model"],
            "messages": [
                {"role": "system", "content": self.generation["system_prompt"]},
                *history,
            ],
            "temperature": self.generation["temperature"],
            "max_tokens": self.generation["max_tokens"],
            "stream": False,
        }
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=_headers(self.profile.get("api_key", "")),
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    raise LLMError(f"模型接口 HTTP {response.status}: {_error_message(body)}")
        except aiohttp.ClientError as exc:
            raise LLMError(f"无法连接模型服务：{exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("服务商返回了无法识别的 Chat Completions 数据") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("模型返回了空回复")
        return content.strip()


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
        if body.get("message"):
            return str(body["message"])
    return str(body)
