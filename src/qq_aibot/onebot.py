from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


CQ_PATTERN = re.compile(r"\[CQ:([^,\]]+)(?:,([^\]]*))?\]")


@dataclass(frozen=True)
class IncomingMessage:
    message_type: str
    user_id: int
    group_id: int | None
    self_id: int
    text: str
    nickname: str
    mentioned: bool
    images: tuple[str, ...] = ()
    image_files: tuple[str, ...] = ()
    timestamp: int = 0
    message_id: str = ""
    reply_to: str = ""
    context_sequence: int = 0

    @property
    def session_id(self) -> str:
        if self.message_type == "group" and self.group_id is not None:
            return f"group:{self.group_id}"
        return f"private:{self.user_id}"


def _params(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key] = _unescape(value)
    return result


def _unescape(value: str) -> str:
    return value.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


def parse_message(event: dict[str, Any]) -> IncomingMessage | None:
    if event.get("post_type") != "message" or event.get("message_type") not in {
        "private",
        "group",
    }:
        return None
    try:
        user_id = int(event["user_id"])
        self_id = int(event["self_id"])
    except (KeyError, TypeError, ValueError):
        return None
    message_type = str(event["message_type"])
    group_id = int(event["group_id"]) if message_type == "group" else None
    raw_message = event.get("raw_message", event.get("message", ""))
    if not isinstance(raw_message, str):
        raw_message = json.dumps(raw_message, ensure_ascii=False)
    mentioned = False
    reply_to = ""
    images: list[str] = []
    image_files: list[str] = []

    def segment(kind: str, params: dict[str, Any]) -> str:
        nonlocal mentioned, reply_to
        if not isinstance(params, dict):
            return " "
        if kind == "text":
            return str(params.get("text", ""))
        if kind == "at" and str(params.get("qq")) == str(self_id):
            mentioned = True
        elif kind == "at":
            return f" @QQ{params.get('qq', '')} "
        if kind == "reply":
            reply_to = str(params.get("id") or "")
        if kind == "image":
            source = params.get("url") or params.get("file")
            if isinstance(source, str):
                images.append(source)
                image_files.append(str(params.get("file") or ""))
            summary = str(params.get("summary") or "").strip()
            return f" [图片{('：' + summary) if summary else ''}] "
        if kind == "face":
            face_id = str(params.get("id", ""))
            names = {"0": "微笑", "1": "撇嘴", "3": "发呆", "4": "得意", "5": "流泪", "6": "害羞", "7": "闭嘴", "8": "睡觉", "9": "大哭", "10": "尴尬", "11": "发怒", "12": "调皮", "13": "呲牙", "14": "惊讶", "15": "难过", "20": "偷笑", "21": "可爱", "22": "白眼", "28": "憨笑", "32": "疑问", "39": "再见", "42": "鼓掌", "49": "委屈", "76": "赞", "96": "冷汗"}
            raw = params.get("raw") or {}
            name = params.get("name") or (raw.get("faceName") if isinstance(raw, dict) else None) or names.get(face_id)
            return f" [QQ表情：{name or ('ID ' + face_id)}] "
        if kind == "mface":
            emoji_id = str(params.get("emoji_id") or "")
            source = params.get("url")
            if not source and re.fullmatch(r"[a-fA-F0-9]{32}", emoji_id):
                source = f"https://gxh.vip.qq.com/club/item/parcel/item/{emoji_id[:2]}/{emoji_id}/raw300.gif"
            if isinstance(source, str) and source:
                images.append(source)
                image_files.append(str(params.get("file") or ""))
            return f" [表情包：{params.get('summary') or '自定义表情'}] "
        return " "

    def replace_segment(match: re.Match[str]) -> str:
        segment_type, raw_params = match.group(1), match.group(2)
        return segment(segment_type, _params(raw_params))

    parts = event.get("message")
    if isinstance(parts, list):
        text = "".join(segment(part.get("type", ""), part.get("data", {})) for part in parts if isinstance(part, dict))
    else:
        text = _unescape(CQ_PATTERN.sub(replace_segment, raw_message))
    text = " ".join(text.split()).strip()
    sender = event.get("sender") or {}
    nickname = str(sender.get("card") or sender.get("nickname") or user_id)
    try:
        timestamp = int(event.get("time", 0))
    except (TypeError, ValueError):
        timestamp = 0
    return IncomingMessage(
        message_type=message_type,
        user_id=user_id,
        group_id=group_id,
        self_id=self_id,
        text=text,
        nickname=nickname,
        mentioned=mentioned,
        images=tuple(images),
        image_files=tuple(image_files),
        timestamp=timestamp,
        message_id=str(event.get("message_id") or ""),
        reply_to=reply_to,
    )


def send_action(message: IncomingMessage, text: str | list[dict[str, Any]]) -> dict[str, Any]:
    if message.message_type == "group":
        return {
            "action": "send_group_msg",
            "params": {"group_id": message.group_id, "message": text},
        }
    return {
        "action": "send_private_msg",
        "params": {"user_id": message.user_id, "message": text},
    }
