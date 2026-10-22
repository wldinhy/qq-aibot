from __future__ import annotations

import asyncio
import base64
import json
import secrets
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .config import project_root


class ImageGenerationError(RuntimeError):
    pass


class ComfyImageGenerator:
    """Run the user's API graphs; serialize GPU jobs independently of chat."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lock = asyncio.Lock()

    def workflow(self, mode: str, prompt: str, image: str | None = None, *, negative_prompt: str | None = None, denoise: float | None = None) -> dict[str, Any]:
        if mode not in {"text_to_image", "image_to_image"}:
            raise ImageGenerationError("未知生图模式")
        key = "image_workflow" if mode == "image_to_image" else "text_workflow"
        custom = self.config.get(key)
        path = Path(custom) if custom else Path(__file__).parent / "workflows" / f"{mode}.json"
        if custom and not path.is_absolute():
            path = project_root() / path
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
            # These bindings match the imported, already executed Animagine graphs.
            graph["2"]["inputs"]["text"] = prompt
            graph["4"]["inputs"]["seed"] = secrets.randbelow(2**53)
            graph["7"]["inputs"]["filename_prefix"] = "qq_aibot"
            if negative_prompt is not None:
                graph["8"]["inputs"]["text"] = negative_prompt
            if mode == "image_to_image":
                if not image:
                    raise ImageGenerationError("请把原图和修改要求放在同一条消息中。")
                graph["9"]["inputs"]["image"] = image
                strength = float(denoise if denoise is not None else self.config.get("img2img_denoise", 0.45))
                if not math.isfinite(strength) or not 0 < strength <= 1:
                    raise ValueError("invalid denoise")
                graph["4"]["inputs"]["denoise"] = strength
                if graph.get("11", {}).get("class_type") == "ImageScaleToTotalPixels":
                    pixels = float(self.config.get("img2img_megapixels", 0.75))
                    if not math.isfinite(pixels) or not 0.1 <= pixels <= 2:
                        raise ValueError("invalid image resolution")
                    graph["11"]["inputs"]["megapixels"] = pixels
            return graph
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ImageGenerationError(f"工作流配置无效：{path}") from exc

    async def _json(self, session: aiohttp.ClientSession, method: str, route: str, **kwargs: Any) -> Any:
        url = self.config["api_base"].rstrip("/") + route
        async with session.request(method, url, **kwargs) as response:
            body = await response.json(content_type=None)
            if response.status != 200:
                raise ImageGenerationError(f"ComfyUI HTTP {response.status}: {str(body)[:500]}")
            return body

    async def _bounded_bytes(self, response: aiohttp.ClientResponse) -> bytes:
        response.raise_for_status()
        limit = int(self.config["max_image_bytes"])
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ImageGenerationError("图片文件过大，请使用小于 20 MB 的图片。")
        if not chunks:
            raise ImageGenerationError("图片文件为空")
        return bytes(chunks)

    async def _upload(self, session: aiohttp.ClientSession, source: str) -> str:
        if source.startswith("base64://"):
            encoded = source[9:]
            if len(encoded) > int(self.config["max_image_bytes"]) * 4 // 3 + 4:
                raise ImageGenerationError("原图过大")
            data = base64.b64decode(encoded, validate=True)
            if len(data) > int(self.config["max_image_bytes"]):
                raise ImageGenerationError("原图过大")
        elif urlsplit(source).scheme in {"http", "https"}:
            async with session.get(source) as response:
                data = await self._bounded_bytes(response)
        else:
            raise ImageGenerationError("QQ 没有提供可下载的原图地址，请重新发送图片附件。")
        # Restrict uploads to supported image signatures, never arbitrary local paths.
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix, mime = ".png", "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            suffix, mime = ".jpg", "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            suffix, mime = ".gif", "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            suffix, mime = ".webp", "image/webp"
        else:
            raise ImageGenerationError("原图格式不支持，请发送 PNG、JPEG、WebP 或 GIF。")
        form = aiohttp.FormData()
        form.add_field("image", data, filename=f"qq_aibot_{secrets.token_hex(8)}{suffix}", content_type=mime)
        form.add_field("type", "input")
        form.add_field("subfolder", "qq_aibot")
        body = await self._json(session, "POST", "/upload/image", data=form)
        return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]

    async def generate(self, mode: str, prompt: str, source: str | None = None, *, negative_prompt: str | None = None, denoise: float | None = None) -> bytes:
        if mode == "image_to_image" and not source:
            raise ImageGenerationError("请把原图和修改要求放在同一条消息中。")
        async with self.lock:
            try:
                timeout = float(self.config["timeout_seconds"])
                # One deadline covers downloading, uploading, queueing and execution.
                return await asyncio.wait_for(self._generate(mode, prompt, source, negative_prompt, denoise), timeout)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
                raise ImageGenerationError("无法完成本地生图，请确认 ComfyUI 已启动、模型已安装且队列未超时。") from exc

    async def _generate(self, mode: str, prompt: str, source: str | None, negative_prompt: str | None, denoise: float | None) -> bytes:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            image = await self._upload(session, source) if mode == "image_to_image" and source else None
            graph = self.workflow(mode, prompt, image, negative_prompt=negative_prompt, denoise=denoise)
            body = await self._json(session, "POST", "/prompt", json={"prompt": graph})
            prompt_id = body["prompt_id"]
            while True:
                history = await self._json(session, "GET", f"/history/{prompt_id}")
                result = history.get(prompt_id)
                if result:
                    status = result.get("status", {})
                    if status.get("status_str") == "error":
                        raise ImageGenerationError("ComfyUI 工作流执行失败，请查看 ComfyUI 日志。")
                    images = result.get("outputs", {}).get("7", {}).get("images", [])
                    if images:
                        async with session.get(self.config["api_base"].rstrip("/") + "/view", params=images[0]) as response:
                            return await self._bounded_bytes(response)
                    if status.get("completed"):
                        raise ImageGenerationError("工作流已结束，但 SaveImage 节点没有返回图片。")
                await asyncio.sleep(1)


def image_segment(data: bytes) -> list[dict[str, Any]]:
    return [{"type": "image", "data": {"file": "base64://" + base64.b64encode(data).decode("ascii")}}]
