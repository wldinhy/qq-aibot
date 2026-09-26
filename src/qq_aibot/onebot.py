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
            result[key] = value
    return result


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

    def replace_segment(match: re.Match[str]) -> str:
        nonlocal mentioned
        segment_type, raw_params = match.group(1), match.group(2)
        params = _params(raw_params)
        if segment_type == "at" and params.get("qq") == str(self_id):
            mentioned = True
            return " "
        if segment_type == "image":
            return " [图片] "
        if segment_type == "reply":
            return " "
        return " "

    text = CQ_PATTERN.sub(replace_segment, raw_message)
    text = " ".join(text.split()).strip()
    sender = event.get("sender") or {}
    nickname = str(sender.get("card") or sender.get("nickname") or user_id)
    return IncomingMessage(
        message_type=message_type,
        user_id=user_id,
        group_id=group_id,
        self_id=self_id,
        text=text,
        nickname=nickname,
        mentioned=mentioned,
    )


def send_action(message: IncomingMessage, text: str) -> dict[str, Any]:
    if message.message_type == "group":
        return {
            "action": "send_group_msg",
            "params": {"group_id": message.group_id, "message": text},
        }
    return {
        "action": "send_private_msg",
        "params": {"user_id": message.user_id, "message": text},
    }
