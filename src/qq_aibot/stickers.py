from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .media import image_mime, MediaError


class StickerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Sticker:
    id: str
    file_path: Path
    tags: tuple[str, ...]
    caption: str


class StickerLibrary:
    def __init__(self, manifest_path: Path, max_file_bytes: int = 2097152) -> None:
        self.manifest_path = Path(manifest_path).expanduser().resolve()
        self.max_file_bytes = max_file_bytes
        self.stickers: dict[str, Sticker] = {}
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.exists():
            raise StickerError(f"表情包清单不存在：{self.manifest_path}")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise StickerError(f"无法读取表情包清单：{exc}") from exc
        if not isinstance(manifest, dict) or not isinstance(manifest.get("stickers"), list):
            raise StickerError("表情包清单格式错误")
        base_dir = self.manifest_path.parent
        for entry in manifest["stickers"]:
            if not isinstance(entry, dict):
                continue
            sticker_id = entry.get("id")
            file_name = entry.get("file")
            tags = entry.get("tags", [])
            caption = entry.get("caption", "")
            if not isinstance(sticker_id, str) or not isinstance(file_name, str):
                continue
            if not sticker_id or not file_name:
                continue
            file_path = base_dir / file_name
            if not isinstance(tags, list):
                tags = []
            if not isinstance(caption, str):
                caption = ""
            self.stickers[sticker_id] = Sticker(
                id=sticker_id,
                file_path=file_path,
                tags=tuple(str(tag) for tag in tags if isinstance(tag, str)),
                caption=caption.strip(),
            )

    def get(self, sticker_id: str) -> Sticker | None:
        return self.stickers.get(sticker_id)

    def list_all(self) -> list[Sticker]:
        return sorted(self.stickers.values(), key=lambda s: s.id)

    def encode_base64(self, sticker_id: str) -> str:
        sticker = self.get(sticker_id)
        if not sticker:
            raise StickerError(f"表情包 ID 不存在：{sticker_id}")
        file_path = sticker.file_path
        file_path = file_path.resolve()
        manifest_dir = self.manifest_path.parent.resolve()
        try:
            file_path.relative_to(manifest_dir)
        except ValueError as exc:
            raise StickerError(f"表情包文件路径非法：{file_path}") from exc
        if not file_path.exists():
            raise StickerError(f"表情包文件缺失：{file_path}")
        if not file_path.is_file():
            raise StickerError(f"表情包路径不是文件：{file_path}")
        try:
            with file_path.open("rb") as stream:
                data = stream.read(self.max_file_bytes + 1)
        except OSError as exc:
            raise StickerError(f"无法读取表情包文件：{exc}") from exc
        if len(data) > self.max_file_bytes:
            raise StickerError(f"表情包文件过大：{len(data)} 字节，最大 {self.max_file_bytes}")
        suffix = file_path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            raise StickerError(f"表情包文件格式不支持：{suffix}")
        try:
            image_mime(data)
        except MediaError as exc:
            raise StickerError("表情包文件内容不是有效的图片格式") from exc
        return base64.b64encode(data).decode("ascii")


def sticker_segment(base64_data: str, caption: str = "") -> list[dict[str, Any]]:
    return [{"type": "image", "data": {"file": f"base64://{base64_data}", "sub_type": 1, "summary": caption}}]
