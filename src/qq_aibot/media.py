from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, unquote

import aiohttp


class MediaError(RuntimeError):
    pass


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise MediaError("下载内容不是受支持的图片")


async def load_image(source: str, file_id: str, config: dict[str, Any],
                     resolve: Callable[[str], Awaitable[dict[str, Any]]]) -> bytes:
    limit = int(config["max_image_bytes"])

    def check(data: bytes) -> bytes:
        if not data or len(data) > limit:
            raise MediaError("图片为空或超过大小限制")
        image_mime(data)
        return data

    def decode(value: str) -> bytes:
        if len(value) > (limit + 2) // 3 * 4:
            raise MediaError("图片超过大小限制")
        return check(base64.b64decode(value, validate=True))

    async def download(url: str) -> bytes:
        if url.startswith("base64://"):
            return decode(url[9:])
        if urlsplit(url).scheme not in {"http", "https"}:
            raise MediaError("没有可下载的图片链接")
        timeout = aiohttp.ClientTimeout(total=float(config["timeout_seconds"]))
        connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise MediaError("图片超过大小限制")
                return check(bytes(data))

    try:
        return await download(source)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, MediaError):
        pass
    if not file_id:
        raise MediaError("图片链接失效，且 QQ 没有提供原图标识")
    try:
        result = await resolve(file_id)
        if result.get("base64"):
            return decode(result["base64"])
        # Local paths are accepted only from NapCat's authenticated get_image response.
        # Never open a path supplied directly in a user's message segment.
        local = str(result.get("file") or "")
        if local.startswith("file://"):
            local = unquote(urlsplit(local).path).lstrip("/")
        if local and urlsplit(local).scheme not in {"http", "https"}:
            path = Path(local)
            if path.is_absolute() and path.is_file():
                def read_local() -> bytes:
                    with path.open("rb") as stream:
                        return check(stream.read(limit + 1))
                return await asyncio.to_thread(read_local)
        return await download(str(result.get("url") or ""))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, MediaError, TypeError) as exc:
        raise MediaError("QQ 图片下载失败，请重新发送图片；NapCat 的 Rkey 或原图下载可能不可用") from exc


def vision_part(data: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{image_mime(data)};base64," + base64.b64encode(data).decode("ascii")}}
