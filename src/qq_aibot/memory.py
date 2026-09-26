from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class ConversationStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )
        self.connection.commit()

    def history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content FROM messages
                WHERE session_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (session_id, max(0, limit)),
        ).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def add(self, session_id: str, role: str, content: str, keep: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, int(time.time())),
            )
            self.connection.execute(
                """
                DELETE FROM messages WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (session_id, session_id, max(1, keep)),
            )

    def clear(self, session_id: str | None = None) -> int:
        with self.connection:
            if session_id is None:
                cursor = self.connection.execute("DELETE FROM messages")
            else:
                cursor = self.connection.execute(
                    "DELETE FROM messages WHERE session_id = ?", (session_id,)
                )
        return cursor.rowcount

    def close(self) -> None:
        self.connection.close()
