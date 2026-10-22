from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatEvent:
    sequence: int
    role: str
    content: str
    message_id: str = ""
    persona: str = ""


class GroupContext:
    """Bounded transcript of messages received during this bot run, per group."""

    def __init__(self, max_sessions: int = 256, max_events: int = 200):
        self.sessions: OrderedDict[str, deque[ChatEvent]] = OrderedDict()
        self.max_sessions = max_sessions
        self.max_events = max_events
        self.sequence = 0

    def add(self, session: str, role: str, content: str, *, message_id: str = "", persona: str = "") -> int:
        self.sequence += 1
        events = self.sessions.setdefault(session, deque(maxlen=self.max_events))
        self.sessions.move_to_end(session)
        events.append(ChatEvent(self.sequence, role, content, message_id, persona))
        while len(self.sessions) > self.max_sessions:
            self.sessions.popitem(last=False)
        return self.sequence

    def history(self, session: str, before: int, limit: int, persona: str) -> list[dict[str, str]]:
        events = self.sessions.get(session, ())
        # A queued request must not see later users' requests as its own prompt.
        selected = [event for event in events if event.sequence < before
                    and (event.role == "user" or event.persona == persona)]
        # Keep other people's turns even if the bot has sent several messages.
        return [{"role": event.role, "content": event.content} for event in selected[-max(1, limit):]]

    def quote(self, session: str, message_id: str) -> str:
        for event in reversed(self.sessions.get(session, ())):
            if event.message_id and event.message_id == message_id:
                return event.content[:1000]
        return ""

    def clear(self, session: str) -> None:
        self.sessions.pop(session, None)
